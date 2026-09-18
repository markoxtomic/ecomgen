# ecomgen correctness audit

**Date:** 2026-09-18 · **Commit:** `3918022` · **Environment:** Windows 11, Python 3.14.7, numpy 2.5.3, Faker 40.39.0, pandas 3.x

**Dataset under test**

```bash
ecomgen generate --preset garden-decor --markets de,at,fr --customers 5000 --months 12 \
  --seed 42 --format all --shopify-export --out ./ds
```

It contains 36 products, 102 variants, 5,000 customers, 4,185 orders, 7,066 order items, 475 returns and 5,490 marketing rows. A second run with all nine markets (`de,at,ch,fr,be,es,it,nl,uk`, same parameters) covers the CHF and GBP paths.

**Method.** The audit is `audit.py`, reproduced in full in the appendix. It is independent of ecomgen: it imports nothing from `ecomgen`, reads the CSVs with pandas (all columns as strings, money parsed into `Decimal`), and reads configured rates straight from the bundled YAML inputs. It uses none of ecomgen's validation code.

## Verdict

The accounting is correct. All 38 hard checks pass on both datasets: referential integrity, cent-exact order arithmetic, per-market VAT, refund bounds, return chronology, non-negative inventory, and FX conversion with the documented rounding. No arithmetic or referential defect was found.

The business model behind the numbers is weaker. Several distributions are mechanical enough that an analyst or an LLM agent will draw wrong or trivial conclusions from them. These are summarised in the findings table below.

## 1. Referential integrity: PASS

| Check | Result |
| --- | --- |
| Primary keys unique (6 tables + marketing `(date, market, channel)`) | pass |
| `variants.product_id`, `orders.customer_id`, `order_items.order_id/variant_id`, `returns.order_id/order_item_id` | 0 orphans each |
| `returns.order_id` equals the order of its `order_item` | pass |
| Every order has ≥ 1 item | pass (0 empty orders) |
| `order.market == customer.market` | pass |
| `order.created_at >= customer.created_at` | pass (0 violations) |
| Items sold only in markets where the product is listed | pass (0 violations) |
| `is_repeat` equals "customer has an earlier order" | pass (0 mismatches) |
| `marketing.attributed_orders` equals orders by (day, market, customer channel) | pass (4,185 = 4,185) |

## 2. Financial reconciliation: PASS (to the cent)

| Check | Result |
| --- | --- |
| `subtotal == Σ qty × unit_price` | 0 mismatches |
| `subtotal − discount + shipping + tax == total` | 0 mismatches |
| All money values have ≤ 2 decimals | pass |
| `discount ≤ subtotal`; discount code present iff discount > 0 | pass |
| `SAVE<n>` code matches the actual discount % | pass (max gap 1 pt, from rounding) |
| Discount usage 27.5% vs configured 0.28; depth 10–25% vs configured [0.10, 0.25] | on target |

## 3. VAT: PASS

Per order, `tax == round_half_up((subtotal − discount + shipping) × vat_rate)` holds for every order in every market.

| Market | Configured | Effective (Σtax / Σtaxable) | Shipping | Currency |
| --- | --- | --- | --- | --- |
| DE | 0.190 | 0.18999 | ok | EUR |
| AT | 0.200 | 0.20000 | ok | EUR |
| FR | 0.200 | 0.20000 | ok | EUR |
| CH (all-markets run) | 0.081 | 0.08100 | ok | CHF |
| UK (all-markets run) | 0.200 | 0.20001 | ok | GBP |
| BE / ES / IT / NL | 0.21 / 0.21 / 0.22 / 0.21 | exact to 4 dp | ok | EUR |

**Observation (realism).** VAT is added on top of the catalogue price, so `.90` prices behave as net prices. EU B2C shops display VAT-inclusive prices, so a real DE order for a "€36.90" item totals €36.90, not €43.91.

## 4. Returns: PASS on the documented rules, with a refund-basis caveat

| Check | Result |
| --- | --- |
| Cumulative refunds per order item ≤ item value | pass (0 over) |
| At most one return per order item | pass |
| Every return dated after its order | pass (lag 3.0–30.0 days) |
| Item return rate vs config: furniture 0.059 vs 0.05, lighting 0.084 vs 0.08, planters 0.058 vs 0.06 | on target |
| Returns dated after the generation window ends | 21 of 475 (Jan 2025) |

**Refund basis.** Every refund equals the full pre-discount, VAT-exclusive line value (`unit_price × quantity`).
- **126 of 475 refunds (27%) exceed what the customer paid net for that line,** because order discounts are ignored.
- **413 of 475 refunds are below what the customer actually paid,** because VAT is not refunded.

So refunds match neither net nor gross paid. `net revenue − refunds` is therefore wrong in both directions, and an agent flagged this independently (see `test_report.md`).

## 5. Inventory: PASS (never negative), but stock is finite and never replenished

- Initial stock is 13,704 units (27–249 per variant). 7,110 units sold and 6,594 remain.
- 27 of 102 variants sell out within the 12 months. The first sells out on 2024-04-22, the median on 2024-08-28.
- Returned units are not restocked.
- **Consequence at scale** (see `test_report.md`, C1): with 50,000 customers over 36 months, every variant sells out by 2024-08-09 and the remaining 29 months have zero orders.

