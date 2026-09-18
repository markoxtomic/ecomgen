"""JSON dataset export."""

from __future__ import annotations

import json
from pathlib import Path

from ecomgen.schemas import Dataset

from ._io import record_values, sync_file


def export_json(dataset: Dataset, output_dir: str | Path) -> list[Path]:
    """Write one records-oriented JSON array per exact dataset table."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for table_name, records in dataset.tables().items():
        path = destination / f"{table_name}.json"
        rows = [record_values(record) for record in records]
        with path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(rows, ensure_ascii=False, indent=2))
            sync_file(handle)
        paths.append(path)
    return paths
