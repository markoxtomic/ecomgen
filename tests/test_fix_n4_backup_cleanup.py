"""Report issue N4: the replaced export's backup must not be left behind.

The swap removed its backup with `shutil.rmtree(..., ignore_errors=True)` and never
retried, so a transient lock (OneDrive, a virus scanner, an open Explorer window)
left a hidden `.<name>.old-*` directory next to every replaced export.
"""

from __future__ import annotations

import shutil

import pytest

from ecomgen.exporters import atomic, export_dataset
from ecomgen.pipeline import generate_dataset


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(markets=("de",), customers=60, months=1, seed=4)


def _leftovers(parent, name):
    return sorted(path.name for path in parent.glob(f".{name}.old-*"))


def test_replacing_an_export_leaves_no_backup_directory(tmp_path, dataset) -> None:
    destination = tmp_path / "out"
    export_dataset(dataset, destination, arguments={"seed": 4})
    export_dataset(dataset, destination, arguments={"seed": 4})

    assert _leftovers(tmp_path, "out") == []


def test_a_transient_lock_does_not_leave_a_backup_behind(tmp_path, dataset, monkeypatch) -> None:
    destination = tmp_path / "out"
    export_dataset(dataset, destination, arguments={"seed": 4})

    real_rmtree = shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(32, "The process cannot access the file")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(atomic.shutil, "rmtree", flaky)
    export_dataset(dataset, destination, arguments={"seed": 4})

    assert calls["n"] >= 2, "cleanup was not retried"
    assert _leftovers(tmp_path, "out") == []
