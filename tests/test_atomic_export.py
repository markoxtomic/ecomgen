"""Regression tests for M1: atomic export, manifest and interrupt handling."""

import csv
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecomgen import cli
from ecomgen.cli import app
from ecomgen.exporters import export_csv
from ecomgen.pipeline import generate_dataset
from ecomgen.schemas import Dataset

SMALL_ARGS = ["--markets", "de", "--customers", "20", "--months", "1"]


class _InterruptingDecimal(Decimal):
    """A Decimal whose string conversion is interrupted, as by a Ctrl+C."""

    def __str__(self) -> str:
        raise KeyboardInterrupt

    def __format__(self, spec: str) -> str:
        raise KeyboardInterrupt


def _small_dataset(seed: int = 42) -> Dataset:
    return generate_dataset(
        markets=("de",), customers=20, months=1, start_date=date(2024, 1, 1), seed=seed
    )


def _interrupting_dataset() -> Dataset:
    """A dataset whose export is interrupted halfway through ``marketing_spend``."""

    dataset = _small_dataset(seed=7)
    rows = list(dataset.marketing_spend)
    middle = len(rows) // 2
    rows[middle] = rows[middle].model_construct(
        **{**dict(rows[middle]), "spend": _InterruptingDecimal("1.00")}
    )
    return replace(dataset, marketing_spend=rows)


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def _generate(out: Path, *extra: str):
    return CliRunner().invoke(app, ["generate", *SMALL_ARGS, "--out", str(out), *extra])


def test_serialization_propagates_interrupt_instead_of_writing_placeholder(tmp_path) -> None:
    with pytest.raises(KeyboardInterrupt):
        export_csv(_interrupting_dataset(), tmp_path)
    for path in tmp_path.glob("*.csv"):
        assert "unprintable" not in path.read_text(encoding="utf-8")


def test_interrupt_mid_export_leaves_no_destination_and_no_temp_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "generate_dataset", lambda **_: _interrupting_dataset())
    out = tmp_path / "out"

    result = _generate(out, "--format", "all", "--shopify-export")

    assert result.exit_code == 130, result.output
    assert "Aborted" in result.output
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


def test_interrupt_mid_export_leaves_existing_output_unchanged(tmp_path, monkeypatch) -> None:
    out = tmp_path / "out"
    assert _generate(out, "--format", "all").exit_code == 0
    before = _snapshot(out)

    monkeypatch.setattr(cli, "generate_dataset", lambda **_: _interrupting_dataset())
    result = _generate(out, "--format", "all", "--shopify-export")

    assert result.exit_code == 130, result.output
    assert _snapshot(out) == before
    assert [path.name for path in tmp_path.iterdir()] == ["out"]


def test_interrupt_during_generation_exits_130(tmp_path, monkeypatch) -> None:
    def interrupted(**_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "generate_dataset", interrupted)
    result = _generate(tmp_path / "out")

    assert result.exit_code == 130
    assert "Aborted" in result.output
    assert list(tmp_path.iterdir()) == []


def test_manifest_lists_every_file_with_rows_and_sha256(tmp_path) -> None:
    import hashlib

    out = tmp_path / "out"
    assert _generate(out, "--format", "all", "--shopify-export", "--seed", "5").exit_code == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    dataset = generate_dataset(
        markets=("de",), customers=20, months=1, start_date=date(2024, 1, 1), seed=5
    )

    assert manifest["generator"] == "ecomgen"
    assert manifest["version"]
    assert manifest["arguments"] == {
        "preset": "garden-decor",
        "markets": ["de"],
        "customers": 20,
        "months": 1,
        "start_date": "2024-01-01",
        "seed": 5,
        "format": "all",
        "shopify_export": True,
    }
    written = sorted(path.name for path in out.iterdir() if path.name != "manifest.json")
    assert sorted(manifest["files"]) == written
    for table, records in dataset.tables().items():
        assert manifest["files"][f"{table}.csv"]["rows"] == len(records)
        assert manifest["files"][f"{table}.json"]["rows"] == len(records)
    assert manifest["files"]["products_shopify.csv"]["rows"] == len(dataset.variants)
    for name, entry in manifest["files"].items():
        assert entry["sha256"] == hashlib.sha256((out / name).read_bytes()).hexdigest()


