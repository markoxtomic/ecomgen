"""Exports must be byte-identical on every platform, and survive a git checkout.

JSON was written in text mode, so Python translated "\\n" to CRLF on Windows and left
it as LF elsewhere: the same seed produced different bytes per OS, and the manifest
digests of a committed export stopped matching once git normalized the line endings
on clone. CSV is unaffected because the csv module always writes CRLF.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ecomgen.exporters import export_dataset
from ecomgen.pipeline import generate_dataset
from ecomgen.validation import validate_path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_output"


def test_json_tables_use_lf_line_endings(tmp_path) -> None:
    dataset = generate_dataset(markets=("de",), customers=40, months=1, seed=2)
    destination = export_dataset(
        dataset, tmp_path / "out", formats=("csv", "json"), arguments={"seed": 2}
    )

    for path in sorted(destination.glob("*.json")):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} was written with CRLF"


def test_csv_tables_use_crlf_on_every_platform(tmp_path) -> None:
    dataset = generate_dataset(markets=("de",), customers=40, months=1, seed=2)
    destination = export_dataset(dataset, tmp_path / "out", formats=("csv",), arguments={"seed": 2})

    body = (destination / "orders.csv").read_bytes()
    assert b"\r\n" in body
    assert b"\n" not in body.replace(b"\r\n", b"")


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="sample export not present")
def test_committed_sample_export_still_validates() -> None:
    """Guards the checkout: digests must survive whatever git does to line endings."""

    report = validate_path(SAMPLE, require_manifest=True)

    assert report.valid, report.errors


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="not a git checkout")
def test_sample_export_is_stored_verbatim_in_git() -> None:
    """The bytes git hands a fresh clone must be the bytes the manifest describes."""

    attributes = subprocess.run(
        ["git", "check-attr", "text", "--", "examples/sample_output/orders.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "text: unset" in attributes, (
        "dataset files must be marked -text in .gitattributes, or git rewrites their "
        f"line endings on checkout and breaks the manifest digests ({attributes.strip()})"
    )
