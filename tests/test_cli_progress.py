"""Regression tests for M3 (CLI half): a real progress bar driven by generate_dataset."""

import io
from datetime import date

from rich.console import Console
from typer.testing import CliRunner

from ecomgen import cli
from ecomgen.cli import app
from ecomgen.pipeline import generate_dataset

DATASET = generate_dataset(
    markets=("de",), customers=10, months=1, start_date=date(2024, 1, 1), seed=1
)


def _fake_generate(calls: list[tuple[int, int]]):
    def fake(*, progress=None, **_):
        assert callable(progress), "the CLI must pass a progress callback"
        for done in (0, 3, 7, 10):
            progress(done, 10)
            calls.append((done, 10))
        return DATASET

    return fake


def _run(tmp_path, monkeypatch, *, terminal: bool) -> tuple[int, str, list[tuple[int, int]]]:
    buffer = io.StringIO()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        cli,
        "console",
        Console(file=buffer, force_terminal=terminal, width=100, legacy_windows=False),
    )
    monkeypatch.setattr(cli, "generate_dataset", _fake_generate(calls))
    result = CliRunner().invoke(app, ["generate", "--out", str(tmp_path / "out")])
    return result.exit_code, buffer.getvalue(), calls


def test_progress_bar_shows_percent_on_a_terminal(tmp_path, monkeypatch) -> None:
    exit_code, output, calls = _run(tmp_path, monkeypatch, terminal=True)

    assert exit_code == 0, output
    assert calls == [(0, 10), (3, 10), (7, 10), (10, 10)]
    assert "Generating dataset" in output
    assert "100%" in output


def test_progress_bar_renders_nothing_when_not_a_terminal(tmp_path, monkeypatch) -> None:
    exit_code, output, calls = _run(tmp_path, monkeypatch, terminal=False)

    assert exit_code == 0, output
    assert len(calls) == 4
    assert "\x1b" not in output
    assert "Generating dataset" not in output
    assert output.startswith("Exported to")