## 6. Seasonality: PASS

| Month | Orders | Curve |
| --- | --- | --- |
| Jan | 207 | 0.55 |
| Feb | 209 | 0.62 |
| Mar | 399 | 0.90 (+ spring spike) |
| Apr | 425 | 1.25 |
| May | 520 | 1.45 |
| Jun | 497 | 1.38 |
| Jul | 426 | 1.22 |
| Aug | 361 | 1.10 |
| Sep | 297 | 0.92 |
| Oct | 259 | 0.72 |
| Nov | 274 | 0.68 |
| Dec | 311 | 0.78 |

Pearson r between orders per day and the preset curve is **0.963** (0.959 on raw monthly counts); the all-markets run gives 0.969. The weekday mix follows the configured multipliers, with Saturday highest (17.5%) and Sunday lowest (11.6%).

## 7. Plausibility and "synthetic in a bad way"

| Metric | Value | Assessment |
| --- | --- | --- |
| Repeat-order share | 25.1% (config p = 0.24) | plausible |
| Buyers with ≥ 2 orders | 24.2% | plausible for DTC |
| Customers who never order | 1,865 / 5,000 (37%) | plausible (sign-ups) |
| AOV gross (EUR) | DE 226.92 · AT 231.13 · FR 290.83 | plausible for garden furniture |
| Item return rate | 5.8–8.4% by category | plausible |
| Gross margin by category | 46–72% | high but possible for DTC |
| CAC (spend / first-time buyers) | meta ≈ €31, google ≈ €28, email ≈ €4, direct/organic €0 | see below |
| ROAS (net rev / spend) | meta 7.5–10, google 9–11, email 61–83 | implausibly high; see below |

**Findings (realism, not arithmetic):**

1. **Spend is derived from orders, not the other way round.** `spend = CAC × attributed_orders`, and impressions and clicks are back-solved from orders.
   - 2,934 of 5,490 marketing rows (53%) have 0 orders, and **all** of them also have 0 spend and 0 impressions.
   - Paid campaigns therefore go dark on 35–43% of days, and ROAS is fixed by construction.
   - No channel can ever be unprofitable: ROAS is ≥ 7 on every paid channel against a break-even of about 2.1.
2. **Repeat orders are charged acquisition cost.** The 1,050 repeat orders count in `attributed_orders` and generate paid spend at CAC rates. The measured "CAC" (≈ €31 for meta) therefore sits at the top of the configured range [16, 31] rather than its middle.
3. **Direct and organic have impressions and clicks** (CTR up to 18%), although neither channel has ad impressions.
4. **Repeat interval ignores the configuration.** Days between repeat orders have a median of 35, a mean of 52 and a p10 of 5.6, against `average_days: 105`. The recency weight `exp(−days/avg)` favours whoever bought *most recently*, so the configured value is not the average interval its name implies.
5. **Cohort retention decays mechanically.** Month-1 retention falls from 27% (January cohort) to 4–7% (August onward). A fixed repeat probability per order is spread over a growing buyer pool, so later cohorts look like a retention collapse that nobody configured.
6. **Order time of day is uniform.** Each UTC hour gets 3.5–4.7% of orders, so 03:00 is as busy as 20:00.
7. **Market AOV differences come from random assortment.** Each product is listed in a random subset of markets. The FR assortment happens to average €197 against DE's €152, so FR (weight 0.75) earns *more* net revenue than DE (weight 1.00). An analyst asking "why is FR so strong?" will find no business reason.
8. **Catalogue text.**
   - 13 of 36 products share a title with another product.
   - Title nouns are drawn from the preset-wide pool, ignoring category: "Timeless Acacia Planter" and "Minimal Acacia Lantern" are in `garden-furniture`.
   - Each product uses only one option dimension, even where the category defines two (e.g. fashion size *and* colour).
9. **Behaviour is identical across markets.** Repeat rate, discount share and orders per buyer vary only by noise: every market uses the same parameters and only demand weight differs.

## 8. Currency conversion and price rounding: PASS

- `unit_price` equals `price_eur × fx_rate_from_eur`, rounded to a `.90` ending below 100 or to a whole unit at or above 100. 0 mismatches across all 9 markets, including CHF (0.95) and GBP (0.86).
- Every price below 100 ends in `.90`; every price at or above 100 is a whole unit.
- Rounding drift against the exact FX price is −1.54% to +1.62%.
- **Gap:** `marketing_spend` has no `currency` column, and no FX table is exported. CH spend is in CHF and UK spend in GBP, so summing spend across markets, or computing COGS (`cost_eur`) against GBP/CHF revenue, silently mixes currencies. The rates exist only in the package's YAML.

## Appendix A: raw audit output (5000 / 12 / de,at,fr)

