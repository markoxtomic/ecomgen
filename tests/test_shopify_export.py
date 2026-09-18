"""Contract tests for the Shopify product CSV export."""

import csv
import io
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecomgen.cli import app
from ecomgen.exporters import (
    SHOPIFY_HEADERS,
    export_dataset,
    export_shopify,
    validate_shopify_export,
)
from ecomgen.pipeline import generate_dataset

EXPECTED_HEADERS = (
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
PRODUCT_FIELDS = ("Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published", "Status")
VALID_PUBLISHED = {"TRUE", "FALSE"}
VALID_STATUSES = {"active", "draft", "archived"}


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(
        preset="fashion", markets=("de",), customers=30, months=1, start_date=date(2024, 1, 1)
    )


@pytest.fixture
def shopify_path(dataset, tmp_path) -> Path:
    return export_shopify(dataset, tmp_path)


@pytest.fixture
def rows(shopify_path) -> list[dict[str, str]]:
    text = shopify_path.read_bytes().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _write_rows(path: Path, rows: list[dict[str, str]], headers=EXPECTED_HEADERS) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_shopify_csv_has_exact_headers_in_the_required_order(shopify_path) -> None:
    with shopify_path.open(encoding="utf-8", newline="") as handle:
        fieldnames = csv.DictReader(handle).fieldnames

    assert SHOPIFY_HEADERS == EXPECTED_HEADERS
    assert fieldnames == list(EXPECTED_HEADERS)


def test_shopify_csv_is_strict_utf8_and_readable(shopify_path) -> None:
    text = shopify_path.read_bytes().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = list(reader)

    assert reader.fieldnames == list(EXPECTED_HEADERS)
    assert rows
    assert all(set(row) == set(EXPECTED_HEADERS) for row in rows)
    assert all(value is not None for row in rows for value in row.values())


def test_shopify_csv_has_exactly_one_row_per_variant(rows, dataset) -> None:
    exported_skus = [row["Variant SKU"] for row in rows]
    variant_skus = [variant.sku for variant in dataset.variants]

    assert len(rows) == len(dataset.variants)
    assert sorted(exported_skus) == sorted(variant_skus)


def test_variant_rows_for_each_handle_are_contiguous(rows) -> None:
    closed_handles: set[str] = set()
    current_handle: str | None = None

    for row in rows:
        handle = row["Handle"]
        assert handle
        if handle != current_handle:
            assert handle not in closed_handles, f"handle {handle!r} appears in multiple groups"
            if current_handle is not None:
                closed_handles.add(current_handle)
            current_handle = handle


def test_skus_and_option_tuples_are_unique(rows) -> None:
    skus = [row["Variant SKU"] for row in rows]
    options_by_handle: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)

    assert all(skus)
    assert len(skus) == len(set(skus))
    for row in rows:
        option = (row["Option1 Name"], row["Option1 Value"])
        assert all(option)
        assert option not in options_by_handle[row["Handle"]], row
        options_by_handle[row["Handle"]].add(option)


def test_every_variant_row_tracks_inventory_with_valid_values(rows, dataset) -> None:
    variants = {variant.sku: variant for variant in dataset.variants}
    assert len(rows) == len(dataset.variants)
    for row in rows:
        variant = variants[row["Variant SKU"]]
        price = Decimal(row["Variant Price"])
        assert row["Variant Inventory Tracker"] == "shopify"
        assert row["Variant Inventory Policy"] in {"deny", "continue"}
        assert row["Variant Fulfillment Service"] == "manual"
        assert re.fullmatch(r"0|[1-9]\d*", row["Variant Inventory Qty"])
        assert price.is_finite() and price >= 0
        assert int(row["Variant Inventory Qty"]) == variant.inventory
        assert price == variant.price_eur


def test_product_fields_only_on_first_row_of_each_handle(rows) -> None:
    seen: set[str] = set()
    for row in rows:
        if row["Handle"] in seen:
            assert all(row[field] == "" for field in PRODUCT_FIELDS), row
        else:
            seen.add(row["Handle"])
            assert all(row[field] for field in PRODUCT_FIELDS), row
            assert row["Status"] in VALID_STATUSES
            assert row["Published"] in VALID_PUBLISHED
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


