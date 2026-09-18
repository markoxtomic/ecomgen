# ecomgen stress-test report

**Date:** 2026-09-18 · **Commit under test:** `3918022` (main) · **Host:** Windows 11 Pro, 16 cores · **Pythons:** 3.14.7 (primary), 3.11.16

**Companion files:** [`correctness_audit.md`](correctness_audit.md) · [`bi_queries.sql`](bi_queries.sql) · [`bi_queries_output.md`](bi_queries_output.md)

**Scope:** test, verify and report only. No source files were changed.

## Summary

The core engine is **arithmetically sound and fully deterministic**.
- An independent pandas audit found zero referential, cent-rounding, VAT, FX or refund-bound errors across 9 markets.
- A fixed seed gives byte-identical output across processes, hash seeds, and Python 3.11 vs 3.14.
- The CLI validates its arguments well and every README command works as documented.

It is **not ready for public promotion**, for three reasons:

1. **It silently produces wrong data at scale (Critical).** Inventory is finite and never replenished. At the "large" matrix size (50,000 customers, 36 months) every preset sells out after 8–11 months. The remaining 25–28 months contain zero orders, yet the CLI prints a normal summary and `ecomgen validate` reports the dataset as valid.
2. **Export is non-atomic, and `validate` cannot tell (Major).** A Ctrl+C or kill mid-export leaves truncated tables and mixed-run directories that `validate` accepts. In one timing window Ctrl+C was swallowed: the run exited 0 and wrote `<unprintable Decimal object>` into `marketing_spend.csv`.
3. **The marketing model is circular (Major).** Spend, impressions and clicks are back-computed from orders, and repeat orders are charged acquisition cost. Every paid channel shows ROAS ≥ 7, so the headline agent/BI use case ("which channel is unprofitable?") has no answer by construction.

Performance is also a blocker for "large" datasets. Order generation is O(orders × customers):
- 50,000 customers in one market takes 2.5–4 minutes.
- 1,000,000 customers did not finish in 5 minutes (1.4 GB in use, no progress output).

## Pass/fail by phase

| Phase | Result | Notes |
| --- | --- | --- |
| 1. Baseline health | **PASS (with notes)** | Clean install on 3.11 and 3.14; 62/62 tests pass; ruff and format clean; 88% coverage. The brief's `--cov` command fails on a fresh `.[dev]` install because `pytest-cov` is missing (m1). All README commands work. |
| 2. Generation matrix | **FAIL** | All 27 cells plus the seed checks generate and validate, but every L cell (50k / 36 mo) silently stops ordering after 8–11 months (C1). Single-market L takes 143–227 s (M3). |
| 3. Correctness audit | **PASS (integrity) / realism issues** | 38/38 hard checks pass. Nine realism findings; see `correctness_audit.md` §7. |
| 4. Adversarial | **PARTIAL** | Argument validation is good. Interruption handling fails (M1), resource bounds are absent (m5), malformed YAML gives raw tracebacks (m6), and tiny populations silently distort (M6). |
| 5.1 Shopify shape | **PASS (with warnings)** | 0 rejection-level issues across 3 presets. Inventory would import untracked (m4). |
| 5.2 BI smoke test | **PASS** | 12 DuckDB queries run, and all return non-null results except the NULLs that are expected by design. |
| 5.3 Agent test | **PARTIAL** | The agent answered all five questions coherently. It surfaced 16 "contradictions"; I triaged 9 as real data-model issues, 3 as agent misreads, and 1 as a bug in my own query (since fixed). |
| 5.4 Determinism | **PASS** | Byte-identical across runs, hash seeds, and Python 3.11 + numpy 2.4.6 vs 3.14 + numpy 2.5.3; different seeds diverge. Safe for CI snapshots. |

## Issues by severity

Commands assume an installed `ecomgen` and a POSIX shell; use `^` line continuations on Windows `cmd`.

### Critical

#### C1: Inventory is exhausted and never replenished, so large datasets silently stop producing orders

Every variant starts with 25–250 units and stock only goes down. Returns are not restocked and nothing is replenished. Once a market's eligible variants reach 0, `generate_orders` breaks out of that day's loop (`orders.py:309-312`), and it does the same every day afterwards.

- **Evidence (matrix, all 9 L cells):** 8–11 of 36 months have orders; 100% of variants end at 0; 0.17–0.25 orders per customer, against 0.80–0.95 at M scale.
- **garden-decor, de,at,fr, 50k / 36:** 8,629 orders from 50,000 customers. The last order is on 2024-08-09, and 29 of 36 months are empty.
- **M scale is affected too:** 19–36 variants sell out within 12 months, starting in April, so the late-year assortment shrinks.
- **Nothing signals the problem.** The CLI summary looks normal and `validate` passes.

```bash
ecomgen generate --customers 50000 --months 36 --out big
python -c "import pandas as pd; o=pd.read_csv('big/orders.csv'); print(o.created_at.str[:7].value_counts().sort_index())"
```

### Major

#### M1: Export is non-atomic, and interrupted or overwritten exports pass `validate`

Tables are written one by one directly into `--out`, which may already contain an older dataset (`exporters/csv.py`, `json.py`). I tested this with 10 interruptions into a pre-filled seed-1 directory while generating seed 2 (`--customers 500 --months 600 --format all --shopify-export`). Five were Ctrl+C, sent as a KeyboardInterrupt, and five were hard kills.

- **Hard kill mid-export:** 4 of 5 left `marketing_spend.csv` truncated at a row boundary (17k–270k of 288k rows). `validate` said **"Valid dataset"** every time.
- **Ctrl+C at +0.15 s:** exit 130, `marketing_spend.csv` truncated to 22,576 rows, `validate` said "Valid dataset".
- **Ctrl+C at +0.4 s:** the interrupt was **swallowed**. The run printed the normal summary and **exited 0**, yet row 88,301 of `marketing_spend.csv` contains `<unprintable Decimal object>`. The KeyboardInterrupt was raised inside the Decimal `__str__` call during pydantic serialization and swallowed there.
- **Every interrupted run left the seed-1 JSON and `products_shopify.csv` beside the new seed-2 CSVs.** `validate --path out/orders.json` reports the stale set as valid.
- **The same mixing happens without an interrupt** when a user follows the README examples in order: the CSVs come from the uk,ch run, while the JSON and Shopify files come from the earlier default run.
- **A read-only `orders.csv` in the target** gives exit 1 (good) but leaves new customers, products and variants next to the old 3-byte `orders.csv`.

```bash
ecomgen generate --seed 1 --customers 500 --months 600 --format all --shopify-export --out x
ecomgen generate --seed 2 --customers 500 --months 600 --format all --shopify-export --out x   # Ctrl+C about 0.1-1 s after products.csv is rewritten
ecomgen validate --path x          # often "Valid dataset" with a truncated marketing_spend.csv
```

#### M2: The marketing model is circular, and every paid channel is profitable by construction

These points come from `generators/marketing.py` and the audit (`correctness_audit.md` §7).

- **Spend is derived from orders, not the other way round:** `spend = CAC × attributed_orders`, and impressions and clicks are back-solved from orders.
- **53% of market-channel-days have 0 orders, and every one of them also has 0 spend and 0 impressions,** so meta and google go dark on 35–43% of days.
- **Repeat orders (25%) are attributed to the acquisition channel and charged CAC again.** Measured CAC therefore sits at the top of the configured range.
- **`direct` and `organic` get impressions and clicks** (CTR up to 18%).
- **ROAS is 7.5–11 for paid channels and 61–83 for email, against a break-even of about 2.1.** "Which channel is unprofitable?" always answers "none". The LLM agent reached the same conclusion and called the attribution "tautological".

```sql
-- in DuckDB over any export
SELECT channel, count(*) FILTER (WHERE spend = 0 AND attributed_orders = 0) AS dark_days,
       sum(spend) / sum(attributed_orders) AS spend_per_order
FROM read_csv_auto('out/marketing_spend.csv') GROUP BY ALL;
```

#### M3: Order generation is O(orders × customers), so large datasets are slow

For every order, `generate_orders` rebuilds the first-time and repeat candidate lists by scanning every customer in the market (`orders.py:313-327`). cProfile on 20k customers in DE over 12 months: 115 s total, of which 85 s is spent in `generate_orders`'s own body.

| Run | Wall-clock | Peak RSS |
| --- | --- | --- |
| 5k / 12 mo, all markets | 4.2–5.8 s | 124–129 MB |
| 5k / 12 mo, single market | 7.6–9.5 s | 97–101 MB |
| 50k / 36 mo, all markets | 22–46 s | 240–265 MB |
| 50k / 36 mo, single market | **143–227 s** | 181–197 MB |
| 1,000,000 / 12 mo, de,at,fr | **>300 s, killed** | 1.4 GB |

