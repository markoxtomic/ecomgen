-- ecomgen BI smoke test (DuckDB)
-- Run: replace {DATA_DIR} with one validated export directory, execute setup, then
--      execute each named query. reports/bi_queries_output.md records the checked run.
--
-- Accounting semantics:
--   subtotal = SUM(unit_price * quantity)                    -- gross, VAT included
--   total    = subtotal - discount + shipping                -- gross, VAT included
--   tax      = total * vat_rate / (1 + vat_rate), rounded    -- included in total
--   net revenue excluding VAT = total - tax
--
-- Never aggregate money across currencies without explicit FX conversion.
-- marketing_spend exposes currency and new_customers; it has no attributed_orders.
-- The manifest is authoritative for selected markets and time-window boundaries.

-- name: setup
SET TimeZone = 'UTC';
CREATE OR REPLACE VIEW products        AS SELECT * FROM read_csv_auto('{DATA_DIR}/products.csv');
CREATE OR REPLACE VIEW variants        AS SELECT * FROM read_csv_auto('{DATA_DIR}/variants.csv');
CREATE OR REPLACE VIEW customers       AS SELECT * FROM read_csv_auto('{DATA_DIR}/customers.csv');
CREATE OR REPLACE VIEW orders          AS SELECT * FROM read_csv_auto('{DATA_DIR}/orders.csv');
CREATE OR REPLACE VIEW order_items     AS SELECT * FROM read_csv_auto('{DATA_DIR}/order_items.csv');
CREATE OR REPLACE VIEW returns         AS SELECT * FROM read_csv_auto('{DATA_DIR}/returns.csv');
CREATE OR REPLACE VIEW marketing_spend AS SELECT * FROM read_csv_auto('{DATA_DIR}/marketing_spend.csv');
CREATE OR REPLACE VIEW manifest        AS SELECT * FROM read_json_auto('{DATA_DIR}/manifest.json');

-- Used only to reconcile market-local acquisition dates with UTC customer timestamps.
CREATE OR REPLACE VIEW market_timezones(market, timezone_name) AS
SELECT * FROM (VALUES
    ('at', 'Europe/Vienna'), ('be', 'Europe/Brussels'), ('ch', 'Europe/Zurich'),
    ('de', 'Europe/Berlin'), ('es', 'Europe/Madrid'), ('fr', 'Europe/Paris'),
    ('it', 'Europe/Rome'), ('nl', 'Europe/Amsterdam'), ('uk', 'Europe/London')
);

-- name: q0_inferred_types
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE column_name IN (
    'created_at', 'total', 'tax', 'unit_price', 'spend', 'new_customers',
    'is_repeat', 'discount_code', 'markets', 'date', 'price_eur', 'currency'
)
ORDER BY table_name, column_name;

-- name: q1_manifest_window
-- Zero violations are expected. order_window_end is exclusive; return_cutoff is inclusive.
WITH bounds AS (
    SELECT
        CAST(metadata.order_window_start AS TIMESTAMPTZ) AS window_start,
        CAST(metadata.order_window_end AS TIMESTAMPTZ) AS window_end,
        CAST(metadata.return_cutoff AS TIMESTAMPTZ) AS return_cutoff,
        metadata.return_delay_days.min AS min_return_days,
        metadata.return_delay_days.max AS max_return_days
    FROM manifest
)
SELECT
    any_value(window_start) AS order_window_start,
    any_value(window_end) AS order_window_end_exclusive,
    any_value(return_cutoff) AS return_cutoff_inclusive,
    count(*) FILTER (WHERE o.created_at < window_start OR o.created_at >= window_end)
        AS orders_outside_window,
    (SELECT count(*) FROM returns r, bounds b WHERE r.created_at > b.return_cutoff)
        AS returns_after_cutoff,
    (SELECT count(*)
     FROM returns r
     JOIN orders ro ON ro.id = r.order_id
     CROSS JOIN bounds b
     WHERE r.created_at - ro.created_at < b.min_return_days * INTERVAL 1 DAY
        OR r.created_at - ro.created_at > b.max_return_days * INTERVAL 1 DAY)
        AS returns_outside_delay
