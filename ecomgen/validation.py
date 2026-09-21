"""Loading and integrity validation for exported datasets."""

from __future__ import annotations

import csv
import importlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ecomgen.accounting import item_paid_values
from ecomgen.config import load_markets
from ecomgen.config.models import CHANNELS, MarketConfig
from ecomgen.exporters.manifest import MANIFEST_NAME, read_manifest, verify_manifest
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

_CENT = Decimal("0.01")
_DISCOUNT_DEPTH_TOLERANCE = Decimal("0.005")
_CURRENT_MANIFEST_ARGUMENTS = frozenset(
    {
        "preset",
        "markets",
        "customers",
        "months",
        "start_date",
        "seed",
        "format",
        "shopify_export",
    }
)
_REQUIRED_METADATA = frozenset(
    {
        "schema_version",
        "pricing_mode",
        "order_window_start",
        "order_window_end",
        "return_cutoff",
        "return_delay_days",
    }
)
_SAVE_CODE = re.compile(r"SAVE([1-9]\d?|100)")
_PAID_CHANNELS = frozenset({"email", "google", "meta"})
# A trailing month holding less than this share of the median active month is a dead
# tail: the symptom of stock-outs silently suppressing demand.
_TAIL_SHARE = Decimal("0.05")
# Plausibility bands. Outside these a dataset is self-consistent but not realistic,
# so they raise warnings, never errors.
_REALISM_RANGES: dict[str, tuple[Decimal, Decimal]] = {
    "repeat order share": (Decimal("0.02"), Decimal("0.60")),
    "item return rate": (Decimal("0.005"), Decimal("0.50")),
    "orders per buyer": (Decimal(1), Decimal(6)),
    "discounted order share": (Decimal(0), Decimal("0.80")),
}


class DatasetLoadError(ValueError):
    """An exported dataset cannot be loaded into its record schemas."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Validation outcome.

    ``errors`` are integrity failures and make the dataset invalid. ``warnings``
    are plausibility concerns: the data is self-consistent but sits outside the
    range a real store would produce, so they never affect ``valid``.
    """

    errors: list[str]
    row_counts: dict[str, int]
    warnings: list[str] = field(default_factory=list)

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


def _load_dataset_format(directory: Path, format_name: str) -> Dataset:
    """Load one complete representation from an export directory."""

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


def load_dataset(path: str | Path) -> Dataset:
    """Load a complete CSV or JSON export directory into typed records."""

    directory, format_name = _dataset_location(path)
    return _load_dataset_format(directory, format_name)


def _duplicates(records: list[Record]) -> list[str]:
    ids = [str(record.id) for record in records]
    seen: set[str] = set()
    return sorted(identifier for identifier in ids if identifier in seen or seen.add(identifier))


def _market_mapping(
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig] | None,
) -> dict[str, MarketConfig]:
    if markets is None:
        return load_markets()
    if isinstance(markets, Mapping):
        return dict(markets)
    return {market.code: market for market in markets}