All of these times are *flattered* by C1, because generation stops early once stock is gone. Some L timings ran concurrently with other tests and are ±30% noisy. There is no progress output: just a spinner that shows nothing. Memory is modest; runtime is the bottleneck.

```bash
ecomgen generate --markets de --customers 50000 --months 36 --out big1
```

#### M4: Refunds ignore discounts and VAT

`refund_amount = unit_price × quantity`, which is the pre-discount, VAT-exclusive line value (`returns.py:84`).
- 126 of 475 refunds (27%) exceed what the customer paid net for the line.
- 413 of 475 are below what the customer paid gross.

Net revenue after refunds and contribution margin are therefore both off, and the agent flagged it independently.

```bash
python audit.py ds ...   # correctness_audit.md Appendix B; see §4, "refund basis"
```

#### M5: `ecomgen validate` misses most semantic corruption

I hand-edited a valid export in 13 ways. Validate caught four: a truncated mid-row file, a total off by 0.01, a missing table, and an extra column. It **accepted** the following:
- zero VAT with the total adjusted to match;
- `currency=USD`;
- an order's market changed away from its customer's market;
- a customer created after their order;
- a wrong `is_repeat`;
- `impressions < clicks` in marketing, a funnel rule the README promises;
- a return timestamp equal to its order timestamp;
- a discount code on an order with no discount;
- products delisted from the market they sold in.

Combined with M1, `validate` cannot be relied on as a CI gate. Coverage of `validation.py` is also the lowest in the package (77%).

```bash
# edit orders.csv: set tax=0.00 and reduce total by the same amount -> still "Valid dataset"
```

#### M6: Tiny populations or long windows silently distort behaviour

Base demand has a floor of 0.5 orders per market-day (`pipeline.py:45`) regardless of customer count.
- **`--customers 1`:** one customer places 24 orders in a year, a 95.8% repeat rate against a configured 0.24.
- **`--customers 500 --months 1200`:** 94% repeat rate, with 13.5k items from 500 people.

Nothing warns about it, and `validate` passes.

```bash
ecomgen generate --customers 1 --out one     # summary: "Repeat rate: 95.8%"
```

#### M7: Windows: `generate` crashes with no output when stdout is NUL

Rich's legacy-console renderer writes the spinner glyph `⠋` via cp1252 and raises `UnicodeEncodeError` before generation starts. No files are written and the exit code is 1. Redirecting to a file or a pipe works. `> NUL` is a common idiom in `.bat` and CI scripts.

```bat
ecomgen generate --customers 50 --months 1 --out t > NUL
```

### Minor

**m1: `pytest-cov` is missing from the `dev` extra.** The requested `pytest --cov=...` command errors with `unrecognized arguments` on a fresh `pip install -e ".[dev]"`. Weakest coverage: `validation.py` 77%, `cli.py` 86% (error paths untested), `marketing.py` 87%, `orders.py` 88%.
*Repro:* `pip install -e ".[dev]" && pytest --cov=ecomgen`

**m2: Repeat behaviour ignores its own configuration.**
- `repeat_purchase.average_days: 105` yields a median repeat gap of 35 days and a mean of 52 (p10 5.6). The weight `exp(−days/avg)` favours whoever bought most recently.
- Month-1 cohort retention decays from 27% (January) to 4–7% (August onward) purely because the buyer pool grows.

*Repro:* `bi_queries.sql` q8, and `correctness_audit.md` §7 items 4–5.

**m3: Market differences come from random assortment, not market behaviour.**
- Each product is listed in a random subset of markets. In the audit dataset the FR assortment averages €197 against DE's €152, so FR (demand weight 0.75) out-earns DE (1.00).
- All other behaviour (repeat, discount, basket size) is identical across markets apart from noise.

*Repro:* `bi_queries.sql` q2 and q6.

**m4: Shopify export imports without inventory tracking.** Checked against help.shopify.com's CSV spec; there were 0 rejection-level issues. However:
- There is no `Variant Inventory Tracker` column. Per Shopify's docs a blank tracker means "inventory isn't tracked", so `Variant Inventory Qty` has no effect.
- The exported quantity is the *post-simulation* remainder: 24–34 variants per preset import sold out.
- `Variant Price` is the unrounded EUR catalogue price (e.g. 78.64), which matches no price charged in any order.
- Title, Body, Vendor, Type and Tags are repeated on 2nd+ variant rows, where the docs say to leave them blank.
- There is no `Status` or `Variant Inventory Policy` column. Option names are lowercase (`color`).
- Not tested against a live store import.

*Repro:* `ecomgen generate --shopify-export --out s`

**m5: No upper bounds on inputs.**
- `--customers 1000000000000` climbs to 1.85 GB in 45 s before I killed it.
- `--months 1200` peaks at 769 MB.
- `--months 1000000` fails with an unhelpful `Error: year must be in 1..9999, not 85357`.
- `--seed -1` fails with `Error: expected non-negative integer`, without naming the option.

*Repro:* `ecomgen generate --months 1000000`

**m6: Malformed preset YAML is handled inconsistently.**
- A YAML syntax error, an empty file, or a top-level list produces a full Rich traceback: `yaml.YAMLError` and `TypeError` are not caught in `cli.py`.
- Duplicate YAML keys are silently accepted, with the last one winning.
- `price_range: [0, 0]` is accepted, producing €0-cost products sold at the 0.90 floor price.

This affects contributors adding presets, not end users, because only bundled presets can be selected.
*Repro:* put a preset file containing `categories: {a: [1, 2` in `ecomgen/config/presets/` and run `ecomgen generate --preset <name>`.

**m7: Multi-currency without a currency column.** `marketing_spend` has no `currency` column (CH is in CHF, UK in GBP) and no FX table is exported. Summing spend across markets, or setting `cost_eur` against GBP or CHF revenue, silently mixes currencies. DuckDB also infers all money columns as DOUBLE.
*Repro:* `ecomgen generate --markets de,uk,ch --out fx`

**m8: VAT is added on top of `.90` prices.** Prices behave as net, but EU B2C shops show VAT-inclusive prices, so a "€36.90" item totals €43.91.
*Repro:* any DE order.

**m9: Returns fall outside the generation window.** 21 of 475 returns (4%) are dated after the window ends, in January 2025, so BI month filters show returns without orders.
*Repro:* audit §4.

**m10: The catalogue text is repetitive and inconsistent.**
- 13 of 36 products share a title with another product.
- Title nouns ignore category, e.g. "Minimal Acacia Lantern" is filed under `garden-furniture`.
- Each product uses one option dimension (documented) and one price for all variants.

*Repro:* `bi_queries.sql` q9.

**m11: Order timestamps are uniform over the 24 hours.** 03:00 UTC gets as many orders as 20:00 (each hour 3.5–4.7%), with no time zone handling per market.
*Repro:* audit §7 item 6.

### Nits

- **n1:** Duplicate exporter modules: `exporters/csv.py` plus a `csv_exporter.py` shim, and the same pattern for JSON and Shopify. `_window` and `_market_values` are copy-pasted across three generators.
- **n2:** The README documents `--start-date DATE`, but it is a plain ISO `str`. `20240101` and `0001-01-01` are accepted; `2024-01-01T12:00` is rejected.
- **n3:** Emails are Faker usernames unrelated to the customer's name (e.g. `neumeisterviktor…` for "Sena Tiefenbacher"), and cities are fictional.
- **n4:** The untracked `test/` directory at the repo root holds generated CSVs and is not in `.gitignore`, so `git add .` would commit it.
- **n5:** The README says an unknown preset "causes generation to exit with status 1". True, but a malformed *bundled* preset also exits 1, via a traceback (see m6).

## What held up well

- Argument validation. `--customers -5`, `--months 0` and `--months -1` exit 2 with clear messages. Unknown preset, market, empty market list, `gb` and invalid dates exit 1 with clear messages. Uppercase and duplicate market codes are normalised. Path traversal in `--preset` is rejected.
- Feb 29 handling. A 2024-02-29 start gives 365 days of marketing rows (window to 2025-02-28); 2023-02-29 is rejected; 2024-01-31 + 1 month ends on Feb 29.
- Output paths. Nonexistent nested `--out` directories are created. An ACL-denied directory, a file used as `--out`, a missing drive and invalid characters all exit 1 with the OS error.
- `--customers 0` produces a valid dataset with headers only, as documented.
- Accounting is exact in `Decimal` across 9 markets and 3 currencies, and deterministic across Python and numpy versions.

## Phase details

### Phase 1: baseline

