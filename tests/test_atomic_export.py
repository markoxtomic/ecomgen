"""Regression tests for M1: atomic export, manifest and interrupt handling."""

import csv
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ecomgen.exporters.atomic as atomic_export
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


def test_foreign_file_racing_with_install_is_restored_and_export_aborts(
    tmp_path, monkeypatch
) -> None:
    out = tmp_path / "out"
    assert _generate(out).exit_code == 0
    before = _snapshot(out)
    real_rename = atomic_export.os.rename
    injected = False

    def rename_with_foreign_file(source, destination) -> None:
        nonlocal injected
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not injected
            and source_path == out
            and destination_path.name.startswith(f".{out.name}.old-")
        ):
            (out / "foreign.txt").write_text("keep me", encoding="utf-8")
            injected = True
        real_rename(source, destination)

    monkeypatch.setattr(atomic_export.os, "rename", rename_with_foreign_file)

    with pytest.raises(atomic_export.OutputDirectoryError, match="foreign.txt"):
        atomic_export.export_dataset(_small_dataset(seed=2), out)

    assert injected
    assert _snapshot(out) == {**before, "foreign.txt": b"keep me"}
    assert [path.name for path in tmp_path.iterdir()] == ["out"]


@pytest.mark.parametrize(
    "install_failure",
    [OSError("install failed"), KeyboardInterrupt()],
    ids=["os-error", "keyboard-interrupt"],
)
def test_rollback_restore_retries_transient_windows_sharing_violation(
    tmp_path, monkeypatch, install_failure
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    (destination / "manifest.json").write_text(
        json.dumps({"generator": "ecomgen", "files": {"old.txt": {}}}),
        encoding="utf-8",
    )
    before = _snapshot(destination)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    real_rename = atomic_export.os.rename
    restore_attempts = 0

    def rename_with_transient_restore_failure(source, target) -> None:
        nonlocal restore_attempts
        source_path = Path(source)
        target_path = Path(target)
        if source_path == staging:
            raise install_failure
        if source_path.name.startswith(f".{destination.name}.old-") and target_path == destination:
            restore_attempts += 1
            if restore_attempts == 1:
                raise PermissionError("file temporarily in use")
        real_rename(source, target)

    monkeypatch.setattr(atomic_export.sys, "platform", "win32")
    monkeypatch.setattr(atomic_export.time, "sleep", lambda _: None)
    monkeypatch.setattr(atomic_export.os, "rename", rename_with_transient_restore_failure)

    with pytest.raises(BaseException) as raised:
        atomic_export._install(staging, destination)

    assert raised.value is install_failure
    assert restore_attempts == 2
    assert _snapshot(destination) == before
    assert _snapshot(staging) == {"new.txt": b"new"}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["destination", "staging"]


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


def test_manifest_records_order_window_and_return_cutoff(tmp_path) -> None:
    out = tmp_path / "out"
    assert _generate(out, "--start-date", "2024-01-01", "--months", "1").exit_code == 0

    metadata = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["metadata"]

    assert metadata["schema_version"] == 1
    assert metadata["pricing_mode"] == "gross_vat_inclusive"
    assert metadata["order_window_start"] == "2024-01-01"
    assert metadata["order_window_end"] == "2024-02-01"
    assert metadata["return_cutoff"] == "2024-03-02"
    assert metadata["return_delay_days"] == {"min": 3, "max": 30}


def test_manifest_is_deterministic_for_a_fixed_seed(tmp_path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    assert _generate(first, "--format", "all").exit_code == 0
    assert _generate(second, "--format", "all").exit_code == 0

    assert _snapshot(first) == _snapshot(second)


def test_verify_manifest_accepts_untouched_output(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    out = tmp_path / "out"
    assert _generate(out, "--format", "all", "--shopify-export").exit_code == 0

    assert verify_manifest(out / "orders.csv") == []


def test_verify_manifest_accepts_legacy_manifest_without_metadata(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest, write_manifest

    export_csv(_small_dataset(), tmp_path)
    manifest_path = write_manifest(tmp_path, {"seed": 42})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert "metadata" not in manifest
    assert verify_manifest(tmp_path) == []


def test_manifest_row_count_rejects_non_array_json_and_unsupported_files(tmp_path) -> None:
    from ecomgen.exporters.manifest import count_rows

    json_path = tmp_path / "products.json"
    json_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON array"):
        count_rows(json_path)

    text_path = tmp_path / "notes.txt"
    text_path.write_text("not dataset data", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported dataset file type"):
        count_rows(text_path)


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {"generator": "another-tool", "files": {}},
        {"generator": "ecomgen", "files": []},
    ],
    ids=["non-object", "wrong-generator", "non-object-files"],
)
def test_read_manifest_rejects_malformed_shapes(tmp_path, manifest) -> None:
    from ecomgen.exporters.manifest import read_manifest

    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert read_manifest(tmp_path) is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "cannot read"),
        (json.dumps({"generator": "another-tool"}), "not an ecomgen manifest"),
        (json.dumps({"generator": "ecomgen", "files": []}), "'files' must be an object"),
    ],
    ids=["invalid-json", "wrong-generator", "non-object-files"],
)
def test_verify_manifest_reports_malformed_manifest_shape(
    tmp_path, payload: str, message: str
) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    (tmp_path / "manifest.json").write_text(payload, encoding="utf-8")

    assert message in "\n".join(verify_manifest(tmp_path))


def test_verify_manifest_rejects_unsafe_names_and_incomplete_entries(tmp_path) -> None:
    from ecomgen.exporters.manifest import verify_manifest

    manifest = {
        "generator": "ecomgen",
        "files": {
            "../outside.csv": {"rows": 0, "sha256": "unused"},
            "manifest.json": {"rows": 0, "sha256": "unused"},
            "orders.csv": {},
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    errors = "\n".join(verify_manifest(tmp_path))

    assert "invalid file name '../outside.csv'" in errors
    assert "invalid file name 'manifest.json'" in errors
    assert "entry for orders.csv needs 'rows' and 'sha256'" in errors


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
