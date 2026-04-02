#!/usr/bin/env python3
"""
One-off: set missing entry prices on swing signals (close_price_at_detection == 0).

Run from project root:
  PYTHONPATH=. python3 scripts/maintenance/fix_entry_prices.py

Prefer fixing prices via live quotes in the app when possible; this uses heuristics.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import and_

from backend.models.database import get_session_factory
from backend.models.tables import Signal


def fix_missing_entry_prices():
    SessionLocal = get_session_factory()
    db = SessionLocal()

    try:
        signals_to_fix = (
            db.query(Signal)
            .filter(
                and_(
                    Signal.section == "swing",
                    Signal.close_price_at_detection == 0.0,
                )
            )
            .all()
        )

        print(f"Found {len(signals_to_fix)} signals with missing entry prices")

        placeholder_prices = {
            "DRREDDY": 7144.0,
            "SUNPHARMA": 1744.0,
            "VARDHMAN": 549.0,
            "LICI": 925.0,
        }

        for signal in signals_to_fix:
            if signal.symbol in placeholder_prices:
                entry_price = placeholder_prices[signal.symbol]
            else:
                entry_price = 800.0

            signal.close_price_at_detection = entry_price
            print(f"Updated {signal.symbol}: entry price set to ₹{entry_price}")

        db.commit()
        print(f"Successfully updated {len(signals_to_fix)} signals")

    except Exception as e:
        print(f"Error fixing entry prices: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    fix_missing_entry_prices()