- `pip install -e ".[dev]"` in a clean uv-seeded venv installs cleanly and `pip check` passes. Two local-environment problems are unrelated to the repo: the scratchpad path exceeded Windows MAX_PATH for numpy's test files, and stdlib `ensurepip` failed. I used a short-path venv.
- **Python 3.14.7:** 62 passed in 9.9 s. **Python 3.11.16:** 62 passed.
- `ruff check .`: all checks passed. `ruff format --check`: 31 files already formatted.
- Every README command ran exactly as written, in README order, in one directory: the quickstart, `--format json`, `--format all --shopify-export`, `--markets uk,ch --months 6 --start-date 2025-01-01`, `presets`, and `validate --path ./output`. All of them work. The quickstart row counts match the README (36 / 102 / 5,000 / 5,490).

### Phase 2: generation matrix

Seed 42 everywhere. Validate passed on all 27 cells. The L rows ran partly in parallel with Phase 4 jobs, so treat their timings as ±30%.

S = 50 customers / 1 month, M = 5,000 / 12 months, L = 50,000 / 36 months; single = de, three = de,at,fr, all = 9 markets.

| scale   | markets   | preset       |   gen s |   peak MB | validate   |   orders |   orders/cust | last_order   | months w/ orders   | variants sold out   |
|:--------|:----------|:-------------|--------:|----------:|:-----------|---------:|--------------:|:-------------|:-------------------|:--------------------|
| S       | single    | electronics  |    1.45 |      69.9 | pass       |       26 |         0.52  | 2024-01-31   | 1/1                | 0/96                |
| S       | single    | fashion      |    0.73 |      69.5 | pass       |       42 |         0.84  | 2024-01-31   | 1/1                | 0/144               |
| S       | single    | garden-decor |    0.73 |      70.1 | pass       |       19 |         0.38  | 2024-01-29   | 1/1                | 0/102               |
| S       | three     | electronics  |    0.81 |      71.3 | pass       |       32 |         0.64  | 2024-01-31   | 1/1                | 0/96                |
| S       | three     | fashion      |    1.18 |      72.5 | pass       |       43 |         0.86  | 2024-01-31   | 1/1                | 0/144               |
| S       | three     | garden-decor |    0.87 |      72.1 | pass       |       22 |         0.44  | 2024-01-31   | 1/1                | 0/102               |
| S       | all       | electronics  |    1.21 |      81.4 | pass       |       45 |         0.9   | 2024-01-31   | 1/1                | 0/96                |
| S       | all       | fashion      |    1.17 |      81.3 | pass       |       43 |         0.86  | 2024-01-31   | 1/1                | 0/144               |
| S       | all       | garden-decor |    1.19 |      81.3 | pass       |       33 |         0.66  | 2024-01-31   | 1/1                | 0/102               |
| M       | single    | electronics  |    8.1  |      99.3 | pass       |     4756 |         0.951 | 2024-12-31   | 12/12              | 25/96               |
| M       | single    | fashion      |    9.52 |     101.4 | pass       |     4590 |         0.918 | 2024-12-31   | 12/12              | 26/144              |
| M       | single    | garden-decor |    7.59 |      96.8 | pass       |     4057 |         0.811 | 2024-12-31   | 12/12              | 19/102              |
| M       | three     | electronics  |    5.23 |     105.8 | pass       |     4755 |         0.951 | 2024-12-31   | 12/12              | 34/96               |
| M       | three     | fashion      |    6.39 |     107.2 | pass       |     4582 |         0.916 | 2024-12-31   | 12/12              | 24/144              |
| M       | three     | garden-decor |    4.7  |     103.6 | pass       |     4185 |         0.837 | 2024-12-31   | 12/12              | 27/102              |
| M       | all       | electronics  |    4.46 |     126.8 | pass       |     4703 |         0.941 | 2024-12-31   | 12/12              | 36/96               |
| M       | all       | fashion      |    5.81 |     128.8 | pass       |     4627 |         0.925 | 2024-12-31   | 12/12              | 21/144              |
| M       | all       | garden-decor |    4.24 |     123.8 | pass       |     3991 |         0.798 | 2024-12-31   | 12/12              | 29/102              |
| L       | single    | electronics  |  142.85 |     180.6 | pass       |     8999 |         0.18  | 2024-09-18   | 9/36               | 96/96               |
| L       | single    | fashion      |  151.75 |     196.5 | pass       |    11246 |         0.225 | 2024-10-29   | 10/36              | 144/144             |
| L       | single    | garden-decor |  226.77 |     182.7 | pass       |     9674 |         0.193 | 2024-09-01   | 9/36               | 102/102             |
| L       | three     | electronics  |   89.81 |     194.5 | pass       |     8870 |         0.177 | 2024-09-16   | 9/36               | 96/96               |
| L       | three     | fashion      |  102.97 |     208.9 | pass       |    10916 |         0.218 | 2024-10-27   | 10/36              | 144/144             |
| L       | three     | garden-decor |   55.91 |     194.6 | pass       |     8629 |         0.173 | 2024-08-09   | 8/36               | 102/102             |
| L       | all       | electronics  |   21.6  |     240.3 | pass       |     8257 |         0.165 | 2024-09-02   | 9/36               | 96/96               |
| L       | all       | fashion      |   46.35 |     264.8 | pass       |    12436 |         0.249 | 2024-11-24   | 11/36              | 144/144             |
| L       | all       | garden-decor |   25.27 |     244   | pass       |     9338 |         0.187 | 2024-08-23   | 8/36               | 102/102             |

**Seeds.** 42 run twice gives the same SHA-256 over all CSV and JSON files (`5eab59905d0f26a0…`). Seeds 1 and 2 differ in every table.

### Phase 4: adversarial

Full list: 40 CLI cases, 7 filesystem cases, 13 validator mutations and 10 interruption runs.

| Case | Behaviour | Verdict |
| --- | --- | --- |
| `--customers 0` | Valid, 0 orders, marketing rows only | OK (documented) |
| `--customers 1` | 24 orders from 1 customer, 95.8% repeat | **silently distorted (M6)** |
| `--months 0`, `--months -1`, `--customers -5` | Exit 2, clear range error | OK |
| `--seed -1` | Exit 1, "expected non-negative integer" | unclear (m5) |
| `--seed 2**70` | Works | OK |
| `--months 1200 --customers 500` | 12 s, 769 MB, 94% repeat | distorted (M6, m5) |
| `--months 1000000` | Exit 1, "year must be in 1..9999, not 85357" | cryptic (m5) |
| `--customers 1e12` | No output, 1.85 GB after 45 s | no bound (m5) |
| `--customers 1e6 --months 12` | Did not finish in 300 s | too slow (M3) |
| Invalid, mis-cased or `../` preset | Exit 1, lists available presets | OK |
| Invalid, empty, `,,` or `gb` market | Exit 1, clear | OK |
| ` DE , de,At ` | Normalised to de, at | OK |
| `--format parquet` | Exit 2 | OK |
| Start 2024-02-29 / 2023-02-29 | Valid / exit 1 with clear message | OK |
| Start `2024-13-01`, `yesterday`, `2024-01-01T12:00` | Exit 1 | OK |
| Start `9999-06-01` | Exit 1, "year must be in 1..9999" | OK-ish |
| YAML: syntax error / empty / top-level list | Exit 1 **with raw traceback** | m6 |
| YAML: missing keys / bad weights / 11 seasonality values / name mismatch / latin-1 | Exit 1, pydantic or clear message | OK (verbose) |
| YAML: duplicate key / price `[0,0]` | Accepted silently | m6 |
| Output: ACL write-denied / file as `--out` / missing drive / `bad\|name` | Exit 1, OS error | OK |
| Output: read-only `orders.csv` already present | Exit 1, **partial mixed dataset left** | M1 |
| Output: nonexistent nested directory | Created | OK |
| Ctrl+C or kill during export | Truncated or mixed dataset; `validate` passes; once exit 0 with a corrupt value | **M1** |
| stdout redirected to NUL (Windows) | UnicodeEncodeError, no output | **M7** |

### Phase 5.1: Shopify

`shopify_check.py` (Appendix B) was run on all three presets. Results: 36 handles each; 96 / 144 / 102 variant rows; handles grouped contiguously; handles match `[a-z0-9-]+`; option values unique per handle; SKUs unique; prices numeric with 2 dp; quantities are non-negative integers; `Published=TRUE`. **0 rejections.** Warnings are listed under m4.

### Phase 5.2: BI

`bi_queries.sql` holds 12 queries: types, revenue by market and month, AOV, repeat rate, return rate by category and by market, contribution margin, ROAS/CAC/CTR/CVR by channel, cohort retention, top products, stock-outs, marketing shape, and a null audit. Output is in `bi_queries_output.md`. All results are sane and non-null, except for NULLs that are expected by design: ROAS for zero-spend channels, and right-censored cohort cells. Contribution margin is 41–42% per market.

### Phase 5.3: agent test

