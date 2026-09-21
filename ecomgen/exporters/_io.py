"""Explicit value serialization and durable file writes shared by the exporters."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import IO, Any

from pydantic import BaseModel

JsonValue = str | int | bool | None | list[Any] | dict[str, Any]


def serialize_value(value: Any) -> JsonValue:
    """Convert one record field to a JSON-compatible value.

    Decimals, dates and datetimes are formatted here rather than through
    pydantic's ``model_dump(mode="json")``: pydantic-core swallows exceptions
    raised while converting a value to text, so a Ctrl+C at the wrong moment
    used to be written to disk as ``<unprintable Decimal object>``. Plain Python
    formatting lets the interrupt propagate. The output matches pydantic's
    format: plain decimal notation, ISO 8601 with ``Z`` for UTC.
    """

    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        # Always emit microseconds: isoformat() drops them on a whole second, which
        # would give one column two formats and defeat consumers that infer one.
        text = value.isoformat(timespec="microseconds")
        return text[:-6] + "Z" if text.endswith("+00:00") else text
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}
    raise TypeError(f"cannot serialize {type(value).__name__} value for export")


def record_values(record: BaseModel) -> dict[str, JsonValue]:
    """Return a record's fields, in schema order, as JSON-compatible values."""

    return {name: serialize_value(getattr(record, name)) for name in type(record).model_fields}


def sync_file(handle: IO[Any]) -> None:
    """Flush a file and force its contents to disk."""

    handle.flush()
    os.fsync(handle.fileno())
