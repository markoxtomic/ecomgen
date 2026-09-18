"""``manifest.json``: a verifiable description of one export directory.

The manifest records the ecomgen version, the run arguments and, for every file
written, its data row count and SHA-256 digest. It contains no timestamps, so it
is byte-identical for a fixed seed and arguments.

Shape::

    {
      "generator": "ecomgen",
      "version": "0.1.0",
      "arguments": {"preset": ..., "markets": [...], "customers": ..., "months": ...,
                    "start_date": "YYYY-MM-DD", "seed": ..., "format": "csv|json|all",
                    "shopify_export": true|false},
      "files": {"<file name>": {"rows": <int>, "sha256": "<hex>"}, ...}
    }

``rows`` excludes the CSV header row; for JSON it is the length of the array.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ecomgen import __version__

from ._io import sync_file

MANIFEST_NAME = "manifest.json"
GENERATOR = "ecomgen"
DATASET_SUFFIXES = frozenset({".csv", ".json"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_rows(path: Path) -> int:
    """Count data rows: CSV records after the header, or JSON array items."""

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return max(0, sum(1 for _ in csv.reader(handle)) - 1)
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("expected a JSON array")
        return len(rows)
    raise ValueError(f"unsupported dataset file type: {path.name}")


def write_manifest(directory: str | Path, arguments: Mapping[str, Any]) -> Path:
    """Describe every file in ``directory`` in a new ``manifest.json``.

    Call this after all dataset files have been written. ``arguments`` must be
    JSON-serializable and should hold the run arguments.
    """

    source = Path(directory)
    files = {
        path.name: {"rows": count_rows(path), "sha256": _sha256(path)}
        for path in sorted(source.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != MANIFEST_NAME
    }
    manifest = {
        "generator": GENERATOR,
        "version": __version__,
        "arguments": dict(arguments),
        "files": files,
    }
    path = source / MANIFEST_NAME
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        sync_file(handle)
    return path


def read_manifest(directory: str | Path) -> dict[str, Any] | None:
    """Return the ecomgen manifest in ``directory``, or ``None`` if there is none.

    A ``manifest.json`` that is unreadable or was not written by ecomgen counts
    as absent.
    """

    path = Path(directory) / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("generator") != GENERATOR:
        return None
    if not isinstance(manifest.get("files"), dict):
        return None
    return manifest


def verify_manifest(directory: str | Path) -> list[str]:
    """Check an export directory against its ``manifest.json``.

    ``directory`` may also be a file inside the export directory. Returns a list
    of human-readable errors, empty when every listed file is present with the
    recorded row count and SHA-256 digest and no unlisted ``.csv``/``.json``
    dataset file sits beside them.
    """

    source = Path(directory)
    if source.is_file():
        source = source.parent
    manifest_path = source / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"missing {MANIFEST_NAME} in {source}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{MANIFEST_NAME}: cannot read: {exc}"]
    if not isinstance(manifest, dict) or manifest.get("generator") != GENERATOR:
        return [f"{MANIFEST_NAME}: not an ecomgen manifest"]
    files = manifest.get("files")
    if not isinstance(files, dict):
        return [f"{MANIFEST_NAME}: 'files' must be an object"]

    errors: list[str] = []
    for name, entry in sorted(files.items()):
        path = source / name
        if Path(name).name != name or name == MANIFEST_NAME:
            errors.append(f"{MANIFEST_NAME}: invalid file name {name!r}")
            continue
        if not isinstance(entry, dict) or not {"rows", "sha256"} <= entry.keys():
            errors.append(f"{MANIFEST_NAME}: entry for {name} needs 'rows' and 'sha256'")
            continue
        if not path.is_file():
            errors.append(f"{name}: listed in {MANIFEST_NAME} but missing")
            continue
        try:
            rows = count_rows(path)
        except (OSError, UnicodeDecodeError, ValueError, csv.Error) as exc:
            errors.append(f"{name}: cannot read: {exc}")
        else:
            if rows != entry["rows"]:
                errors.append(f"{name}: expected {entry['rows']} rows, found {rows}")
        if _sha256(path) != entry["sha256"]:
            errors.append(f"{name}: SHA-256 mismatch; the file changed after export")

    for path in sorted(source.iterdir(), key=lambda item: item.name):
        if (
            path.is_file()
            and path.suffix.lower() in DATASET_SUFFIXES
            and path.name != MANIFEST_NAME
            and path.name not in files
        ):
            errors.append(f"{path.name}: not listed in {MANIFEST_NAME}")
    return errors
