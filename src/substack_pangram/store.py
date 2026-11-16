"""JSONL files: appended one fsynced line at a time, read tolerating a truncated last line.

Every long-running stage appends a row as soon as it has one, and a restarted
stage skips what is already on disk. Killing a run at any point loses at most
the request in flight.
"""

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Replace ``path`` whole, atomically. For files built in one go (the worklist, the rankings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def latest_by_post(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """The last row recorded for each ``post_id``: a retry's answer supersedes the failure it retried."""
    return {row["post_id"]: row for row in rows}
