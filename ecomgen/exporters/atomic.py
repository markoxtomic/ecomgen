"""Atomic export of a complete dataset directory.

All files are written and fsynced into a temporary sibling of the destination
(same volume), then the whole directory is moved into place. A partially written
export therefore never appears at the destination.

Windows cannot atomically replace a non-empty directory, so an existing
destination is swapped in three steps: rename it to a backup name, rename the
temporary directory to the destination, delete the backup. If the second rename
fails, the backup is renamed back. Between the two renames (two metadata
operations) the destination briefly does not exist; a hard kill in exactly that
window leaves the previous export intact in a hidden ``.<name>.old-*`` sibling
directory instead of at the destination.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from ecomgen.schemas import Dataset

from .csv import export_csv
from .json import export_json
from .manifest import MANIFEST_NAME, read_manifest, write_manifest
from .shopify import export_shopify


class OutputDirectoryError(ValueError):
    """The requested output directory cannot safely be replaced."""


def check_output_dir(destination: str | Path) -> None:
    """Refuse destinations whose replacement could delete files ecomgen did not write.

    A missing or empty directory is accepted. A non-empty directory is accepted
    only when it holds an ecomgen ``manifest.json`` and nothing except the files
    that manifest lists.
    """

    path = Path(destination)
    if not path.exists():
        return
    if not path.is_dir():
        raise OutputDirectoryError(f"--out {path} exists and is not a directory")
    entries = sorted(entry.name for entry in path.iterdir())
    if not entries:
        return
    manifest = read_manifest(path)
    if manifest is None:
        raise OutputDirectoryError(
            f"--out {path} is not empty and is not an ecomgen export (no {MANIFEST_NAME}); "
            "choose an empty or new --out directory so no existing files are replaced"
        )
    foreign = [name for name in entries if name != MANIFEST_NAME and name not in manifest["files"]]
    if foreign:
        raise OutputDirectoryError(
            f"--out {path} contains files that are not part of its ecomgen export: "
            f"{', '.join(foreign[:5])}; move them away or choose an empty or new --out "
            "directory"
        )


def _retry(operation: Callable[[], Any]) -> Any:
    """Retry briefly on Windows sharing violations, e.g. from a virus scanner."""

    for attempt in range(5):
        try:
            return operation()
        except PermissionError:
            if sys.platform != "win32" or attempt == 4:
                raise
            time.sleep(0.05 * 2**attempt)
    return None  # pragma: no cover


def _sync_directory(path: Path) -> None:
    if sys.platform == "win32":
        return  # Directory handles cannot be fsynced on Windows.
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _install(staging: Path, destination: Path) -> None:
    """Move a finished staging directory to ``destination``, replacing it if present."""

    if not destination.exists():
        _retry(lambda: os.rename(staging, destination))
        _sync_directory(destination.parent)
        return

    backup = Path(tempfile.mkdtemp(prefix=f".{destination.name}.old-", dir=destination.parent))
    backup.rmdir()
    _retry(lambda: os.rename(destination, backup))
    try:
        check_output_dir(backup)
        _retry(lambda: os.rename(staging, destination))
    except BaseException:
        _retry(lambda: os.rename(backup, destination))
        raise
    _sync_directory(destination.parent)
    # The export is already in place, so a failure here costs nothing but a stray
    # directory; retry through the same sharing-violation backoff as the renames and
    # only then give up quietly.
    try:
        _retry(lambda: shutil.rmtree(backup))
    except OSError:
        shutil.rmtree(backup, ignore_errors=True)


def export_dataset(
    dataset: Dataset,
    destination: str | Path,
    *,
    formats: Collection[str] = ("csv",),
    shopify: bool = False,
    arguments: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Atomically write ``dataset`` and its ``manifest.json`` to ``destination``.

    ``formats`` holds ``"csv"`` and/or ``"json"``. Any existing ecomgen export at
    ``destination`` is replaced as a whole, which also removes stale tables from
    earlier runs. On any error or interrupt the destination is left unchanged and
    the temporary directory is removed.
    """

    target = Path(os.path.abspath(destination))
    check_output_dir(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        if "csv" in formats:
            export_csv(dataset, staging)
        if "json" in formats:
            export_json(dataset, staging)
        if shopify:
            export_shopify(dataset, staging)
        write_manifest(staging, arguments or {}, metadata=metadata)
        check_output_dir(target)
        _install(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target
