from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import Signal, Trade
from backend.services.dhan_client import DhanClient
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


class ManualSetupResponse(BaseModel):
    index: str
    spot: float
    expiry: str
    lot_size: int
    market_open: bool
    ce: dict
    pe: dict


class ManualPlaceTradeRequest(BaseModel):
    index: str = "nifty"  # nifty | sensex
    side: str  # CE | PE
    strike: Optional[float] = None
    lots: int = 1
    sl_percent: Optional[float] = None  # backward compatibility
    sl_price: Optional[float] = None
    expiry: Optional[str] = None
    t1_price: Optional[float] = None
    t1_lots: Optional[int] = None
    t2_price: Optional[float] = None
    t2_lots: Optional[int] = None


class ManualModifySlRequest(BaseModel):
    sl_price: float


class ManualModifyTargetsRequest(BaseModel):
    t1_price: Optional[float] = None
    t1_lots: Optional[int] = None
    t2_price: Optional[float] = None
    t2_lots: Optional[int] = None
    sl_price: Optional[float] = None


def _is_sensex_name(name: str) -> bool:
    return "sensex" in str(name or "").lower()


def _market_open_now() -> bool:
    from zoneinfo import ZoneInfo

    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    mkt_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return now_ist.weekday() < 5 and mkt_open <= now_ist <= mkt_close


def _display_index_name(index_key: str) -> str:
    return "SENSEX" if str(index_key).lower() == "sensex" else "NIFTY 50"


def _get_index_spec(db: Session, index_key: str):
    scanner = IndexTradingScanner(db)
    key = str(index_key or "nifty").lower()
    for spec in scanner.indices:
        nm = str(spec.name or "").lower()
        if key == "sensex" and "sensex" in nm:
            return spec, scanner
        if key != "sensex" and "nifty" in nm:
            return spec, scanner
    raise HTTPException(status_code=400, detail=f"Index spec not configured for {index_key}")


def _pick_option(
    chain_data: dict,
    side: str,
    *,
    spot: float,
    strike_pref: Optional[float] = None,
) -> Optional[dict]:
    oc = chain_data.get("oc", {}) if isinstance(chain_data, dict) else {}
    if not isinstance(oc, dict):
        return None
    key = "ce" if str(side).upper() == "CE" else "pe"
    best = None
    best_dist = float("inf")
    anchor = float(strike_pref) if strike_pref is not None else float(spot)
    for strike_str, strike_data in oc.items():
        try:
            strike = float(strike_str)
        except (TypeError, ValueError):
            continue
        opt = (strike_data or {}).get(key) if isinstance(strike_data, dict) else None
        if not isinstance(opt, dict):
            continue
        sec_id = opt.get("security_id")
        ltp = opt.get("last_price")
        if not sec_id or ltp is None:
            continue
        try:
            ltp_num = float(ltp)
        except (TypeError, ValueError):
            continue
        if ltp_num <= 0:
            continue
        dist = abs(strike - anchor)
        if dist < best_dist:
            greeks = opt.get("greeks", {}) or {}
            best_dist = dist
            best = {
                "strike": strike,
                "option_security_id": str(sec_id),
                "ltp": round(ltp_num, 2),
                "oi": int(opt.get("oi", 0) or 0),
                "volume": int(opt.get("volume", 0) or 0),
                "delta": float(greeks.get("delta", 0) or 0),
            }
    return best


