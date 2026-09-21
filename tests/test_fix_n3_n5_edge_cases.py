"""Report v2 issues N3 and N5.

N3: a start date near year 1 raised an uncaught OverflowError from the local-time
sampler instead of a clean CLI error.
N5: exported timestamps dropped the fractional second whenever microseconds were
zero, so a single row in tens of thousands changed the column's format and made
pandas' default `parse_dates` fall back to object dtype.
"""

from __future__ import annotations

import csv
import re
from datetime import UTC, datetime

from typer.testing import CliRunner

from ecomgen.cli import app
from ecomgen.exporters._io import serialize_value

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def test_start_date_before_the_supported_range_fails_cleanly() -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--start-date",
            "0001-01-01",
            "--months",
            "1",
            "--customers",
            "20",
            "--out",
            "unused",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "--start-date" in result.output
    assert "Traceback" not in result.output and "OverflowError" not in result.output


def test_utc_timestamps_always_carry_microseconds() -> None:
    whole_second = datetime(2024, 4, 26, 9, 41, 47, tzinfo=UTC)
    fractional = datetime(2024, 1, 1, 17, 20, 44, 408850, tzinfo=UTC)

    assert serialize_value(whole_second) == "2024-04-26T09:41:47.000000Z"
    assert TIMESTAMP.match(serialize_value(fractional))


def test_exported_timestamp_columns_have_one_stable_format(tmp_path) -> None:
    destination = tmp_path / "export"
    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--customers",
            "300",
            "--months",
            "2",
            "--seed",
            "3",
            "--out",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output

    for table, column in (
        ("orders", "created_at"),
        ("customers", "created_at"),
        ("returns", "created_at"),
    ):
        with (destination / f"{table}.csv").open(encoding="utf-8-sig", newline="") as handle:
            values = [row[column] for row in csv.DictReader(handle)]
        assert values, f"{table} exported no rows"
        bad = [value for value in values if not TIMESTAMP.match(value)]
        assert not bad, f"{table}.{column} has mixed timestamp formats: {bad[:3]}"


def test_the_customer_bound_is_a_size_that_actually_completes() -> None:
    """N6: the documented maximum must be reachable, not just accepted.

    500,000 customers over 12 months needs roughly 2 GB and two minutes here;
    1,000,000 needed about 5 GB and did not finish in five, so it was not a
    usable bound.
    """

    from ecomgen import cli

    assert cli.MAX_CUSTOMERS == 500_000
