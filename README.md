# ecomgen

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Synthetic data · E-commerce · Shopify · BI testing · Python CLI**

Generate a coherent, realistic synthetic dataset for a multi-market direct-to-consumer store.

`ecomgen` exists so BI pipelines, data agents, demos, and Shopify workflows can be
developed and tested without exposing real customer data. A single command creates a
connected catalog, customers, orders, returns, and daily marketing performance using
market-aware currencies, tax, demand, and locale data.

## Quickstart

```bash
ecomgen generate --preset garden-decor --markets de,at,fr --customers 5000 --months 12 --seed 42 --out ./output
```

This writes seven CSV files and a `manifest.json` to `./output`:

```text
products.csv
variants.csv
customers.csv
orders.csv
order_items.csv
returns.csv
marketing_spend.csv
manifest.json
```

Use `--format json` for records-oriented JSON files or `--format all` for both CSV
and JSON.

Requires Python 3.11+; see [installation](#requirements-and-installation) below.

## Requirements and installation

- Python 3.11 or newer

Install the project in editable mode:

```bash
python -m pip install -e .
```

## CLI reference

### `ecomgen generate`

Generate and export a complete synthetic dataset.

```text
ecomgen generate [OPTIONS]

--preset TEXT           Bundled preset name. Default: garden-decor
--markets TEXT          Comma-separated market codes. Default: de,at,fr
--customers INTEGER     Number of customers, 0 to 500,000. Default: 5000
--months INTEGER        Number of months, 1 to 120. Default: 12
--start-date DATE       Start of the generation window. Default: 2024-01-01
--seed INTEGER          Random seed; a non-negative integer. Default: 42
--out PATH              Output directory: new, empty, or an earlier ecomgen
                        export, which is replaced. Default: dataset
--format [csv|json|all] Dataset export format. Default: csv
--shopify-export        Also write products_shopify.csv. Default: disabled
--help                  Show command help.
```

Generation holds the whole dataset in memory. Expect roughly 45 s and 1 GB per
200,000 customers over a 12-month window (about 2 s and 100 MB for the 5,000-customer
default), scaling linearly; `--customers` is capped at 500,000 so a run finishes on a
normal laptop. `--start-date` must be 1970-01-01 or later.

The fixed `2024-01-01` default is intentional: omitting `--start-date` does not make
the generated period depend on the day the command is run.

`--customers`, `--months`, or `--seed` outside its range exits with status 2 and
names the option. The window, plus 30 days for returns, must end by 9999-12-31;
otherwise generation exits with status 1 and an error naming `--start-date` and
`--months`.

Examples:

```bash
# JSON only
ecomgen generate --format json --out ./output

# CSV and JSON, plus the Shopify product CSV
ecomgen generate --format all --shopify-export --out ./output

# A six-month UK and Switzerland dataset
ecomgen generate --markets uk,ch --months 6 --start-date 2025-01-01 --out ./output
```

Bundled market codes are `de`, `at`, `ch`, `fr`, `be`, `es`, `it`, `nl`, and `uk`.
Repeated market codes are de-duplicated. An unknown market, an empty market list, or
an unknown preset causes generation to exit with status 1. So does an `--out`
directory that is not empty and is not an earlier ecomgen export; see
[Output directory and manifest](#output-directory-and-manifest). Pressing Ctrl+C
prints `Aborted`, leaves the destination untouched, and exits with status 130.

### `ecomgen presets`

List bundled generation presets in stable alphabetical order:

```bash
ecomgen presets
```

```text
electronics
fashion
garden-decor
```

### `ecomgen validate`

Load a complete CSV or JSON export and check its schemas, relationships, inventory,
return chronology and refund bounds, order arithmetic, market/currency consistency,
and marketing/customer reconciliation.

```text
ecomgen validate --path PATH
```

`--path` is required and must be readable. It may identify an export directory or
one `.csv`/`.json` table within that directory; all seven tables of the selected
format must be present beside it. The manifest is authoritative: validation requires
`manifest.json`, verifies every listed row count and SHA-256 digest, rejects unlisted
dataset files, reads the selected markets and semantic window metadata from it, and
compares CSV and JSON representations when both are present. A valid dataset exits
with status 0. Load or integrity errors are printed and exit with status 1.

```bash
ecomgen validate --path ./output
```

## Data model

| Table | Key fields | Purpose |
| --- | --- | --- |
| `products` | `id`, `title`, `category`, `description_short`, `price_eur`, `cost_eur`, `markets` | Base catalog and market availability |
| `variants` | `id`, `product_id`, `sku`, option fields, `price_eur`, `inventory` | Sellable product options and remaining stock |
| `customers` | `id`, `market`, identity and location fields, `created_at`, `acquisition_channel` | Locale-aware synthetic customers |
| `orders` | `id`, `customer_id`, `market`, `created_at`, `currency`, `fx_rate_from_eur`, gross VAT-inclusive totals, `discount_code`, `is_repeat` | Market-local transactions serialized in UTC |
| `order_items` | `id`, `order_id`, `variant_id`, `quantity`, `unit_price` | Order line items |
| `returns` | `id`, `order_id`, `order_item_id`, `reason`, `refund_amount`, `created_at` | Item-level returns and refunds |
| `marketing_spend` | `date`, `market`, `channel`, `currency`, `fx_rate_from_eur`, `spend`, `impressions`, `clicks`, `new_customers` | Daily channel spend and acquisitions in the market currency; there is no `attributed_orders` field |

The core relationships are:

```text
products 1──* variants 1──* order_items *──1 orders *──1 customers
                                      └──0..1 returns

marketing_spend is keyed by date + market + channel. `new_customers` equals the
number of customers acquired on that market-local date with that market and
`acquisition_channel`. Orders are not copied into the marketing table and there is
no `attributed_orders` column.
```

Money is denominated in each market's `currency`. Orders and marketing rows also
carry `fx_rate_from_eur`, the rate used to price them, so `amount / fx_rate_from_eur`
converts any figure to EUR and compares it with the EUR-denominated `cost_eur`
without reading the bundled market configuration.

Each return points to both its order and order item. The validator checks those
foreign keys and confirms that the item belongs to the referenced order.

## How realism is modeled

- **Catalog:** each preset category produces 12 products. Prices and cost ratios come
  from category ranges. `price_eur` is a gross, VAT-inclusive list price, while
  `cost_ratio_range` is a share of *net* revenue, so `cost_eur` is drawn from that
  ratio of the net catalog price (gross ÷ 1.19, the reference VAT rate of the largest
  bundled market). Realised gross margin therefore moves slightly with each market's
  own VAT rate. Titles use that category's own adjective, material, and noun
  vocabularies and are unique across the generated catalog. Products receive one
  configured option dimension and a variant for each value in that dimension. Every
  product is available in every selected market, so assortment does not create a
  hidden market-performance difference.
- **Customers:** customer counts are allocated by relative market demand. Faker uses
  each market's locale for names and cities. Generated emails use the reserved
  `example.test` domain. Customer and order hours are sampled from each market's
  configured local-time profile, including daylight-saving transitions, then
  serialized as UTC timestamps (`Z` in CSV).
- **Demand:** daily order counts combine market demand weights, the preset's 12
  monthly seasonality multipliers, weekday multipliers, date-range spikes, bounded
  noise, and Poisson sampling.
- **Lifecycle:** an order cannot predate its customer. Repeat orders use the preset's
  repeat probability and recency weighting based on the configured average interval;
  bundled presets target repeat probabilities between 20% and 29%. Daily demand is
  proportional to the customer count (there is no minimum), and no customer places
  more than `ceil(2 × window days / average_days)` repeat orders, so tiny populations
  keep plausible repeat rates.
- **Baskets and inventory:** basket units are Poisson-distributed and biased toward
  lower-priced variants, so expensive catalogs tend toward smaller baskets. Units are
  drawn over the market's whole assortment; a unit of a sold-out variant is a lost
  sale, and an order whose every unit is sold out is dropped. Stock is decremented per
  unit and cannot become negative.
- **Replenishment:** every day, each variant whose on-hand plus on-order stock is at
  or below `max(reorder_point, recent daily sales × lead_time_days)` gets a purchase
  order of `max(restock_quantity, recent daily sales × cover_days)` units, arriving
  `lead_time_days` later. Recent daily sales average the trailing 28 days. The policy
  is the preset's optional `inventory` block (defaults: `reorder_point: 30`,
  `restock_quantity: 120`, `lead_time_days: 10`, `cover_days: 45`). If stock-outs
  drop more than 5% of intended orders, generation emits a `GenerationWarning`; above
  25% it fails with `StockoutError` instead of writing a dataset that misrepresents
  demand.
- **Market pricing:** EUR catalog prices are converted with the configured FX rate.
  Prices below 100 use `.90` endings; prices at or above 100 are rounded to whole
  units. These prices and market shipping charges are gross and already include VAT;
  currency and the included VAT amount are market-specific.
- **Accounting:** money uses `Decimal`, all exported customer-facing prices are gross
  (VAT-inclusive), and values are rounded to two decimal places. `tax` reports the
  VAT already included in `total`; it is not added a second time:

  ```text
  subtotal          = Σ(unit_price × quantity)
  gross_before_tax  = subtotal − discount + shipping
  tax               = ROUND_HALF_UP(gross_before_tax × vat_rate / (1 + vat_rate), 0.01)
  total             = gross_before_tax
  net_revenue_ex_vat = total − tax
  ```
- **Discounts:** usage and depth follow preset ranges. Applied discounts receive a
  generated `SAVE<n>` code.
- **Returns:** each order item is sampled using its category's return rate and
  weighted reasons. Returns occur 3–30 days after the order and refund what the
  customer paid for the whole line, in the order currency, never more:

  ```text
  line             = unit_price × quantity
  exact_share      = discount × line / subtotal
  allocated_share  = FLOOR_TO_CENTS(exact_share) + largest-remainder cent
  refund           = line − allocated_share
  ```

  `unit_price` is already VAT-inclusive, so VAT is not added again. The line carries
  its pro-rata share of the order discount; shipping is not refunded. Remaining
  discount cents are assigned by largest remainder with item id as the stable
  tie-break, so line allocations reconcile exactly to the order discount. The same
  allocation (`ecomgen.accounting.item_paid_values`) caps cumulative refunds per
  order item in `ecomgen validate`.
- **Marketing and acquisition:** spend comes first and buys customers; orders never
  feed back into spend. Customers are split across markets by demand weight and
  across channels by a multinomial draw on the preset `channel_mix` weights. Each
  paid channel (`email`, `google`, `meta`) in each market gets a budget of
  `expected customers × CAC`, with the CAC drawn once from `cac_range` (EUR). Daily
  spend follows seasonality, weekday and spike multipliers with log-normal noise.
  Daily CAC rises with daily spend (`CAC × (spend / mean planned spend)^0.35`), so
  extra spend has diminishing returns. The channel's customers are then placed on
  market-local days by a multinomial draw in proportion to what each day's spend
  bought, which keeps `--customers` exact. `direct` and `organic` acquire customers along the
  calendar multipliers with zero spend, impressions and clicks. Every day has one
  row per selected market and channel. `spend` is in the market's `currency`.
  Impressions come from a per-channel CPM and clicks from a CTR, with
  `impressions >= clicks >= new_customers` for paid channels. Acquisition cost is
  therefore paid once per customer; repeat orders cost nothing. Channel efficiency
  is configurable: each bundled preset gives `meta` a CAC above what its customers
  earn back, so it has a negative contribution margin (revenue minus COGS, refunds
  and spend) while `google` and `email` stay profitable.

## Add a preset

Presets are bundled package resources; the CLI does not load an arbitrary preset
path. To add one:

1. Copy an existing file in `ecomgen/config/presets/` to
   `ecomgen/config/presets/<name>.yaml`.
2. Set the top-level `name` to the exact filename stem.
3. Define every field in the model below.
4. Run the config tests and `ecomgen presets`.

```yaml
name: homeware
categories:
  ceramics:
    price_range: [20, 150]
    cost_ratio_range: [0.30, 0.50]
    variant_options:
      color: [White, Sand]
      size: [Small, Large]
    return_rate: 0.08
    return_reasons:
      damaged: 0.6
      changed_mind: 0.4
    title_words:
      adjectives: [Modern, Handcrafted]
      materials: [Stoneware, Porcelain]
      nouns: [Vase, Bowl]

seasonality: [0.8, 0.8, 0.9, 1.0, 1.1, 1.1, 1.0, 1.0, 1.1, 1.2, 1.5, 1.4]
special_spikes:
  - name: black-friday
    start: "11-24"
    end: "11-30"
    multiplier: 2.2

channel_mix:
  meta:    {weight: 0.30, cac_range: [70, 110]}
  google:  {weight: 0.30, cac_range: [10, 25]}
  organic: {weight: 0.15, cac_range: [0, 3]}
  email:   {weight: 0.15, cac_range: [1, 5]}
  direct:  {weight: 0.10, cac_range: [0, 2]}

repeat_purchase:
  probability: 0.25
  average_days: 90

discount:
  usage_rate: 0.30
  depth_range: [0.10, 0.25]

# Optional; these are the defaults.
inventory:
  reorder_point: 30
  restock_quantity: 120
  lead_time_days: 10
  cover_days: 45
```

Configuration is validated strictly: unknown fields are rejected; category ranges
must be ordered and in bounds; option and category-local title-word lists cannot be
empty, and each category must contribute 12 titles that are unique across the catalog;
seasonality must contain exactly 12 positive values; spike dates must be real
`MM-DD` calendar dates (`02-31` is rejected; `02-29` is allowed and simply never
matches in non-leap years; `start` after `end` wraps across the new year);
CAC ranges must be non-negative and ordered; and the five channel weights must sum
to 1. `weight` is the channel's share of new customers; `cac_range` (EUR) sets what
a paid channel spends per acquired customer and is ignored for `direct` and
`organic`. Set a paid channel's CAC above the contribution its customers earn to
make it unprofitable. Return-reason weights must be positive and are normalized when sampled.
Inventory values are whole numbers: `restock_quantity` and `lead_time_days` at least
1, `reorder_point` and `cover_days` at least 0.

## Shopify CSV

Pass `--shopify-export` to add `products_shopify.csv` independently of
`--format`. It follows Shopify's product CSV import format, with one row per
variant and these columns:

```text
Handle, Title, Body (HTML), Vendor, Type, Tags, Published,
Option1 Name, Option1 Value, Variant SKU, Variant Inventory Tracker,
Variant Inventory Qty, Variant Inventory Policy, Variant Fulfillment Service,
Variant Price, Status
```

- Rows of the same product are contiguous. The product-level fields `Title`,
  `Body (HTML)`, `Vendor`, `Type`, `Tags`, `Published`, and `Status` are filled only
  on the first row of each `Handle` and left blank on its other variant rows.
- `Status` is `active` and `Published` is `TRUE`.
- Option names are title-cased, for example `Color` or `Size`.
- `Variant Inventory Tracker` is `shopify`, so Shopify tracks the imported
  quantity. `Variant Inventory Policy` is `deny` (no overselling), and
  `Variant Fulfillment Service` is `manual`.
- `Variant Inventory Qty` is the remaining stock at the end of the generated
  period, after all simulated orders; sold-out variants import with 0.
- `Variant Price` is the variant's gross, VAT-inclusive EUR **shelf** price: the
  catalog price rounded the way the generated orders price it (`.90` below 100,
  whole units above). A store imported from this file therefore charges what the
  order history shows in the EUR markets. It is not converted to other currencies.

The export is for product import workflows only; it does not import customers,
orders, returns, or marketing data.

### Manual Shopify dev-store import checklist

Automated exporter and validator checks inspect local files only. They do **not**
connect to Shopify or mutate a live store, so a manual import is still required to
verify Shopify's current importer behavior.

Use a new or disposable Shopify development store with no production data:

1. Set the development store's base currency to EUR. The CSV contains gross
   VAT-inclusive EUR list prices, not per-market converted prices.
2. Generate a dedicated export with `--shopify-export`, then run
   `ecomgen validate --path <output>`. Do not continue if validation reports an
   error or if `manifest.json` does not list `products_shopify.csv`.
3. Before upload, inspect the CSV: confirm the expected header, one row per variant,
   contiguous rows for each `Handle`, unique non-empty `Variant SKU` values,
   non-negative inventory, and product-level values only on the first row per handle.
4. In Shopify Admin, go to **Products → Import**, upload `products_shopify.csv`, and
   review Shopify's preview and warning count before starting the import. Abort on
   unexpected column mappings, replacements, or validation warnings.
5. After import, compare Shopify's product and variant counts with `manifest.json`.
   Spot-check products from each option shape present, including a single-variant
   product if the export contains one and at least one multi-variant product. Verify
   title, description, product type/tags, option name and values, SKU, gross price,
   and inventory quantity.
6. Confirm imported products are active and published as intended, inventory is
   tracked by Shopify, fulfillment is manual, and the inventory policy denies sales
   after stock reaches zero.
7. Open products in the storefront preview and confirm text, option selection, price,
   and availability render correctly. If multiple locations exist, verify where
   Shopify assigned the imported quantity.
8. Record the store, import time, source manifest digest, Shopify warnings, and
   spot-check results. Delete the synthetic products afterward or discard the
   development store so the test cannot contaminate another workflow.

Never use a production store for this checklist. A successful local validation is a
prerequisite, not evidence that a Shopify import has occurred.

## Output directory and manifest

Every export is atomic. All files are written and flushed to disk in a temporary
directory next to `--out`, which then replaces `--out` as a whole. An interrupted or
failed run never leaves a half-written or mixed dataset behind.

- A new or empty `--out` directory is always accepted.
- An earlier ecomgen export (a directory with an ecomgen `manifest.json` and only
  the files it lists) is replaced completely. Tables from the earlier run that the
  new run does not write, such as JSON files after switching to `--format csv`,
  are removed.
- Any other non-empty directory is refused with exit status 1, so ecomgen never
  deletes files it did not write.

Windows cannot atomically replace a non-empty directory, so an existing export is
swapped with two renames: the old directory moves to a hidden `.<name>.old-*`
sibling, the new one moves into place, and the old one is deleted. If the process
is killed in the instant between the two renames, `--out` is missing and the
previous export survives in that `.old-*` directory. A hard kill during writing can
leave a `.<name>.tmp-*` sibling, which is safe to delete; `--out` itself is never
partially written.

`manifest.json` describes the export. It contains no timestamps, so a fixed seed
and arguments give an identical manifest:

```json
{
  "generator": "ecomgen",
  "version": "0.1.0",
  "arguments": {
    "preset": "garden-decor",
    "markets": ["de", "at", "fr"],
    "customers": 5000,
    "months": 12,
    "start_date": "2024-01-01",
    "seed": 42,
    "format": "csv",
    "shopify_export": false
  },
  "metadata": {
    "schema_version": 1,
    "pricing_mode": "gross_vat_inclusive",
    "order_window_start": "2024-01-01",
    "order_window_end": "2025-01-01",
    "return_cutoff": "2025-01-31",
    "return_delay_days": {"min": 3, "max": 30}
  },
  "files": {
    "customers.csv": {"rows": 5000, "sha256": "..."},
    "...": {}
  }
}
```

`rows` counts data rows: CSV rows after the header, or the length of a JSON array.
Every file written is listed, including `products_shopify.csv`. The SHA-256 digests
detect edited, truncated, or swapped tables.

The manifest's semantic metadata is the source of truth for downstream checks and BI
queries. The order window is half-open:
`order_window_start <= created_at < order_window_end`. The end is exclusive, so a
12-month run beginning `2024-01-01` contains no order at or after
`2025-01-01T00:00:00Z`. Returns may extend beyond that order window, but never beyond
the inclusive `return_cutoff`, which is 30 days after the exclusive order-window end.
Consumers should read these values from the manifest rather than recomputing them
from CLI arguments.

## Summary output

While the dataset is generated, an interactive terminal shows a progress bar with
the percentage done and the estimated time remaining. The bar is not drawn when
output is redirected to a file, a pipe, or `NUL`.

After a successful export, `ecomgen` prints the destination, row counts, revenue by
market, and item return and order repeat rates. The generated values depend on the
options and seed; the output has this form:

```text
Exported to output
       Generated dataset
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ Table             ┃ Rows ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ products          │   36 │
│ variants          │  102 │
│ customers         │ 5,000│
│ orders            │  ... │
│ order_items       │  ... │
│ returns           │  ... │
│ marketing_spend   │ 5,490│
└───────────────────┴──────┘
Revenue: AT ..., DE ..., FR ...
Return rate: ...%  Repeat rate: ...%
```

If generation had to deviate from the requested behaviour, for example because
stock-outs suppressed orders, each problem is printed after the summary on its own
line starting with `Warning:`. When stock-outs suppress so much demand that the
dataset would be misleading, `ecomgen` prints `Error: ...` instead, writes nothing,
and exits with status 1.

The fixed counts shown are for the quickstart's three-category `garden-decor`
preset, 5,000 customers, and 12-month window beginning in leap year 2024. The
ellipsis values are stochastic outputs and are printed as concrete values when the
command runs.

## Determinism

For the same installed code and dependency versions, preset and market
configuration, CLI options, and `--seed`, generation produces the same records.
`ecomgen` uses one seeded NumPy generator for dataset distributions and derives a
stable per-market seed for each Faker instance. A different seed changes the
dataset. Pin dependencies if byte-for-byte reproducibility must survive dependency
upgrades.

## BI smoke queries

`reports/bi_queries.sql` contains DuckDB smoke queries for revenue, VAT, returns,
acquisition, cohorts, inventory, and manifest-boundary checks. Replace `{DATA_DIR}`
with an export directory and run the setup block followed by each named query.
Currency is always retained in grouping keys; do not sum local-currency revenue or
marketing spend across markets without an explicit FX conversion. The contribution
query returns a value only where local order/spend currency is EUR because product
cost is exported only as `cost_eur`.

`reports/bi_queries_output.md` records the checked DuckDB 1.5.5 results for the
500-customer sample in `examples/sample_output`, generated with the fixed
`garden-decor`, `de,at,fr`, 12-month, 2024-01-01, seed-42 arguments.

## Development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the test suite and linter:

```bash
python -m pytest
python -m ruff check .
```

## Roadmap

- LLM-generated product copy and reviews
- Web sessions
- Parquet/DuckDB export
- Shopify dev-store store-in-a-box

## License

MIT
