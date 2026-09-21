"""Report issue m4 (remainder): Shopify prices must match what customers pay.

The export carried the raw catalog price (e.g. 588.98) while orders in the EUR
markets charged the rounded list price (589.00), so a store imported from the CSV
priced every product differently from the order history shipped beside it.
"""

from __future__ import annotations

import csv
from decimal import Decimal

from ecomgen.exporters.shopify import export_shopify
from ecomgen.pipeline import generate_dataset
from ecomgen.pricing import round_list_price


def _shopify_rows(directory):
    with (directory / "products_shopify.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_variant_price_is_the_rounded_list_price(tmp_path) -> None:
    dataset = generate_dataset(markets=("de",), customers=200, months=2, seed=11)
    export_shopify(dataset, tmp_path)
    prices = {variant.id: variant.price_eur for variant in dataset.variants}
    by_sku = {variant.sku: variant.id for variant in dataset.variants}

    for row in _shopify_rows(tmp_path):
        expected = round_list_price(prices[by_sku[row["Variant SKU"]]])
        assert Decimal(row["Variant Price"]) == expected, row["Variant SKU"]


def test_variant_price_equals_what_eur_customers_are_charged(tmp_path) -> None:
    dataset = generate_dataset(markets=("de",), customers=600, months=3, seed=5)
    export_shopify(dataset, tmp_path)
    charged = {item.variant_id: item.unit_price for item in dataset.order_items}
    by_sku = {variant.sku: variant.id for variant in dataset.variants}

    compared = 0
    for row in _shopify_rows(tmp_path):
        variant_id = by_sku[row["Variant SKU"]]
        if variant_id not in charged:
            continue
        compared += 1
        assert Decimal(row["Variant Price"]) == charged[variant_id], row["Variant SKU"]
    assert compared > 20, "too few sold variants to be a meaningful check"