FROM orders o CROSS JOIN bounds;

-- name: q2_catalog_semantics
-- Every selected market should receive every product, and product titles should be unique.
WITH selected AS (
    SELECT unnest(arguments.markets) AS market FROM manifest
),
missing AS (
    SELECT p.id, s.market
    FROM products p CROSS JOIN selected s
    WHERE NOT json_contains(p.markets, to_json(s.market))
)
SELECT
    (SELECT count(*) FROM products) AS products,
    (SELECT count(DISTINCT title) FROM products) AS unique_titles,
    (SELECT count(*) FROM missing) AS missing_product_market_pairs;

-- name: q3_revenue_by_market_month
SELECT
    market,
    currency,
    strftime(created_at AT TIME ZONE 'UTC', '%Y-%m') AS month,
    count(*) AS orders,
    round(sum(total), 2) AS gross_revenue_vat_inclusive,
    round(sum(tax), 2) AS included_vat,
    round(sum(total - tax), 2) AS net_revenue_ex_vat,
    round(sum(shipping), 2) AS gross_shipping
FROM orders
GROUP BY ALL
ORDER BY market, month;

-- name: q4_aov
SELECT
    market,
    currency,
    count(*) AS orders,
    round(avg(total), 2) AS aov_gross_vat_inclusive,
    round(avg(total - tax), 2) AS aov_net_ex_vat,
    round(median(total), 2) AS median_gross,
    round(avg((discount > 0)::INT), 3) AS discounted_share
FROM orders
GROUP BY ALL
ORDER BY market;

-- name: q5_repeat_customer_rate
WITH per_customer AS (
    SELECT c.market, c.id, count(o.id) AS n_orders
    FROM customers c
    LEFT JOIN orders o ON o.customer_id = c.id
    GROUP BY ALL
)
SELECT
    market,
    count(*) AS customers,
    count(*) FILTER (WHERE n_orders >= 1) AS buyers,
    round(
        count(*) FILTER (WHERE n_orders >= 2)
        / nullif(count(*) FILTER (WHERE n_orders >= 1), 0),
        3
    ) AS repeat_customer_rate,
    round(avg(n_orders) FILTER (WHERE n_orders >= 1), 2) AS orders_per_buyer
FROM per_customer
GROUP BY ALL
ORDER BY market;

-- name: q6_return_rate_by_category
WITH lines AS (
    SELECT
        oi.id,
        p.category,
        oi.unit_price * oi.quantity
            * (1 - o.discount / nullif(o.subtotal, 0)) AS gross_paid_line
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    JOIN variants v ON v.id = oi.variant_id
    JOIN products p ON p.id = v.product_id
)
SELECT
    l.category,
    count(*) AS items_sold,
    count(r.id) AS items_returned,
    round(count(r.id) / count(*), 3) AS item_return_rate,
    round(sum(coalesce(r.refund_amount, 0)) / nullif(sum(l.gross_paid_line), 0), 3)
        AS gross_value_return_rate,
    mode(r.reason) AS top_reason
FROM lines l
LEFT JOIN returns r ON r.order_item_id = l.id
GROUP BY ALL
ORDER BY item_return_rate DESC;

-- name: q7_return_rate_by_market
SELECT
    o.market,
    o.currency,
    count(DISTINCT oi.id) AS items,
    count(r.id) AS returned,
    round(count(r.id) / count(DISTINCT oi.id), 4) AS item_return_rate,
    round(sum(coalesce(r.refund_amount, 0)), 2) AS gross_refunds_vat_inclusive
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
LEFT JOIN returns r ON r.order_item_id = oi.id
GROUP BY ALL
ORDER BY item_return_rate DESC;