```text
rows: products=36 variants=102 customers=5000 orders=4185 items=7066 returns=475 marketing=5490
[PASS] PK unique: products.id  
[PASS] PK unique: variants.id  
[PASS] PK unique: customers.id  
[PASS] PK unique: orders.id  
[PASS] PK unique: order_items.id  
[PASS] PK unique: returns.id  
[PASS] PK unique: marketing (date,market,channel)  
[PASS] FK no orphans: variants.product_id  orphans=0
[PASS] FK no orphans: orders.customer_id  orphans=0
[PASS] FK no orphans: order_items.order_id  orphans=0
[PASS] FK no orphans: order_items.variant_id  orphans=0
[PASS] FK no orphans: returns.order_id  orphans=0
[PASS] FK no orphans: returns.order_item_id  orphans=0
[PASS] returns.order_id == order_items.order_id  
[PASS] every order has >=1 item  empty orders=0
[PASS] order.market == customer.market  
[PASS] order.created_at >= customer.created_at  violations=0
[PASS] product.markets subset of generated markets  
[PASS] item sold only in markets where product is listed  violations=0
[PASS] is_repeat == (customer has an earlier order)  mismatches=0
[PASS] marketing.attributed_orders == orders by (day, market, customer channel)  sum attributed=4185 orders=4185
[PASS] subtotal == sum(qty * unit_price) to the cent  bad=0
[PASS] subtotal - discount + shipping + tax == total to the cent  bad=0
[PASS] all money columns have <= 2 decimal places  
[PASS] discount <= subtotal  
[PASS] discount_code present iff discount > 0  mismatch=0
[PASS] SAVE<n> code matches actual discount % (±1pt)  max gap=1.00pt
[INFO] discount usage  27.5% of orders (config usage_rate 0.28); depth 10-25% (config [0.1, 0.25])
[PASS] VAT at: tax == round_half_up((subtotal-discount+shipping)*0.2)  bad=0; effective rate=0.20000 vs configured 0.200; shipping ok=True; currency ok=True
[PASS] VAT de: tax == round_half_up((subtotal-discount+shipping)*0.19)  bad=0; effective rate=0.18999 vs configured 0.190; shipping ok=True; currency ok=True
[PASS] VAT fr: tax == round_half_up((subtotal-discount+shipping)*0.2)  bad=0; effective rate=0.20000 vs configured 0.200; shipping ok=True; currency ok=True
[INFO] VAT basis  VAT is added on top of catalogue prices (prices behave as net); EU B2C storefronts normally display gross (VAT-inclusive) prices ending in .90/.99
[PASS] cumulative refunds per order_item <= item value  over=0
[PASS] at most one return per order_item  
[PASS] every return dated strictly after its order  lag days min=3.0 max=30.0
[INFO] returns after generation window end  21 of 475 returns dated >= 2025-01-01
[INFO] refund basis  refund == pre-discount net line value for all returns: True; refunds exceeding the discounted net amount actually paid: 126/475; refunds below the VAT-inclusive amount paid: 413/475
[INFO] item return rate by category (actual vs config)  garden-furniture: 0.059 vs 0.05; outdoor-lighting: 0.084 vs 0.08; planters: 0.058 vs 0.06
[INFO] return reasons  {('garden-furniture', 'damaged'): 0.48, ('garden-furniture', 'not_as_expected'): 0.27, ('garden-furniture', 'too_large'): 0.17, ('garden-furniture', 'changed_mind'): 0.08, ('outdoor-lighting', 'defective'): 0.36, ('outdoor-lighting', 'damaged'): 0.32, ('outdoor-lighting', 'not_as_expected'): 0.19, ('outdoor-lighting', 'changed_mind'): 0.13, ('planters', 'damaged'): 0.43, ('planters', 'not_as_expec
[INFO] return rate by market (items)  {'at': 0.075, 'de': 0.0656, 'fr': 0.0673}
[PASS] inventory never negative (final)  min=0
[INFO] inventory  initial units=13704 sold=7110 remaining=6594; variants sold out=27/102; initial per-variant range 27-249
[INFO] sell-out dates  first=2024-04-22 median=2024-08-28 last=2024-12-31
[INFO] returns restocked?  no - returned units are not added back to variants.inventory
[INFO] monthly orders  2024-01:207, 2024-02:209, 2024-03:399, 2024-04:425, 2024-05:520, 2024-06:497, 2024-07:426, 2024-08:361, 2024-09:297, 2024-10:259, 2024-11:274, 2024-12:311
[PASS] orders/day correlate with preset seasonality (r >= 0.7)  pearson r (orders/day vs curve)=0.963; raw monthly counts r=0.959
[INFO] first-time orders vs customers created per month  2024-01:159/424, 2024-02:150/413, 2024-03:293/450, 2024-04:320/399, 2024-05:376/424, 2024-06:388/397, 2024-07:317/427, 2024-08:269/433, 2024-09:227/406, 2024-10:191/400, 2024-11:205/409, 2024-12:240/418
[INFO] AOV by market (gross, local ccy)  {'at': {'orders': 403, 'aov': 231.13, 'median': 184.44, 'max': 1314.84, 'currency': 'EUR'}, 'de': {'orders': 2091, 'aov': 226.92, 'median': 159.08, 'max': 1783.45, 'currency': 'EUR'}, 'fr': {'orders': 1691, 'aov': 290.83, 'median': 198.54, 'max': 1463.76, 'currency': 'EUR'}}
[INFO] orders per customer market share  {'de': 0.5, 'fr': 0.404, 'at': 0.096}
[INFO] repeat  repeat-order share=0.251 (config p=0.24); customers with >=2 orders=760/3135 buyers (0.242); customers who never ordered=1865/5000; max orders per customer=9
[INFO] days between repeat orders  median=35.2 mean=51.6 p10=5.6 (config average_days=105)
[INFO] customer signup -> first order lag (days)  median=35.60 p90=119.2
[INFO] basket units  {1: 2059, 2: 1491, 3: 498, 4: 112, 5: 23, 6: 2}
[INFO] lines per order  {1: 2083, 2: 1482, 3: 488, 4: 107, 5: 23, 6: 2}
[INFO] order hour-of-day share (UTC)  min=0.035 max=0.047 03:00=0.039 20:00=0.042 (uniform=0.042)
[INFO] weekday share (Mon=0)  {0: 0.124, 1: 0.139, 2: 0.135, 3: 0.144, 4: 0.167, 5: 0.175, 6: 0.116}
[INFO] unit_price endings  {'.90': 4194, '.00': 2872}
[INFO] duplicate product titles  13 products share a title with another (29 unique of 36)
[INFO] product option dimensions per product  {1: 36}
[INFO] email uniqueness  True; domain={'example.test'}
[INFO] gross margin range by category  {'garden-furniture': {'min': 0.46, 'max': 0.573}, 'outdoor-lighting': {'min': 0.526, 'max': 0.677}, 'planters': {'min': 0.565, 'max': 0.716}}
[INFO] CAC (spend / first-time buyers) by market,channel  {('at', 'direct'): 0.0, ('at', 'email'): 3.97, ('at', 'google'): 27.41, ('at', 'meta'): 29.51, ('at', 'organic'): 0.0, ('de', 'direct'): 0.0, ('de', 'email'): 4.3, ('de', 'google'): 27.55, ('de', 'meta'): 31.67, ('de', 'organic'): 0.0, ('fr', 'direct'): 0.0, ('fr', 'email'): 4.06, ('fr', 'google'): 28.57, ('fr', 'meta'): 30.84, ('fr', 'organic'): 0.0}
[INFO] ROAS (net revenue of channel-acquired customers / spend)  {('at', 'email'): 70.83, ('at', 'google'): 9.95, ('at', 'meta'): 8.42, ('de', 'email'): 61.47, ('de', 'google'): 9.19, ('de', 'meta'): 7.5, ('fr', 'email'): 83.02, ('fr', 'google'): 10.98, ('fr', 'meta'): 10.01}
[INFO] paid spend per attributed order  min=1.00 max=30.99 (config cac ranges: meta=[16, 31], google=[14, 28], organic=[0, 3], email=[1, 5], direct=[0, 2])
[INFO] marketing rows with 0 attributed orders  2934/5490; of those with spend>0: 0, impressions>0: 0
[INFO] paid channels with spend>0 on every day?  {'direct': '0.00', 'email': '0.35', 'google': '0.57', 'meta': '0.61', 'organic': '0.00'}
[INFO] repeat orders attributed to acquisition channel & charged CAC  1050 repeat orders are counted in attributed_orders and generate paid spend for meta/google/email (spend = CAC * attributed_orders)
[INFO] CTR distribution  min=0.0080 max=0.1795
[INFO] customer acquisition channel mix  {'meta': 0.303, 'google': 0.282, 'organic': 0.175, 'direct': 0.122, 'email': 0.118} vs config {'meta': 0.3, 'google': 0.28, 'organic': 0.18, 'email': 0.12, 'direct': 0.12}
[INFO] customer created_at spread  2024-01-01 00:06:56.421351+00:00 .. 2024-12-31 23:47:53.330456+00:00
[PASS] unit_price == documented FX conversion + .90 / whole-unit rounding  mismatches=0
[PASS] prices < 100 end in .90  
[PASS] prices >= 100 are whole units  
[INFO] rounding drift vs exact FX price  min=-0.641% max=1.578%
[INFO] marketing_spend currency column present  False
FAILS: 0
```

