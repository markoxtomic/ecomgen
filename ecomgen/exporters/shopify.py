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
import io
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

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
PRODUCT_FIELDS = ("Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published", "Status")
VALID_INVENTORY_POLICIES = frozenset({"deny", "continue"})
VALID_PUBLISHED = frozenset({"TRUE", "FALSE"})
VALID_STATUSES = frozenset({"active", "draft", "archived"})

_CANONICAL_INVENTORY = re.compile(r"0|[1-9]\d*")
_TWO_DECIMAL_PRICE = re.compile(r"\d+\.\d{2}")


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


def _variant_skus_from_context(directory: Path) -> list[str] | None:
    """Read variant SKUs from a complete sibling dataset representation, if possible."""

    json_path = directory / "variants.json"
    if json_path.is_file():
        try:
            rows = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        else:
            if isinstance(rows, list):
                skus = [
                    row.get("sku")
                    for row in rows
                    if isinstance(row, Mapping) and isinstance(row.get("sku"), str)
                ]
                if len(skus) == len(rows):
                    return skus

    csv_path = directory / "variants.csv"
    if csv_path.is_file():
        try:
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                rows = list(reader)
        except (OSError, UnicodeError, csv.Error):
            pass
        else:
            if reader.fieldnames is not None and "sku" in reader.fieldnames:
                skus = [row.get("sku") for row in rows]
                if all(isinstance(sku, str) for sku in skus):
                    return [sku for sku in skus if isinstance(sku, str)]
    return None


def _variant_count_from_manifest(directory: Path) -> int | None:
    """Return the recorded variant count when no readable sibling table is available."""

    try:
        manifest: Any = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("files"), Mapping):
        return None
    files = manifest["files"]
    for name in ("variants.json", "variants.csv"):
        entry = files.get(name)
        if isinstance(entry, Mapping):
            rows = entry.get("rows")
            if isinstance(rows, int) and not isinstance(rows, bool) and rows >= 0:
                return rows
    return None