`agent_test.py` (Appendix C) gives the model the schema, 5 sample rows per table and the BI outputs, then asks five business questions and requests every contradiction. Backend: `claude -p` (Claude Code CLI), because no API key was configured. The script uses the Anthropic SDK with `claude-opus-5` when `ANTHROPIC_API_KEY` is set.

**How the answers held up.** The agent answered coherently:
- AT has the worst return rate, but that is not statistically significant; outdoor-lighting defects are the real driver.
- No channel is unprofitable.
- FR is strong because of AOV and mix.
- Inventory cannot be judged without history.
- Retention is weak but partly an artifact.

**Triage of the contradictions it reported:**

| Agent contradiction | Triage |
| --- | --- |
| Attribution is tautological (attributed == orders) | **Real**: M2 |
| Repeat revenue credited to acquisition channel; ROAS implausible | **Real**: M2 |
| Direct/organic have impressions and clicks | **Real**: M2 |
| Paid channels dark on ~40% of days | **Real**: M2 |
| Refunds equal the undiscounted price | **Real**: M4 |
| Product titles contradict categories; duplicate titles | **Real**: m10 |
| Retention decays mechanically | **Real**: m2 |
| Behaviour identical across markets | **Real**: m3 |
| Emails do not match names; fictional cities | Real, cosmetic: n3 |
| Censored cohort cells reported as 0 | **My query's bug**, fixed in q8 (now NULL) |
| Order prices do not match variant `price_eur` | Documented (FX + .90 rounding); variants share one price: m10 |
| Catalogue cost ratio vs realised COGS | Agent misread (5-row sample); realised 45.8% is consistent with config |
| Stockouts "go up and down" without restocks | Agent misread q10 (sell-outs per month, not a stock level) |
| Contribution margin omits fulfilment and payment costs | Data scope, not a bug (no such columns) |
| AT return rate not significant | Correct statistics, not a bug |
| Market eligibility not verifiable from the aggregates | Verified separately: 0 violations |

The full agent answers are in Appendix C.

### Phase 5.4: determinism

`ecomgen generate --seed 42 --format all --shopify-export` was run with `PYTHONHASHSEED=0`, with `PYTHONHASHSEED=12345`, and in the Python 3.11 venv (numpy 2.4.6 vs 2.5.3). All 15 output files have identical SHA-256 digests. Fixed-seed exports are **safe as CI snapshot fixtures**. As the README says, pin Faker, because names come from its locale data.

## Recommended fixes before promoting publicly

In priority order:

1. **Fix C1 (stock-out).** Add replenishment: a reorder point and lead time, or restock returned units plus periodic purchase orders. Alternatively scale initial stock to expected demand. At minimum, report the number of orders dropped because of stock-outs in the summary and fail or warn when demand is suppressed. Add a regression test at 50k / 36 asserting orders in every month.
2. **Make export atomic and self-describing (M1).**
   - Write into a temp directory and rename it into place.
   - Refuse a non-empty `--out` unless `--force` is given, and remove stale tables when it is.
   - Write a `manifest.json` (args, seed, version, per-table row count and SHA-256) and have `validate` check it. That catches truncation and mixed runs.
   - Handle KeyboardInterrupt explicitly: clean up and exit 130.
3. **Rework marketing generation (M2).**
   - Generate daily budgets first, then impressions, clicks and conversions.
   - Charge CAC only on first orders, or add an `order_type` / first-touch column.
   - Give unpaid channels no impressions.
   - Allow configured unprofitable channels so agent and BI questions have real answers.
4. **Fix the O(n²) hotspot (M3).** Keep first-order candidates as a pointer into customers sorted by `created_at` plus a pool of unordered IDs, and keep repeat candidates incrementally. Add a real progress bar (per month) and sane input bounds (m5).
5. **Correct the refund basis (M4).** Refund what was paid (discount pro-rated, VAT included), or export `refund_net` and `refund_tax`. Document the choice.
6. **Extend `validate` (M5).** Add VAT per market, currency per market, customer-before-order chronology, `is_repeat`, the funnel invariants, market eligibility, `return > order` strictly, and manifest checks. Raise `validation.py` coverage.
7. **Handle Windows and CI (M7, m1).** Build the console with `legacy_windows=False` or drop the spinner when stdout is not a TTY. Add `pytest-cov` to the `dev` extra. Run CI on Windows plus Python 3.11 and 3.14.
8. **Fix the demand floor (M6).** Make the 0.5 floor proportional to population, or warn when the implied repeat rate deviates strongly from config.
9. **Make the Shopify export import-ready (m4).** Add `Variant Inventory Tracker=shopify`, `Variant Inventory Policy`, `Status`; blank repeated product fields on variant rows; export opening stock and rounded prices.
10. **Realism polish (m2, m3, m7–m11).**
    - Add a `currency` column to `marketing_spend` and an `fx_rates` table.
    - Add a VAT-inclusive pricing option.
    - Use category-scoped title nouns with unique titles.
    - Add a local-time hour-of-day curve.
    - Clip or flag returns beyond the window.
    - Give `average_days` its documented meaning.
    - Vary behaviour per market.

---

## Appendix A: test harness scripts

All scripts live outside the repo and import nothing from ecomgen except through its CLI. The one exception is `interrupt.py`, which calls `ecomgen.cli.app` in-process so that Ctrl+C semantics can be simulated.

### A.1 `runner.py` (wall-clock and peak RSS)

```python
"""Run a command, measuring wall-clock time and peak RSS of the whole process tree.

Usage: python runner.py [--timeout S] [--tail N] -- cmd args...
Prints output tail then a JSON line: {"exit":..,"secs":..,"peak_mb":..}
"""

import json
import io
import subprocess
import sys
import time

import psutil

sys.stdout.reconfigure(encoding="utf-8")

args = sys.argv[1:]
timeout = None
tail = 25
while args and args[0] != "--":
    if args[0] == "--timeout":
        timeout = float(args[1])
        args = args[2:]
    elif args[0] == "--tail":
        tail = int(args[1])
        args = args[2:]
    else:
        break
if args and args[0] == "--":
    args = args[1:]

start = time.perf_counter()
proc = subprocess.Popen(
    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**__import__("os").environ,
    "COLUMNS": "120", "PYTHONIOENCODING": "utf-8"},
)
ps = psutil.Process(proc.pid)
peak = 0
timed_out = False
import threading

chunks = []
reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()))
reader.start()
while proc.poll() is None:
    try:
        rss = ps.memory_info().rss + sum(c.memory_info().rss for c in ps.children(recursive=True))
        peak = max(peak, rss)
    except psutil.Error:
        pass
    if timeout and time.perf_counter() - start > timeout:
        timed_out = True
        for c in ps.children(recursive=True):
            c.kill()
        proc.kill()
        break
    time.sleep(0.05)
proc.wait()
reader.join()
secs = time.perf_counter() - start
out = b"".join(c for c in chunks if c).decode("utf-8", "replace").splitlines()
print("\n".join(out[-tail:]) if tail else "")
print(json.dumps({"exit": proc.returncode, "secs": round(secs, 2), "peak_mb": round(peak / 2**20, 1),
                  "timed_out": timed_out}))
```

### A.2 `matrix.py` (Phase 2)

