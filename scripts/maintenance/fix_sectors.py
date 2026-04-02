#!/usr/bin/env python3
"""
Backfill sector on signals where sector is NULL (uses sector_mapping service).

Run from project root:
  PYTHONPATH=. python3 scripts/maintenance/fix_sectors.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.models.database import get_session_factory
from backend.models.tables import Signal
from backend.services.sector_mapping import get_sectors_for_stock


def fix_signal_sectors():
    """Update sector information for all existing signals with missing sectors."""
    SessionLocal = get_session_factory()
    db = SessionLocal()

    try:
        signals = db.query(Signal).filter(Signal.sector.is_(None)).all()

        print(f"Found {len(signals)} signals with missing sectors")

        updated_count = 0
        for signal in signals:
            sectors = get_sectors_for_stock(signal.symbol)

            if sectors:
                signal.sector = sectors[0]
                updated_count += 1
                print(f"Updated {signal.symbol} -> {sectors[0]}")

        db.commit()
        print(f"Successfully updated {updated_count} signals with sector information")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    fix_signal_sectors()
