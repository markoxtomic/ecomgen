"""CSV dataset export."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ecomgen.schemas import Dataset, Record

from ._io import record_values, sync_file


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def export_csv(dataset: Dataset, output_dir: str | Path) -> list[Path]:
    """Write one UTF-8 CSV file per exact dataset table."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for table_name, records in dataset.tables().items():
        path = destination / f"{table_name}.csv"
        model = records[0].__class__ if records else _table_model(table_name)
        headers = list(model.model_fields)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for record in records:
                row = record_values(record)
                writer.writerow({key: _csv_value(value) for key, value in row.items()})
            sync_file(handle)
        paths.append(path)
    return paths


def _table_model(table_name: str) -> type[Record]:
    from ecomgen.schemas import (
        Customer,
        MarketingSpend,
        Order,
        OrderItem,
        Product,
        Return,
        Variant,
    )

    return {
        "products": Product,
        "variants": Variant,
        "customers": Customer,
        "orders": Order,
        "order_items": OrderItem,
        "returns": Return,
        "marketing_spend": MarketingSpend,
    }[table_name]