-- name: q8_contribution_margin_by_market
-- Product cost is exported only as EUR. Contribution is therefore NULL for CHF/GBP
-- markets rather than silently mixing cost_eur with local-currency revenue and spend.
WITH sales AS (
    SELECT
        market,
        currency,
        sum(total) AS gross_sales,
        sum(tax) AS included_vat,
        sum(total - tax) AS net_sales_ex_vat
    FROM orders
    GROUP BY ALL
),
costs AS (
    SELECT o.market, sum(p.cost_eur * oi.quantity) AS cogs_eur
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    JOIN variants v ON v.id = oi.variant_id
    JOIN products p ON p.id = v.product_id
    GROUP BY ALL
),
refunds_net AS (
    SELECT
        o.market,
        sum(r.refund_amount) AS gross_refunds,
        sum(r.refund_amount * (1 - o.tax / nullif(o.total, 0))) AS refunds_ex_vat
    FROM returns r
    JOIN orders o ON o.id = r.order_id
    GROUP BY ALL
),
marketing AS (
    SELECT market, currency, sum(spend) AS spend
    FROM marketing_spend
    GROUP BY ALL
)
SELECT
    s.market,
    s.currency,
    round(s.gross_sales, 2) AS gross_sales_vat_inclusive,
    round(s.included_vat, 2) AS included_vat,
    round(s.net_sales_ex_vat, 2) AS net_sales_ex_vat,
    round(coalesce(r.refunds_ex_vat, 0), 2) AS refunds_ex_vat,
    round(c.cogs_eur, 2) AS cogs_eur,
    round(m.spend, 2) AS marketing_spend_local,
    CASE WHEN s.currency = 'EUR' THEN round(
        s.net_sales_ex_vat - coalesce(r.refunds_ex_vat, 0) - c.cogs_eur - m.spend,
        2
    ) END AS contribution_eur,
    CASE WHEN s.currency = 'EUR' THEN round(
        (
            s.net_sales_ex_vat - coalesce(r.refunds_ex_vat, 0) - c.cogs_eur - m.spend
        ) / nullif(s.net_sales_ex_vat, 0),
        3
    ) END AS contribution_pct
FROM sales s
JOIN costs c USING (market)
JOIN marketing m USING (market, currency)
LEFT JOIN refunds_net r USING (market)
ORDER BY s.market;

-- name: q9_roas_cac_by_channel
-- Revenue is attributed through the customer's acquisition_channel. new_customers
-- comes directly from marketing_spend; no order-attribution field exists there.
WITH revenue AS (
    SELECT
        o.market,
        o.currency,
        c.acquisition_channel AS channel,
        count(*) AS orders,
        sum(o.total - o.tax) AS net_revenue_ex_vat
    FROM orders o
    JOIN customers c ON c.id = o.customer_id
    GROUP BY ALL
),
acquisition AS (
    SELECT
        market,
        currency,
        channel,
        sum(spend) AS spend,
        sum(new_customers) AS new_customers,
        sum(clicks) AS clicks,
        sum(impressions) AS impressions
    FROM marketing_spend
    GROUP BY ALL
)
SELECT
    a.market,
    a.currency,
    a.channel,
    round(a.spend, 2) AS spend,
    a.new_customers,
    coalesce(r.orders, 0) AS lifetime_orders,
    round(r.net_revenue_ex_vat / nullif(a.spend, 0), 2) AS net_roas,
    round(a.spend / nullif(a.new_customers, 0), 2) AS cac,
    round(a.clicks / nullif(a.impressions, 0), 4) AS ctr,
    round(a.new_customers / nullif(a.clicks, 0), 4) AS acquisition_cvr
FROM acquisition a
LEFT JOIN revenue r USING (market, currency, channel)
ORDER BY a.market, a.spend DESC;

-- name: q10_marketing_customer_reconciliation
-- Customer timestamps are UTC; convert them back to market local time before joining
-- the date-grained marketing table. Zero mismatches are expected.
WITH acquired AS (
    SELECT
        CAST(timezone(t.timezone_name, c.created_at) AS DATE) AS date,
        c.market,
        c.acquisition_channel AS channel,
        count(*) AS customer_rows
    FROM customers c
    JOIN market_timezones t USING (market)
    GROUP BY ALL
),
planned AS (
    SELECT date, market, channel, sum(new_customers) AS new_customers
    FROM marketing_spend
    GROUP BY ALL
)
SELECT
    count(*) FILTER (
        WHERE coalesce(p.new_customers, 0) <> coalesce(a.customer_rows, 0)
    ) AS mismatched_date_market_channel_rows,
    sum(coalesce(p.new_customers, 0)) AS marketing_new_customers,
    sum(coalesce(a.customer_rows, 0)) AS customer_rows
