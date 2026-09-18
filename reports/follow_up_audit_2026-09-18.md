# ecomgen follow-up audit

**Date:** 2026-09-18  
**Scope:** Documentation and BI follow-up only (plan Tasks 5–6)

## Status

The implementation, documentation, checked-in sample, and BI smoke output now use the
corrected schema and semantics. Local release gates completed successfully. This
report does not supersede the historical
`reports/correctness_audit.md` or `reports/test_report.md`, and it does not treat
their pre-correction results as evidence for the current worktree.

## Implemented in this follow-up

- `README.md` now defines gross VAT-inclusive pricing and the exact reconciliation:
  `total = subtotal - discount + shipping`, included
  `tax = ROUND_HALF_UP(total * vat_rate / (1 + vat_rate), 0.01)`, and
  net revenue excluding VAT as `total - tax`.
- Refund documentation now uses the VAT-inclusive paid line after its pro-rata
  discount. It does not add VAT again and does not refund shipping.
- Catalog documentation now states that every product is available in every selected
  market and that category-local title vocabularies produce unique catalog titles.
- Timestamp documentation now distinguishes market-local sampling from UTC
  serialization, including local-date marketing reconciliation.
- Window documentation now treats `order_window_end` as exclusive and
  `return_cutoff` as the inclusive 30-day boundary supplied by manifest metadata.
- Marketing documentation and BI queries use the current fields `currency` and
  `new_customers`; they do not reference the removed `attributed_orders` field.
- Validation documentation now makes `manifest.json` authoritative for file hashes,
  row counts, selected markets, schema semantics, and date boundaries.
- `reports/bi_queries.sql` now keeps currency in monetary grouping keys, separates
  gross VAT-inclusive revenue from included VAT and ex-VAT revenue, derives
  acquisition metrics from `new_customers`, reconciles customer UTC timestamps to
  market-local acquisition dates, checks manifest windows and all-market assortment,
  and avoids presenting an end-inventory snapshot as a historical stockout date.
- The contribution-margin query emits results only for EUR markets. CHF/GBP results
  remain NULL because `cost_eur` cannot be combined with local-currency revenue or
  spend without an exported or explicitly supplied FX conversion.
- `README.md` now includes a manual Shopify development-store import checklist. It
  separates local file validation from a real Shopify importer check and explicitly
  states that automated checks do not connect to or mutate a live store.

## Verified implementation

Source, tests, generated artifacts, and executable checks confirm that the current
worktree defines:

- gross VAT-inclusive pricing metadata in `ecomgen.pipeline`;
- included-VAT order arithmetic and market-local order sampling in the order
  generator;
- VAT-inclusive discounted-line refunds and a 3–30 day return delay;
- all-selected-market catalog assignment and category-local title inputs;
- UTC timestamp serialization backed by market-local timezone samplers;
- marketing `currency` and `new_customers` fields with no `attributed_orders`;
- manifest-required CLI validation and manifest-aware integrity checks.

## Verification evidence

- Python 3.11.16: 214 tests passed with 91.87% statement coverage.
- Python 3.14.7: 214 tests passed with 91.63% statement coverage.
- `ruff check .`, `ruff format --check .`, and `git diff --check` passed.
- The source distribution and universal wheel built successfully.
- Three CLI exports confirmed byte-identical same-seed output across all 16 files,
  while a different seed changed the output; all three exports passed CLI validation.
- `examples/sample_output` was regenerated with 500 customers, 12 months,
  `garden-decor`, markets `de,at,fr`, start date `2024-01-01`, seed 42, CSV+JSON, and
  Shopify export. Validation reported `Valid dataset: 7,164 rows across 7 tables`.
- DuckDB 1.5.5 executed all 17 sections in `reports/bi_queries.sql`; checked results
  are recorded in `reports/bi_queries_output.md`.

## External manual gate

No Shopify store was mutated. Acceptance by Shopify itself remains a deliberate
manual check: perform the README checklist in a disposable development store and
record Shopify's import preview, warnings, counts, and spot checks before claiming a
live import was accepted.

## Follow-up verdict

The remaining audit findings are closed at the code, automated-test, generated-sample,
and BI smoke-test levels. The only intentionally external evidence is the optional
manual Shopify development-store import; the automated validator does not claim to
replace Shopify's importer.
