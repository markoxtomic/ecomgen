"""The 13 corruptions from reports/test_report.md (issue M5), pinned as a suite.

Each corruption is applied twice: once leaving `manifest.json` alone, where the hash
check alone would catch it, and once with the manifest rewritten so hashes and row
counts match again. The second mode is the real test: only the semantic checks can
catch those, and they are what a buggy *producer* would trip.
"""

from __future__ import annotations

import csv
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from ecomgen.exporters import export_dataset
from ecomgen.exporters.manifest import write_manifest
from ecomgen.pipeline import generate_dataset, manifest_metadata
from ecomgen.validation import validate_path

ARGUMENTS = {
    "preset": "garden-decor",
    "markets": ["de", "at"],
    "customers": 400,
    "months": 4,
    "start_date": "2024-01-01",
    "seed": 7,
    "format": "csv",
    "shopify_export": False,
}


def _rows(directory: Path, table: str) -> list[dict[str, str]]:
    with (directory / f"{table}.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write(directory: Path, table: str, rows: list[dict[str, str]]) -> None:
    with (directory / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def truncate_mid_row(d: Path) -> None:
    path = d / "orders.csv"
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def shift_total_by_a_cent(d: Path) -> None:
    rows = _rows(d, "orders")
    rows[0]["total"] = str(Decimal(rows[0]["total"]) + Decimal("0.01"))
    _write(d, "orders", rows)


def drop_a_table(d: Path) -> None:
    (d / "returns.csv").unlink()


def add_an_unknown_column(d: Path) -> None:
    rows = _rows(d, "orders")
    for row in rows:
        row["surprise"] = "1"
    _write(d, "orders", rows)


def order_market_differs_from_customer(d: Path) -> None:
    rows = _rows(d, "orders")
    for row in rows:
        if row["market"] == "de":
            row["market"] = "at"
            break
    _write(d, "orders", rows)


def zero_the_vat(d: Path) -> None:
    rows = _rows(d, "orders")
    rows[0]["tax"] = "0.00"
    _write(d, "orders", rows)


def foreign_currency(d: Path) -> None:
    rows = _rows(d, "orders")
    rows[0]["currency"] = "USD"
    _write(d, "orders", rows)


def order_precedes_its_customer(d: Path) -> None:
    orders, customers = _rows(d, "orders"), _rows(d, "customers")
    for customer in customers:
        if customer["id"] == orders[0]["customer_id"]:
            customer["created_at"] = "2030-01-01T00:00:00Z"
    _write(d, "customers", customers)


def first_order_marked_repeat(d: Path) -> None:
    rows = _rows(d, "orders")
    seen: set[str] = set()
    for row in rows:
        if row["customer_id"] not in seen:
            seen.add(row["customer_id"])
            row["is_repeat"] = "True"
            break
    _write(d, "orders", rows)


def impossible_marketing_funnel(d: Path) -> None:
    rows = _rows(d, "marketing_spend")
    for row in rows:
        if int(row["clicks"]) > 0:
            row["impressions"] = "0"
            break
    _write(d, "marketing_spend", rows)


def return_at_the_same_instant_as_its_order(d: Path) -> None:
    returns, orders = _rows(d, "returns"), {r["id"]: r for r in _rows(d, "orders")}
    returns[0]["created_at"] = orders[returns[0]["order_id"]]["created_at"]
    _write(d, "returns", returns)


def discount_code_without_a_discount(d: Path) -> None:
    rows = _rows(d, "orders")
    for row in rows:
        if not row["discount_code"]:
            row["discount_code"] = "SAVE99"
            break
    _write(d, "orders", rows)


def product_delisted_from_the_market_it_sold_in(d: Path) -> None:
    rows = _rows(d, "products")
    for row in rows:
        row["markets"] = '["at"]'
    _write(d, "products", rows)


CORRUPTIONS = [
    truncate_mid_row,
    shift_total_by_a_cent,
    drop_a_table,
    add_an_unknown_column,
    order_market_differs_from_customer,
    zero_the_vat,
    foreign_currency,
    order_precedes_its_customer,
    first_order_marked_repeat,
    impossible_marketing_funnel,
    return_at_the_same_instant_as_its_order,
    discount_code_without_a_discount,
    product_delisted_from_the_market_it_sold_in,
]


@pytest.fixture(scope="module")
def valid_export(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("valid") / "export"
    dataset = generate_dataset(
        preset="garden-decor", markets=("de", "at"), customers=400, months=4, seed=7
    )
    from datetime import date

    export_dataset(
        dataset,
        directory,
        formats=("csv",),
        shopify=False,
        arguments=ARGUMENTS,
        metadata=manifest_metadata(date(2024, 1, 1), 4),
    )
    assert validate_path(directory, require_manifest=True).valid
    return directory


@pytest.mark.parametrize("corrupt", CORRUPTIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("resign_manifest", [False, True], ids=["raw", "resigned"])
def test_every_known_corruption_is_caught(valid_export, tmp_path, corrupt, resign_manifest) -> None:
    directory = tmp_path / "copy"
    shutil.copytree(valid_export, directory)
    corrupt(directory)
    if resign_manifest:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        (directory / "manifest.json").unlink()
        write_manifest(directory, manifest["arguments"], metadata=manifest.get("metadata"))

    report = validate_path(directory, require_manifest=True)

    assert not report.valid, f"{corrupt.__name__} went undetected (resigned={resign_manifest})"


def test_the_suite_covers_every_reported_corruption() -> None:
    assert len(CORRUPTIONS) == 13