FROM planned p
FULL OUTER JOIN acquired a USING (date, market, channel);

-- name: q11_cohort_retention
-- First-order UTC-month cohort. Cells beyond the last order month are right-censored.
WITH activity AS (
    SELECT customer_id, date_trunc('month', created_at AT TIME ZONE 'UTC') AS month
    FROM orders
),
first_order AS (
    SELECT customer_id, min(month) AS cohort FROM activity GROUP BY ALL
),
cohort_activity AS (
    SELECT DISTINCT
        f.cohort,
        a.customer_id,
        datediff('month', f.cohort, a.month) AS month_number
    FROM activity a
    JOIN first_order f USING (customer_id)
),
last_month AS (
    SELECT max(month) AS month FROM activity
)
SELECT
    strftime(cohort, '%Y-%m') AS cohort,
    count(DISTINCT customer_id) FILTER (WHERE month_number = 0) AS size,
    CASE WHEN cohort + INTERVAL 1 MONTH <= any_value(last_month.month) THEN
        round(count(DISTINCT customer_id) FILTER (WHERE month_number = 1) / size, 3)
    END AS m1,
    CASE WHEN cohort + INTERVAL 2 MONTH <= any_value(last_month.month) THEN
        round(count(DISTINCT customer_id) FILTER (WHERE month_number = 2) / size, 3)
    END AS m2,
    CASE WHEN cohort + INTERVAL 3 MONTH <= any_value(last_month.month) THEN
        round(count(DISTINCT customer_id) FILTER (WHERE month_number = 3) / size, 3)
    END AS m3,
    CASE WHEN cohort + INTERVAL 6 MONTH <= any_value(last_month.month) THEN
        round(count(DISTINCT customer_id) FILTER (WHERE month_number = 6) / size, 3)
    END AS m6
FROM cohort_activity, last_month
GROUP BY cohort
ORDER BY cohort;

-- name: q12_top_products
-- Keep id in the grouping key even though current generation requires unique titles.
SELECT
    p.id,
    p.title,
    p.category,
    sum(oi.quantity) AS units,
    round(sum(oi.unit_price * oi.quantity), 2) AS gross_merchandise_value,
    (SELECT count(*) FROM products p2 WHERE p2.title = p.title) AS title_occurrences
FROM order_items oi
JOIN variants v ON v.id = oi.variant_id
JOIN products p ON p.id = v.product_id
GROUP BY ALL
ORDER BY gross_merchandise_value DESC
LIMIT 10;

-- name: q13_ending_inventory
-- This is an end-of-export snapshot, not a historical stockout date.
SELECT
    p.category,
    count(*) AS variants,
    sum(v.inventory) AS ending_units,
    count(*) FILTER (WHERE v.inventory = 0) AS zero_inventory_variants,
    round(avg(v.inventory), 1) AS average_ending_units
FROM variants v
JOIN products p ON p.id = v.product_id
GROUP BY ALL
ORDER BY p.category;

-- name: q14_marketing_daily_shape
SELECT
    market,
    currency,
    channel,
    count(*) AS days,
    count(*) FILTER (WHERE spend > 0) AS days_with_spend,
    count(*) FILTER (WHERE impressions = 0) AS days_zero_impressions,
    sum(new_customers) AS new_customers,
    round(sum(spend) / nullif(sum(new_customers), 0), 2) AS spend_per_new_customer
FROM marketing_spend
GROUP BY ALL
ORDER BY market, channel;

-- name: q15_null_audit
SELECT 'orders' AS table_name,
       count(*) FILTER (WHERE total IS NULL OR tax IS NULL OR created_at IS NULL) AS nulls
FROM orders
UNION ALL
SELECT 'order_items', count(*) FILTER (WHERE unit_price IS NULL) FROM order_items
UNION ALL
SELECT 'returns', count(*) FILTER (WHERE refund_amount IS NULL) FROM returns
UNION ALL
SELECT 'marketing_spend',
       count(*) FILTER (WHERE spend IS NULL OR currency IS NULL OR new_customers IS NULL)
FROM marketing_spend;