```python
"""Phase 2 generation matrix: presets x markets x scales, plus seed checks."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
VENV = Path(sys.executable).parent
ECOMGEN = str(VENV / "ecomgen.exe")
RUNNER = str(HERE / "runner.py")
OUT = HERE / "matrix"
OUT.mkdir(exist_ok=True)

PRESETS = ["electronics", "fashion", "garden-decor"]
MARKETS = {"single": "de", "three": "de,at,fr", "all": "de,at,ch,fr,be,es,it,nl,uk"}
SCALES = {"S": (50, 1), "M": (5000, 12), "L": (50000, 36)}


def run(cmd, timeout=3600):
    p = subprocess.run([sys.executable, RUNNER, "--timeout", str(timeout), "--tail", "3", "--", *cmd],
                       capture_output=True, text=True, encoding="utf-8")
    lines = p.stdout.strip().splitlines()
    return json.loads(lines[-1]), "\n".join(lines[:-1])


def stats(path: Path, months: int):
    o = pd.read_csv(path / "orders.csv", parse_dates=["created_at"])
    v = pd.read_csv(path / "variants.csv")
    c = pd.read_csv(path / "customers.csv")
    months_with_orders = o.created_at.dt.strftime("%Y-%m").nunique() if len(o) else 0
    return {
        "customers": len(c), "orders": len(o),
        "orders_per_customer": round(len(o) / len(c), 3) if len(c) else None,
        "last_order": str(o.created_at.max().date()) if len(o) else None,
        "months_with_orders": f"{months_with_orders}/{months}",
        "variants_out_of_stock": f"{int((v.inventory == 0).sum())}/{len(v)}",
    }


def digest(path: Path):
    h = hashlib.sha256()
    for f in sorted(path.glob("*.csv")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


results = []
for scale, (customers, months) in SCALES.items():
    for mname, markets in MARKETS.items():
        for preset in PRESETS:
            d = OUT / f"{preset}_{mname}_{scale}"
            shutil.rmtree(d, ignore_errors=True)
            gen, _ = run([ECOMGEN, "generate", "--preset", preset, "--markets", markets,
                          "--customers", str(customers), "--months", str(months),
                          "--seed", "42", "--out", str(d)])
            val, val_out = run([ECOMGEN, "validate", "--path", str(d)])
            row = {"preset": preset, "markets": mname, "scale": scale,
                   "gen_secs": gen["secs"], "gen_peak_mb": gen["peak_mb"], "gen_exit": gen["exit"],
                   "val_secs": val["secs"], "val_peak_mb": val["peak_mb"], "val_exit": val["exit"],
                   "val_msg": val_out.strip().splitlines()[-1] if val_out.strip() else ""}
            if gen["exit"] == 0:
                row.update(stats(d, months))
            results.append(row)
            print(json.dumps(row), flush=True)

# Seeds: 42 twice (determinism), 1 vs 2 (divergence) on the M/three config.
seed_rows = {}
for label, seed in [("42a", 42), ("42b", 42), ("s1", 1), ("s2", 2)]:
    d = OUT / f"seed_{label}"
    shutil.rmtree(d, ignore_errors=True)
    gen, _ = run([ECOMGEN, "generate", "--customers", "5000", "--months", "12",
                  "--seed", str(seed), "--out", str(d), "--format", "all", "--shopify-export"])
    seed_rows[label] = digest(d)
    print(label, seed_rows[label], gen, flush=True)

pd.DataFrame(results).to_csv(OUT / "matrix_results.csv", index=False)
json.dump(seed_rows, open(OUT / "seed_digests.json", "w"), indent=1)
print("DONE")
```

### A.3 `adversarial.py` (Phase 4 CLI cases)

```python
"""Phase 4 adversarial CLI cases. Each case is run in a fresh output dir."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PY = sys.executable
ECOMGEN = str(Path(PY).parent / "ecomgen.exe")
RUNNER = str(HERE / "runner.py")
OUT = HERE / "adv"
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir()
COPY = os.environ["TEMP"] + r"\egcopy"  # package copy with malformed presets

CASES = [
    # name, extra args, uses package copy?, timeout
    ("customers_0", ["--customers", "0"], False, 120),
    ("customers_1", ["--customers", "1"], False, 120),
    ("customers_1_all_mkts", ["--customers", "1", "--markets", "de,at,ch,fr,be,es,it,nl,uk"], False, 120),
    ("months_0", ["--months", "0"], False, 60),
    ("customers_neg", ["--customers", "-5"], False, 60),
    ("months_neg", ["--months", "-1"], False, 60),
    ("seed_neg", ["--seed", "-1"], False, 60),
    ("seed_huge", ["--seed", str(2**70), "--customers", "50", "--months", "1"], False, 60),
    ("months_1200", ["--months", "1200", "--customers", "500"], False, 300),
    ("months_1e6", ["--months", "1000000"], False, 60),
    ("customers_1e12", ["--customers", "1000000000000"], False, 45),
    ("customers_1e6", ["--customers", "1000000", "--months", "12"], False, 300),
    ("preset_invalid", ["--preset", "nope"], False, 60),
    ("preset_case", ["--preset", "Fashion"], False, 60),
    ("preset_traversal", ["--preset", "../markets"], False, 60),
    ("market_invalid", ["--markets", "de,xx"], False, 60),
    ("market_empty", ["--markets", ""], False, 60),
    ("market_commas", ["--markets", ",,"], False, 60),
    ("market_upper_dupe", ["--markets", " DE , de,At ", "--customers", "50", "--months", "1"], False, 60),
    ("market_gb_alias", ["--markets", "gb"], False, 60),
    ("format_invalid", ["--format", "parquet"], False, 60),
    ("start_feb29_leap", ["--start-date", "2024-02-29", "--customers", "300"], False, 120),
    ("start_feb29_nonleap", ["--start-date", "2023-02-29"], False, 60),
    ("start_jan31_1month", ["--start-date", "2024-01-31", "--months", "1", "--customers", "100"], False, 60),
    ("start_bad", ["--start-date", "2024-13-01"], False, 60),
    ("start_words", ["--start-date", "yesterday"], False, 60),
    ("start_compact", ["--start-date", "20240101", "--customers", "50", "--months", "1"], False, 60),
    ("start_datetime", ["--start-date", "2024-01-01T12:00", "--customers", "50", "--months", "1"], False, 60),
    ("start_year_9999", ["--start-date", "9999-06-01", "--months", "12"], False, 60),
    ("start_year_1", ["--start-date", "0001-01-01", "--months", "1", "--customers", "50"], False, 60),
    ("yaml_syntax", ["--preset", "broken"], True, 60),
    ("yaml_missing_keys", ["--preset", "missingkeys"], True, 60),
    ("yaml_top_list", ["--preset", "toplist"], True, 60),
    ("yaml_empty", ["--preset", "empty"], True, 60),
    ("yaml_name_mismatch", ["--preset", "mismatch"], True, 60),
    ("yaml_bad_weights", ["--preset", "badweights"], True, 60),
    ("yaml_11_seasonality", ["--preset", "season11"], True, 60),
    ("yaml_latin1", ["--preset", "latin1"], True, 60),
    ("yaml_dup_key", ["--preset", "dupkey", "--customers", "50", "--months", "1"], True, 60),
    ("yaml_zero_price_rr1", ["--preset", "edgeprice", "--customers", "200", "--months", "2"], True, 60),
]


def run(cmd, timeout, env=None):
    p = subprocess.run([PY, RUNNER, "--timeout", str(timeout), "--tail", "6", "--", *cmd],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    lines = p.stdout.strip().splitlines()
    return json.loads(lines[-1]), "\n".join(lines[:-1])


results = []
for name, extra, use_copy, timeout in CASES:
    d = OUT / name
    env = dict(os.environ)
    cmd = [ECOMGEN]
    if use_copy:
        env["PYTHONPATH"] = COPY
        cmd = [PY, "-m", "ecomgen.cli"]
    meta, out = run([*cmd, "generate", "--out", str(d), *extra], timeout, env)
    row = {"case": name, "args": " ".join(extra), **meta, "output": out[-600:]}
    if meta["exit"] == 0 and d.exists():
        vmeta, vout = run([ECOMGEN, "validate", "--path", str(d)], 120)
        row["validate"] = f"exit={vmeta['exit']} {vout.strip().splitlines()[-1] if vout.strip() else ''}"
    else:
        row["files_left"] = sorted(p.name for p in d.glob("*")) if d.exists() else []
    results.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)

json.dump(results, open(OUT / "results.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("DONE")
```

### A.4 `interrupt.py` (Ctrl+C and kill during export)

```python
"""Interrupt `ecomgen generate` mid-export and inspect what is left on disk.

The child is a thin wrapper that maps CTRL_BREAK to KeyboardInterrupt (i.e. exactly
what Ctrl+C raises in a foreground console), then calls the real Typer app.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PY = sys.executable
ECOMGEN = str(Path(PY).parent / "ecomgen.exe")
BASE = HERE / "interrupt"
shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir()
WRAPPER = BASE / "wrapper.py"
WRAPPER.write_text(
    "import signal, sys\n"
    "def h(*a):\n    raise KeyboardInterrupt\n"
    "signal.signal(signal.SIGBREAK, h)\n"
    "from ecomgen.cli import app\n"
    "sys.argv = ['ecomgen', *sys.argv[1:]]\n"
    "app()\n")
ARGS = ["generate", "--customers", "500", "--months", "600", "--format", "all",
        "--shopify-export"]
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

seed1 = BASE / "seed1_complete"
subprocess.run([ECOMGEN, *ARGS, "--seed", "1", "--out", str(seed1)], capture_output=True, env=env)


def inspect(d: Path, t0: float):
    report = {}
    for f in sorted(d.iterdir()):
        new = f.stat().st_mtime >= t0
        state = "new" if new else "OLD(seed1)"
        if f.suffix == ".json":
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                state += ",CORRUPT"
        elif f.suffix == ".csv":
            data = f.read_bytes()
            if data and not data.endswith(b"\n"):
                state += ",TRUNCATED"
        report[f.name] = state
    v = subprocess.run([ECOMGEN, "validate", "--path", str(d)], capture_output=True, text=True,
                       encoding="utf-8", env=env)
    vj = subprocess.run([ECOMGEN, "validate", "--path", str(d / "orders.json")],
                        capture_output=True, text=True, encoding="utf-8", env=env)
    return report, (v.returncode, v.stdout.strip().splitlines()[:3]), \
        (vj.returncode, vj.stdout.strip().splitlines()[:2])


results = []
for mode in ("ctrl_c", "kill"):
    for delay in (0.0, 0.15, 0.4, 0.8, 1.5):
        d = BASE / f"{mode}_{delay}"
        shutil.copytree(seed1, d)
        time.sleep(1.1)  # mtime resolution margin
        t0 = time.time()
        p = subprocess.Popen([PY, str(WRAPPER), *ARGS, "--seed", "2", "--out", str(d)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        # wait for export to begin: first table rewritten
        while p.poll() is None and (d / "products.csv").stat().st_mtime < t0:
            time.sleep(0.005)
        time.sleep(delay)
        if p.poll() is None:
            if mode == "ctrl_c":
                os.kill(p.pid, signal.CTRL_BREAK_EVENT)
            else:
                p.kill()
        out = p.communicate()[0].decode("utf-8", "replace").strip().splitlines()
        rep, val, valj = inspect(d, t0)
        row = {"mode": mode, "delay": delay, "exit": p.returncode, "stdout_tail": out[-2:],
               "files": rep, "validate_dir": val, "validate_json": valj}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
json.dump(results, open(BASE / "results.json", "w"), indent=1)
```