def validate_shopify_export(path: str | Path) -> list[str]:
    """Validate a Shopify product CSV and return actionable errors.

    Malformed or unreadable input is reported rather than raised. When a sibling
    variants export or manifest is available, its variant population is also
    reconciled to the Shopify rows.
    """

    source = Path(path)
    try:
        text = source.read_bytes().decode("utf-8")
    except OSError as exc:
        return [f"cannot read file: {exc}"]
    except UnicodeDecodeError as exc:
        return [f"file is not valid UTF-8: {exc}"]

    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        return [f"cannot parse CSV: {exc}"]
    if not records:
        return ["missing header row"]

    headers = records[0]
    if headers != list(SHOPIFY_HEADERS):
        message = (
            "headers must exactly match the required order; "
            f"expected {list(SHOPIFY_HEADERS)!r}, found {headers!r}"
        )
        return [message]

    errors: list[str] = []
    rows: list[tuple[int, dict[str, str]]] = []
    for row_number, values in enumerate(records[1:], start=2):
        if len(values) != len(SHOPIFY_HEADERS):
            errors.append(
                f"row {row_number}: expected {len(SHOPIFY_HEADERS)} columns, found {len(values)}"
            )
            continue
        rows.append((row_number, dict(zip(SHOPIFY_HEADERS, values, strict=True))))

    seen_handles: set[str] = set()
    closed_handles: set[str] = set()
    seen_skus: set[str] = set()
    options_by_handle: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    current_handle: str | None = None

    for row_number, row in rows:
        handle = row["Handle"]
        if not handle:
            errors.append(f"row {row_number}: Handle must be nonempty")
        elif handle != current_handle:
            if handle in closed_handles:
                errors.append(
                    f"row {row_number}: Handle {handle!r} is not contiguous; "
                    "all variants for a product must be adjacent"
                )
            if current_handle is not None:
                closed_handles.add(current_handle)
            current_handle = handle

        first_product_row = bool(handle) and handle not in seen_handles
        if first_product_row:
            seen_handles.add(handle)
            missing = [field for field in PRODUCT_FIELDS if not row[field]]
            if missing:
                errors.append(
                    f"row {row_number}: first row for Handle {handle!r} must populate "
                    f"product fields {missing}"
                )
            if row["Published"] not in VALID_PUBLISHED:
                errors.append(
                    f"row {row_number}: Published must be one of "
                    f"{sorted(VALID_PUBLISHED)}, found {row['Published']!r}"
                )
            if row["Status"] not in VALID_STATUSES:
                errors.append(
                    f"row {row_number}: Status must be one of "
                    f"{sorted(VALID_STATUSES)}, found {row['Status']!r}"
                )
        elif handle:
            populated = [field for field in PRODUCT_FIELDS if row[field]]
            if populated:
                errors.append(
                    f"row {row_number}: only the first row for Handle {handle!r} may "
                    f"populate product fields {populated}"
                )

        sku = row["Variant SKU"]
        if not sku:
            errors.append(f"row {row_number}: Variant SKU must be nonempty")
        elif sku in seen_skus:
            errors.append(f"row {row_number}: Variant SKU {sku!r} is duplicated")
        else:
            seen_skus.add(sku)

        option = (row["Option1 Name"], row["Option1 Value"])
        if not all(option):
            errors.append(f"row {row_number}: Option1 Name and Option1 Value must both be nonempty")
        elif handle and option in options_by_handle[handle]:
            errors.append(
                f"row {row_number}: option tuple {option!r} is duplicated for Handle {handle!r}"
            )
        elif handle:
            options_by_handle[handle].add(option)

        if row["Variant Inventory Tracker"] != INVENTORY_TRACKER:
            errors.append(
                f"row {row_number}: Variant Inventory Tracker must be "
                f"{INVENTORY_TRACKER!r}, found {row['Variant Inventory Tracker']!r}"
            )
        if row["Variant Fulfillment Service"] != FULFILLMENT_SERVICE:
            errors.append(
                f"row {row_number}: Variant Fulfillment Service must be "
                f"{FULFILLMENT_SERVICE!r}, found {row['Variant Fulfillment Service']!r}"
            )
        if row["Variant Inventory Policy"] not in VALID_INVENTORY_POLICIES:
            errors.append(
                f"row {row_number}: Variant Inventory Policy must be one of "
                f"{sorted(VALID_INVENTORY_POLICIES)}, "
                f"found {row['Variant Inventory Policy']!r}"
            )

        inventory = row["Variant Inventory Qty"]
        if _CANONICAL_INVENTORY.fullmatch(inventory) is None:
            errors.append(
                f"row {row_number}: Variant Inventory Qty must be a canonical "
                f"nonnegative integer, found {inventory!r}"
            )

        price_text = row["Variant Price"]
        valid_price = _TWO_DECIMAL_PRICE.fullmatch(price_text) is not None
        if valid_price:
            try:
                price = Decimal(price_text)
            except InvalidOperation:
                valid_price = False
            else:
                valid_price = price.is_finite() and price >= 0
        if not valid_price:
            errors.append(
                f"row {row_number}: Variant Price must be a finite nonnegative amount "
                f"with exactly two decimal places, found {price_text!r}"
            )

    expected_skus = _variant_skus_from_context(source.parent)
    exported_skus = [row["Variant SKU"] for _, row in rows]
    if expected_skus is not None:
        if len(rows) != len(expected_skus):
            errors.append(
                f"expected one Shopify row per variant ({len(expected_skus)}), "
                f"found {len(rows)} rows"
            )
        missing_skus = sorted(set(expected_skus) - set(exported_skus))
        unexpected_skus = sorted(set(exported_skus) - set(expected_skus))
        if missing_skus:
            errors.append(f"Shopify export is missing variant SKUs: {missing_skus[:10]}")
        if unexpected_skus:
            errors.append(f"Shopify export has unknown variant SKUs: {unexpected_skus[:10]}")
    else:
        expected_count = _variant_count_from_manifest(source.parent)
        if expected_count is not None and len(rows) != expected_count:
            errors.append(
                f"expected one Shopify row per variant ({expected_count}), found {len(rows)} rows"
            )

    return errors
