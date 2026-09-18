"""Shopify-compatible product CSV export."""

from __future__ import annotations

import csv
import html
from pathlib import Path

from ecomgen.schemas import Dataset

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
    "Variant Price",
    "Variant Inventory Qty",
)


def export_shopify(dataset: Dataset, output_dir: str | Path) -> Path:
    """Write one Shopify import row per variant using neutral EUR prices."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "products_shopify.csv"
    products = {product.id: product for product in dataset.products}

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHOPIFY_HEADERS)
        writer.writeheader()
        for variant in dataset.variants:
            product = products[variant.product_id]
            writer.writerow(
                {
                    "Handle": product.id,
                    "Title": product.title,
                    "Body (HTML)": f"<p>{html.escape(product.description_short)}</p>",
                    "Vendor": "ecomgen",
                    "Type": product.category,
                    "Tags": product.category,
                    "Published": "TRUE",
                    "Option1 Name": variant.option_name,
                    "Option1 Value": variant.option_value,
                    "Variant SKU": variant.sku,
                    "Variant Price": format(variant.price_eur, ".2f"),
                    "Variant Inventory Qty": variant.inventory,
                }
            )
        sync_file(handle)
    return path
