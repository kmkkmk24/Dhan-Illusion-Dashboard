#!/usr/bin/env python3
"""
Remove ETF signals from the database.

Run from project root:
  PYTHONPATH=. python3 scripts/maintenance/cleanup_etfs.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.models.database import get_session_factory
from backend.models.tables import Signal


def cleanup_etf_signals():
    """Remove ETF and mutual fund signals from database."""
    SessionLocal = get_session_factory()
    db = SessionLocal()

    try:
        etf_keywords = [
            "etf",
            "bees",
            "amc",
            "mutual",
            "fund",
            "index",
            "nifty",
            "sensex",
            "gold",
            "liquid",
        ]

        signals = db.query(Signal).all()

        print(f"Checking {len(signals)} signals for ETFs/Mutual Funds...")

        removed_count = 0
        for signal in signals:
            symbol_lower = signal.symbol.lower()

            is_etf = any(keyword in symbol_lower for keyword in etf_keywords)

            if is_etf:
                print(f"Removing ETF/Fund: {signal.symbol}")
                db.delete(signal)
                removed_count += 1

        db.commit()
        print(f"Successfully removed {removed_count} ETF/Mutual Fund signals")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    cleanup_etf_signals()
