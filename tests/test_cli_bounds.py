"""Regression tests for m5: CLI inputs have upper bounds and clear errors."""

from datetime import date

import pytest
from typer.testing import CliRunner

from ecomgen import cli
from ecomgen.cli import app
from ecomgen.pipeline import generate_dataset

DATASET = generate_dataset(
    markets=("de",), customers=5, months=1, start_date=date(2024, 1, 1), seed=1
)


def _text(output: str) -> str:
    """Collapse Rich panel borders and line wrapping into plain text."""

    return " ".join(output.replace("│", " ").replace("|", " ").split())


def _invoke(tmp_path, *args: str):
    return CliRunner().invoke(app, ["generate", "--out", str(tmp_path / "out"), *args])


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    recorded: list[dict] = []

    def fake(**kwargs):
        recorded.append(kwargs)
        return DATASET

    monkeypatch.setattr(cli, "generate_dataset", fake)
    return recorded


def test_bounds_are_the_documented_values() -> None:
    assert cli.MAX_CUSTOMERS == 1_000_000
    assert cli.MAX_MONTHS == 120


@pytest.mark.parametrize(("option", "value"), [("--customers", "1000001"), ("--months", "121")])
def test_values_above_the_upper_bound_are_rejected(tmp_path, calls, option, value) -> None:
    result = _invoke(tmp_path, option, value)

    assert result.exit_code == 2
    text = _text(result.output)
    assert option in text
    assert value in text
    assert calls == []


def test_values_at_the_upper_bound_are_accepted(tmp_path, calls) -> None:
    result = _invoke(tmp_path, "--customers", "1000000", "--months", "120")

    assert result.exit_code == 0, result.output
    assert calls[0]["customers"] == 1_000_000
    assert calls[0]["months"] == 120


def test_negative_seed_error_names_the_option(tmp_path, calls) -> None:
    result = _invoke(tmp_path, "--seed", "-1")

    assert result.exit_code == 2
    assert "--seed" in _text(result.output)
    assert calls == []


def test_help_states_the_bounds() -> None:
    result = CliRunner().invoke(app, ["generate", "--help"], env={"COLUMNS": "200"})

    text = _text(result.output)
    assert "1000000" in text or "1,000,000" in text
    assert "120" in text


@pytest.mark.parametrize(
    ("start", "months"), [("9999-06-01", "12"), ("9999-11-15", "1"), ("9990-01-01", "120")]
)
def test_window_past_year_9999_names_start_date_and_months(tmp_path, start, months) -> None:
    result = _invoke(tmp_path, "--start-date", start, "--months", months)

    assert result.exit_code == 1
    text = _text(result.output)
    assert "--start-date" in text
    assert "--months" in text
    assert "year must be in" not in text
    assert not (tmp_path / "out").exists()


def test_window_ending_early_enough_in_year_9999_is_accepted(tmp_path, calls) -> None:
    result = _invoke(tmp_path, "--start-date", "9999-11-01", "--months", "1")

    assert result.exit_code == 0, result.output
    assert calls[0]["start_date"] == date(9999, 11, 1)
