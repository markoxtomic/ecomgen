"""Shared helpers for CLI tests.

Rich colours its output and wraps it to the terminal width. CI runs the suite on a
narrow console and, on some runners, with colour enabled, which splits option names
across lines and buries them in escape sequences. Tests therefore ask for a wide,
plain console and compare against normalised text.
"""

from __future__ import annotations

import re

_ANSI = re.compile("\x1b\\[[0-9;]*m")

PLAIN_CONSOLE = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}


def plain(output: str) -> str:
    """Return CLI output without colour, panel borders or wrapping artefacts."""

    return " ".join(_ANSI.sub("", output).replace("│", " ").replace("|", " ").split())