### A.5 `validate_mutations.py` (validator blind spots)

```python
"""Mutate a valid export and see whether `ecomgen validate` notices."""

import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
ECOMGEN = str(Path(sys.executable).parent / "ecomgen.exe")
SRC = HERE / "audit" / "ds"
BASE = HERE / "mutations"
shutil.rmtree(BASE, ignore_errors=True)


def csv(d, t):
    return pd.read_csv(d / f"{t}.csv", dtype=str, keep_default_na=False)


def save(df, d, t):
    df.to_csv(d / f"{t}.csv", index=False)


def m_truncate(d):
    p = d / "orders.csv"
    b = p.read_bytes()
    p.write_bytes(b[: len(b) // 2])


def m_total_cent(d):
    o = csv(d, "orders"); o.loc[0, "total"] = str(Decimal(o.loc[0, "total"]) + Decimal("0.01"))
    save(o, d, "orders")


def m_drop_returns(d):
    (d / "returns.csv").unlink()


def m_extra_col(d):
    o = csv(d, "orders"); o["foo"] = "1"; save(o, d, "orders")


def m_market_mismatch(d):  # order market != customer market (consistent otherwise)
    o = csv(d, "orders"); i = o.index[o.market == "de"][0]; o.loc[i, "market"] = "fr"
    save(o, d, "orders")


def m_wrong_vat(d):  # zero tax but total adjusted so arithmetic still reconciles
    o = csv(d, "orders"); t = Decimal(o.loc[0, "tax"])
    o.loc[0, "tax"] = "0.00"; o.loc[0, "total"] = str(Decimal(o.loc[0, "total"]) - t)
    save(o, d, "orders")


def m_currency(d):
    o = csv(d, "orders"); o.loc[0, "currency"] = "USD"; save(o, d, "orders")


def m_order_before_customer(d):
    o = csv(d, "orders"); c = csv(d, "customers")
    cid = o.loc[0, "customer_id"]
    c.loc[c.id == cid, "created_at"] = "2030-01-01T00:00:00Z"; save(c, d, "customers")


def m_is_repeat(d):
    o = csv(d, "orders"); o.loc[0, "is_repeat"] = "True"; save(o, d, "orders")


def m_marketing(d):
    m = csv(d, "marketing_spend"); m.loc[0, "attributed_orders"] = "999"
    m.loc[0, "clicks"] = "1"; m.loc[0, "impressions"] = "0"; save(m, d, "marketing_spend")


def m_return_refund_zero_before(d):  # return 1 day BEFORE... use exactly equal time
    r = csv(d, "returns"); o = csv(d, "orders").set_index("id")
    r.loc[0, "created_at"] = o.loc[r.loc[0, "order_id"], "created_at"]; save(r, d, "returns")


def m_negative_discount_code(d):
    o = csv(d, "orders"); i = o.index[o.discount_code == ""][0]; o.loc[i, "discount_code"] = "SAVE99"
    save(o, d, "orders")


def m_product_not_in_market(d):
    p = csv(d, "products"); p["markets"] = '["at"]'; save(p, d, "products")


def m_mixed_stale_json(d):  # stale JSON with more tables than csv
    for f in d.glob("*.csv"):
        if f.name not in ("orders.csv",):
            f.unlink()


MUTATIONS = [m_truncate, m_total_cent, m_drop_returns, m_extra_col, m_market_mismatch,
             m_wrong_vat, m_currency, m_order_before_customer, m_is_repeat, m_marketing,
             m_return_refund_zero_before, m_negative_discount_code, m_product_not_in_market,
             m_mixed_stale_json]
for m in MUTATIONS:
    d = BASE / m.__name__
    shutil.copytree(SRC, d)
    for j in d.glob("*.json"):
        if m is not m_mixed_stale_json:
            j.unlink()
    m(d)
    r = subprocess.run([ECOMGEN, "validate", "--path", str(d)], capture_output=True, text=True,
                       encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    verdict = "CAUGHT" if r.returncode else "ACCEPTED"
    print(f"{m.__name__:<30} exit={r.returncode} {verdict:<8} {' | '.join(lines[:2])[:200]}")
```

## Appendix B: `shopify_check.py`

```python
"""Check products_shopify.csv against Shopify's product CSV import rules
(help.shopify.com/en/manual/products/import-export/using-csv, legacy column names)."""

import csv
import re
import sys
from collections import defaultdict

path = sys.argv[1]
raw = open(path, "rb").read()
rows = list(csv.DictReader(open(path, encoding="utf-8", newline="")))
headers = list(rows[0].keys()) if rows else []
LEGACY_OK = {"Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
             "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value", "Option3 Name",
             "Option3 Value", "Variant SKU", "Variant Price", "Variant Inventory Qty",
             "Variant Inventory Tracker", "Variant Inventory Policy",
             "Variant Fulfillment Service", "Variant Grams", "Status", "Variant Requires Shipping",
             "Variant Taxable", "Cost per item", "Variant Compare At Price"}
issues = []


def issue(sev, msg):
    issues.append((sev, msg))
    print(f"[{sev}] {msg}")


print(f"rows={len(rows)} headers={headers}")
print(f"UTF-8 BOM: {raw.startswith(b'\xef\xbb\xbf')}; line endings CRLF: {b'\r\n' in raw}")
unknown = [h for h in headers if h not in LEGACY_OK]
if unknown:
    issue("REJECT", f"unrecognised headers {unknown}")
for req in ("Handle", "Title"):
    if req not in headers:
        issue("REJECT", f"missing required header {req}")

by_handle = defaultdict(list)
order = []
for i, r in enumerate(rows):
    if r["Handle"] not in by_handle:
        order.append(r["Handle"])
    by_handle[r["Handle"]].append((i, r))
# contiguous grouping
positions = {h: [i for i, _ in v] for h, v in by_handle.items()}
noncontig = [h for h, p in positions.items() if p != list(range(p[0], p[0] + len(p)))]
if noncontig:
    issue("REJECT", f"variant rows of {len(noncontig)} handles are not contiguous")
bad_handle = [h for h in by_handle if not re.fullmatch(r"[a-z0-9-]+", h)]
if bad_handle:
    issue("REJECT", f"invalid handles {bad_handle[:5]}")
for h, lst in by_handle.items():
    names = {r["Option1 Name"] for _, r in lst}
    vals = [r["Option1 Value"] for _, r in lst]
    if len(names) != 1:
        issue("REJECT", f"{h}: inconsistent Option1 Name {names}")
    if len(vals) != len(set(vals)):
        issue("REJECT", f"{h}: duplicate option values {vals}")
    if len(lst) > 100:
        issue("REJECT", f"{h}: >100 variants")
    if not lst[0][1]["Title"]:
        issue("REJECT", f"{h}: first row has no Title")
    if len(lst) > 1 and any(r["Title"] for _, r in lst[1:]):
        repeated_title = True
skus = [r["Variant SKU"] for r in rows]
if len(skus) != len(set(skus)):
    issue("WARN", "duplicate SKUs")
bad_pub = {r["Published"] for r in rows} - {"TRUE", "FALSE", "true", "false"}
if bad_pub:
    issue("REJECT", f"invalid Published values {bad_pub}")
for r in rows:
    if not re.fullmatch(r"\d+(\.\d{1,2})?", r["Variant Price"]):
        issue("REJECT", f"bad price {r['Variant Price']}")
    if not re.fullmatch(r"\d+", str(r["Variant Inventory Qty"])):
        issue("REJECT", f"bad inventory {r['Variant Inventory Qty']}")
    if len(r["Variant SKU"]) > 255:
        issue("REJECT", "SKU too long")

print("--- summary")
print(f"products={len(by_handle)} variants={len(rows)} "
      f"variants/product min={min(map(len, by_handle.values()))} max={max(map(len, by_handle.values()))}")
titles = defaultdict(set)
for h, lst in by_handle.items():
    titles[lst[0][1]["Title"]].add(h)
dup_titles = {t: len(hs) for t, hs in titles.items() if len(hs) > 1}
print(f"repeated Title/Body/Vendor/Type/Tags on 2nd+ variant rows: "
      f"{sum(len(v) - 1 for v in by_handle.values())} rows (docs: leave blank)")
print(f"products sharing a title: {dup_titles}")
print(f"zero-inventory variants: {sum(1 for r in rows if r['Variant Inventory Qty'] == '0')}")
print(f"'Variant Inventory Tracker' present: {'Variant Inventory Tracker' in headers}")
print(f"'Status' present: {'Status' in headers}; 'Variant Inventory Policy' present: "
      f"{'Variant Inventory Policy' in headers}")
print(f"Tags sample: {rows[0]['Tags']!r}; Type sample: {rows[0]['Type']!r}")
print(f"price sample: {[r['Variant Price'] for r in rows[:4]]}")
print("REJECT count:", sum(1 for s, _ in issues if s == "REJECT"))
```