def _as_utc(value: datetime) -> datetime:
    """Normalize datetimes for comparisons, treating legacy naive values as UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _before(left: datetime, right: datetime) -> bool:
    return _as_utc(left) < _as_utc(right)


def _at_or_before(left: datetime, right: datetime) -> bool:
    return _as_utc(left) <= _as_utc(right)


def _manifest_metadata_errors(manifest: Mapping[str, Any] | None) -> list[str]:
    if manifest is None:
        return []
    arguments = manifest.get("arguments")
    metadata = manifest.get("metadata")
    is_current = (
        isinstance(metadata, Mapping)
        or isinstance(arguments, Mapping)
        and _CURRENT_MANIFEST_ARGUMENTS <= arguments.keys()
    )
    if not is_current:
        return []
    if not isinstance(metadata, Mapping):
        return [f"{MANIFEST_NAME}: current exports require semantic metadata"]
    missing = sorted(_REQUIRED_METADATA - metadata.keys())
    errors = [f"{MANIFEST_NAME}: metadata is missing required fields: {missing}"] if missing else []
    if metadata.get("schema_version") != 1:
        errors.append(f"{MANIFEST_NAME}: unsupported metadata schema_version")
    if metadata.get("pricing_mode") != "gross_vat_inclusive":
        errors.append(f"{MANIFEST_NAME}: metadata pricing_mode must be 'gross_vat_inclusive'")
    delay = metadata.get("return_delay_days")
    if not (
        isinstance(delay, Mapping)
        and isinstance(delay.get("min"), int)
        and isinstance(delay.get("max"), int)
        and 0 <= delay["min"] <= delay["max"]
    ):
        errors.append(f"{MANIFEST_NAME}: metadata return_delay_days must be an ordered range")
    parsed_dates: dict[str, date] = {}
    for key in ("order_window_start", "order_window_end", "return_cutoff"):
        value = metadata.get(key)
        try:
            parsed_dates[key] = date.fromisoformat(value)
        except (TypeError, ValueError):
            errors.append(f"{MANIFEST_NAME}: metadata {key} must be an ISO date")
    if len(parsed_dates) == 3 and not (
        parsed_dates["order_window_start"]
        <= parsed_dates["order_window_end"]
        <= parsed_dates["return_cutoff"]
    ):
        errors.append(f"{MANIFEST_NAME}: metadata window dates are not ordered")
    return errors


def _return_cutoff(manifest: Mapping[str, Any] | None) -> datetime | None:
    if not isinstance(manifest, Mapping):
        return None
    metadata = manifest.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("return_cutoff")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), time.min)
        except ValueError:
            return None
    return _as_utc(parsed)


def _selected_markets(manifest: Mapping[str, Any] | None) -> set[str] | None:
    if not isinstance(manifest, Mapping):
        return None
    arguments = manifest.get("arguments")
    if not isinstance(arguments, Mapping):
        return None
    values = arguments.get("markets")
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        return None
    return set(values)


def validate_dataset(
    dataset: Dataset,
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Run cross-table and accounting integrity checks."""

    errors: list[str] = []
    market_configs = _market_mapping(markets)
    selected_markets = _selected_markets(manifest)
    cutoff = _return_cutoff(manifest)
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
    customer_orders: defaultdict[str, list[Order]] = defaultdict(list)
    items_by_order: defaultdict[str, list[OrderItem]] = defaultdict(list)
    line_subtotals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for item in dataset.order_items:
        items_by_order[item.order_id].append(item)
        line_subtotals[item.order_id] += item.unit_price * item.quantity

    for product in dataset.products:
        invalid_markets = sorted(set(product.markets) - set(market_configs))
        if invalid_markets:
            errors.append(f"product {product.id}: unknown markets {invalid_markets}")
        if selected_markets is not None:
            unselected = sorted(set(product.markets) - selected_markets)
            if unselected:
                errors.append(
                    f"product {product.id}: markets not selected for this run: {unselected}"
                )
    for variant in dataset.variants:
        if variant.product_id not in products:
            errors.append(f"variant {variant.id}: orphan product_id {variant.product_id}")
        if variant.inventory < 0:
            errors.append(f"variant {variant.id}: inventory must be nonnegative")
    for order in dataset.orders:
        customer = customers.get(order.customer_id)
        market = market_configs.get(order.market)
        if customer is None:
            errors.append(f"order {order.id}: orphan customer_id {order.customer_id}")
        else:
            customer_orders[customer.id].append(order)
            if customer.market != order.market:
                errors.append(
                    f"order {order.id}: market {order.market} does not match "
                    f"customer {customer.id} market {customer.market}"
                )
            if _before(order.created_at, customer.created_at):
                errors.append(
                    f"order {order.id}: created_at precedes customer {customer.id} created_at"
                )
        if market is None:
            errors.append(f"order {order.id}: unknown market {order.market}")
        else:
            if order.currency != market.currency:
                errors.append(
                    f"order {order.id}: currency {order.currency} does not match "
                    f"market {order.market} currency {market.currency}"
                )
            if order.fx_rate_from_eur != market.fx_rate_from_eur:
                errors.append(
                    f"order {order.id}: fx_rate_from_eur {order.fx_rate_from_eur} does not "
                    f"match market {order.market} rate {market.fx_rate_from_eur}"
                )
            expected_shipping = market.shipping_cost.quantize(_CENT, rounding=ROUND_HALF_UP)
            if order.shipping != expected_shipping:
                errors.append(
                    f"order {order.id}: shipping {order.shipping} does not match "
                    f"market {order.market} shipping {expected_shipping}"
                )
            gross = order.subtotal - order.discount + order.shipping
            expected_tax = (gross * market.vat_rate / (Decimal(1) + market.vat_rate)).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            if order.tax != expected_tax:
                errors.append(
                    f"order {order.id}: VAT {order.tax} does not match included-tax "
                    f"amount {expected_tax} for market {order.market}"
                )
        if selected_markets is not None and order.market not in selected_markets:
            errors.append(f"order {order.id}: market {order.market} was not selected for this run")
        line_subtotal = line_subtotals[order.id]
        if line_subtotal != order.subtotal:
            errors.append(
                f"order {order.id}: subtotal {order.subtotal} != line subtotal {line_subtotal}"
            )
        expected_total = order.subtotal - order.discount + order.shipping
        if order.total != expected_total:
            errors.append(
                f"order {order.id}: total {order.total} != subtotal - discount + "
                f"shipping ({expected_total})"
            )
        if (order.discount > 0) != (order.discount_code is not None):
            errors.append(f"order {order.id}: discount code must exist iff discount is positive")
        if order.discount_code is not None:
            match = _SAVE_CODE.fullmatch(order.discount_code)
            if match is None:
                errors.append(
                    f"order {order.id}: discount code {order.discount_code!r} "
                    "must have SAVE<n> shape"
                )
            elif order.subtotal <= 0:
                errors.append(f"order {order.id}: discount code cannot apply to zero subtotal")
            else:
                coded_depth = Decimal(match.group(1)) / Decimal(100)
                try:
                    actual_depth = order.discount / order.subtotal
                except (InvalidOperation, ZeroDivisionError):
                    errors.append(f"order {order.id}: discount code depth cannot be calculated")
                else:
                    rounding_tolerance = _CENT / order.subtotal
                    if abs(actual_depth - coded_depth) > (
                        _DISCOUNT_DEPTH_TOLERANCE + rounding_tolerance
                    ):
                        errors.append(
                            f"order {order.id}: discount code {order.discount_code} "
                            f"is inconsistent with discount depth {actual_depth:.4f}"
                        )
    for customer in dataset.customers:
        if customer.market not in market_configs:
            errors.append(f"customer {customer.id}: unknown market {customer.market}")
        if selected_markets is not None and customer.market not in selected_markets:
            errors.append(
                f"customer {customer.id}: market {customer.market} was not selected for this run"
            )
    for customer_id, related_orders in customer_orders.items():
        chronological = sorted(
            related_orders, key=lambda order: (_as_utc(order.created_at), order.id)
        )
        for position, order in enumerate(chronological):
            expected_repeat = position > 0
            if order.is_repeat != expected_repeat:
                errors.append(
                    f"order {order.id}: is_repeat must be {str(expected_repeat).lower()} "
                    f"for customer {customer_id} in (created_at, id) order"
                )
    for item in dataset.order_items:
        order = orders.get(item.order_id)
        variant = variants.get(item.variant_id)
        if order is None:
            errors.append(f"order item {item.id}: orphan order_id {item.order_id}")
        if variant is None:
            errors.append(f"order item {item.id}: orphan variant_id {item.variant_id}")
        if order is not None and variant is not None:
            product = products.get(variant.product_id)
            if product is not None and order.market not in product.markets:
                errors.append(
                    f"order item {item.id}: product {product.id} is not eligible "
                    f"in order market {order.market}"
                )
    paid_values: dict[str, Decimal] = {}
    for order_id, related_items in items_by_order.items():
        order = orders.get(order_id)
        if order is None or len({item.id for item in related_items}) != len(related_items):
            continue
        try:
            paid_values.update(item_paid_values(order, related_items))
        except ValueError as exc:
            errors.append(f"order {order.id}: discount allocation failed: {exc}")
            continue
    refunded: defaultdict[str, Decimal] = defaultdict(Decimal)
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
        if order is not None and _at_or_before(returned.created_at, order.created_at):
            errors.append(f"return {returned.id}: return date must be strictly after order date")
        if cutoff is not None and _as_utc(returned.created_at) > cutoff:
            errors.append(
                f"return {returned.id}: created_at exceeds manifest return cutoff "
                f"{cutoff.isoformat()}"
            )
        refunded[item.id] += returned.refund_amount
    for item_id, refund_total in refunded.items():
        item = items[item_id]
        order = orders.get(item.order_id)
        if order is not None:
            item_value = paid_values.get(
                item.id,
                (item.unit_price * item.quantity).quantize(_CENT, rounding=ROUND_HALF_UP),
            )
        else:
            item_value = (item.unit_price * item.quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
        refund_total = refund_total.quantize(_CENT, rounding=ROUND_HALF_UP)
        if refund_total > item_value:
            errors.append(
                f"order item {item_id}: cumulative refunds {refund_total} exceed "
                f"item value {item_value}"
            )
    marketing_keys: set[tuple[date, str, str]] = set()
    marketing_customers: dict[tuple[date, str, str], int] = {}
    for row in dataset.marketing_spend:
        key = (row.date, row.market, row.channel)
        if key in marketing_keys:
            errors.append(
                f"marketing: duplicate (date, market, channel) key "
                f"({row.date}, {row.market}, {row.channel})"
            )
        marketing_keys.add(key)
        marketing_customers[key] = row.new_customers
        market = market_configs.get(row.market)
        if market is None:
            errors.append(f"marketing {key}: unknown market {row.market}")
        elif row.fx_rate_from_eur != market.fx_rate_from_eur:
            errors.append(
                f"marketing {key}: fx_rate_from_eur {row.fx_rate_from_eur} does not match "
                f"market {row.market} rate {market.fx_rate_from_eur}"
            )
        elif row.currency != market.currency:
            errors.append(
                f"marketing {key}: currency {row.currency} does not match "
                f"market currency {market.currency}"
            )
        if selected_markets is not None and row.market not in selected_markets:
            errors.append(f"marketing {key}: market was not selected for this run")
        if row.channel not in CHANNELS:
            errors.append(f"marketing {key}: unknown channel {row.channel}")
        if row.channel in _PAID_CHANNELS:
            if row.impressions < row.clicks:
                errors.append(
                    f"marketing {key}: impressions {row.impressions} must be >= clicks {row.clicks}"
                )
            if row.clicks < row.new_customers:
                errors.append(
                    f"marketing {key}: clicks {row.clicks} must be >= "
                    f"new_customers {row.new_customers}"
                )
        elif row.impressions != 0 or row.clicks != 0:
            errors.append(f"marketing {key}: unpaid channels must have zero impressions and clicks")
    acquired: defaultdict[tuple[date, str, str], int] = defaultdict(int)
    for customer in dataset.customers:
        market = market_configs.get(customer.market)
        if market is None:
            continue
        local_date = _as_utc(customer.created_at).astimezone(ZoneInfo(market.timezone)).date()
        acquired[(local_date, customer.market, customer.acquisition_channel)] += 1
    for key in sorted(set(marketing_customers) | set(acquired)):
        actual = marketing_customers.get(key, 0)
        expected = acquired.get(key, 0)
        if actual != expected:
            errors.append(
                f"marketing {key}: new_customers {actual} does not reconcile "
                f"to customer acquisitions {expected}"
            )
    errors.extend(_zero_order_tail_errors(dataset, manifest))
    return ValidationReport(errors=errors, row_counts=rows, warnings=_realism_warnings(dataset))


def _month_key(moment: datetime) -> tuple[int, int]:
    utc = _as_utc(moment)
    return utc.year, utc.month


def _expected_months(dataset: Dataset, manifest: Mapping[str, Any] | None) -> list[tuple[int, int]]:
    """Every month the dataset claims to cover, from the manifest when available."""

    metadata = manifest.get("metadata") if isinstance(manifest, Mapping) else None
    start = end = None
    if isinstance(metadata, Mapping):
        try:
            start = date.fromisoformat(str(metadata["order_window_start"]))
            end = date.fromisoformat(str(metadata["order_window_end"]))
        except (KeyError, TypeError, ValueError):
            start = end = None
    if start is None or end is None:
        moments = [_as_utc(order.created_at) for order in dataset.orders]
        if not moments:
            return []
        start, end = min(moments).date(), max(moments).date()
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    # The window end is exclusive, so drop it unless it is the only month.
    if len(months) > 1 and (end.day == 1 or (end.year, end.month) != months[-1]):
        months.pop()
    return months


def _zero_order_tail_errors(dataset: Dataset, manifest: Mapping[str, Any] | None) -> list[str]:
    """Reject datasets whose trailing months stop carrying orders (report C1)."""

    if not dataset.orders:
        return []
    months = _expected_months(dataset, manifest)
    if len(months) < 3:
        return []
    counts: defaultdict[tuple[int, int], int] = defaultdict(int)
    for order in dataset.orders:
        counts[_month_key(order.created_at)] += 1
    active = sorted(count for count in counts.values() if count)
    if not active:
        return []
    median = Decimal(active[len(active) // 2])
    floor = median * _TAIL_SHARE
    tail: list[tuple[int, int]] = []
    for month in reversed(months):
        if Decimal(counts.get(month, 0)) > floor:
            break
        tail.append(month)
    if not tail or len(tail) == len(months):
        return []
    labels = ", ".join(f"{year:04d}-{month:02d}" for year, month in reversed(tail))
    return [
        (
            f"orders: the last {len(tail)} of {len(months)} months have no orders "
            f"(or under {_TAIL_SHARE:.0%} of the median month): {labels}; "
            "generation stopped early, so the dataset misrepresents demand"
        )
    ]


def _realism_warnings(dataset: Dataset) -> list[str]:
    """Flag self-consistent datasets whose headline metrics are implausible."""

    warnings: list[str] = []
    orders = dataset.orders
    if not orders:
        return warnings
    buyers: defaultdict[str, int] = defaultdict(int)
    for order in orders:
        buyers[order.customer_id] += 1
    measured = {
        "repeat order share": Decimal(sum(order.is_repeat for order in orders)) / len(orders),
        "orders per buyer": Decimal(len(orders)) / len(buyers),
        "discounted order share": Decimal(sum(order.discount > 0 for order in orders))
        / len(orders),
    }
    if dataset.order_items:
        measured["item return rate"] = Decimal(len(dataset.returns)) / len(dataset.order_items)
    for name, value in measured.items():
        low, high = _REALISM_RANGES[name]
        if not low <= value <= high:
            warnings.append(f"{name} {value:.3f} is outside the plausible range [{low}, {high}]")
    return warnings


def _representation_table(error: str) -> str | None:
    lowered = error.lower()
    if lowered.startswith("order item"):
        return "order_items"
    for prefix, table in (
        ("product", "products"),
        ("variant", "variants"),
        ("customer", "customers"),
        ("order", "orders"),
        ("return", "returns"),
        ("marketing", "marketing_spend"),
    ):
        if lowered.startswith(prefix):
            return table
    return None


def _format_errors(format_name: str, errors: list[str]) -> list[str]:
    formatted = []
    for error in errors:
        table = _representation_table(error)
        label = f"{table}.{format_name}" if table is not None else format_name.upper()
        formatted.append(f"{label}: {error}")
    return formatted


def _semantic_tables(dataset: Dataset) -> dict[str, list[str]]:
    """Canonicalize table rows so export row order is not treated as data."""

    return {
        table: sorted(
            json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for record in records
        )
        for table, records in dataset.tables().items()
    }


def _optional_shopify_errors(directory: Path) -> list[str]:
    path = directory / "products_shopify.csv"
    if not path.is_file():
        return []
    # Import lazily to avoid an exporters -> validation import cycle.
    module = importlib.import_module("ecomgen.exporters.shopify")
    hook = getattr(module, "validate_shopify_export", None)
    if not callable(hook):
        return []
    try:
        result = hook(path)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return [f"{path.name}: Shopify validation failed: {exc}"]
    return [f"{path.name}: {error}" for error in result]


def validate_path(
    path: str | Path,
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig] | None = None,
    *,
    require_manifest: bool = False,
) -> ValidationReport:
    """Load and validate an exported dataset without raising for user data errors."""

    source = Path(path)
    directory = source.parent if source.is_file() else source
    errors: list[str] = []
    warnings: list[str] = []
    row_counts: dict[str, int] = {}
    manifest_path = directory / MANIFEST_NAME
    manifest = read_manifest(directory)
    if require_manifest or manifest_path.is_file():
        errors.extend(verify_manifest(directory))
    errors.extend(_manifest_metadata_errors(manifest))

    datasets: dict[str, Dataset] = {}
    for format_name in ("csv", "json"):
        present = {
            table for table in Dataset.TABLES if (directory / f"{table}.{format_name}").is_file()
        }
        if present and len(present) != len(Dataset.TABLES):
            missing = sorted(set(Dataset.TABLES) - present)
            errors.append(
                f"incomplete {format_name.upper()} representation; missing tables: {missing}"
            )
            continue
        if not present:
            continue
        try:
            dataset = _load_dataset_format(directory, format_name)
        except DatasetLoadError as exc:
            errors.extend(f"{format_name.upper()}: {error}" for error in str(exc).splitlines())
            continue
        datasets[format_name] = dataset
        report = validate_dataset(dataset, markets=markets, manifest=manifest)
        if not row_counts:
            row_counts = report.row_counts
            warnings.extend(report.warnings)
        errors.extend(_format_errors(format_name, report.errors))

    if not datasets:
        errors.append(f"no complete CSV or JSON dataset representation found in {directory}")
    if {"csv", "json"} <= datasets.keys() and _semantic_tables(datasets["csv"]) != _semantic_tables(
        datasets["json"]
    ):
        errors.append("CSV and JSON dataset representations differ semantically")

    errors.extend(_optional_shopify_errors(directory))
    return ValidationReport(errors=errors, row_counts=row_counts, warnings=warnings)
