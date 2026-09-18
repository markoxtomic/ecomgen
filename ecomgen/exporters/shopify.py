"""Shopify-compatible product CSV export.

The columns follow Shopify's product CSV import format. Product-level fields are
filled only on the first row of each handle; additional variant rows leave them
blank, as Shopify's documentation requires. Inventory is tracked by Shopify
(``Variant Inventory Tracker = shopify``); a blank tracker would import the
quantity as untracked.
"""

from __future__ import annotations

import csv
import html
from collections import defaultdict
from pathlib import Path

from ecomgen.schemas import Dataset, Variant

from ._io import sync_file

SHOPIFY_HEADERS = (
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Variant SKU",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Status",
)

INVENTORY_TRACKER = "shopify"
INVENTORY_POLICY = "deny"  # Do not sell a variant once its stock reaches zero.
FULFILLMENT_SERVICE = "manual"
STATUS = "active"


def _option_name(name: str) -> str:
    return name.replace("_", " ").title()


def export_shopify(dataset: Dataset, output_dir: str | Path) -> Path:
    """Write one Shopify import row per variant, grouped by product handle.

    ``Variant Price`` is the variant's EUR list price and ``Variant Inventory
    Qty`` its remaining stock at the end of the generated period.
    """

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "products_shopify.csv"
    variants_by_product: defaultdict[str, list[Variant]] = defaultdict(list)
    for variant in dataset.variants:
        variants_by_product[variant.product_id].append(variant)
    products = {product.id: product for product in dataset.products}

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHOPIFY_HEADERS)
        writer.writeheader()
        for product_id, variants in variants_by_product.items():
            product = products[product_id]
            for position, variant in enumerate(variants):
                row = {
                    "Handle": product.id,
                    "Option1 Name": _option_name(variant.option_name),
                    "Option1 Value": variant.option_value,
                    "Variant SKU": variant.sku,
                    "Variant Inventory Tracker": INVENTORY_TRACKER,
                    "Variant Inventory Qty": variant.inventory,
                    "Variant Inventory Policy": INVENTORY_POLICY,
                    "Variant Fulfillment Service": FULFILLMENT_SERVICE,
                    "Variant Price": format(variant.price_eur, ".2f"),
                }
                if position == 0:
                    row |= {
                        "Title": product.title,
                        "Body (HTML)": f"<p>{html.escape(product.description_short)}</p>",
                        "Vendor": "ecomgen",
                        "Type": product.category,
                        "Tags": product.category,
                        "Published": "TRUE",
                        "Status": STATUS,
                    }
                writer.writerow(row)
        sync_file(handle)
    return path