## Appendix C: `agent_test.py` and full agent output

```python
"""Agent coherence test: hand an LLM the ecomgen schema, samples, and BI aggregates,
ask five business questions, and collect every contradiction it reports.

Backend: Anthropic SDK (claude-opus-5) when credentials resolve, otherwise the
logged-in `claude -p` CLI. Usage: python agent_test.py <dataset_dir> <bi_output.md> <out.md>
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

data_dir, bi_md, out_md = map(Path, sys.argv[1:4])
TABLES = ["products", "variants", "customers", "orders", "order_items", "returns",
          "marketing_spend"]

parts = ["# Dataset: synthetic DTC garden-decor store, markets DE/AT/FR, 2024, EUR.",
         "Documented semantics: orders.total = subtotal - discount + shipping + tax; "
         "returns refund an order item; marketing_spend attributes orders to the ordering "
         "customer's acquisition_channel; direct/organic have no paid spend; "
         "products.cost_eur is unit COGS; variants.inventory is REMAINING stock at period end.",
         "", "## Schema and sample rows"]
for t in TABLES:
    df = pd.read_csv(data_dir / f"{t}.csv")
    parts += [f"### {t} ({len(df)} rows)", df.sample(5, random_state=0).to_csv(index=False)]
parts += ["## Pre-computed analyst query results (DuckDB)", bi_md.read_text(encoding="utf-8")]
context = "\n".join(parts)

QUESTIONS = """You are a senior e-commerce analyst. Using ONLY the data above, answer:
1. Which market has the worst return rate, and why?
2. Which marketing channel is unprofitable once COGS and refunds are considered? Show the numbers.
3. France has fewer customers than Germany yet similar revenue. Explain the driver.
4. Is inventory healthy? What would you plan for next season?
5. What does cohort retention say about the business, and is it believable?

Then list every CONTRADICTION or implausibility you notice in the data (values that cannot all be
true for a real store, metrics that are suspiciously mechanical, or questions the data cannot
answer although it looks like it should). Be concrete and cite the numbers.

Reply with JSON only: {"answers": {"1": "...", ..., "5": "..."},
"contradictions": [{"title": "...", "evidence": "...", "severity": "high|medium|low"}]}"""


def ask_sdk(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model="claude-opus-5",
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"refused: {msg.stop_details}")
    return "".join(b.text for b in msg.content if b.type == "text")


def ask_cli(prompt: str) -> str:
    exe = shutil.which("claude")
    r = subprocess.run([exe, "-p", "--output-format", "text", "--tools", ""], input=prompt,
                       capture_output=True, text=True, encoding="utf-8", timeout=900)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return r.stdout


prompt = context + "\n\n" + QUESTIONS
have_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
backend = "anthropic-sdk claude-opus-5" if have_key else "claude -p (Claude Code CLI)"
raw = ask_sdk(prompt) if have_key else ask_cli(prompt)
start, end = raw.find("{"), raw.rfind("}")
result = json.loads(raw[start:end + 1])

lines = [f"# Agent coherence test\n\nBackend: {backend}. Prompt size: {len(prompt):,} chars.\n",
         "## Answers\n"]
for k, v in result["answers"].items():
    lines.append(f"**Q{k}.** {v}\n")
lines.append("## Contradictions surfaced by the agent\n")
for c in result["contradictions"]:
    lines.append(f"- **[{c['severity']}] {c['title']}** — {c['evidence']}")
out_md.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
```

### Agent output (verbatim)

Backend: claude -p (Claude Code CLI). Prompt size: 17,273 chars.

## Answers

**Q1.** Austria has the highest return rate on paper: 55 of 733 items, or 7.50%. France is at 6.73% (184/2,733) and Germany at 6.56% (236/3,600). By value, AT refunds are 5,302/75,243 = 7.0% of net merch, against 6.8% for DE and 6.2% for FR. The gap is probably noise, though. At Germany's rate AT would have about 48 returns instead of 55, only 7 more. The binomial standard error on 733 items is about 0.9 pp, so the 0.94 pp gap to DE is roughly 1 standard error and not significant. The data cannot explain why AT is higher, because there is no market-by-category or market-by-reason breakdown. The strongest return driver in the data is category, not market: outdoor-lighting returns 8.4% of items (top reason 'defective'), against 5.9% for garden furniture and 5.8% for planters. Several outdoor-lighting SKUs are sold only in AT/DE (for example outdoor-lighting-009 and -005), so a mix skew toward lighting is a possible cause for AT. However, DE has the same assortment and the lowest rate, which weakens that explanation. The actionable issue is the defect rate in outdoor lighting, not Austria.

**Q2.** No channel is unprofitable. q7 ROAS reconciles to net merchandise revenue (subtotal minus discount): meta 8.63 x 29,260 = €252.5k (€201.5/order), google 9.99 x 24,312 = €242.9k (€210.7/order), email 70.34 x 1,589 = €111.7k (€210.0/order). The remaining 863.6k - 607.1k = €256.4k across 1,247 organic and direct orders comes to €205.6/order, which matches the overall €206.4. Blended across the store, COGS is 395.8k/863.6k = 45.8% of net merch and refunds are 56.4k/863.6k = 6.5%. That leaves 47.6% of net merch before marketing, so break-even ROAS is about 2.1. Contribution after COGS, refunds and spend by channel: meta 252.5k x 0.476 = 120.3k - 29.3k = +€91.0k; google 242.9k x 0.476 = 115.7k - 24.3k = +€91.4k; email 111.7k x 0.476 = 53.2k - 1.6k = +€51.6k. Organic and direct carry no spend and are pure positive contribution. Looking at acquisition only: meta CAC is €31.09 and google €27.95, against first-order contribution of about €96-100, so both pay back on the first order. Two caveats. First, attribution gives each customer's lifetime orders, including repeats, to their acquisition channel, so paid ROAS is inflated by retention revenue and shows nothing about incrementality. Second, q6 margin ignores shipping, fulfilment and payment costs, which the data does not contain. On the data given, the premise of an unprofitable channel is false.

**Q3.** France has fewer customers than Germany (1,943 vs 2,591) and fewer orders (1,691 vs 2,091), but more net merch (€399.9k vs €388.5k). The driver is basket value, not frequency. Net-merch AOV is €236.46 in FR against €185.79 in DE (+27%). The gross AOV and median show the same pattern: €290.83 vs €226.92, and €198.54 vs €159.08. Frequency is identical (1.33 vs 1.34 orders per buyer), and repeat rates are nearly identical (24.9% vs 24.2%). FR turns a slightly larger share of customers into buyers (1,267/1,943 = 65.2% vs 1,562/2,591 = 60.3%), which helps a little. The higher AOV comes from price per item, not items per basket. FR actually has fewer lines per order (2,733/1,691 = 1.62 vs 3,600/2,091 = 1.72), but net merch per line is €146 against €108 (+36%). That points to a mix skewed toward high-ticket garden furniture. It fits with some cheaper planter and lighting SKUs being listed only for AT/DE. Higher-priced items also carry more COGS: FR COGS is 47.2% of net merch against 44.7% in DE, so FR has the lowest contribution margin (40.9% vs 41.5%) despite the highest AOV. VAT does not explain it: 'gross' is net x 1.20 in FR and x 1.19 in DE, only a 1-point difference.

