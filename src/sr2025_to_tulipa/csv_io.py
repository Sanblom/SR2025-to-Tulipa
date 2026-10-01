from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from sr2025_to_tulipa.source_validation import SourceValidationError


def write_rows(rows: list[object], output_path: Path) -> None:
    """Write dataclass records to a CSV file."""
    if not rows:
        raise SourceValidationError(f"Cannot write empty file {output_path}.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    first_row = asdict(rows[0])  # type: ignore[arg-type]
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(first_row))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)  # type: ignore[arg-type]