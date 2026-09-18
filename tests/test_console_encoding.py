"""Regression tests for M7: console output must never crash on a narrow encoding."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ecomgen import cli
from ecomgen.cli import app

GENERATE = ["generate", "--markets", "de", "--customers", "20", "--months", "1"]
# Variables that change Rich's terminal detection or Python's stream encoding; the
# tests start from a plain environment, like a user's shell or a CI runner.
CLEARED_ENV = (
    "FORCE_COLOR",
    "NO_COLOR",
    "TTY_COMPATIBLE",
    "TTY_INTERACTIVE",
    "COLUMNS",
    "TERM",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONLEGACYWINDOWSSTDIO",
)


def _env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in CLEARED_ENV}
    env.update(extra)
    return env


def _assert_exported(out: Path) -> None:
    assert (out / "orders.csv").is_file()
    assert (out / "manifest.json").is_file()


@pytest.mark.parametrize("force_terminal", [False, True], ids=["file", "forced-terminal"])
def test_generate_with_cp1252_stdout_redirected_to_file(tmp_path, force_terminal) -> None:
    out = tmp_path / "out"
    log = tmp_path / "stdout.txt"
    env = _env(PYTHONIOENCODING="cp1252")
    if force_terminal:
        # Makes Rich draw the live progress display, as on an interactive console.
        env["FORCE_COLOR"] = "1"

    with log.open("wb") as handle:
        completed = subprocess.run(
            [sys.executable, "-m", "ecomgen.cli", *GENERATE, "--out", str(out)],
            stdout=handle,
            stderr=subprocess.PIPE,
            env=env,
            timeout=300,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    _assert_exported(out)
    assert b"Exported to" in log.read_bytes()


def test_console_degrades_to_ascii_when_encoding_is_narrow(tmp_path, monkeypatch) -> None:
    # A stream that cannot be switched to UTF-8 and rejects unencodable text.
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
    monkeypatch.setattr(
        cli, "console", Console(file=stream, force_terminal=True, legacy_windows=False, width=80)
    )

    result = CliRunner().invoke(app, [*GENERATE, "--out", str(tmp_path / "out")])

    output = raw.getvalue().decode("cp1252")
    assert result.exit_code == 0, output
    assert "Exported to" in output
    assert "+---" in output or "|" in output  # ASCII table borders


@pytest.mark.skipif(sys.platform != "win32", reason="NUL and cmd.exe are Windows-only")
def test_generate_with_stdout_redirected_to_nul(tmp_path) -> None:
    out = tmp_path / "out"
    command = subprocess.list2cmdline(
        [sys.executable, "-m", "ecomgen.cli", *GENERATE, "--out", str(out)]
    )

    completed = subprocess.run(
        f"{command} > NUL",
        shell=True,
        stderr=subprocess.PIPE,
        env=_env(),
        timeout=300,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    _assert_exported(out)