def test_validate_shopify_export_accepts_generated_file(shopify_path) -> None:
    assert validate_shopify_export(shopify_path) == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("Handle", "", "Handle must be nonempty"),
        ("Variant SKU", "", "Variant SKU must be nonempty"),
        ("Option1 Name", "", "must both be nonempty"),
        ("Variant Inventory Tracker", "", "Inventory Tracker must be 'shopify'"),
        ("Variant Inventory Qty", "01", "canonical nonnegative integer"),
        ("Variant Inventory Qty", "-1", "canonical nonnegative integer"),
        ("Variant Inventory Policy", "backorder", "Inventory Policy must be one of"),
        ("Variant Fulfillment Service", "custom", "Fulfillment Service must be 'manual'"),
        ("Variant Price", "1.2", "exactly two decimal places"),
        ("Variant Price", "NaN", "finite nonnegative"),
        ("Variant Price", "-1.00", "finite nonnegative"),
        ("Published", "yes", "Published must be one of"),
        ("Status", "published", "Status must be one of"),
    ],
)
def test_validate_shopify_export_reports_invalid_values(
    shopify_path, rows, field: str, value: str, message: str
) -> None:
    rows[0][field] = value
    _write_rows(shopify_path, rows)

    errors = validate_shopify_export(shopify_path)

    assert any(message in error for error in errors), errors


def test_validate_shopify_export_reports_structural_errors(shopify_path, rows) -> None:
    groups: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["Handle"]].append(row)
    first_handle, second_handle = list(groups)[:2]
    first_group = groups[first_handle]
    assert len(first_group) >= 2

    first_group[1]["Variant SKU"] = first_group[0]["Variant SKU"]
    first_group[1]["Option1 Name"] = first_group[0]["Option1 Name"]
    first_group[1]["Option1 Value"] = first_group[0]["Option1 Value"]
    first_group[1]["Title"] = "Repeated product title"
    reordered = [
        first_group[0],
        *groups[second_handle],
        *first_group[1:],
        *(row for row in rows if row["Handle"] not in {first_handle, second_handle}),
    ]
    _write_rows(shopify_path, reordered)

    errors = validate_shopify_export(shopify_path)
    combined = "\n".join(errors)

    assert "not contiguous" in combined
    assert "Variant SKU" in combined and "duplicated" in combined
    assert "option tuple" in combined and "duplicated" in combined
    assert "only the first row" in combined


def test_validate_shopify_export_reports_headers_encoding_and_csv_errors(
    shopify_path, rows
) -> None:
    _write_rows(shopify_path, rows, headers=tuple(reversed(EXPECTED_HEADERS)))
    assert "headers must exactly match" in "\n".join(validate_shopify_export(shopify_path))

    shopify_path.write_bytes(b"\xff")
    assert "not valid UTF-8" in "\n".join(validate_shopify_export(shopify_path))

    shopify_path.write_text('Handle,"unterminated', encoding="utf-8")
    assert "cannot parse CSV" in "\n".join(validate_shopify_export(shopify_path))


def test_validate_shopify_export_reports_missing_empty_and_wrong_width_rows(
    tmp_path,
) -> None:
    path = tmp_path / "products_shopify.csv"
    assert "cannot read file" in "\n".join(validate_shopify_export(path))

    path.write_text("", encoding="utf-8")
    assert validate_shopify_export(path) == ["missing header row"]

    path.write_text(",".join(EXPECTED_HEADERS) + "\nonly-one-column\n", encoding="utf-8")
    errors = validate_shopify_export(path)
    assert any("expected 16 columns, found 1" in error for error in errors), errors


def test_validate_shopify_export_falls_back_to_csv_variant_context(shopify_path, rows) -> None:
    (shopify_path.parent / "variants.json").write_text("{", encoding="utf-8")
    with (shopify_path.parent / "variants.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku"])
        writer.writeheader()
        writer.writerows({"sku": row["Variant SKU"]} for row in rows)

    assert validate_shopify_export(shopify_path) == []

    rows[0]["Variant SKU"] = "UNEXPECTED-SKU"
    _write_rows(shopify_path, rows)
    errors = "\n".join(validate_shopify_export(shopify_path))
    assert "missing variant SKUs" in errors
    assert "unknown variant SKUs" in errors


def test_validate_shopify_export_uses_manifest_when_variant_context_is_unusable(
    shopify_path, rows
) -> None:
    (shopify_path.parent / "variants.json").write_text(
        json.dumps([{"not_sku": "malformed"}]),
        encoding="utf-8",
    )
    (shopify_path.parent / "manifest.json").write_text(
        json.dumps(
            {
                "generator": "ecomgen",
                "files": {"variants.json": {"rows": len(rows) + 1, "sha256": "unused"}},
            }
        ),
        encoding="utf-8",
    )

    errors = validate_shopify_export(shopify_path)

    assert any(
        f"expected one Shopify row per variant ({len(rows) + 1}), found {len(rows)} rows" in error
        for error in errors
    ), errors


def test_cli_validate_checks_shopify_export_when_present(dataset, tmp_path) -> None:
    output = tmp_path / "output"
    export_dataset(dataset, output, formats=("json",), shopify=True)
    shopify_path = output / "products_shopify.csv"
    with shopify_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    _write_rows(shopify_path, rows[:-1])

    result = CliRunner().invoke(app, ["validate", "--path", str(output)])

    assert result.exit_code == 1
    assert "products_shopify.csv" in result.stdout
    assert "expected one Shopify row per variant" in result.stdout
