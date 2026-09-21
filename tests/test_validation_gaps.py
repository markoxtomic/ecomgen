"""Report v2 open items: zero-order tails, severity levels and realism ranges.

A dataset whose trailing months carry no orders is the visible symptom of the C1
stock-out bug, so `validate` has to reject it even when the manifest is re-signed.
Implausible-but-consistent metrics are warnings: they do not make a dataset invalid.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from ecomgen.cli import app
from ecomgen.exporters import export_dataset
from ecomgen.exporters.manifest import write_manifest
from ecomgen.pipeline import generate_dataset, manifest_metadata
from ecomgen.schemas import Dataset
from ecomgen.validation import validate_path

ARGS = {"preset": "garden-decor", "markets": ("de",), "customers": 400, "months": 6, "seed": 42}


def _export(dataset: Dataset, directory, months: int = 6):
    export_dataset(
        dataset,
        directory,
        formats=("csv",),
        shopify=False,
        arguments={
            "preset": "garden-decor",
            "markets": ["de"],
            "customers": ARGS["customers"],
            "months": months,
            "start_date": "2024-01-01",
            "seed": 42,
            "format": "csv",
            "shopify_export": False,
        },
        metadata=manifest_metadata(
            dataset.orders[0].created_at.date().replace(month=1, day=1), months
        )
        if dataset.orders
        else None,
    )
    return directory


def _resign(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    (directory / "manifest.json").unlink()
    write_manifest(directory, manifest["arguments"], metadata=manifest.get("metadata"))


def test_zero_order_tail_is_rejected_even_with_a_valid_manifest(tmp_path) -> None:
    dataset = generate_dataset(**ARGS)
    cutoff = min(o.created_at for o in dataset.orders) + timedelta(days=90)
    truncated = Dataset(
        products=dataset.products,
        variants=dataset.variants,
        customers=dataset.customers,
        orders=[o for o in dataset.orders if o.created_at < cutoff],
        order_items=[],
        returns=[],
        marketing_spend=dataset.marketing_spend,
    )
    kept = {o.id for o in truncated.orders}
    object.__setattr__(
        truncated, "order_items", [i for i in dataset.order_items if i.order_id in kept]
    )
    object.__setattr__(truncated, "returns", [r for r in dataset.returns if r.order_id in kept])

    directory = _export(truncated, tmp_path / "tail")
    _resign(directory)
    report = validate_path(directory, require_manifest=True)

    assert not report.valid
    assert any("no orders" in e or "zero-order" in e for e in report.errors), report.errors


def test_healthy_dataset_has_no_tail_error_and_no_warnings(tmp_path) -> None:
    directory = _export(generate_dataset(**ARGS), tmp_path / "ok")

    report = validate_path(directory, require_manifest=True)

    assert report.valid, report.errors
    assert report.warnings == [], report.warnings


def _without_returns(dataset: Dataset) -> Dataset:
    """A store where nothing is ever returned: consistent, but implausible."""

    return Dataset(
        products=dataset.products,
        variants=dataset.variants,
        customers=dataset.customers,
        orders=dataset.orders,
        order_items=dataset.order_items,
        returns=[],
        marketing_spend=dataset.marketing_spend,
    )


def test_implausible_metrics_warn_but_stay_valid(tmp_path) -> None:
    directory = _export(_without_returns(generate_dataset(**ARGS)), tmp_path / "warn")
    _resign(directory)

    report = validate_path(directory, require_manifest=True)

    assert report.valid, report.errors
    assert any("return rate" in w for w in report.warnings), report.warnings


@pytest.mark.parametrize("make_warning", [False, True])
def test_cli_exit_codes_distinguish_warnings_from_errors(tmp_path, make_warning) -> None:
    dataset = generate_dataset(**ARGS)
    if make_warning:
        dataset = _without_returns(dataset)
    directory = _export(dataset, tmp_path / f"cli{make_warning}")
    _resign(directory)

    result = CliRunner().invoke(app, ["validate", "--path", str(directory)])

    assert result.exit_code == 0, result.output
    assert ("Warning:" in result.output) is make_warning, result.output
