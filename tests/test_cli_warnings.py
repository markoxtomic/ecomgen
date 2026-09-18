"""Regression tests for C1 (CLI half): stock-out warnings and errors are surfaced."""

import warnings
from datetime import date

from typer.testing import CliRunner

from ecomgen import cli
from ecomgen.cli import app
from ecomgen.errors import GenerationWarning, StockoutError
from ecomgen.pipeline import generate_dataset

DATASET = generate_dataset(
    markets=("de",), customers=10, months=1, start_date=date(2024, 1, 1), seed=1
)
MESSAGES = (
    "18 of 102 variants sold out; 240 orders could not be placed",
    "demand in de was suppressed from 2024-09-01 onwards",
)


def _invoke(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(cli, "generate_dataset", fake)
    return CliRunner().invoke(app, ["generate", "--out", str(tmp_path / "out")])


def test_generation_warnings_are_printed_after_the_summary(tmp_path, monkeypatch) -> None:
    def fake(**_):
        for message in MESSAGES:
            warnings.warn(message, GenerationWarning, stacklevel=2)
        warnings.warn(MESSAGES[0], GenerationWarning, stacklevel=2)  # repeated
        return DATASET

    result = _invoke(tmp_path, monkeypatch, fake)

    assert result.exit_code == 0, result.output
    lines = [" ".join(line.split()) for line in result.output.splitlines()]
    warning_lines = [line for line in lines if line.startswith("Warning:")]
    assert warning_lines == [f"Warning: {message}" for message in (*MESSAGES, MESSAGES[0])]
    summary_end = max(index for index, line in enumerate(lines) if "Return rate" in line)
    assert lines.index(warning_lines[0]) > summary_end
    assert (tmp_path / "out" / "orders.csv").is_file()


def test_stockout_error_prints_a_clean_error_and_exits_1(tmp_path, monkeypatch) -> None:
    def fake(**_):
        raise StockoutError("stock-outs suppressed 62% of demand; add inventory")

    result = _invoke(tmp_path, monkeypatch, fake)

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "Error: stock-outs suppressed 62% of demand; add inventory" in output
    assert "Traceback" not in result.output
    assert not (tmp_path / "out").exists()
