# ecomgen stress-test report v2 (post-fix verification)

**Date:** 2026-09-21 · **Branch:** `fix/stress-test-findings-cursor` (pushed) · **Baseline compared against:** `3918022` (the v1 report's commit)
**Host:** Windows 11 Pro, 16 cores · **Pythons:** 3.14.7 and 3.11.16, both in fresh venvs

**Companion files:** [`test_report.md`](test_report.md) (v1, the issue list) · [`correctness_audit.md`](correctness_audit.md) (v1 audit) · [`bi_queries.sql`](bi_queries.sql)

**Method.** Every v1 repro was re-run against the fixed code, not taken from any implementer's report. Independent checks (pandas audit, corruption suite, interrupt sweep, Shopify spec check, DuckDB BI, agent test) import nothing from `ecomgen` except its CLI. All timings on this machine; v1 "before" numbers were re-measured today at commit `3918022` rather than reused, except where noted.

## Verdict

**The critical and major issues are genuinely fixed.** C1, M1, M2, M3, M4, M5, M6, M7 all verified against the original repros.

**Not yet promotable.** Four requirements from the fix brief were not implemented, three minor issues are only half done, three nits were not touched, and verification surfaced **four new defects**, two of them introduced by the fixes themselves. Details in "Open items".

## Scorecard

| Issue | v1 state | v2 state | Verified how |
| --- | --- | --- | --- |
| **C1** stock-out dead tail | orders in 8/36 months, silent | **fixed** – 36/36 months in all 9 L-cells, 0 variants sold out, 40,343 orders | 27-cell matrix; `--customers 50000 --months 36` |
| **M1** non-atomic export | 5/10 interrupts left truncated tables that validated as "Valid" | **fixed** – 10/10 interrupts leave the previous dataset intact and valid; Ctrl+C exits 130, kill exits 1; destination never partial | real SIGINT/kill sweep, 50k/120-month exports |
| **M2** circular marketing | every paid channel ROAS ≥ 7 | **fixed** – meta ROAS 0.98–1.08 (**−€121k contribution**), google ~4.9, email 29–54; spend is planned per day, not derived from orders | independent audit + DuckDB q9 |
| **M3** O(orders × customers) | 180.5 s (DE 50k/12mo) | **fixed** – **9.96 s** (18×); 50k/36mo 58.8 s → 9.1 s | serial before/after on this host |
| **M4** refund basis | 27% of refunds exceeded what was paid | **fixed** – refund equals the paid line amount on every return, cap holds | independent recomputation of the cent allocation |
| **M5** validator blind spots | 4/13 corruptions caught | **fixed** – **13/13** caught, and 13/13 again when the manifest is re-signed so hashes match | external corruption suite, both modes |
| **M6** tiny populations | 1 customer → 24 orders/yr | **fixed** – 1 customer → ≤1 order; 5 customers plausible | adversarial cases |
| **M7** Windows `> NUL` | crashed, no output | **fixed** – exits 0 and writes the dataset | `cmd /c ... > NUL` |
| **m1** pytest-cov / CI | `--cov` failed on fresh install | **fixed** – works; CI matrix added (3.11 + 3.14, Linux + Windows) | fresh venv on both Pythons |
| **m2** repeat semantics | gap median 35 d vs `average_days: 105` | **partial** – median 53.6 d, mean 68.3 d; cohort m1 retention no longer collapses (0.05–0.12 band) | independent audit |
| **m3** market realism | assortment random; behaviour identical | **partial** – every product in every market (AOV spread now 1.09×), but **no per-market behaviour differences** and channel CAC is shared across markets | audit + CAC check |
| **m4** Shopify import | no inventory tracker | **partial** – tracker/policy/fulfilment/status all correct, continuation rows blank, 0 spec violations; but **only 4.9% of `Variant Price` values match what customers pay** (588.98 vs 589.00) | Shopify spec check + price join |
| **m5** input bounds | unbounded | **fixed** – bounds enforced and documented; clear errors naming the option | adversarial cases |
| **m6** malformed YAML | raw tracebacks | **fixed** – clean errors incl. duplicate keys and `price_range: [0,0]` | adversarial cases |
| **m7** multi-currency | no currency column, no FX | **partial** – `marketing_spend.currency` added; **no FX rate exported**, so cross-currency BI is still manual (contribution margin is NULL for CHF/GBP) | audit + BI q8 |
| **m8** VAT on top of .90 | prices behaved as net | **fixed, by a contract change** – prices are now gross/VAT-inclusive (see below) | audit |
| **m9** returns past window | 21 undeclared | **fixed** – returns bounded by a manifest-declared `return_cutoff`; validator enforces it | audit + corruption suite |
| **m10** catalogue text | 13/36 duplicate titles, nouns ignored category | **fixed** – titles unique, nouns category-scoped | audit |
| **m11** uniform order hours | 03:00 == 20:00 | **fixed** – market-local hour curve with DST | audit (00–03h share 0.000, 19–20h 0.230) |
| **n1** duplicate exporter shims | present | **not done** | `ls ecomgen/exporters` |
| **n2** `--start-date` typing | plain string | **not done** | `cli.py:153` |
| **n3** emails vs names | unrelated | **not done** | audit sample |
| **n4** `test/` ignored | untracked CSVs | **fixed** | `.gitignore` |
| **n5** README preset claim | traceback path | **fixed** (follows from m6) | adversarial cases |

## Performance (M3), measured back to back on this host

| Run | v1 (`3918022`) | v2 | Note |
| --- | --- | --- | --- |
| DE only, 50k customers, 12 mo | **180.5 s** / 175 MB | **9.96 s** / 310 MB | 18× faster; target was < 30 s |
| 3 markets, 50k, 36 mo | 58.8 s / 189 MB | **9.12 s** / 336 MB | v1 was fast only because it stopped ordering in August |
| 3 markets, 5k, 12 mo | 5.03 s / 99 MB | **2.03 s** / 104 MB | |
| Matrix L-cells (9 runs) | 22–227 s | **7.7–21.2 s** | all 36/36 months |
| 1,000,000 customers, 12 mo | >300 s, 1.4 GB, killed | **>300 s, 4.2 GB, killed** | still fails, now inside the documented bound |

Order counts: v1 produced 9,692 orders for DE 50k/12mo (4 months of data); v2 produces 39,992 across all 12.

## Corruption catch rate (M5)

13 corruptions from v1, each injected into a valid export:

| Mode | v1 | v2 |
| --- | --- | --- |
| Raw tamper (manifest untouched) | 4/13 | **13/13** |
| Tamper + **re-signed manifest** (hashes match again) | n/a | **13/13** |

The second mode matters: it proves the semantic checks catch the corruption, not just the hash. Each is caught by a specific message, e.g. `order ord-…: VAT 0.00 does not match included-tax amount 43.12 for market fr`.

## Interrupt behaviour (M1)

10 runs against a directory holding a complete previous export, interrupted mid-export:

| Mode | Runs | Destination afterwards | Exit | Corrupt files |
| --- | --- | --- | --- | --- |
| Real Ctrl+C (SIGINT → KeyboardInterrupt) | 5 | previous dataset intact, validates | 130 | 0 |
| Hard kill (TerminateProcess) | 5 | previous dataset intact, validates | 1 | 0 |

In v1 the same sweep produced truncated `marketing_spend.csv` files that `validate` accepted, and one run that exited 0 after writing `<unprintable Decimal object>`. Neither recurs. Residue: a hard kill leaves a `.<name>.tmp-*` staging directory (documented), and one run left a `.<name>.old-*` backup (see N4).

## Determinism

Seed 42 produces byte-identical output across: two runs, `PYTHONHASHSEED` 0 vs 999, and Python 3.11 + numpy 2.4.6 vs 3.14 + numpy 2.5.3 — all 16 files including `manifest.json`. Seeds 1 and 2 differ in all 8 CSVs. Matrix digests: `42a == 42b == 0a7c91af06fcb632`, `s1 = 112fc1fa…`, `s2 = 4cd6e11a…`.

**Fixed-seed output changed from v1** (expected, because the generators changed). No stored fixtures needed updating because the repo has none; see N5.

## Suite, lint, coverage

| Check | Result |
| --- | --- |
| `pytest` (fresh venv, Python 3.14.7) | **214 passed** in 76 s |
| `pytest` (fresh venv, Python 3.11.16) | **214 passed** in 108 s |
| `pytest --cov=ecomgen --cov-report=term-missing` | works on a clean install; **92%** total (v1: 88%) |
| `ruff check .` / `ruff format --check .` | clean |
| `validation.py` coverage | 87% (v1: 77%) |

## Consumer checks

- **Shopify:** 0 spec violations. `Variant Inventory Tracker=shopify`, `Variant Inventory Policy=deny`, `Variant Fulfillment Service=manual`, `Status=active` on first rows, product fields blank on continuation rows, titles unique. Not tested against a live store.
- **BI (DuckDB, 16 queries):** all return sane results. The only NULLs are ROAS/CTR for the zero-spend channels and right-censored cohort cells, both by design.
- **Agent test:** the dataset now supports a correct answer to "which channel is unprofitable" (meta, ≈ −€121k on €211k spend, with the agent deriving a ≈2.37 break-even ROAS independently). In v1 that question had no answer, because every channel was profitable by construction. The agent also confirmed customer↔marketing attribution reconciles exactly. Remaining contradictions it raised are listed below.
  - Two of its claims were false positives that I checked and dismissed: the `SAVE22` code is correct (17.08/77.90 = 21.93%, rounds to 22), and order prices "not matching the catalogue" is the documented FX/`.90` rounding.
  - One "contradiction" in the first run was my harness's fault: `agent_test.py` still described the old `total = … + tax` formula. I corrected the prompt and re-ran; the finding disappeared. Numbers quoted here come from the corrected run.

## Open items

### Requirements from the fix brief that were not implemented

1. **`validate` does not detect a zero-order tail.** Required by the C1 and M5 briefs. I deleted the last 3 months of orders (with their items and returns), re-signed the manifest, and `validate` reported `Valid dataset`, exit 0.
   *Repro:* drop all orders after month 9 from a 12-month export, rewrite the manifest, run `ecomgen validate --path <dir>`.
2. **No severity levels.** `validate` has no error/warning distinction; everything is an error.
3. **No realism-range checks** (out-of-range repeat rate, AOV, return rate) in `validate`.
4. **No in-repo test pinning the 13/13 corruption suite.** The validator has ~20 targeted tests, but nothing asserts the full set, so a regression in one check would not be obvious. (My external suite covers it; it lives outside the repo.)

### New defects found during verification

5. **N1 — COGS is computed from the gross price (regression from the m8 pricing change).** `cost_ratio_range` is applied to the VAT-inclusive `price_eur`, so the realised cost ratio against net revenue is 1.19× the configured band: garden-furniture 47–65% against a configured 38–55%. Every margin and contribution figure is systematically thin.
   *Repro:* `cost_eur / (price_eur / 1.19)` per category vs `cost_ratio_range` in the preset.
6. **N2 — Channel CAC does not vary by market.** Meta's realised CAC is €126.6131 (AT) and €126.6124 (FR); the base CAC is drawn per channel and shared across markets, so markets cannot differ in channel efficiency. Same root cause as the unfinished half of m3.
7. **N3 — `--start-date 0001-01-01` crashes with an uncaught `OverflowError`** in `generators/local_time.py:28` (raw traceback, via `local_day_bounds`). It exited cleanly in v1, so this is a regression from m11.
8. **N4 — Atomic-export cleanup can silently leave hidden directories.** `atomic.py` removes its backup with `shutil.rmtree(..., ignore_errors=True)` and never retries, so a transient lock (OneDrive, antivirus) leaves an empty `.<name>.old-*` sibling. Observed twice in `examples/` and once in the interrupt sweep; it also broke a `git stash` here.
9. **N5 — Timestamps are serialized inconsistently.** When microseconds are zero the fractional part is dropped (`2024-04-26T09:41:47Z` beside `2024-01-01T17:20:44.408850Z`), which makes pandas' default `parse_dates` fall back to object dtype. It hit 1 row in 46k. Cheap fix: always emit microseconds.
10. **N6 — `--customers 1000000` is inside the documented bound but does not finish.** 300 s timeout, 4.2 GB resident (v1: 1.4 GB). Either lower the bound or make it complete.

### Partial fixes (see scorecard)

11. m2 (repeat interval 54 d vs configured 105), m3 (no per-market behaviour), m4 (Shopify prices don't match paid prices), m7 (no FX export).

### Nits not done

12. n1 (duplicate exporter shim modules), n2 (`--start-date` typed as `str`), n3 (emails unrelated to names).

### Realism observations from the agent test (not bugs, worth knowing)

- `new_customers` counts sign-ups, not buyers: 38% never order, so CAC per *buyer* is ≈1.6× the reported CAC.
- Paid channels spend on all 366 days with near-identical CTR across markets; no budget reaction to performance.
- Contribution margin has no shipping, fulfilment or payment cost lines, so it overstates profitability.
- Ending inventory sits near the reorder level in all variants and never hits zero, which reads as "nothing ever sells out".
- Multi-unit lines are rare: 99.3% of order items have `quantity = 1` (v1: 99.4%), so "items sold" is effectively a line count. Pre-existing, not a regression.
- Shipping is a flat fee on every order, including €589 ones, with no free-shipping threshold.
- All customers are created inside the generated window, so there is no pre-existing customer base and no retention beyond 12 months in a 1-year dataset.

## Process notes

- **Contract change needing sign-off:** prices are now **gross (VAT-inclusive)**: `total = subtotal − discount + shipping`, with `tax` the VAT contained in `total`. The v1 invariant `subtotal − discount + shipping + tax == total` no longer holds. This is defensible (it matches EU B2C and Shopify's `taxes_included`), but it changes every downstream query, and it was not the approach specified in the fix plan.
- **Commit hygiene:** Agent A's five generation-core fixes are individual `fix(<ID>)` commits. The rest of the work (M5, the minor issues, validation, README, BI, regenerated samples) arrived as one uncommitted working tree from the Cursor agents and is preserved as a single commit, so those issues have no per-issue commits and no fail-before evidence.
- **CI deviation:** the matrix runs Python 3.11 and 3.14; the brief asked for 3.11 and 3.13.
- **Backups:** the pre-commit working tree is preserved on `backup/cursor-worktree-snapshot` and in `stash@{0}`.

## Recommended order of work before promoting

1. Fix N1 (COGS basis) — it silently distorts every margin number.
2. Implement the zero-order-tail check, severity levels and realism ranges in `validate`, and add the 13/13 corruption test to the repo.
3. Fix N3 (crash) and N5 (timestamp format); decide N6 (bound vs performance).
4. Finish m4 (Shopify prices), m7 (export FX), m3 (per-market behaviour incl. N2), m2 (repeat interval).
5. Fix N4 (retry backup cleanup) and the three nits.
6. Confirm or revert the gross-pricing contract change, then re-run this protocol.
