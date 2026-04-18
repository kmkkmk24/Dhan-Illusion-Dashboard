from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import Signal
from backend.services.index_trading_scanner import IndexTradingScanner, get_index_scan_cache
from backend.services.trade_tracker import evaluate_hold_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/index-trading", tags=["index-trading"])


class TrackingRequest(BaseModel):
    enable: bool
    entry_price: Optional[float] = None
    target_price: Optional[float] = None
    sl_price: Optional[float] = None
    option_security_id: Optional[str] = None
    expiry: Optional[str] = None
    direction: Optional[str] = None
    spot: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    oi: Optional[float] = None
    volume: Optional[float] = None


@router.get("/signals")
def get_index_signals(
    mode: str = Query("intraday", description="intraday or positional"),
    db: Session = Depends(get_db),
):
    mode = "positional" if mode == "positional" else "intraday"
    cached = get_index_scan_cache(mode)
    if cached.get("signals"):
        # Merge tracking state from DB into cached signals
        cached_signals = cached.get("signals", [])
        if cached_signals:
            ids = [s.get("signal_id") for s in cached_signals if s.get("signal_id")]
            if ids:
                db_map = {
                    s.id: s for s in db.query(Signal).filter(Signal.id.in_(ids)).all()
                }
                for s in cached_signals:
                    db_sig = db_map.get(s.get("signal_id"))
                    if db_sig:
                        s["is_tracked"] = db_sig.is_tracked or False
                        s["track_status"] = db_sig.track_status or "NONE"
                        s["track_status_reason"] = db_sig.track_status_reason or ""
                        s["track_current_price"] = db_sig.track_current_price
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
            "is_tracked": s.is_tracked or False,
            "track_status": s.track_status or "NONE",
            "track_status_reason": s.track_status_reason or "",
            "track_current_price": s.track_current_price,
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


@router.post("/track/{signal_id}")
def toggle_tracking(
    signal_id: int,
    request: TrackingRequest,
    db: Session = Depends(get_db),
):
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        return {"error": "Signal not found"}

    signal.is_tracked = request.enable
    if request.enable:
        signal.track_status = "HOLD"
        signal.track_status_reason = "Trade opened"
        signal.track_entry_price = request.entry_price or signal.close_price_at_detection
        signal.track_target_price = request.target_price
        signal.track_sl_price = request.sl_price
        signal.track_option_security_id = request.option_security_id
        signal.track_expiry = request.expiry
        signal.track_direction = request.direction or signal.signal_type
        signal.track_entry_oi = request.oi
        signal.track_entry_volume = request.volume
        signal.track_entry_spot = request.spot
        signal.track_support = request.support
        signal.track_resistance = request.resistance
        signal.track_current_price = signal.track_entry_price
        signal.track_last_updated = datetime.utcnow()
    else:
        signal.track_status = "NONE"
        signal.track_status_reason = None
        signal.track_entry_price = None
        signal.track_target_price = None
        signal.track_sl_price = None
        signal.track_current_price = None
        signal.track_option_security_id = None
        signal.track_expiry = None
        signal.track_direction = None
        signal.track_entry_oi = None
        signal.track_entry_volume = None
        signal.track_entry_spot = None
        signal.track_support = None
        signal.track_resistance = None
        signal.track_last_updated = None

    db.commit()
    return {"success": True, "is_tracked": signal.is_tracked, "status": signal.track_status}


@router.post("/update-tracking")
async def update_tracking(db: Session = Depends(get_db)):
    """Update HOLD analysis for all actively tracked signals every 2 minutes."""
    tracked_signals = (
        db.query(Signal)
        .filter(
            Signal.is_tracked == True,
            Signal.track_status.in_(["HOLD", "EARLY-EXIT"]),
            Signal.section == "index",
        )
        .all()
    )

    if not tracked_signals:
        return {"message": "No signals being tracked", "updated": 0}

    updated_count = 0
    results = []

    for signal in tracked_signals:
        try:
            result = await evaluate_hold_status(signal)
            signal.track_status = result["status"]
            signal.track_status_reason = result["reason"]
            signal.track_current_price = result.get("current_price") or signal.track_current_price
            signal.track_last_updated = datetime.utcnow()

            # Stop tracking once trade is closed
            if result["status"] in ("SL-HIT", "TARGET-HIT"):
                signal.is_tracked = False

            updated_count += 1
            results.append({
                "signal_id": signal.id,
                "index": signal.symbol,
                "status": result["status"],
                "reason": result["reason"],
            })
        except Exception as e:
            logger.error(f"Error updating tracking for signal {signal.id}: {e}")

    db.commit()
    return {
        "message": f"Updated {updated_count} tracked signal(s)",
        "updated": updated_count,
        "results": results,
    }
