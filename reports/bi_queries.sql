-- ecomgen BI smoke test (DuckDB)
-- Dataset: ecomgen generate --preset garden-decor --markets de,at,fr --customers 5000
--          --months 12 --seed 42 --out <DATA_DIR>
-- Run:     replace {DATA_DIR} with the export directory, then execute in DuckDB
--          (reports/bi_queries_output.md was produced by splitting on "-- name:").
-- Money is in each order's local currency (orders.currency). marketing_spend has no
-- currency column; it is spend in the market's currency. Revenue below is NET of VAT.

-- name: setup
CREATE OR REPLACE VIEW products        AS SELECT * FROM read_csv_auto('{DATA_DIR}/products.csv');
CREATE OR REPLACE VIEW variants        AS SELECT * FROM read_csv_auto('{DATA_DIR}/variants.csv');
CREATE OR REPLACE VIEW customers       AS SELECT * FROM read_csv_auto('{DATA_DIR}/customers.csv');
CREATE OR REPLACE VIEW orders          AS SELECT * FROM read_csv_auto('{DATA_DIR}/orders.csv');
CREATE OR REPLACE VIEW order_items     AS SELECT * FROM read_csv_auto('{DATA_DIR}/order_items.csv');
CREATE OR REPLACE VIEW returns         AS SELECT * FROM read_csv_auto('{DATA_DIR}/returns.csv');
CREATE OR REPLACE VIEW marketing_spend AS SELECT * FROM read_csv_auto('{DATA_DIR}/marketing_spend.csv');

-- name: q0_inferred_types
-- Sanity: does DuckDB infer sensible types without hints?
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE column_name IN ('created_at', 'total', 'unit_price', 'spend', 'is_repeat', 'discount_code',
                      'markets', 'date', 'price_eur')
ORDER BY table_name, column_name;

-- name: q1_revenue_by_market_month
-- Net revenue (ex VAT, after discount, incl. shipping charged) by market and month.
SELECT market, currency, strftime(created_at, '%Y-%m') AS month,
       count(*) AS orders,
       round(sum(subtotal - discount + shipping), 2) AS net_revenue,
       round(sum(total), 2) AS gross_revenue
FROM orders
GROUP BY ALL
ORDER BY market, month;

-- name: q2_aov
SELECT market, currency, count(*) AS orders,
       round(avg(total), 2) AS aov_gross,
       round(avg(subtotal - discount), 2) AS aov_net_merch,
       round(median(total), 2) AS median_gross,
       round(avg((discount > 0)::INT), 3) AS discounted_share
FROM orders GROUP BY ALL ORDER BY market;

-- name: q3_repeat_customer_rate
WITH per_customer AS (
    SELECT c.market, c.id, count(o.id) AS n_orders
    FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
    GROUP BY ALL)
SELECT market,
       count(*) AS customers,
       count(*) FILTER (WHERE n_orders >= 1) AS buyers,
       round(count(*) FILTER (WHERE n_orders >= 2) / count(*) FILTER (WHERE n_orders >= 1), 3)
           AS repeat_customer_rate,
       round(avg(n_orders) FILTER (WHERE n_orders >= 1), 2) AS orders_per_buyer
FROM per_customer GROUP BY ALL ORDER BY market;

-- name: q4_return_rate_by_category
SELECT p.category,
       count(*) AS items_sold,
       count(r.id) AS items_returned,
       round(count(r.id) / count(*), 3) AS item_return_rate,
       round(sum(r.refund_amount) / sum(oi.unit_price * oi.quantity), 3) AS value_return_rate,
       mode(r.reason) AS top_reason
FROM order_items oi
JOIN variants v ON v.id = oi.variant_id
JOIN products p ON p.id = v.product_id
LEFT JOIN returns r ON r.order_item_id = oi.id
GROUP BY ALL ORDER BY item_return_rate DESC;

-- name: q5_return_rate_by_market
SELECT o.market, count(DISTINCT oi.id) AS items, count(r.id) AS returned,
       round(count(r.id) / count(DISTINCT oi.id), 4) AS item_return_rate
FROM order_items oi JOIN orders o ON o.id = oi.order_id
LEFT JOIN returns r ON r.order_item_id = oi.id
GROUP BY ALL ORDER BY item_return_rate DESC;

-- name: q6_contribution_margin_by_market
-- CM = net merchandise revenue - COGS - refunds (+ COGS of returned units not recovered)
--      - paid marketing. COGS uses cost_eur (all three markets are EUR here; for CHF/GBP
--      an FX rate would be needed and none is exported).
WITH items AS (
    SELECT o.market, oi.id, oi.quantity, oi.unit_price, p.cost_eur,
           oi.unit_price * oi.quantity * (1 - o.discount / o.subtotal) AS net_line
    FROM order_items oi JOIN orders o ON o.id = oi.order_id
    JOIN variants v ON v.id = oi.variant_id JOIN products p ON p.id = v.product_id),
agg AS (
    SELECT i.market, sum(net_line) AS net_merch, sum(cost_eur * quantity) AS cogs,
           sum(coalesce(r.refund_amount, 0)) AS refunds
    FROM items i LEFT JOIN returns r ON r.order_item_id = i.id GROUP BY ALL),