def _parse_manual_plan(notes: str | None, *, quantity: int, lot_size: int) -> dict:
    default_t1 = max(lot_size, (quantity // (2 * lot_size)) * lot_size) if lot_size > 0 else quantity // 2
    default_t1 = min(max(default_t1, 0), quantity)
    default_t2 = max(0, quantity - default_t1)
    default = {
        "strategy": "manual-index",
        "executed_qty": 0,
        "t1_price": None,
        "t1_qty": default_t1,
        "t1_done": False,
        "t2_price": None,
        "t2_qty": default_t2,
        "t2_done": False,
    }
    text = str(notes or "")
    if "manual-index:" not in text:
        return default
    try:
        payload = text.split("manual-index:", 1)[1].strip()
        obj = json.loads(payload)
        if not isinstance(obj, dict):
            return default
        merged = {**default, **obj}
        for key in ("executed_qty", "t1_qty", "t2_qty"):
            merged[key] = int(merged.get(key) or 0)
        for key in ("t1_done", "t2_done"):
            merged[key] = bool(merged.get(key))
        for key in ("t1_price", "t2_price"):
            merged[key] = float(merged[key]) if merged.get(key) is not None else None
        return merged
    except Exception:
        return default


def _manual_plan_to_notes(plan: dict) -> str:
    return f"manual-index:{json.dumps(plan, separators=(',', ':'))}"


def _remaining_qty(trade: Trade, plan: dict) -> int:
    qty = int(trade.quantity or 0)
    done = int(plan.get("executed_qty") or 0)
    return max(0, qty - done)


def _extract_ltp(ltp_data: dict, security_id: str) -> Optional[float]:
    try:
        for segment_data in ltp_data.values():
            if isinstance(segment_data, dict):
                for sid, info in segment_data.items():
                    if str(sid) == str(security_id):
                        val = info.get("last_price") or info.get("ltp")
                        return float(val) if val is not None else None
            elif isinstance(segment_data, list):
                for item in segment_data:
                    if str(item.get("security_id", "")) == str(security_id):
                        val = item.get("last_price") or item.get("ltp")
                        return float(val) if val is not None else None
    except Exception:
        return None
    return None


async def _attach_live_option_ltps(signals: list[dict]) -> None:
    """Attach real-time option LTP to each signal row in-place."""
    if not signals:
        return
    # Segment can differ by index (NIFTY options on NSE_FNO, SENSEX on BSE_FNO).
    by_segment: dict[str, list[str]] = {"NSE_FNO": [], "BSE_FNO": []}
    for s in signals:
        sid = s.get("option_security_id")
        if not sid:
            continue
        seg = "BSE_FNO" if "sensex" in str(s.get("index", "")).lower() else "NSE_FNO"
        by_segment.setdefault(seg, []).append(str(sid))

    if not by_segment["NSE_FNO"] and not by_segment["BSE_FNO"]:
        return

    client = DhanClient()
    try:
        seg_quotes: dict[str, dict] = {}
        for seg, ids in by_segment.items():
            if not ids:
                continue
            # Unique IDs to keep request small.
            uniq = list(dict.fromkeys(ids))
            quotes = await client.get_market_quote_ltp(uniq, seg)
            if isinstance(quotes, dict):
                seg_quotes[seg] = quotes

        for s in signals:
            sid = s.get("option_security_id")
            if not sid:
                s["live_option_ltp"] = None
                continue
            seg = "BSE_FNO" if "sensex" in str(s.get("index", "")).lower() else "NSE_FNO"
            ltp = _extract_ltp(seg_quotes.get(seg, {}), str(sid))
            s["live_option_ltp"] = ltp
    finally:
        await client.close()


@router.get("/signals")
async def get_index_signals(
    mode: str = Query("intraday", description="intraday or positional"),
    db: Session = Depends(get_db),
):
    mode = "positional" if mode == "positional" else "intraday"
    cached = get_index_scan_cache(mode)
    if cached.get("signals"):
        # Merge tracking state from DB into cached signals, and append DB-only active
        # records (e.g., manual execution-desk trades).
        cached_signals = list(cached.get("signals", []))
        if cached_signals:
            ids = [s.get("signal_id") for s in cached_signals if s.get("signal_id")]
            db_map = {}
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

            # Include active index signals that are not in scanner cache.
            active_db = (
                db.query(Signal)
                .filter(Signal.section == "index", Signal.status == "active")
                .order_by(Signal.current_score.desc())
                .all()
            )
            existing_ids = {s.get("signal_id") for s in cached_signals if s.get("signal_id")}
            missing = [s for s in active_db if s.id not in existing_ids]
            if missing:
                missing_ids = [s.id for s in missing]
                trades = (
                    db.query(Trade)
                    .filter(Trade.signal_id.in_(missing_ids))
                    .order_by(Trade.created_at.desc())
                    .all()
                )
                trade_map: dict[int, Trade] = {}
                for t in trades:
                    if t.signal_id and t.signal_id not in trade_map:
                        trade_map[t.signal_id] = t
                for s in missing:
                    t = trade_map.get(s.id)
                    cached_signals.append({
                        "signal_id": s.id,
                        "index": s.symbol,
                        "security_id": s.security_id,
                        "direction": s.track_direction or s.signal_type,
                        "score": s.current_score,
                        "spot": s.track_entry_spot or s.close_price_at_detection,
                        "strike": t.strike_price if t else None,
                        "expiry": t.expiry_date.isoformat() if t and t.expiry_date else s.track_expiry,
                        "premium": s.track_entry_price or (t.entry_price if t else None),
                        "premium_sl": s.track_sl_price or (t.sl_price if t else None),
                        "lot_size": t.lot_size if t else None,
                        "option_security_id": s.track_option_security_id or (t.security_id if t else None),
                        "is_tracked": s.is_tracked or False,
                        "track_status": s.track_status or "NONE",
                        "track_status_reason": s.track_status_reason or "",
                        "track_current_price": s.track_current_price,
                        "live_option_ltp": None,
                    })
        await _attach_live_option_ltps(cached_signals)
        cached["signals"] = cached_signals
        return cached

    signals = (
        db.query(Signal)
        .filter(Signal.section == "index", Signal.status == "active")
        .order_by(Signal.current_score.desc())
        .all()
    )
    signal_ids = [s.id for s in signals]
    trades = (
        db.query(Trade)
        .filter(Trade.signal_id.in_(signal_ids))
        .order_by(Trade.created_at.desc())
        .all()
        if signal_ids
        else []
    )
    trade_map: dict[int, Trade] = {}
    for t in trades:
        if t.signal_id and t.signal_id not in trade_map:
            trade_map[t.signal_id] = t
    fallback = []
    for s in signals:
        t = trade_map.get(s.id)
        fallback.append({
            "signal_id": s.id,
            "index": s.symbol,
            "security_id": s.security_id,
            "direction": s.track_direction or s.signal_type,
            "score": s.current_score,
            "spot": s.track_entry_spot or s.close_price_at_detection,
            "strike": t.strike_price if t else None,
            "expiry": t.expiry_date.isoformat() if t and t.expiry_date else s.track_expiry,
            "premium": s.track_entry_price or (t.entry_price if t else None),
            "premium_sl": s.track_sl_price or (t.sl_price if t else None),
            "lot_size": t.lot_size if t else None,
            "is_tracked": s.is_tracked or False,
            "track_status": s.track_status or "NONE",
            "track_status_reason": s.track_status_reason or "",
            "track_current_price": s.track_current_price,
            "option_security_id": s.track_option_security_id or (t.security_id if t else None),
            "live_option_ltp": None,
        })
    await _attach_live_option_ltps(fallback)
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


@router.get("/manual/setup", response_model=ManualSetupResponse)
async def get_manual_setup(
    index: str = Query("nifty", description="nifty or sensex"),
    db: Session = Depends(get_db),
):
    spec, scanner = _get_index_spec(db, index)
    client = DhanClient()
    try:
        expiries = await client.get_expiry_list(spec.security_id, spec.option_segment)
        expiry = scanner._select_expiry(expiries, scanner.expiry_policy)
        if not expiry:
            raise HTTPException(status_code=502, detail="No valid option expiry available")
        chain = await client.get_option_chain(spec.security_id, spec.option_segment, expiry)
        if not chain or not isinstance(chain.get("data"), dict):
            raise HTTPException(status_code=502, detail="Could not fetch option chain")
        chain_data = chain["data"]
        spot = float(chain_data.get("last_price", 0) or 0)
        if spot <= 0:
            raise HTTPException(status_code=502, detail="Spot price unavailable")
        ce = _pick_option(chain_data, "CE", spot=spot)
        pe = _pick_option(chain_data, "PE", spot=spot)
        if not ce or not pe:
            raise HTTPException(status_code=502, detail="Could not find ATM CE/PE strikes")
        return {
            "index": _display_index_name(index),
            "spot": round(spot, 2),
            "expiry": expiry,
            "lot_size": int(spec.lot_size or 1),
            "market_open": _market_open_now(),
            "ce": ce,
            "pe": pe,
        }
    finally:
        await client.close()


@router.get("/manual/open")
async def get_manual_open_trades(
    index: str = Query("nifty", description="nifty or sensex"),
    db: Session = Depends(get_db),
):
    wanted_symbol = _display_index_name(index).lower()
    trades = (
        db.query(Trade)
        .filter(
            Trade.status == "open",
            Trade.trade_type == "option",
            Trade.notes.isnot(None),
            Trade.notes.like("%manual-index%"),
        )
        .order_by(Trade.created_at.desc())
        .all()
    )
    rows = [t for t in trades if wanted_symbol in str(t.symbol or "").lower()]
    out = []
    for t in rows:
        lot_size = int(t.lot_size or 1)
        plan = _parse_manual_plan(t.notes, quantity=int(t.quantity or 0), lot_size=lot_size)
        remaining_qty = _remaining_qty(t, plan)
        out.append({
            "trade_id": t.id,
            "signal_id": t.signal_id,
            "index": t.symbol,
            "symbol": t.symbol,
            "side": t.option_type,
            "strike": t.strike_price,
            "expiry": t.expiry_date.isoformat() if t.expiry_date else None,
            "lots": int((t.quantity or 0) / lot_size) if t.quantity else None,
            "lot_size": lot_size,
            "quantity": t.quantity,
            "remaining_qty": remaining_qty,
            "remaining_lots": int(remaining_qty / lot_size) if lot_size else None,
            "entry_price": t.entry_price,
            "sl_price": t.sl_price,
            "sl_order_id": t.sl_order_id,
            "t1_price": plan.get("t1_price"),
            "t1_qty": plan.get("t1_qty"),
            "t1_lots": int((plan.get("t1_qty") or 0) / lot_size) if lot_size else 0,
            "t1_done": bool(plan.get("t1_done")),
            "t2_price": plan.get("t2_price"),
            "t2_qty": plan.get("t2_qty"),
            "t2_lots": int((plan.get("t2_qty") or 0) / lot_size) if lot_size else 0,
            "t2_done": bool(plan.get("t2_done")),
            "executed_qty": int(plan.get("executed_qty") or 0),
            "option_security_id": t.security_id,
            "live_ltp": None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    await _attach_live_option_ltps(out)
    for row in out:
        row["live_ltp"] = row.get("live_option_ltp")
    return {"count": len(out), "rows": out}


@router.post("/manual/place")
async def place_manual_trade(
    req: ManualPlaceTradeRequest,
    db: Session = Depends(get_db),
):
    side = str(req.side or "").upper()
    if side not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="side must be CE or PE")
    if req.lots < 1:
        raise HTTPException(status_code=400, detail="lots must be >= 1")

    spec, scanner = _get_index_spec(db, req.index)
    client = DhanClient()
    try:
        expiry = req.expiry
        if not expiry:
            expiries = await client.get_expiry_list(spec.security_id, spec.option_segment)
            expiry = scanner._select_expiry(expiries, scanner.expiry_policy)
        if not expiry:
            raise HTTPException(status_code=502, detail="No valid option expiry available")

        chain = await client.get_option_chain(spec.security_id, spec.option_segment, expiry)
        if not chain or not isinstance(chain.get("data"), dict):
            raise HTTPException(status_code=502, detail="Could not fetch option chain")
        chain_data = chain["data"]
        spot = float(chain_data.get("last_price", 0) or 0)
        if spot <= 0:
            raise HTTPException(status_code=502, detail="Spot price unavailable")

        option = _pick_option(chain_data, side, spot=spot, strike_pref=req.strike)
        if not option:
            raise HTTPException(status_code=404, detail="Requested strike not available")

        cmp_price = float(option["ltp"])
        sl_price = (
            float(req.sl_price)
            if req.sl_price is not None
            else round(
                max(
                    0.05,
                    cmp_price * (
                        1 - max(1.0, abs(float(req.sl_percent or 20.0))) / 100.0
                    ),
                ),
                2,
            )
        )
        quantity = int(req.lots) * int(spec.lot_size or 1)
        is_open = _market_open_now()
        lot_size = int(spec.lot_size or 1)
        if req.t1_lots is None:
            t1_qty = max(lot_size, (int(req.lots) // 2) * lot_size) if int(req.lots) > 1 else 0
        else:
            t1_qty = max(0, int(req.t1_lots) * lot_size)
        t1_qty = min(t1_qty, quantity)
        if req.t2_lots is None:
            t2_qty = max(0, quantity - t1_qty)
        else:
            t2_qty = max(0, int(req.t2_lots) * lot_size)
            if t1_qty + t2_qty > quantity:
                t2_qty = max(0, quantity - t1_qty)
        t1_price = float(req.t1_price) if req.t1_price is not None else round(cmp_price * 1.05, 2)
        t2_price = float(req.t2_price) if req.t2_price is not None else round(cmp_price * 1.10, 2)

        option_type = "CALL" if side == "CE" else "PUT"
        buy_res = await client.place_order(
            security_id=option["option_security_id"],
            exchange_segment=spec.option_quote_segment,
            transaction_type="BUY",
            product_type="MARGIN",
            order_type="MARKET" if is_open else "LIMIT",
            quantity=quantity,
            price=0 if is_open else cmp_price,
            validity="DAY",
            drv_expiry_date=expiry,
            drv_option_type=option_type,
            drv_strike_price=option["strike"],
            after_market_order=not is_open,
        )
        if buy_res and buy_res.get("error"):
            raise HTTPException(status_code=400, detail=f"Buy order failed: {buy_res.get('detail', 'Unknown')}")

        sl_res = await client.create_forever_order(
            security_id=option["option_security_id"],
            exchange_segment=spec.option_quote_segment,
            transaction_type="SELL",
            product_type="MARGIN",
            order_type="MARKET",
            order_flag="SINGLE",
            quantity=quantity,
            price=0,
            trigger_price=sl_price,
        )
        if sl_res and sl_res.get("error"):
            raise HTTPException(status_code=400, detail=f"SL order failed: {sl_res.get('detail', 'Unknown')}")

        signal = Signal(
            security_id=spec.security_id,
            symbol=_display_index_name(req.index),
            section="index",
            signal_type=side,
            occurrence_number=1,
            first_detected_date=date.today(),
            last_updated_date=date.today(),
            current_score=0.0,
            peak_score=0.0,
            status="active",
            close_price_at_detection=spot,
            is_tracked=True,
            track_status="HOLD",
            track_status_reason="Manual execution desk trade",
            track_entry_price=cmp_price,
            track_sl_price=sl_price,
            track_current_price=cmp_price,
            track_last_updated=datetime.utcnow(),
            track_option_security_id=option["option_security_id"],
            track_expiry=expiry,
            track_direction=side,
            track_entry_oi=option.get("oi"),
            track_entry_volume=option.get("volume"),
            track_entry_spot=spot,
        )
        db.add(signal)
        db.flush()

        trade = Trade(
            signal_id=signal.id,
            security_id=option["option_security_id"],
            symbol=signal.symbol,
            trade_type="option",
            strike_price=option["strike"],
            expiry_date=datetime.strptime(expiry, "%Y-%m-%d").date(),
            option_type=side,
            premium=cmp_price,
            lot_size=int(spec.lot_size or 1),
            entry_price=cmp_price,
            sl_price=sl_price,
            quantity=quantity,
            entry_date=date.today(),
            status="open",
            buy_order_id=(buy_res or {}).get("orderId", ""),
            sl_order_id=(sl_res or {}).get("orderId", ""),
            notes=_manual_plan_to_notes({
                "strategy": "manual-index",
                "executed_qty": 0,
                "t1_price": t1_price if t1_qty > 0 else None,
                "t1_qty": t1_qty,
                "t1_done": t1_qty <= 0,
                "t2_price": t2_price if t2_qty > 0 else None,
                "t2_qty": t2_qty,
                "t2_done": t2_qty <= 0,
            }),
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)

        return {
            "ok": True,
            "message": "Trade executed and tracking started",
            "market_open": is_open,
            "trade_id": trade.id,
            "signal_id": signal.id,
            "index": signal.symbol,
            "side": side,
            "strike": option["strike"],
            "expiry": expiry,
            "entry_price": cmp_price,
            "sl_price": sl_price,
            "t1_price": t1_price if t1_qty > 0 else None,
            "t1_lots": int(t1_qty / lot_size) if lot_size else 0,
            "t2_price": t2_price if t2_qty > 0 else None,
            "t2_lots": int(t2_qty / lot_size) if lot_size else 0,
            "lots": req.lots,
            "lot_size": lot_size,
            "quantity": quantity,
            "buy_order_id": (buy_res or {}).get("orderId"),
            "sl_order_id": (sl_res or {}).get("orderId"),
        }
    finally:
        await client.close()


@router.post("/manual/trades/{trade_id}/sl")
async def modify_manual_trade_sl(
    trade_id: int,
    req: ManualModifySlRequest,
    db: Session = Depends(get_db),
):
    if req.sl_price <= 0:
        raise HTTPException(status_code=400, detail="sl_price must be > 0")

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "open":
        raise HTTPException(status_code=400, detail=f"Trade is {trade.status}, cannot modify SL")

    segment = "BSE_FNO" if _is_sensex_name(trade.symbol or "") else "NSE_FNO"
    plan = _parse_manual_plan(trade.notes, quantity=int(trade.quantity or 0), lot_size=int(trade.lot_size or 1))
    remaining_qty = _remaining_qty(trade, plan)
    if remaining_qty <= 0:
        raise HTTPException(status_code=400, detail="No remaining quantity to protect")
    client = DhanClient()
    try:
        if trade.sl_order_id:
            await client.cancel_forever_order(trade.sl_order_id)

        new_sl = await client.create_forever_order(
            security_id=trade.security_id,
            exchange_segment=segment,
            transaction_type="SELL",
            product_type="MARGIN",
            order_type="MARKET",
            order_flag="SINGLE",
            quantity=remaining_qty,
            price=0,
            trigger_price=float(req.sl_price),
        )
        if new_sl and new_sl.get("error"):
            raise HTTPException(status_code=400, detail=f"SL modify failed: {new_sl.get('detail', 'Unknown')}")

        trade.sl_price = float(req.sl_price)
        trade.sl_order_id = (new_sl or {}).get("orderId", "")
        trade.notes = (trade.notes or "") + " | sl-updated"

        if trade.signal_id:
            signal = db.query(Signal).filter(Signal.id == trade.signal_id).first()
            if signal:
                signal.track_sl_price = float(req.sl_price)
                signal.track_last_updated = datetime.utcnow()

        db.commit()
        return {
            "ok": True,
            "trade_id": trade.id,
            "sl_price": trade.sl_price,
            "sl_order_id": trade.sl_order_id,
        }
    finally:
        await client.close()


@router.post("/manual/trades/{trade_id}/targets")
async def modify_manual_trade_targets(
    trade_id: int,
    req: ManualModifyTargetsRequest,
    db: Session = Depends(get_db),
):
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "open":
        raise HTTPException(status_code=400, detail=f"Trade is {trade.status}, cannot modify targets")

    lot_size = int(trade.lot_size or 1)
    total_qty = int(trade.quantity or 0)
    plan = _parse_manual_plan(trade.notes, quantity=total_qty, lot_size=lot_size)

    if req.t1_price is not None:
        plan["t1_price"] = float(req.t1_price)
        plan["t1_done"] = False if (plan.get("t1_qty") or 0) > 0 else True
    if req.t2_price is not None:
        plan["t2_price"] = float(req.t2_price)
        plan["t2_done"] = False if (plan.get("t2_qty") or 0) > 0 else True
    if req.t1_lots is not None:
        plan["t1_qty"] = max(0, int(req.t1_lots) * lot_size)
    if req.t2_lots is not None:
        plan["t2_qty"] = max(0, int(req.t2_lots) * lot_size)

    executed = int(plan.get("executed_qty") or 0)
    remaining = max(0, total_qty - executed)
    if (plan.get("t1_qty", 0) + plan.get("t2_qty", 0)) > remaining:
        raise HTTPException(status_code=400, detail="T1+T2 quantity exceeds remaining quantity")

    if req.sl_price is not None:
        # Reuse existing SL modify flow with remaining qty.
        await modify_manual_trade_sl(trade_id, ManualModifySlRequest(sl_price=float(req.sl_price)), db)
        db.refresh(trade)

    trade.notes = _manual_plan_to_notes(plan)
    db.commit()
    return {
        "ok": True,
        "trade_id": trade.id,
        "t1_price": plan.get("t1_price"),
        "t1_qty": plan.get("t1_qty"),
        "t2_price": plan.get("t2_price"),
        "t2_qty": plan.get("t2_qty"),
        "executed_qty": plan.get("executed_qty"),
        "remaining_qty": max(0, total_qty - int(plan.get("executed_qty") or 0)),
        "sl_price": trade.sl_price,
    }


@router.post("/manual/monitor")
async def monitor_manual_targets(
    index: str = Query("all", description="nifty|sensex|all"),
    db: Session = Depends(get_db),
):
    if not _market_open_now():
        return {"ok": True, "market_open": False, "checked": 0, "events": []}

    trades = (
        db.query(Trade)
        .filter(
            Trade.status == "open",
            Trade.trade_type == "option",
            Trade.notes.isnot(None),
            Trade.notes.like("manual-index:%"),
        )
        .order_by(Trade.created_at.asc())
        .all()
    )
    if str(index).lower() in {"nifty", "sensex"}:
        wanted = _display_index_name(index).lower()
        trades = [t for t in trades if wanted in str(t.symbol or "").lower()]

    if not trades:
        return {"ok": True, "market_open": True, "checked": 0, "events": []}

    quote_rows = [{
        "trade_id": t.id,
        "index": t.symbol,
        "option_security_id": t.security_id,
    } for t in trades]
    await _attach_live_option_ltps(quote_rows)
    live_map = {r["trade_id"]: r.get("live_option_ltp") for r in quote_rows}

    events: list[dict] = []
    client = DhanClient()
    try:
        for t in trades:
            ltp = live_map.get(t.id)
            if ltp is None:
                continue
            lot_size = int(t.lot_size or 1)
            plan = _parse_manual_plan(t.notes, quantity=int(t.quantity or 0), lot_size=lot_size)
            remaining = _remaining_qty(t, plan)
            if remaining <= 0:
                continue
            segment = "BSE_FNO" if _is_sensex_name(t.symbol or "") else "NSE_FNO"
            option_type = "CALL" if (t.option_type or "").upper() == "CE" else "PUT"

            async def _sell_qty(qty: int) -> dict:
                return await client.place_order(
                    security_id=t.security_id,
                    exchange_segment=segment,
                    transaction_type="SELL",
                    product_type="MARGIN",
                    order_type="MARKET",
                    quantity=qty,
                    price=0,
                    validity="DAY",
                    drv_expiry_date=t.expiry_date.strftime("%Y-%m-%d") if t.expiry_date else None,
                    drv_option_type=option_type,
                    drv_strike_price=t.strike_price,
                )

            # T1 partial exit
            t1_price = plan.get("t1_price")
            t1_qty = int(plan.get("t1_qty") or 0)
            if (not plan.get("t1_done")) and t1_price and t1_qty > 0 and float(ltp) >= float(t1_price):
                qty = min(t1_qty, remaining)
                if qty > 0:
                    sell_res = await _sell_qty(qty)
                    if not sell_res or sell_res.get("error"):
                        events.append({"trade_id": t.id, "event": "t1_failed", "detail": sell_res})
                    else:
                        plan["executed_qty"] = int(plan.get("executed_qty") or 0) + qty
                        plan["t1_done"] = True
                        remaining = _remaining_qty(t, plan)
                        events.append({"trade_id": t.id, "event": "t1_hit", "qty": qty, "ltp": ltp})
                        if t.sl_order_id:
                            await client.cancel_forever_order(t.sl_order_id)
                        if remaining > 0:
                            new_sl = await client.create_forever_order(
                                security_id=t.security_id,
                                exchange_segment=segment,
                                transaction_type="SELL",
                                product_type="MARGIN",
                                order_type="MARKET",
                                order_flag="SINGLE",
                                quantity=remaining,
                                price=0,
                                trigger_price=float(t.sl_price or 0),
                            )
                            if new_sl and not new_sl.get("error"):
                                t.sl_order_id = new_sl.get("orderId", "")

            # T2 full remaining
            t2_price = plan.get("t2_price")
            t2_qty = int(plan.get("t2_qty") or 0)
            remaining = _remaining_qty(t, plan)
            if (not plan.get("t2_done")) and t2_price and remaining > 0 and float(ltp) >= float(t2_price):
                qty = min(max(t2_qty, 0), remaining) if t2_qty > 0 else remaining
                qty = qty if qty > 0 else remaining
                sell_res = await _sell_qty(qty)
                if not sell_res or sell_res.get("error"):
                    events.append({"trade_id": t.id, "event": "t2_failed", "detail": sell_res})
                else:
                    plan["executed_qty"] = int(plan.get("executed_qty") or 0) + qty
                    plan["t2_done"] = True
                    events.append({"trade_id": t.id, "event": "t2_hit", "qty": qty, "ltp": ltp})
                    if t.sl_order_id:
                        await client.cancel_forever_order(t.sl_order_id)
                    if _remaining_qty(t, plan) <= 0:
                        t.status = "closed"
                        t.exit_date = date.today()
                        if t.signal_id:
                            sig = db.query(Signal).filter(Signal.id == t.signal_id).first()
                            if sig:
                                sig.is_tracked = False
                                sig.track_status = "TARGET-HIT"
                                sig.track_status_reason = "T2 target reached"
                                sig.track_last_updated = datetime.utcnow()
            t.notes = _manual_plan_to_notes(plan)

        db.commit()
    finally:
        await client.close()
    return {"ok": True, "market_open": True, "checked": len(trades), "events": events}


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
