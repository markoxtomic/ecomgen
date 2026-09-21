# ecomgen fix verification (v3)

**Date:** 2026-09-21 · **Branch:** `fix/stress-test-findings-cursor` · **Baseline:** the open items in [`test_report_v2.md`](test_report_v2.md)
**Host:** Windows 11 Pro, 16 cores · **Pythons:** 3.14.7 and 3.11.16, fresh venvs

This round closes the items v2 left open. Every claim below was re-measured against the code, with the independent harness (pandas audit, corruption suite, DuckDB, Shopify spec check, agent test) that imports nothing from `ecomgen` but its CLI.

## What changed

| Item | v2 state | Fix | Commit |
| --- | --- | --- | --- |
| **N1** COGS from the gross price | realised cost 1.19× the configured band | cost ratios apply to the net catalog price (gross ÷ 1.19 reference VAT); margin now moves with each market's VAT | `85aa967` |
| **M5** no severity, no tail check, no realism ranges, no 13/13 test | 4 requirements unmet | `validate` gains warnings vs errors, a zero-order tail check, four realism bands, and the 13 corruptions are pinned in-repo (26 cases: raw + re-signed manifest) | `97611f7` |
| **N5** mixed timestamp formats | one row in 46k lacked microseconds | always serialize microseconds | `77d7be3` |
| **N3** year-1 crash · **N6** unreachable bound | uncaught `OverflowError`; 1,000,000 accepted but never finished | `--start-date` must be ≥ 1970-01-01; `--customers` capped at 500,000 with measured scaling documented | `96d34fe` |
| **m4** Shopify prices | 4.9% matched what customers pay | shared `ecomgen/pricing.py`; export quotes the shelf price | `2ff81a6` |
| **m7** no FX exported | CHF/GBP contribution left NULL | `orders` and `marketing_spend` carry `fx_rate_from_eur`; validator checks it | `39882de` |
| **N2 / m3** markets identical | one CAC draw, one return rate, one repeat probability for all markets | per-market `cac_multiplier`, `return_rate_multiplier`, `repeat_multiplier` | `0ad3b3b` |
| **N4** stale backup dirs | `ignore_errors=True`, no retry | cleanup goes through the same sharing-violation backoff | `9084372` |
| **n1, n2, n3** | untouched | exporter shims removed; `--start-date` is a typed date option; emails derive from the customer's name | `abadaf7` |

**m2 (repeat interval) is closed as adequate, not fixed further.** Measured mean gap is 88 days over a 36-month window against a configured `average_days: 105` (inside the existing ±20% test band). The shortfall at 12 months (65 days) is right-censoring: a customer acquired mid-window cannot show a 105-day gap. Treating this as a defect would mean over-fitting the generator to an uncensored statistic.

## Verification results

| Check | Result |
| --- | --- |
| Test suite, Python 3.14.7 / 3.11.16 | **262 passed** on both (was 214) |
| Coverage | **92%** |
| `ruff check` / `ruff format --check` | clean, 60 files |
| Independent pandas audit (5k, de/at/fr) | **44/44 checks pass, 0 failures** |
| Corruption suite | **13/13 caught** raw *and* with a re-signed manifest |
| Generation matrix, 27 cells | 0 generation failures, 0 validation failures; **every cell covers all its months**; 0 variants sold out |
| Determinism | 16/16 files byte-identical across two hash seeds and both Pythons; seeds 1 vs 2 differ |
| Shopify spec check | 0 violations; all 102 prices equal what DE customers are charged |
| DuckDB BI, 16 queries | all sane; CHF/GBP contribution now computed through the exported FX rate |
| Agent coherence test | no semantic contradictions; remaining points are realism observations |

### Timings (matrix v3, this host)

| Scale | Wall clock | Peak RSS | Orders per customer |
| --- | --- | --- | --- |
| S (50 customers, 1 month) | 0.8–1.7 s | 70–82 MB | 0.36–0.78 |
| M (5,000 / 12 months) | 1.8–4.6 s | 99–130 MB | 0.79–0.94 |
| L (50,000 / 36 months) | 9.1–15.9 s | 317–432 MB | 0.80–0.95 |

For reference, the original code took 180.5 s for a single-market 50k/12-month run and produced orders in only 4 of 12 months.

### Contribution margin now differs by market (m7 + N2 together)

From `reports/bi_queries.sql` q8 on a DE/UK/CH run, converted to EUR through the exported rate:

| Market | Net sales (EUR) | COGS | Marketing | Contribution | % |
| --- | --- | --- | --- | --- | --- |
| CH | 44,661.60 | 17,456.80 | 16,701.40 | 9,019.10 | 20.2% |
| DE | 224,970.00 | 99,548.60 | 80,397.10 | 30,638.90 | 13.6% |
| UK | 158,619.00 | 70,100.50 | 62,464.80 | 15,238.20 | 9.6% |

In v2 the two non-EUR rows were NULL and all markets behaved identically.

## Output changes for a fixed seed

Every consumer of a pinned seed must re-baseline. Changed by this round:

- `cost_eur` (net-basis cost draw), and therefore every margin figure;
- `email` (derived from the customer's name);
- all `created_at` values that fell on a whole second (now `.000000`);
- `orders` and `marketing_spend` gain `fx_rate_from_eur`;
- `products_shopify.csv` prices are the rounded shelf price;
- any multi-market run, because of the new market multipliers.

`examples/sample_output` is regenerated and validates clean. The repo has no golden-hash fixtures, so nothing else needed updating; determinism is still enforced by run-twice comparison.

## Still open (deliberate)

1. **The gross/VAT-inclusive pricing contract still needs your sign-off.** `total = subtotal − discount + shipping` with `tax` contained in `total`. It is consistent everywhere now (README, validator, BI, audit), but it differs from the original invariant.
2. **Realism items the agent still notes, all by design and documented:** ~38% of acquired customers never order (so CAC per *buyer* is higher than CAC per acquisition); 99.3% of order lines have quantity 1; shipping is a flat fee with no free-shipping threshold; ending inventory sits near the reorder point rather than tracking demand; there is no customer history before the window.
3. **Commit hygiene from the previous round is unchanged:** the Cursor work remains one commit (`6779e85`) without per-issue history; everything in this round is one commit per issue with a failing-first regression test.
4. **CI matrix runs 3.11 and 3.14**, not the 3.13 originally specified.
5. **No live Shopify import was performed.** The export conforms to the documented CSV spec; acceptance by Shopify's importer remains a manual check.