mkt AS (SELECT market, sum(spend) AS spend FROM marketing_spend GROUP BY ALL)
SELECT a.market, round(net_merch, 2) AS net_merch, round(cogs, 2) AS cogs,
       round(refunds, 2) AS refunds, round(spend, 2) AS marketing,
       round(net_merch - cogs - refunds - spend, 2) AS contribution,
       round((net_merch - cogs - refunds - spend) / net_merch, 3) AS cm_pct
FROM agg a JOIN mkt m USING (market) ORDER BY market;

-- name: q7_roas_cac_by_channel
-- Attribution as documented: orders -> customer's acquisition_channel.
WITH rev AS (
    SELECT c.acquisition_channel AS channel, sum(o.subtotal - o.discount) AS net_rev,
           count(*) FILTER (WHERE NOT o.is_repeat) AS new_customers, count(*) AS orders
    FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY ALL),
sp AS (SELECT channel, sum(spend) AS spend, sum(attributed_orders) AS attributed,
              sum(clicks) AS clicks, sum(impressions) AS impressions
       FROM marketing_spend GROUP BY ALL)
SELECT channel, round(spend, 2) AS spend, orders, attributed, new_customers,
       round(net_rev / nullif(spend, 0), 2) AS roas,
       round(spend / nullif(new_customers, 0), 2) AS cac,
       round(clicks / nullif(impressions, 0), 4) AS ctr,
       round(attributed / nullif(clicks, 0), 4) AS cvr
FROM sp JOIN rev USING (channel) ORDER BY spend DESC;

-- name: q8_cohort_retention
-- First-order month cohort; share of cohort ordering again in months +1, +2, +3, +6.
WITH o AS (SELECT customer_id, date_trunc('month', created_at) AS m FROM orders),
first AS (SELECT customer_id, min(m) AS cohort FROM o GROUP BY ALL),
act AS (SELECT DISTINCT f.cohort, o.customer_id, datediff('month', f.cohort, o.m) AS k
        FROM o JOIN first f USING (customer_id))
-- Cells whose month lies beyond the last order month are right-censored and shown as NULL.
, last_m AS (SELECT max(m) AS last_m FROM o)
SELECT strftime(cohort, '%Y-%m') AS cohort,
       count(DISTINCT customer_id) FILTER (WHERE k = 0) AS size,
       CASE WHEN cohort + INTERVAL 1 MONTH <= any_value(last_m) THEN
           round(count(DISTINCT customer_id) FILTER (WHERE k = 1) / size, 3) END AS m1,
       CASE WHEN cohort + INTERVAL 2 MONTH <= any_value(last_m) THEN
           round(count(DISTINCT customer_id) FILTER (WHERE k = 2) / size, 3) END AS m2,
       CASE WHEN cohort + INTERVAL 3 MONTH <= any_value(last_m) THEN
           round(count(DISTINCT customer_id) FILTER (WHERE k = 3) / size, 3) END AS m3,
       CASE WHEN cohort + INTERVAL 6 MONTH <= any_value(last_m) THEN
           round(count(DISTINCT customer_id) FILTER (WHERE k = 6) / size, 3) END AS m6
FROM act, last_m GROUP BY cohort ORDER BY cohort;

-- name: q9_top_products
-- Product title is not unique; group by id to avoid merging distinct products.
SELECT p.id, p.title, p.category, sum(oi.quantity) AS units,
       round(sum(oi.unit_price * oi.quantity), 2) AS merch_revenue,
       (SELECT count(*) FROM products p2 WHERE p2.title = p.title) AS products_with_this_title
FROM order_items oi JOIN variants v ON v.id = oi.variant_id JOIN products p ON p.id = v.product_id
GROUP BY ALL ORDER BY merch_revenue DESC LIMIT 10;

-- name: q10_stockouts_by_month
-- When does the catalogue sell out? (variants at 0 remaining, by month of last sale)
WITH last_sale AS (
    SELECT oi.variant_id, max(o.created_at) AS last_sold
    FROM order_items oi JOIN orders o ON o.id = oi.order_id GROUP BY ALL)
SELECT strftime(last_sold, '%Y-%m') AS month, count(*) AS variants_sold_out
FROM variants v JOIN last_sale l ON l.variant_id = v.id
WHERE v.inventory = 0 GROUP BY ALL ORDER BY month;

-- name: q11_marketing_daily_shape
-- Does paid media run every day? (spend only exists on days with attributed orders)
SELECT channel,
       count(*) AS days,
       count(*) FILTER (WHERE spend > 0) AS days_with_spend,
       count(*) FILTER (WHERE impressions = 0) AS days_zero_impressions,
       round(sum(spend) / nullif(sum(attributed_orders), 0), 2) AS spend_per_attributed_order
FROM marketing_spend GROUP BY ALL ORDER BY channel;

-- name: q12_null_audit
SELECT 'orders' AS t, count(*) FILTER (WHERE total IS NULL OR created_at IS NULL) AS nulls FROM orders
UNION ALL SELECT 'order_items', count(*) FILTER (WHERE unit_price IS NULL) FROM order_items
UNION ALL SELECT 'returns', count(*) FILTER (WHERE refund_amount IS NULL) FROM returns
UNION ALL SELECT 'marketing', count(*) FILTER (WHERE spend IS NULL) FROM marketing_spend
UNION ALL SELECT 'orders.discount_code empty', count(*) FILTER (WHERE discount_code IS NULL) FROM orders;
