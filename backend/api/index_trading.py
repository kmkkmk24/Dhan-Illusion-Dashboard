from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import Signal
from backend.services.index_trading_scanner import IndexTradingScanner, get_index_scan_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/index-trading", tags=["index-trading"])


@router.get("/signals")
def get_index_signals(
    mode: str = Query("intraday", description="intraday or positional"),
    db: Session = Depends(get_db),
):
    mode = "positional" if mode == "positional" else "intraday"
    cached = get_index_scan_cache(mode)
    if cached.get("signals"):
        return cached

    signals = (
        db.query(Signal)
        .filter(Signal.section == "index", Signal.status == "active")
        .order_by(Signal.current_score.desc())
        .all()
    )
    fallback = [
        {
            "signal_id": s.id,
            "index": s.symbol,
            "security_id": s.security_id,
            "direction": s.signal_type,
            "score": s.current_score,
            "spot": s.close_price_at_detection,
        }
        for s in signals
    ]
    return {
        "as_of": None,
        "signals": fallback,
        "summary": {
            "source": "db",
            "count": len(fallback),
            "date": date.today().isoformat(),
            "mode": mode,
        },
    }


@router.post("/scan/run")
async def run_index_scan(
    mode: str = Query("intraday", description="intraday or positional"),
    db: Session = Depends(get_db),
):
    scanner = IndexTradingScanner(db)
    result = await scanner.scan_indices(mode=mode)
    return result
