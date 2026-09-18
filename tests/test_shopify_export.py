"""Regression tests for m4: the Shopify CSV imports with inventory tracking."""

import csv
from datetime import date

import pytest

from ecomgen.exporters import SHOPIFY_HEADERS, export_shopify
from ecomgen.pipeline import generate_dataset

PRODUCT_FIELDS = ("Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published", "Status")


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(
        preset="fashion", markets=("de",), customers=30, months=1, start_date=date(2024, 1, 1)
    )


@pytest.fixture
def rows(dataset, tmp_path) -> list[dict[str, str]]:
    path = export_shopify(dataset, tmp_path)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(SHOPIFY_HEADERS)
        return list(reader)


def test_shopify_headers_include_inventory_and_status_columns() -> None:
    assert set(SHOPIFY_HEADERS) >= {
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
        "Variant Inventory Tracker",
        "Variant Inventory Policy",
        "Variant Fulfillment Service",
        "Status",
    }


def test_every_variant_row_tracks_inventory_with_valid_values(rows, dataset) -> None:
    assert len(rows) == len(dataset.variants)
    for row in rows:
        assert row["Variant Inventory Tracker"] == "shopify"
        assert row["Variant Inventory Policy"] in {"deny", "continue"}
        assert row["Variant Fulfillment Service"] == "manual"
        assert row["Variant Inventory Qty"].isdigit()


def test_product_fields_only_on_first_row_of_each_handle(rows) -> None:
    seen: set[str] = set()
    for row in rows:
        if row["Handle"] in seen:
            assert all(row[field] == "" for field in PRODUCT_FIELDS), row
        else:
            seen.add(row["Handle"])
            assert all(row[field] for field in PRODUCT_FIELDS), row
            assert row["Status"] in {"active", "draft", "archived"}
            assert row["Published"] == "TRUE"
    handles = [row["Handle"] for row in rows]
    assert handles == sorted(handles, key=handles.index), "variant rows must be contiguous"
    assert sum(bool(row["Title"]) for row in rows) == len(seen)


def test_option_names_are_title_case_and_values_match_variants(rows, dataset) -> None:
    variants = {variant.sku: variant for variant in dataset.variants}
    for row in rows:
        variant = variants[row["Variant SKU"]]
        assert row["Option1 Name"] == variant.option_name.replace("_", " ").title()
        assert row["Option1 Name"][0].isupper()
        assert row["Option1 Value"] == variant.option_value
        assert row["Variant Price"] == format(variant.price_eur, ".2f")
        assert int(row["Variant Inventory Qty"]) == variant.inventory
