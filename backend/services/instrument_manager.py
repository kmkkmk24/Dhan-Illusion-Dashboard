"""
Manages the instrument master list, including F&O stock identification.

Downloads the Dhan scrip master CSV, parses it, identifies F&O eligible stocks,
and caches everything in the local SQLite database.
"""

import logging
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from backend.models.tables import Instrument
from backend.services.dhan_client import download_scrip_master

logger = logging.getLogger(__name__)

# NSE sector index mapping (Nifty sectoral indices)
# Security IDs for sector indices will be loaded from the scrip master
SECTOR_INDICES = {
    "NIFTY BANK": "Nifty Bank",
    "NIFTY IT": "Nifty IT",
    "NIFTY PHARMA": "Nifty Pharma",
    "NIFTY AUTO": "Nifty Auto",
    "NIFTY FMCG": "Nifty FMCG",
    "NIFTY METAL": "Nifty Metal",
    "NIFTY REALTY": "Nifty Realty",
    "NIFTY ENERGY": "Nifty Energy",
    "NIFTY INFRA": "Nifty Infra",
    "NIFTY PSU BANK": "Nifty PSU Bank",
    "NIFTY MEDIA": "Nifty Media",
    "NIFTY COMMODITIES": "Nifty Commodities",
    "NIFTY CONSUMPTION": "Nifty Consumption",
    "NIFTY FIN SERVICE": "Nifty Financial Services",
    "NIFTY HEALTHCARE": "Nifty Healthcare",
}


async def refresh_instruments(db: Session) -> dict:
    """
    Download the latest scrip master and update the instruments table.
    Returns a summary of the refresh operation.
    """
    df = await download_scrip_master()

    # Filter for NSE equity cash segment
    equity_df = df[
        (df["EXCH_ID"] == "NSE")
        & (df["SEGMENT"] == "E")
        & (df["INSTRUMENT"].isin(["EQUITY", "ETF"]))
    ].copy()

    # Identify F&O eligible stocks: find unique underlying symbols in the
    # derivatives segment of the scrip master
    fno_df = df[
        (df["EXCH_ID"] == "NSE")
        & (df["SEGMENT"] == "D")
    ]
    fno_symbols = set(fno_df["UNDERLYING_SYMBOL"].dropna().unique())

    # Build mapping: UNDERLYING_SECURITY_ID -> NSE trading symbol (e.g., 1333 -> HDFCBANK)
    fno_trading_symbols = {}
    for _, row in fno_df.drop_duplicates("UNDERLYING_SYMBOL").iterrows():
        usid = row.get("UNDERLYING_SECURITY_ID")
        usym = row.get("UNDERLYING_SYMBOL")
        if pd.notna(usid) and pd.notna(usym):
            fno_trading_symbols[str(int(usid))] = str(usym)

    logger.info(f"Found {len(equity_df)} NSE equity instruments, {len(fno_symbols)} F&O symbols")

    added = 0
    updated = 0

    for _, row in equity_df.iterrows():
        security_id = str(row.get("SEM_SMST_SECURITY_ID", row.get("SECURITY_ID", "")))
        if not security_id:
            continue

        symbol = str(row.get("SYMBOL_NAME", row.get("SM_SYMBOL_NAME", "")))
        display_name = str(row.get("DISPLAY_NAME", row.get("SEM_CUSTOM_SYMBOL", symbol)))
        instrument_type = str(row.get("INSTRUMENT_TYPE", row.get("SEM_EXCH_INSTRUMENT_TYPE", "")))
        lot_size_raw = row.get("LOT_SIZE", row.get("SEM_LOT_UNITS", 1))
        lot_size = int(lot_size_raw) if lot_size_raw and str(lot_size_raw).isdigit() else 1
        isin = str(row.get("ISIN", ""))
        trading_symbol = fno_trading_symbols.get(security_id)
        is_fno = trading_symbol is not None

        existing = db.query(Instrument).filter(Instrument.security_id == security_id).first()

        if existing:
            existing.symbol = symbol
            existing.display_name = display_name
            existing.instrument_type = instrument_type
            existing.lot_size = lot_size
            existing.isin = isin
            existing.is_fno = is_fno
            if trading_symbol:
                existing.trading_symbol = trading_symbol
            existing.updated_at = datetime.utcnow()
            updated += 1
        else:
            instrument = Instrument(
                security_id=security_id,
                exchange="NSE",
                segment="E",
                symbol=symbol,
                trading_symbol=trading_symbol,
                display_name=display_name,
                instrument_type=instrument_type,
                lot_size=lot_size,
                isin=isin,
                is_fno=is_fno,
            )
            db.add(instrument)
            added += 1

    # Also store F&O lot sizes from the derivatives data
    _update_fno_lot_sizes(db, fno_df)

    db.commit()

    summary = {
        "total_equity": len(equity_df),
        "fno_symbols": len(fno_symbols),
        "added": added,
        "updated": updated,
    }
    logger.info(f"Instrument refresh complete: {summary}")
    return summary


def _update_fno_lot_sizes(db: Session, fno_df) -> None:
    """Update lot sizes for F&O stocks from the derivatives segment data."""
    futures_df = fno_df[
        fno_df["INSTRUMENT"].isin(["FUTSTK", "FUTIDX"])
    ].drop_duplicates(subset=["UNDERLYING_SYMBOL"], keep="first")

    updated = 0
    for _, row in futures_df.iterrows():
        underlying = str(row.get("UNDERLYING_SYMBOL", ""))
        lot_size_raw = row.get("LOT_SIZE", None)
        if not underlying or pd.isna(lot_size_raw):
            continue

        try:
            lot_size = int(float(lot_size_raw))
        except (ValueError, TypeError):
            continue

        if lot_size <= 0:
            continue

        instrument = db.query(Instrument).filter(
            Instrument.trading_symbol == underlying,
            Instrument.exchange == "NSE",
        ).first()

        if instrument:
            instrument.lot_size = lot_size
            updated += 1

    logger.info(f"Updated lot sizes for {updated} F&O instruments")


def get_fno_stocks(db: Session) -> list[Instrument]:
    """Return all F&O eligible stocks."""
    return db.query(Instrument).filter(Instrument.is_fno == True).all()  # noqa: E712


def get_all_equity_stocks(db: Session) -> list[Instrument]:
    """Return all NSE equity stocks."""
    return db.query(Instrument).filter(
        Instrument.exchange == "NSE",
        Instrument.segment == "E",
    ).all()


def search_instruments(db: Session, query: str, limit: int = 20) -> list[Instrument]:
    """Search instruments by symbol or display name."""
    search_term = f"%{query.upper()}%"
    return db.query(Instrument).filter(
        (Instrument.symbol.like(search_term))
        | (Instrument.display_name.like(search_term))
    ).limit(limit).all()