def test_manifest_is_deterministic_for_a_fixed_seed(tmp_path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    assert _generate(first, "--format", "all").exit_code == 0
    assert _generate(second, "--format", "all").exit_code == 0

    assert _snapshot(first) == _snapshot(second)


def test_verify_manifest_accepts_untouched_output(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    out = tmp_path / "out"
    assert _generate(out, "--format", "all", "--shopify-export").exit_code == 0

    assert verify_manifest(out) == []


def test_verify_manifest_reports_a_changed_value(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    out = tmp_path / "out"
    assert _generate(out).exit_code == 0
    path = out / "orders.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    rows[1][rows[0].index("total")] = "0.01"
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)

    errors = verify_manifest(out)
    assert any("orders.csv" in error and "SHA-256" in error for error in errors), errors


def test_verify_manifest_reports_a_truncated_file(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    out = tmp_path / "out"
    assert _generate(out, "--format", "json").exit_code == 0
    csv_out = tmp_path / "csv"
    assert _generate(csv_out).exit_code == 0
    json_path = out / "marketing_spend.json"
    json_path.write_bytes(json_path.read_bytes()[: json_path.stat().st_size // 2])
    lines = (csv_out / "marketing_spend.csv").read_bytes().splitlines(keepends=True)
    (csv_out / "marketing_spend.csv").write_bytes(b"".join(lines[: len(lines) // 2]))

    json_errors = verify_manifest(out)
    csv_errors = verify_manifest(csv_out)
    assert any("marketing_spend.json" in error for error in json_errors), json_errors
    assert any("marketing_spend.csv" in error and "rows" in error for error in csv_errors), (
        csv_errors
    )


def test_verify_manifest_reports_missing_extra_and_unlisted_files(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    assert verify_manifest(tmp_path) == [f"missing manifest.json in {tmp_path}"]

    out = tmp_path / "out"
    assert _generate(out).exit_code == 0
    (out / "returns.csv").unlink()
    (out / "orders.json").write_text("[]", encoding="utf-8")

    errors = verify_manifest(out)
    assert any("returns.csv" in error and "missing" in error for error in errors), errors
    assert any("orders.json" in error and "not listed" in error for error in errors), errors


def test_non_ecomgen_output_directory_is_refused_and_untouched(tmp_path) -> None:
    out = tmp_path / "mine"
    out.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")

    result = _generate(out)

    assert result.exit_code == 1
    assert "empty or new" in " ".join(result.output.split())
    assert _snapshot(out) == {"notes.txt": b"keep me"}
    assert [path.name for path in tmp_path.iterdir()] == ["mine"]


def test_empty_output_directory_is_accepted(tmp_path) -> None:
    out = tmp_path / "empty"
    out.mkdir()

    assert _generate(out).exit_code == 0
    assert (out / "orders.csv").is_file()


def test_reexport_over_old_output_removes_stale_files(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    out = tmp_path / "out"
    assert _generate(out, "--format", "all", "--shopify-export").exit_code == 0
    assert (out / "orders.json").is_file()

    assert _generate(out, "--format", "csv", "--seed", "2").exit_code == 0

    names = {path.name for path in out.iterdir()}
    assert names == {f"{table}.csv" for table in Dataset.TABLES} | {"manifest.json"}
    assert verify_manifest(out) == []
    assert [path.name for path in tmp_path.iterdir()] == ["out"]


def test_explicit_serialization_matches_previous_pydantic_output(tmp_path) -> None:
    dataset = _small_dataset()
    export_csv(dataset, tmp_path)

    for table, records in dataset.tables().items():
        with (tmp_path / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for record, row in zip(records, rows, strict=True):
            expected = {
                key: json.dumps(value, separators=(",", ":"))
                if isinstance(value, list)
                else ("" if value is None else str(value))
                for key, value in record.model_dump(mode="json").items()
            }
            assert row == expected
