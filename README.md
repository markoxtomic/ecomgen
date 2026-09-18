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
--customers INTEGER     Number of customers; may be zero. Default: 5000
--months INTEGER        Number of months; must be at least 1. Default: 12
--start-date DATE       Start of the generation window. Default: 2024-01-01
--seed INTEGER          Random seed. Default: 42
--out PATH              Output directory: new, empty, or an earlier ecomgen
                        export, which is replaced. Default: dataset
--format [csv|json|all] Dataset export format. Default: csv
--shopify-export        Also write products_shopify.csv. Default: disabled
--help                  Show command help.
```

The fixed `2024-01-01` default is intentional: omitting `--start-date` does not make
the generated period depend on the day the command is run.

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
return chronology and refund bounds, and order arithmetic.

```text
ecomgen validate --path PATH
```

`--path` is required and must be readable. It may identify an export directory or
one `.csv`/`.json` table within that directory; all seven tables of the selected
format must be present beside it. A valid dataset exits with status 0. Load or
integrity errors are printed and exit with status 1.

```bash
ecomgen validate --path ./output
```

## Data model

| Table | Key fields | Purpose |
| --- | --- | --- |
| `products` | `id`, `title`, `category`, `description_short`, `price_eur`, `cost_eur`, `markets` | Base catalog and market availability |
| `variants` | `id`, `product_id`, `sku`, option fields, `price_eur`, `inventory` | Sellable product options and remaining stock |
| `customers` | `id`, `market`, identity and location fields, `created_at`, `acquisition_channel` | Locale-aware synthetic customers |
| `orders` | `id`, `customer_id`, `market`, `created_at`, currency and totals, `discount_code`, `is_repeat` | Market-local transactions |
| `order_items` | `id`, `order_id`, `variant_id`, `quantity`, `unit_price` | Order line items |
| `returns` | `id`, `order_id`, `order_item_id`, `reason`, `refund_amount`, `created_at` | Item-level returns and refunds |
| `marketing_spend` | `date`, `market`, `channel`, `spend`, `impressions`, `clicks`, `attributed_orders` | Daily channel performance by market |

The core relationships are:

```text
products 1──* variants 1──* order_items *──1 orders *──1 customers
                                      └──0..1 returns

marketing_spend is keyed by date + market + channel and attributes orders through
the ordering customer's acquisition channel.
```

Each return points to both its order and order item. The validator checks those
foreign keys and confirms that the item belongs to the referenced order.

## How realism is modeled

- **Catalog:** each preset category produces 12 products. Prices and cost ratios come
  from category ranges; products receive one configured option dimension and a
  variant for each value in that dimension. Availability varies across selected
  markets.
- **Customers:** customer counts are allocated by relative market demand. Faker uses
  each market's locale for names and cities. Generated emails use the reserved
  `example.test` domain.
- **Demand:** daily order counts combine market demand weights, the preset's 12
  monthly seasonality multipliers, weekday multipliers, date-range spikes, bounded
  noise, and Poisson sampling.
- **Lifecycle:** an order cannot predate its customer. Repeat orders use the preset's
  repeat probability and recency weighting based on the configured average interval;
  bundled presets target repeat probabilities between 20% and 29%.
- **Baskets and inventory:** basket units are Poisson-distributed and biased toward
  lower-priced variants, so expensive catalogs tend toward smaller baskets. Stock is
  decremented per unit and cannot become negative.
- **Market pricing:** EUR catalog prices are converted with the configured FX rate.
  Prices below 100 use `.90` endings; prices at or above 100 are rounded to whole
  units. Shipping, currency, and VAT are market-specific.
- **Accounting:** money uses `Decimal`. Every order satisfies
  `subtotal - discount + shipping + tax = total`, with monetary values rounded to
  two decimal places.
- **Discounts:** usage and depth follow preset ranges. Applied discounts receive a
  generated `SAVE<n>` code.
- **Returns:** each order item is sampled using its category's return rate and
  weighted reasons. Returns occur 3–30 days after the order and refund the line-item
  value, never more.
- **Marketing:** every day has one row per selected market and each of `direct`,
  `email`, `google`, `meta`, and `organic`. Orders are attributed to the customer's
  acquisition channel. Paid-channel spend is sampled from configured CAC ranges;
  direct and organic spend remain zero. Funnel counts satisfy
  `impressions >= clicks >= attributed_orders`.

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

seasonality: [0.8, 0.8, 0.9, 1.0, 1.1, 1.1, 1.0, 1.0, 1.1, 1.2, 1.5, 1.4]
special_spikes:
  - name: black-friday
    start: "11-24"
    end: "11-30"
    multiplier: 2.2

channel_mix:
  meta:    {weight: 0.30, cac_range: [12, 28]}
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

title_words:
  adjectives: [Modern, Handcrafted]
  materials: [Stoneware, Porcelain]
  nouns: [Vase, Bowl]
```

Configuration is validated strictly: unknown fields are rejected; category ranges
must be ordered and in bounds; option and title-word lists cannot be empty;
seasonality must contain exactly 12 positive values; spike dates must be real
`MM-DD` calendar dates (`02-31` is rejected; `02-29` is allowed and simply never
matches in non-leap years; `start` after `end` wraps across the new year);
CAC ranges must be non-negative and ordered; and the five channel weights must sum
to 1. Return-reason weights must be positive and are normalized when sampled.

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
- `Variant Price` is the variant's gross, VAT-inclusive EUR list price
  (`variants.price_eur`), not a market-converted price.

The export is for product import workflows only; it does not import customers,
orders, returns, or marketing data.

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
  "files": {
    "customers.csv": {"rows": 5000, "sha256": "..."},
    "...": {}
  }
}
```

`rows` counts data rows: CSV rows after the header, or the length of a JSON array.
Every file written is listed, including `products_shopify.csv`. The SHA-256 digests
detect edited, truncated, or swapped tables.

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
