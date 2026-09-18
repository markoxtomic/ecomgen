"""Loading and integrity validation for exported datasets."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ecomgen.schemas import (
    Customer,
    Dataset,
    MarketingSpend,
    Order,
    OrderItem,
    Product,
    Record,
    Return,
    Variant,
)

TABLE_MODELS: dict[str, type[Record]] = {
    "products": Product,
    "variants": Variant,
    "customers": Customer,
    "orders": Order,
    "order_items": OrderItem,
    "returns": Return,
    "marketing_spend": MarketingSpend,
}


class DatasetLoadError(ValueError):
    """An exported dataset cannot be loaded into its record schemas."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    errors: list[str]
    row_counts: dict[str, int]

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def is_valid(self) -> bool:
        return self.valid

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValueError("\n".join(self.errors))


def _dataset_location(path: str | Path) -> tuple[Path, str]:
    source = Path(path)
    if source.is_file():
        if source.suffix.lower() not in {".csv", ".json"}:
            raise DatasetLoadError("dataset file must have a .csv or .json extension")
        return source.parent, source.suffix.lower()[1:]
    if not source.is_dir():
        raise DatasetLoadError(f"dataset path does not exist: {source}")

    csv_count = sum((source / f"{table}.csv").is_file() for table in Dataset.TABLES)
    json_count = sum((source / f"{table}.json").is_file() for table in Dataset.TABLES)
    if not csv_count and not json_count:
        raise DatasetLoadError(f"no dataset CSV or JSON tables found in {source}")
    return source, "json" if json_count > csv_count else "csv"


def _read_rows(path: Path, table: str, format_name: str) -> list[dict[str, Any]]:
    table_path = path / f"{table}.{format_name}"
    if not table_path.is_file():
        raise DatasetLoadError(f"missing required table: {table_path.name}")
    if format_name == "json":
        try:
            rows = json.loads(table_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetLoadError(f"cannot read {table_path.name}: {exc}") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DatasetLoadError(f"{table_path.name} must contain a JSON array of objects")
        return rows

    try:
        with table_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as exc:
        raise DatasetLoadError(f"cannot read {table_path.name}: {exc}") from exc
    if table == "products":
        for number, row in enumerate(rows, start=2):
            try:
                row["markets"] = json.loads(row["markets"])
            except (KeyError, json.JSONDecodeError) as exc:
                raise DatasetLoadError(
                    f"{table_path.name} row {number}: markets must be a JSON array"
                ) from exc
    for row in rows:
        if row.get("discount_code") == "":
            row["discount_code"] = None
    return rows


def load_dataset(path: str | Path) -> Dataset:
    """Load a complete CSV or JSON export directory into typed records."""

    directory, format_name = _dataset_location(path)
    tables: dict[str, list[Record]] = {}
    errors: list[str] = []
    for table, model in TABLE_MODELS.items():
        try:
            rows = _read_rows(directory, table, format_name)
        except DatasetLoadError as exc:
            errors.append(str(exc))
            continue
        records: list[Record] = []
        for number, row in enumerate(rows, start=1):
            try:
                records.append(model.model_validate(row))
            except ValidationError as exc:
                errors.append(f"{table} row {number}: {exc.errors(include_url=False)}")
        tables[table] = records
    if errors:
        raise DatasetLoadError("\n".join(errors))
    return Dataset(**tables)


def _duplicates(records: list[Record]) -> list[str]:
    ids = [str(record.id) for record in records]
    seen: set[str] = set()
    return sorted(identifier for identifier in ids if identifier in seen or seen.add(identifier))


def validate_dataset(dataset: Dataset) -> ValidationReport:
    """Run cross-table and accounting integrity checks."""

    errors: list[str] = []
    rows = {name: len(records) for name, records in dataset.tables().items()}
    for table in ("products", "variants", "customers", "orders", "order_items", "returns"):
        duplicates = _duplicates(getattr(dataset, table))
        if duplicates:
            errors.append(f"{table}: duplicate ids: {duplicates[:10]}")

    products = {record.id: record for record in dataset.products}
    variants = {record.id: record for record in dataset.variants}
    customers = {record.id: record for record in dataset.customers}
    orders = {record.id: record for record in dataset.orders}
    items = {record.id: record for record in dataset.order_items}
    line_subtotals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for item in dataset.order_items:
        line_subtotals[item.order_id] += item.unit_price * item.quantity

    for variant in dataset.variants:
        if variant.product_id not in products:
            errors.append(f"variant {variant.id}: orphan product_id {variant.product_id}")
        if variant.inventory < 0:
            errors.append(f"variant {variant.id}: inventory must be nonnegative")
    for order in dataset.orders:
        if order.customer_id not in customers:
            errors.append(f"order {order.id}: orphan customer_id {order.customer_id}")
        line_subtotal = line_subtotals[order.id]
        if line_subtotal != order.subtotal:
            errors.append(
                f"order {order.id}: subtotal {order.subtotal} != line subtotal {line_subtotal}"
            )
        expected_total = order.subtotal - order.discount + order.shipping + order.tax
        if order.total != expected_total:
            errors.append(
                f"order {order.id}: total {order.total} != subtotal - discount + "
                f"shipping + tax ({expected_total})"
            )
    for item in dataset.order_items:
        if item.order_id not in orders:
            errors.append(f"order item {item.id}: orphan order_id {item.order_id}")
        if item.variant_id not in variants:
            errors.append(f"order item {item.id}: orphan variant_id {item.variant_id}")
    for returned in dataset.returns:
        order = orders.get(returned.order_id)
        item = items.get(returned.order_item_id)
        if order is None:
            errors.append(f"return {returned.id}: orphan order_id {returned.order_id}")
        if item is None:
            errors.append(f"return {returned.id}: orphan order_item_id {returned.order_item_id}")
            continue
        if item.order_id != returned.order_id:
            errors.append(f"return {returned.id}: order does not match its order item")
        if order is not None and returned.created_at < order.created_at:
            errors.append(f"return {returned.id}: return date precedes order date")
        item_value = item.unit_price * item.quantity
        if returned.refund_amount > item_value:
            errors.append(
                f"return {returned.id}: refund {returned.refund_amount} exceeds "
                f"item value {item_value}"
            )
    return ValidationReport(errors=errors, row_counts=rows)


def validate_path(path: str | Path) -> ValidationReport:
    """Load and validate an exported dataset without raising for user data errors."""

    try:
        dataset = load_dataset(path)
    except DatasetLoadError as exc:
        return ValidationReport(errors=str(exc).splitlines(), row_counts={})
    return validate_dataset(dataset)