## Appendix B: audit code

Run with:

```bash
python audit.py <dataset_dir> ecomgen/config/presets/garden-decor.yaml ecomgen/config/markets.yaml 2024-01-01 12
```

```python
"""Independent correctness audit of an ecomgen CSV export.

Deliberately imports nothing from ecomgen. Configured rates are read straight from
the bundled YAML (the *inputs*), everything else is recomputed from the CSVs.

Usage: python audit.py <dataset_dir> <preset_yaml> <markets_yaml> <start_date> <months>
"""

import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

D = Decimal
CENT = D("0.01")
ds, preset_path, markets_path, start, months = sys.argv[1:6]
ds = Path(ds)
months = int(months)
preset = yaml.safe_load(open(preset_path, encoding="utf-8"))
markets_cfg = yaml.safe_load(open(markets_path, encoding="utf-8"))

MONEY = {"price_eur", "cost_eur", "subtotal", "discount", "shipping", "tax", "total",
         "unit_price", "refund_amount", "spend"}


def load(name):
    df = pd.read_csv(ds / f"{name}.csv", dtype=str, keep_default_na=False)
    for col in df.columns:
        if col in MONEY:
            df[col + "_d"] = df[col].map(D)
            df[col] = df[col].astype(float)
        elif col in {"quantity", "inventory", "impressions", "clicks", "attributed_orders"}:
            df[col] = df[col].astype(int)
        elif col == "created_at":
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
        elif col == "date":
            df[col] = pd.to_datetime(df[col])
    return df


P, V, C, O, I, R, M = (load(t) for t in
                       ["products", "variants", "customers", "orders", "order_items",
                        "returns", "marketing_spend"])
O["is_repeat"] = O["is_repeat"].map({"True": True, "False": False, "true": True,
                                     "false": False})
P["markets_list"] = P["markets"].map(json.loads)
findings = []  # (check, status, detail)


def check(name, ok, detail=""):
    findings.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def info(name, detail):
    findings.append((name, "INFO", detail))
    print(f"[INFO] {name}  {detail}")


print(f"rows: products={len(P)} variants={len(V)} customers={len(C)} orders={len(O)} "
      f"items={len(I)} returns={len(R)} marketing={len(M)}")

# ---------------------------------------------------------------- 1. referential integrity
for name, df in [("products", P), ("variants", V), ("customers", C), ("orders", O),
                 ("order_items", I), ("returns", R)]:
    check(f"PK unique: {name}.id", df["id"].is_unique)
check("PK unique: marketing (date,market,channel)",
      not M.duplicated(["date", "market", "channel"]).any())
fk = [("variants.product_id", V.product_id, P.id), ("orders.customer_id", O.customer_id, C.id),
      ("order_items.order_id", I.order_id, O.id), ("order_items.variant_id", I.variant_id, V.id),
      ("returns.order_id", R.order_id, O.id), ("returns.order_item_id", R.order_item_id, I.id)]
for name, child, parent in fk:
    orphans = int((~child.isin(parent)).sum())
    check(f"FK no orphans: {name}", orphans == 0, f"orphans={orphans}")
ri = R.merge(I[["id", "order_id"]], left_on="order_item_id", right_on="id",
             suffixes=("", "_item"))
check("returns.order_id == order_items.order_id", (ri.order_id == ri.order_id_item).all())
check("every order has >=1 item", O.id.isin(I.order_id).all(),
      f"empty orders={int((~O.id.isin(I.order_id)).sum())}")
oc = O.merge(C[["id", "market", "created_at"]], left_on="customer_id", right_on="id",
             suffixes=("", "_cust"))
check("order.market == customer.market", (oc.market == oc.market_cust).all())
check("order.created_at >= customer.created_at", (oc.created_at >= oc.created_at_cust).all(),
      f"violations={int((oc.created_at < oc.created_at_cust).sum())}")
selected = set(C.market) | set(O.market) | set(M.market)
check("product.markets subset of generated markets",
      P.markets_list.map(lambda ms: set(ms) <= selected).all())
iv = I.merge(V[["id", "product_id"]], left_on="variant_id", right_on="id", suffixes=("", "_v")) \
      .merge(P[["id", "markets_list", "category"]], left_on="product_id", right_on="id",
             suffixes=("", "_p")) \
      .merge(O[["id", "market", "created_at", "subtotal_d", "discount_d", "currency"]],
             left_on="order_id", right_on="id", suffixes=("", "_o"))
not_avail = int((~iv.apply(lambda r: r.market in r.markets_list, axis=1)).sum())
check("item sold only in markets where product is listed", not_avail == 0,
      f"violations={not_avail}")
# is_repeat consistent with order history
Os = O.sort_values(["customer_id", "created_at"])
Os["nth"] = Os.groupby("customer_id").cumcount()
check("is_repeat == (customer has an earlier order)", (Os.is_repeat == (Os.nth > 0)).all(),
      f"mismatches={int((Os.is_repeat != (Os.nth > 0)).sum())}")
# marketing attribution reconciles with orders
oc["date"] = oc.created_at.dt.tz_localize(None).dt.normalize()
oc2 = oc.merge(C[["id", "acquisition_channel"]], left_on="customer_id", right_on="id",
               suffixes=("", "_c2"))
att = oc2.groupby(["date", "market", "acquisition_channel"]).size().rename("n").reset_index()
mm = M.merge(att, left_on=["date", "market", "channel"],
             right_on=["date", "market", "acquisition_channel"], how="left").fillna({"n": 0})
check("marketing.attributed_orders == orders by (day, market, customer channel)",
      (mm.attributed_orders == mm.n).all() and int(mm.n.sum()) == len(O),
      f"sum attributed={int(M.attributed_orders.sum())} orders={len(O)}")

# ---------------------------------------------------------------- 2. financial reconciliation
line = I.assign(v=I.unit_price_d * I.quantity).groupby("order_id")["v"].sum()
Oc = O.set_index("id")
sub_bad = int((Oc.subtotal_d != line.reindex(Oc.index)).sum())
check("subtotal == sum(qty * unit_price) to the cent", sub_bad == 0, f"bad={sub_bad}")
recon = Oc.subtotal_d - Oc.discount_d + Oc.shipping_d + Oc.tax_d
tot_bad = int((recon != Oc.total_d).sum())
check("subtotal - discount + shipping + tax == total to the cent", tot_bad == 0,
      f"bad={tot_bad}")
check("all money columns have <= 2 decimal places",
      all(df[c].map(lambda x: -x.as_tuple().exponent <= 2).all()
          for df in (P, V, O, I, R, M) for c in df.columns if c.endswith("_d")))
check("discount <= subtotal", (O.discount_d <= O.subtotal_d).all())
has_code = O.discount_code != ""
check("discount_code present iff discount > 0", ((O.discount > 0) == has_code).all(),
      f"mismatch={int(((O.discount > 0) != has_code).sum())}")
pct = (O[has_code].discount / O[has_code].subtotal * 100).round()
code_pct = O[has_code].discount_code.str.removeprefix("SAVE").astype(int)
check("SAVE<n> code matches actual discount % (±1pt)", ((pct - code_pct).abs() <= 1).all(),
      f"max gap={float((pct - code_pct).abs().max()):.2f}pt")
info("discount usage", f"{has_code.mean():.1%} of orders (config usage_rate "
     f"{preset['discount']['usage_rate']}); depth {pct.min():.0f}-{pct.max():.0f}% "
     f"(config {preset['discount']['depth_range']})")

# ---------------------------------------------------------------- 3. VAT
for mkt, g in O.groupby("market"):
    rate = D(str(markets_cfg[mkt]["vat_rate"]))
    taxable = g.subtotal_d - g.discount_d + g.shipping_d
    expected = taxable.map(lambda x: (x * rate).quantize(CENT, rounding=ROUND_HALF_UP))
    bad = int((expected != g.tax_d).sum())
    eff = float(g.tax_d.sum() / taxable.sum())
    ship_ok = (g.shipping_d == D(str(markets_cfg[mkt]["shipping_cost"])).quantize(CENT)).all()
    cur_ok = (g.currency == markets_cfg[mkt]["currency"]).all()
    check(f"VAT {mkt}: tax == round_half_up((subtotal-discount+shipping)*{rate})", bad == 0,
          f"bad={bad}; effective rate={eff:.5f} vs configured {float(rate):.3f}; "
          f"shipping ok={ship_ok}; currency ok={cur_ok}")
# Price display convention: EU B2C list prices are VAT-inclusive; ecomgen adds VAT on top.
info("VAT basis", "VAT is added on top of catalogue prices (prices behave as net); EU B2C "
     "storefronts normally display gross (VAT-inclusive) prices ending in .90/.99")

# ---------------------------------------------------------------- 4. returns
item_val = I.set_index("id").pipe(lambda x: x.unit_price_d * x.quantity)
cum = R.groupby("order_item_id")["refund_amount_d"].sum()
over = int((cum > item_val.reindex(cum.index)).sum())
check("cumulative refunds per order_item <= item value", over == 0, f"over={over}")
check("at most one return per order_item", R.order_item_id.is_unique)
ro = R.merge(O[["id", "created_at"]], left_on="order_id", right_on="id", suffixes=("", "_o"))
lag = (ro.created_at - ro.created_at_o).dt.total_seconds() / 86400
check("every return dated strictly after its order", (lag > 0).all(),
      f"lag days min={lag.min():.1f} max={lag.max():.1f}")
window_end = pd.Timestamp(start, tz="UTC") + pd.DateOffset(months=months)
after = int((R.created_at >= window_end).sum())
info("returns after generation window end", f"{after} of {len(R)} returns dated >= {window_end.date()}")
# refund vs what the customer actually paid for that line (discount pro-rated, VAT added)
rp = R.merge(iv[["id", "unit_price_d", "quantity", "subtotal_d", "discount_d", "market"]],
             left_on="order_item_id", right_on="id", suffixes=("", "_i"))
rp["line"] = rp.unit_price_d * rp.quantity
rp["paid_net"] = rp.apply(lambda r: r.line * (1 - r.discount_d / r.subtotal_d), axis=1)
rp["paid_gross"] = rp.apply(lambda r: r.paid_net * (1 + D(str(markets_cfg[r.market]["vat_rate"]))),
                            axis=1)
over_net = int((rp.refund_amount_d > rp.paid_net.map(lambda x: x.quantize(CENT))).sum())
info("refund basis", f"refund == pre-discount net line value for all returns: "
     f"{bool((rp.refund_amount_d == rp.line).all())}; refunds exceeding the discounted net amount "
     f"actually paid: {over_net}/{len(rp)}; refunds below the VAT-inclusive amount paid: "
     f"{int((rp.refund_amount_d < rp.paid_gross).sum())}/{len(rp)}")
cat_rr = iv.assign(returned=iv.id.isin(R.order_item_id)).groupby("category")["returned"].mean()
cfg_rr = {k: v["return_rate"] for k, v in preset["categories"].items()}
info("item return rate by category (actual vs config)",
     "; ".join(f"{k}: {cat_rr[k]:.3f} vs {cfg_rr[k]}" for k in cat_rr.index))
reasons = R.merge(iv[["id", "category"]], left_on="order_item_id", right_on="id") \
    .groupby("category")["reason"].value_counts(normalize=True).round(2)
info("return reasons", reasons.to_dict().__repr__()[:400])
info("return rate by market (items)",
     iv.assign(r=iv.id.isin(R.order_item_id)).groupby("market")["r"].mean().round(4).to_dict())

# ---------------------------------------------------------------- 5. inventory
check("inventory never negative (final)", (V.inventory >= 0).all(), f"min={V.inventory.min()}")
sold = I.groupby("variant_id")["quantity"].sum().reindex(V.id).fillna(0).astype(int)
initial = V.set_index("id").inventory + sold
info("inventory", f"initial units={int(initial.sum())} sold={int(sold.sum())} "
     f"remaining={int(V.inventory.sum())}; variants sold out={int((V.inventory == 0).sum())}/"
     f"{len(V)}; initial per-variant range {initial.min()}-{initial.max()}")
so = I.merge(O[["id", "created_at"]], left_on="order_id", right_on="id", suffixes=("", "_o"))
zero_ids = V.id[V.inventory == 0]
sellout = so[so.variant_id.isin(zero_ids)].groupby("variant_id")["created_at"].max()
if len(sellout):
    info("sell-out dates", f"first={sellout.min().date()} median={sellout.median().date()} "
         f"last={sellout.max().date()}")
info("returns restocked?", "no - returned units are not added back to variants.inventory")

# ---------------------------------------------------------------- 6. seasonality
O["month"] = O.created_at.dt.tz_localize(None).dt.to_period("M")
monthly = O.groupby("month").size()
days = pd.Series({p: p.days_in_month for p in monthly.index})
season = pd.Series({p: preset["seasonality"][p.month - 1] for p in monthly.index})
per_day = monthly / days
r_raw = np.corrcoef(monthly.values, season.values)[0, 1]
r_pd = np.corrcoef(per_day.values, season.values)[0, 1]
info("monthly orders", ", ".join(f"{p}:{n}" for p, n in monthly.items()))
check("orders/day correlate with preset seasonality (r >= 0.7)", r_pd >= 0.7,
      f"pearson r (orders/day vs curve)={r_pd:.3f}; raw monthly counts r={r_raw:.3f}")
# first months capped by the customer pool? count customers available vs first orders
first = O[~O.is_repeat].groupby("month").size()
created = C.assign(m=C.created_at.dt.tz_localize(None).dt.to_period("M")).groupby("m").size()
info("first-time orders vs customers created per month",
     ", ".join(f"{p}:{first.get(p, 0)}/{created.get(p, 0)}" for p in monthly.index))

# ---------------------------------------------------------------- 7. plausibility
aov = O.groupby("market").agg(orders=("id", "size"), aov=("total", "mean"),
                              median=("total", "median"), max=("total", "max"),
                              currency=("currency", "first"))
info("AOV by market (gross, local ccy)", aov.round(2).to_dict("index"))
info("orders per customer market share",
     {m: round(n / len(O), 3) for m, n in O.market.value_counts().items()})
buyers = O.groupby("customer_id").size()
info("repeat", f"repeat-order share={O.is_repeat.mean():.3f} (config p="
     f"{preset['repeat_purchase']['probability']}); customers with >=2 orders="
     f"{(buyers >= 2).sum()}/{len(buyers)} buyers ({(buyers >= 2).mean():.3f}); customers who never "
     f"ordered={len(C) - len(buyers)}/{len(C)}; max orders per customer={buyers.max()}")
gap = Os.groupby("customer_id")["created_at"].diff().dt.total_seconds().dropna() / 86400
info("days between repeat orders",
     f"median={gap.median():.1f} mean={gap.mean():.1f} p10={gap.quantile(.1):.1f} "
     f"(config average_days={preset['repeat_purchase']['average_days']})")
lag0 = oc.merge(Os[Os.nth == 0][["id"]], on="id")
first_lag = (lag0.created_at - lag0.created_at_cust).dt.total_seconds() / 86400
info("customer signup -> first order lag (days)",
     f"median={first_lag.median():.2f} p90={first_lag.quantile(.9):.1f}")
info("basket units", I.groupby("order_id")["quantity"].sum().value_counts().sort_index().to_dict())
info("lines per order", I.groupby("order_id").size().value_counts().sort_index().to_dict())
hours = O.created_at.dt.hour.value_counts(normalize=True).sort_index()
info("order hour-of-day share (UTC)", f"min={hours.min():.3f} max={hours.max():.3f} "
     f"03:00={hours.get(3, 0):.3f} 20:00={hours.get(20, 0):.3f} (uniform=0.042)")
wd = O.created_at.dt.dayofweek.value_counts(normalize=True).sort_index().round(3).to_dict()
info("weekday share (Mon=0)", wd)
cents = I.unit_price_d.map(lambda x: str(x)[-3:]).value_counts().to_dict()
info("unit_price endings", cents)
info("duplicate product titles",
     f"{int(P.title.duplicated(keep=False).sum())} products share a title with another "
     f"({P.title.nunique()} unique of {len(P)})")
info("product option dimensions per product", V.groupby("product_id")["option_name"].nunique()
     .value_counts().to_dict())
info("email uniqueness", f"{C.email.is_unique}; domain={set(C.email.str.split('@').str[1])}")
margin = P.assign(m=1 - P.cost_eur / P.price_eur).groupby("category")["m"].agg(["min", "max"])
info("gross margin range by category", margin.round(3).to_dict("index"))

# marketing / CAC
Mp = M[M.channel.isin(["meta", "google", "email"])]
new_cust = O[~O.is_repeat].merge(C[["id", "acquisition_channel"]], left_on="customer_id",
                                 right_on="id").groupby(["market", "acquisition_channel"]).size()
spend = M.groupby(["market", "channel"])["spend"].sum()
rev_net = O.merge(C[["id", "acquisition_channel"]], left_on="customer_id", right_on="id",
                  suffixes=("", "_c")).assign(net=lambda d: d.subtotal - d.discount) \
    .groupby(["market", "acquisition_channel"])["net"].sum()
cac = (spend / new_cust.rename_axis(["market", "channel"])).round(2)
roas = (rev_net.rename_axis(["market", "channel"]) / spend).round(2)
info("CAC (spend / first-time buyers) by market,channel", cac.dropna().to_dict())
info("ROAS (net revenue of channel-acquired customers / spend)",
     roas.replace(np.inf, np.nan).dropna().to_dict())
cpo = (Mp.spend / Mp.attributed_orders).dropna()
info("paid spend per attributed order", f"min={cpo.min():.2f} max={cpo.max():.2f} "
     f"(config cac ranges: " + ", ".join(f"{k}={v['cac_range']}" for k, v in
                                          preset["channel_mix"].items()) + ")")
zero_days = M[M.attributed_orders == 0]
info("marketing rows with 0 attributed orders",
     f"{len(zero_days)}/{len(M)}; of those with spend>0: {(zero_days.spend > 0).sum()}, "
     f"impressions>0: {(zero_days.impressions > 0).sum()}")
info("paid channels with spend>0 on every day?", {ch: f"{(g.spend > 0).mean():.2f}" for ch, g in
                                                   M.groupby("channel")})
info("repeat orders attributed to acquisition channel & charged CAC",
     f"{int(O.is_repeat.sum())} repeat orders are counted in attributed_orders and generate paid "
     "spend for meta/google/email (spend = CAC * attributed_orders)")
ctr = (M.clicks / M.impressions).replace([np.inf], np.nan).dropna()
info("CTR distribution", f"min={ctr.min():.4f} max={ctr.max():.4f}")
cust_ch = C.acquisition_channel.value_counts(normalize=True).round(3).to_dict()
info("customer acquisition channel mix", f"{cust_ch} vs config "
     f"{ {k: v['weight'] for k, v in preset['channel_mix'].items()} }")
info("customer created_at spread", f"{C.created_at.min()} .. {C.created_at.max()}")

# ---------------------------------------------------------------- 8. currency & rounding
def local_price(eur, fx):
    conv = D(eur) * D(str(fx))
    if conv < 100:
        r = (conv + D("0.10")).quantize(D(1), rounding=ROUND_HALF_UP) - D("0.10")
        return max(D("0.90"), r).quantize(CENT)
    return conv.quantize(D(1), rounding=ROUND_HALF_UP).quantize(CENT)


ivp = iv.merge(V[["id", "price_eur_d"]], left_on="variant_id", right_on="id",
               suffixes=("", "_var"))
exp = ivp.apply(lambda r: local_price(r.price_eur_d, markets_cfg[r.market]["fx_rate_from_eur"]),
                axis=1)
check("unit_price == documented FX conversion + .90 / whole-unit rounding",
      (exp == ivp.unit_price_d).all(), f"mismatches={int((exp != ivp.unit_price_d).sum())}")
lt100 = ivp[ivp.unit_price_d < 100]
ge100 = ivp[ivp.unit_price_d >= 100]
check("prices < 100 end in .90", lt100.unit_price_d.map(lambda x: str(x).endswith(".90")).all())
check("prices >= 100 are whole units", ge100.unit_price_d.map(lambda x: x == x.to_integral()).all())
drift = (ivp.unit_price_d.astype(float) / (ivp.price_eur_d.astype(float) *
         ivp.market.map(lambda m: markets_cfg[m]["fx_rate_from_eur"])) - 1)
info("rounding drift vs exact FX price", f"min={drift.min():.3%} max={drift.max():.3%}")
info("marketing_spend currency column present", "currency" in M.columns)

pd.DataFrame(findings, columns=["check", "status", "detail"]).to_csv(
    ds.parent / f"audit_{ds.name}.csv", index=False)
print("FAILS:", sum(1 for f in findings if f[1] == "FAIL"))
```