**Q4.** The data cannot answer this properly. There is only a period-end inventory snapshot, with no inventory history, receipts, purchase orders or lead times. From what is visible, the stock looks overstocked, not tight. Sample variants hold 85-153 units at year end. The best-selling products shift 117-240 units a year spread over about 3 variants, which is roughly 40-80 units per variant per year. That means about 1.5-3 years of cover at 2024 run-rate. Against that, q10 reports up to 5 variants sold out per month, and the total climbs to 5 in December. So a small tail of variants is stocked out while the bulk is overstocked: a distribution problem, not a volume problem. For next season: (a) Demand is strongly seasonal. DE runs 94-100 orders in Jan-Feb, 259-262 in May-Jun and 128-164 in Q4, and FR peaks at 202 in May. Land stock by March, cover the May-June peak, and avoid buying for Q4. (b) Cut buys on the long-cover variants and work down the existing stock through markdowns before the spring peak. (c) Replenish the variants that sold out and find out when they ran out, because the lost-sales window cannot be measured. (d) Fix outdoor-lighting quality (8.4% returns, 'defective') with the supplier before reordering. (e) Rebuild the plan once real opening stock and receipts are available.

**Q5.** Taken at face value, retention is weak and declining. m1 falls from 27% for the January cohort to 3.5-7% for cohorts from August onward. m6 is 1-4%. Overall, 22-25% of buyers ever repeat, at 1.32-1.34 orders per buyer. That would describe an acquisition-driven business with low loyalty, which is typical for occasional-purchase garden furniture. It is only partly believable. (a) Zeros that mean 'not yet observable' are reported as 0 instead of NULL. m6 for the Jul-Dec cohorts, m3 for Oct-Dec, m2 for Nov-Dec and m1 for Dec all fall in 2025, which is outside the data. The table looks like a retention collapse that is really right-censoring. (b) The steady cohort-over-cohort decline is itself suspicious. It is what you get when a generator assigns each customer a number of orders spread over the remaining year: early cohorts have more months to use them up. It does not show a real change in loyalty. (c) Some curves rise with age (May m2 0.091 > m1 0.072; Aug and Sep m2 > m1; Jul and Sep m3 ≥ m2), which is possible but noisy on cohorts of 200-390. (d) There is only 12 months of data, so there is no year-over-year view of real long-run retention. Use m1 from the Jan-Apr cohorts (13-27%) as the realistic benchmark, and treat the late-cohort numbers as undefined.

## Contradictions surfaced by the agent

- **[high] Attributed orders equal orders exactly: attribution is tautological** — q7 shows attributed == orders for every channel (1,253 / 1,153 / 532 / 739 / 508). These sum to 4,185, which is every order in the dataset. new_customers sums to 3,135, which equals q3 buyers exactly (306 + 1,562 + 1,267). marketing_spend.attributed_orders is derived from customers.acquisition_channel, not measured independently. That makes ROAS, CVR and 'attributed' circular, and they cannot validate channel performance.
- **[high] Lifetime (repeat) revenue credited to acquisition channel inflates paid ROAS** — About 25% of buyers repeat, and 1,050 orders (4,185 - 3,135) are repeat orders. All of them are credited to the original acquisition channel. Paid ROAS of 8.6-10.0 on €38-44/day budgets, with meta and google CPA of €21-23 for a €200+ AOV furniture store, is implausibly efficient. Email is credited with 380 new-customer acquisitions at a €4.18 CAC and ROAS of 70, but email normally reaches existing contacts.
- **[medium] 'Direct' and 'organic' have impressions and clicks** — The sample row '2024-11-28,fr,direct,0.0,136,22,3' shows impressions and clicks for direct traffic. q7 gives direct a CTR of 10.56% and CVR of 13.71%. Direct traffic has no ad impressions, so these values are fabricated.
- **[medium] Paid channels randomly dark on ~40% of days** — q11 shows meta with spend on 665 of 1,098 market-days (433 zero-impression days) and google with 627 of 1,098 (471 zero). The sample row '2024-04-16,at,meta,0.0,0,0,0' is one of these. Always-on paid campaigns do not switch off on/off at random day to day. Organic also shows zero impressions on 607 of 1,098 days, yet it is credited with 739 orders.
- **[medium] Order line prices do not match catalog or variant prices; variant options do not affect price** — q9 merch_revenue / units is an exact integer for all top-10 products: 633, 404, 441, 397, 257, 209, 330, 193, 312, 153. Variants are priced differently in the catalog: furniture-009 Rattan is €403.75 but sells at €404.0, and furniture-001 Rattan is €329.60 but sells at €330. The planters-009 Medium and Large variants are both €85.61. Order-item unit prices are rounded values (193.0, 103.0, 441.0) unrelated to variant price_eur. Every product appears to sell at a single price regardless of variant.
- **[medium] Refunds equal full list unit price even on discounted orders** — Return refund_amount values (330.0, 152.0, 404.0, 77.9) equal line unit_price. 27.5% of orders carry about an 11% discount (SAVE11: 7.93/71.9, 39.12/363.9). Refunding the undiscounted price overrefunds the customer. Refunds also seem to ignore line quantity: item-000001259-01 has qty 2 at €103, and it is unclear whether one unit or both would be refunded.
- **[medium] Catalog cost ratio contradicts realized COGS ratio** — In the products sample, cost_eur/price_eur ranges 29-41% (27.35/93.38 = 29%, 13.08/37.82 = 35%, 29.36/71.84 = 41%). Realized COGS is 395.8k / 863.6k = 45.8% of net merch. Discounts only reduce revenue by about 3%, so either transaction prices sit well below catalog prices or COGS is computed on a different price basis.
- **[medium] Product titles contradict categories; duplicate titles** — 'Sculpted Stone Planter' appears in outdoor-lighting (€37.82) and in garden-furniture (€397). 'Timeless Acacia Side Table' and 'Minimal Rattan Planter' are in outdoor-lighting. 'Minimal Acacia Lantern' and 'Timeless Acacia Planter' (€633) are in garden-furniture. q9 flags 2 products each titled 'Sculpted Rattan Bench' (€404 vs €209) and 'Sculpted Stone Planter'. As a result, the category return rates in q4 (for example lighting 'defective' at 8.4%) cannot be trusted as product insight.
- **[medium] Stockouts vs period-end-only inventory** — variants.inventory is remaining stock at period end, and there are no receipts or inventory history. q10 nevertheless reports monthly stockouts that go up and down (1, 2, 4, 4, 3, 3, 2, 3, 5). Without restocks, a variant that sold out in April cannot keep selling or come back. So either restocks exist but are not in the data, or q10 is reconstructed from assumptions. Meanwhile sample variants hold 85-153 units, 1.5-3 years of cover.
- **[medium] Censored cohort cells reported as 0** — q8 shows m6 = 0 for the Jul-Dec cohorts, m3 = 0 for Oct-Dec, m2 = 0 for Nov-Dec and m1 = 0 for Dec. All of these fall in 2025, outside the data window, and should be NULL. Separately, m1 decays steadily from 0.27 (Jan) to about 0.04-0.07 (Aug-Nov), which looks like a generator allocating a fixed number of orders over the remaining year, not a real change in loyalty.
- **[low] Suspiciously uniform behavior across markets** — Orders per buyer is 1.32 / 1.34 / 1.33 and repeat rate 0.219 / 0.242 / 0.249. Discounted share is 0.266 / 0.267 / 0.286, all with one code (SAVE11). Item return rates are 6.6-7.5%. Three different countries showing near-identical behavior suggests shared generator parameters.
- **[low] AT 'worst return rate' is not statistically distinguishable** — AT is 55/733 = 7.5% against DE 6.56%. That is about 7 returns above expectation, roughly 1 standard error (±0.9 pp). The data looks like it can answer 'why', but there is no market-by-category or market-by-reason cut.
- **[low] Contribution margin omits real cost lines** — q6 contribution of 41-42% includes shipping revenue (€4.90 DE, €5.90 AT/FR) but no shipping, fulfilment, payment or return-logistics cost. It also does not show whether returned goods are restocked or written off. Real CM for bulky garden furniture would be materially lower.
- **[low] ROAS NaN / CAC 0 for unpaid channels (q7 CHECK flag)** — Organic and direct have spend 0, which gives ROAS = NaN (the 2 nulls) and CAC = 0. A CAC of 0 is misleading and should be N/A. Customers acquired there are not free.
- **[low] Synthetic identity fields are inconsistent** — Email local parts do not match customer names: 'neumeisterviktor' belongs to Sena Tiefenbacher and 'gregoirepeltier' to Noémi Poirier. Cities are fictional ('Renaud-sur-Mer', 'Bigot', 'Saint Xavier'). 1,865 of 5,000 customers never ordered but have an acquisition channel, including customers created as late as 2024-12-24.
- **[low] Market eligibility not verifiable** — products.markets restricts some SKUs to ['at','de'] (for example planters-008 and outdoor-lighting-009). No query checks whether FR orders contain non-FR SKUs, so the assortment effect used to explain FR's higher AOV cannot be confirmed.
