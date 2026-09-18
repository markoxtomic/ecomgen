# BI smoke-test output

**Checked:** 2026-09-18 with DuckDB 1.5.5

The checked-in sample was generated with:

```text
ecomgen generate --preset garden-decor --markets de,at,fr --customers 500 \
  --months 12 --start-date 2024-01-01 --seed 42 \
  --out examples/sample_output --format all --shopify-export
```

Its manifest declares schema version 1, gross VAT-inclusive pricing, the half-open
order window `2024-01-01` to `2025-01-01`, and the inclusive return cutoff
`2025-01-31`. `ecomgen validate --path examples/sample_output` reported:

```text
Valid dataset: 7,164 rows across 7 tables
```

All 17 named sections in `reports/bi_queries.sql` executed successfully (`setup` and
`q0` through `q15`). Key results:

- `q0_inferred_types`: timestamps were inferred as `TIMESTAMP WITH TIME ZONE`,
  dates as `DATE`, booleans as `BOOLEAN`, counts as `BIGINT`, and exported money as
  `DOUBLE`.
- `q1_manifest_window`: 0 orders outside the order window, 0 returns after the
  cutoff, and 0 returns outside the configured 3–30 day delay.
- `q2_catalog_semantics`: 36 products, 36 unique titles, and 0 missing
  product/market pairs.
- `q3_revenue_by_market_month`: 36 market-month rows; annual gross revenue was
  AT EUR 4,371.62, DE EUR 33,856.99, and FR EUR 34,080.23.
- `q4_aov`: gross AOV was AT 145.72, DE 170.99, and FR 235.04.
- `q5_repeat_customer_rate`: buyer repeat rates were AT 0.217, DE 0.201, and
  FR 0.330.
- `q6_return_rate_by_category`: item return rates were outdoor-lighting 0.089,
  planters 0.057, and garden-furniture 0.044.
- `q7_return_rate_by_market`: item return rates were DE 0.0660, AT 0.0652, and
  FR 0.0618.
- `q8_contribution_margin_by_market`: contribution after ex-VAT refunds, COGS, and
  marketing was AT -327.79, DE -522.48, and FR 3,744.12. All selected markets use
  EUR, so no currency-safety NULL was needed in this sample.
- `q9_roas_cac_by_channel`: 15 market/channel rows were returned. Organic and
  direct channels correctly have NULL ROAS/CTR when spend or impressions is zero;
  their CAC is 0 because customers were acquired with zero spend.
- `q10_marketing_customer_reconciliation`: 0 mismatched date/market/channel rows;
  both sources total 500 acquired customers.
- `q11_cohort_retention`: 12 monthly cohorts were returned. Later M1/M2/M3/M6 cells
  are NULL where the observation window right-censors them.
- `q12_top_products`: every returned product had `title_occurrences = 1`; the
  highest-GMV product was `Modular Acacia Bench` at EUR 8,246.00.
- `q13_ending_inventory`: 0 zero-inventory variants across all three categories.
- `q14_marketing_daily_shape`: 15 market/channel rows covered 366 days each and
  reconciled to 500 new customers.
- `q15_null_audit`: 0 audited nulls in orders, order items, returns, and marketing.

These figures are deterministic for the checked-in dependency lock, configuration,
arguments, and seed. They are smoke-test evidence, not commercial forecasts.
