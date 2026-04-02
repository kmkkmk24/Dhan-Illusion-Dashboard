#!/usr/bin/env python3
"""
Backfill sectors using fuzzy / keyword mapping (more aggressive than fix_sectors.py).

Run from project root:
  PYTHONPATH=. python3 scripts/maintenance/fix_sectors_improved.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.models.database import get_session_factory
from backend.models.tables import Signal


def get_sector_by_symbol_name(symbol: str) -> str:
    """Map symbol to sector using fuzzy matching and known patterns."""
    symbol_lower = symbol.lower()

    pharma_keywords = ["pharma", "drug", "medic", "bio", "lab", "life", "health"]
    if any(keyword in symbol_lower for keyword in pharma_keywords):
        return "Nifty Pharma"

    finance_keywords = ["bank", "financ", "wealth", "amc", "mutual", "fund", "insurance"]
    if any(keyword in symbol_lower for keyword in finance_keywords):
        return "Nifty Fin Services"

    auto_keywords = ["auto", "motor", "wheel", "tyre", "vehicle"]
    if any(keyword in symbol_lower for keyword in auto_keywords):
        return "Nifty Auto"

    textile_keywords = ["textile", "cotton", "fabric", "garment"]
    if any(keyword in symbol_lower for keyword in textile_keywords):
        return "Nifty Consumer Durables"

    it_keywords = ["tech", "software", "system", "digital", "data"]
    if any(keyword in symbol_lower for keyword in it_keywords):
        return "Nifty IT"

    specific_mappings = {
        "LUPIN": "Nifty Pharma",
        "STRIDES PHARMA SCI LTD": "Nifty Pharma",
        "IPCA LABORATORIES LTD": "Nifty Pharma",
        "SAKAR HEALTHCARE LIMITED": "Nifty Pharma",
        "ANAND RATHI WEALTH LTD": "Nifty Fin Services",
        "WHEELS INDIA LTD": "Nifty Auto",
        "VARDHMAN TEXTILES LIMITED": "Nifty Consumer Durables",
        "GE VERNOVA T&D INDIA LTD": "Nifty Energy",
        "PRIVI SPECIALITY CHE LTD": "Nifty Chemicals",
    }

    for company, sector in specific_mappings.items():
        if company.lower() in symbol_lower:
            return sector

    return "Others"


def fix_signal_sectors_improved():
    SessionLocal = get_session_factory()
    db = SessionLocal()

    try:
        signals = db.query(Signal).all()

        print(f"Found {len(signals)} signals to check")

        updated_count = 0
        for signal in signals:
            sector = get_sector_by_symbol_name(signal.symbol)

            if sector != "Others" or signal.sector is None:
                old_sector = signal.sector
                signal.sector = sector
                updated_count += 1
                print(f"Updated {signal.symbol}: {old_sector} -> {sector}")

        db.commit()
        print(f"Successfully updated {updated_count} signals with sector information")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    fix_signal_sectors_improved()
