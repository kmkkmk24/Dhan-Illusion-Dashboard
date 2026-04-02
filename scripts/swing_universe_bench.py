#!/usr/bin/env python3
"""
CLI wrapper for swing universe rebuild (same logic as Swing Scan auto-rebuild).

Usage (from project root):
  export DHAN_ACCESS_TOKEN="..."
  export DHAN_CLIENT_ID="..."   # if not in UI credentials
  export DHAN_API_KEY=...
  export DHAN_API_SECRET=...

  # Optional overrides (see also config.yaml swing_universe:)
  export SWING_FILTER_MAX_AFTER_LTP=100
  export SWING_FILTER_HISTORY_DAYS=300
  export SWING_FILTER_MAX_LTP=5000
  export SWING_BENCH_HIST_MIN_INTERVAL_SEC=0.28
  export SWING_BENCH_HIST_CHUNK_PAUSE_SEC=0.18
  export SWING_LTP_BATCH_PAUSE_SEC=2.0
  export SWING_SKIP_UPDATE_LATEST=1   # only write dated CSV, not swing_universe_latest.csv

  PYTHONPATH=. python3 scripts/swing_universe_bench.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _params_from_env():
    from backend.services.swing_universe_builder import load_swing_universe_params

    p = load_swing_universe_params()
    if v := os.environ.get("SWING_FILTER_MAX_LTP"):
        p.max_ltp = float(v)
    if v := os.environ.get("SWING_FILTER_HISTORY_DAYS"):
        p.history_calendar_days = int(v)
    if v := os.environ.get("SWING_FILTER_MAX_AFTER_LTP"):
        p.max_after_ltp = int(v)
    if v := os.environ.get("SWING_BENCH_HIST_MIN_INTERVAL_SEC"):
        p.hist_min_interval = float(v)
    if v := os.environ.get("SWING_BENCH_HIST_CHUNK_PAUSE_SEC"):
        p.hist_chunk_pause = float(v)
    if v := os.environ.get("SWING_LTP_BATCH_PAUSE_SEC"):
        p.ltp_batch_pause_sec = float(v)
    return p


async def main() -> None:
    from backend.models.database import get_session_factory
    from backend.services.swing_universe_builder import rebuild_swing_universe_csv

    try:
        from backend.config import get_dhan_credentials

        get_dhan_credentials()
    except ValueError as e:
        print(f"Credentials: {e}", file=sys.stderr)
        sys.exit(1)

    p = _params_from_env()
    write_latest = not os.environ.get("SWING_SKIP_UPDATE_LATEST")

    sf = get_session_factory()
    db = sf()
    try:
        result = await rebuild_swing_universe_csv(
            db, params=p, write_latest=write_latest
        )
    finally:
        db.close()

    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
