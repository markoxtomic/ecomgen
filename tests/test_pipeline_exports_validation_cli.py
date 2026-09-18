import csv
import json
from dataclasses import replace
from datetime import date

from typer.testing import CliRunner

from ecomgen.cli import app
from ecomgen.exporters import SHOPIFY_HEADERS, export_csv, export_json, export_shopify
from ecomgen.pipeline import generate_dataset
from ecomgen.schemas import Dataset
from ecomgen.validation import validate_dataset, validate_path


def _dataset(seed: int = 42) -> Dataset:
    return generate_dataset(
        markets=("de",),
        customers=30,
        months=1,
        start_date=date(2024, 1, 1),
        seed=seed,
    )


def test_csv_and_json_export_every_table_with_stable_headers(tmp_path) -> None:
    dataset = _dataset()
    csv_dir = tmp_path / "csv"
    json_dir = tmp_path / "json"

    assert len(export_csv(dataset, csv_dir)) == 7
    assert len(export_json(dataset, json_dir)) == 7
    for table, records in dataset.tables().items():
        csv_path = csv_dir / f"{table}.csv"
        json_path = json_dir / f"{table}.json"
        assert csv_path.is_file()
        assert json_path.is_file()
        with csv_path.open(encoding="utf-8", newline="") as handle:
            assert csv.DictReader(handle).fieldnames
        assert len(json.loads(json_path.read_text(encoding="utf-8"))) == len(records)
    assert validate_path(csv_dir).valid
    assert validate_path(json_dir).valid


def test_empty_tables_still_export_headers(tmp_path) -> None:
    dataset = Dataset([], [], [], [], [], [], [])
    export_csv(dataset, tmp_path)

    for table in Dataset.TABLES:
        assert (tmp_path / f"{table}.csv").read_text(encoding="utf-8").strip()


def test_shopify_export_has_required_headers_and_one_row_per_variant(tmp_path) -> None:
    dataset = _dataset()
    path = export_shopify(dataset, tmp_path)

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert set(SHOPIFY_HEADERS).issubset(reader.fieldnames or [])
    assert len(rows) == len(dataset.variants)
    assert {row["Variant SKU"] for row in rows} == {variant.sku for variant in dataset.variants}


def test_validation_accepts_valid_and_reports_invalid_dataset() -> None:
    dataset = _dataset()
    assert validate_dataset(dataset).valid
    order = dataset.orders[0]
    invalid_order = order.model_copy(update={"subtotal": order.subtotal + 1})
    invalid = replace(dataset, orders=[invalid_order, *dataset.orders[1:]])

    report = validate_dataset(invalid)
    assert not report.valid
    assert any("line subtotal" in error for error in report.errors)
    assert any("subtotal - discount" in error for error in report.errors)


def test_pipeline_is_deterministic_and_updates_remaining_inventory() -> None:
    first = _dataset(42)
    second = _dataset(42)
    different = _dataset(43)

    assert first == second
    assert first != different
    sold = {
        variant_id: sum(
            item.quantity for item in first.order_items if item.variant_id == variant_id
        )
        for variant_id in {item.variant_id for item in first.order_items}
    }
    assert sold
    assert all(variant.inventory >= 0 for variant in first.variants)


def test_cli_generate_presets_and_validate_smoke(tmp_path) -> None:
    runner = CliRunner()
    presets_result = runner.invoke(app, ["presets"])
    assert presets_result.exit_code == 0
    assert "garden-decor" in presets_result.stdout

    output = tmp_path / "output"
    generate_result = runner.invoke(
        app,
        [
            "generate",
            "--markets",
            "de",
            "--customers",
            "20",
            "--months",
            "1",
            "--out",
            str(output),
            "--format",
            "json",
        ],
    )
    assert generate_result.exit_code == 0, generate_result.stdout
    assert (output / "orders.json").is_file()
    assert json.loads((output / "orders.json").read_text(encoding="utf-8"))
    assert not (output / "orders.csv").exists()

    validate_result = runner.invoke(app, ["validate", "--path", str(output)])
    assert validate_result.exit_code == 0
    assert "Valid dataset" in validate_result.stdout
