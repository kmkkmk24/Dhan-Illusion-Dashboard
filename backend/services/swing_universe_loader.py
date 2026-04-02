"""
Load `security_id` list from CSV produced by swing universe rebuild
(Swing Scan auto-rebuild or `scripts/swing_universe_bench.py`).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List


def load_swing_universe_security_ids(csv_path: Path) -> List[str]:
    """
    Return security_ids in file order (header row with `security_id` column expected).
    """
    path = Path(csv_path)
    if not path.is_file():
        return []

    out: List[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if "security_id" in fields:
            for row in reader:
                sid = (row.get("security_id") or "").strip()
                if sid:
                    out.append(sid)
            return out

        f.seek(0)
        raw = csv.reader(f)
        for i, row in enumerate(raw):
            if not row or not row[0].strip():
                continue
            if i == 0 and row[0].strip().lower() == "security_id":
                continue
            out.append(row[0].strip())
    return out
