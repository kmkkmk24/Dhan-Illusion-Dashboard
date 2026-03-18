import asyncio
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import Signal, SignalHistory, Instrument, Trade
from backend.models.schemas import SignalOut, SignalDetailOut, SignalHistoryOut
from backend.services.dhan_client import DhanClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])

_option_cost_cache: dict[str, dict] = {}
_option_cost_cache_date: date | None = None


class CmpRequest(BaseModel):
    security_ids: list[str]


@router.get("/", response_model=list[SignalOut])
def list_signals(
    section: str = Query(None, description="Filter by section: swing or fno"),
    status: str = Query(None, description="Filter by status: active, exploded, invalidated, dismissed. Omit for all."),
    db: Session = Depends(get_db),
):
    """List signals filtered by section and status."""
    query = db.query(Signal)
    if section:
        query = query.filter(Signal.section == section)
    if status:
        query = query.filter(Signal.status == status)
    return query.order_by(Signal.current_score.desc()).all()


@router.get("/{signal_id}", response_model=SignalDetailOut)
def get_signal_detail(signal_id: int, db: Session = Depends(get_db)):
    """Get full signal detail including history and past occurrences."""
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Signal not found")

    history = (
        db.query(SignalHistory)
        .filter(SignalHistory.signal_id == signal_id)
        .order_by(SignalHistory.date)
        .all()
    )

    past_occurrences = (
        db.query(Signal)
        .filter(
            Signal.security_id == signal.security_id,
            Signal.id != signal_id,
        )
        .order_by(Signal.first_detected_date)
        .all()
    )

    return SignalDetailOut(
        **{c.name: getattr(signal, c.name) for c in Signal.__table__.columns},
        history=[SignalHistoryOut.model_validate(h) for h in history],
        past_occurrences=[SignalOut.model_validate(o) for o in past_occurrences],
    )


@router.post("/cmp")
async def get_cmp(data: CmpRequest):
    """Fetch Current Market Price for a list of security IDs."""
    from backend.services.dhan_client import DhanClient
    import logging
    logger = logging.getLogger(__name__)

    if not data.security_ids:
        return {}

    client = DhanClient()
    try:
        result = await client.get_market_quote_ltp(data.security_ids, "NSE_EQ")
        await client.close()

        if not result:
            return {}

        cmp_map = {}

        # Response format: {"data": {"NSE_EQ": {"11536": {"last_price": 4520}}}, "status": "success"}
        data_obj = result.get("data", result)

        if isinstance(data_obj, dict):
            for segment_key, segment_data in data_obj.items():
                if isinstance(segment_data, dict):
                    for sec_id, price_data in segment_data.items():
                        if isinstance(price_data, dict):
                            ltp = price_data.get("last_price", price_data.get("ltp", price_data.get("lastTradedPrice")))
                            if ltp is not None:
                                cmp_map[str(sec_id)] = float(ltp)
                        elif isinstance(price_data, (int, float)):
                            cmp_map[str(sec_id)] = float(price_data)

        logger.info(f"CMP fetched for {len(cmp_map)} instruments")
        return cmp_map
    except Exception as e:
        logger.error(f"CMP fetch error: {e}")
        await client.close()
        return {}


class OptionCostItem(BaseModel):
    security_id: str
    direction: str  # "CE" or "PE"


class OptionCostRequest(BaseModel):
    items: list[OptionCostItem]


def _get_nearest_expiry(expiries: list[str]) -> str | None:
    """Stock options only have monthly expiries — return the nearest valid one."""
    today = date.today()
    for exp_str in sorted(expiries):
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if exp_date >= today:
            return exp_str
    return None


def _extract_atm_cost(chain: dict, direction: str, lot_size: int) -> dict | None:
    chain_data = chain.get("data", {})
    spot_price = chain_data.get("last_price", 0)
    oc = chain_data.get("oc", {})
    if not oc or not spot_price:
        return None

    atm_strike, min_diff = None, float("inf")
    for strike_str in oc.keys():
        try:
            diff = abs(float(strike_str) - spot_price)
        except ValueError:
            continue
        if diff < min_diff:
            min_diff = diff
            atm_strike = strike_str

    if not atm_strike:
        return None

    side = "ce" if direction in ("CE", "manual", "consolidation") else "pe"
    premium = oc[atm_strike].get(side, {}).get("last_price", 0)
    if not premium or premium <= 0:
        return None

    return {
        "premium": round(premium, 2),
        "strike": float(atm_strike),
        "lot_size": lot_size,
        "lot_cost": round(premium * lot_size, 2),
        "expiry": "",
        "side": side.upper(),
    }


def _invalidate_cache_if_stale():
    global _option_cost_cache, _option_cost_cache_date
    today = date.today()
    if _option_cost_cache_date != today:
        _option_cost_cache = {}
        _option_cost_cache_date = today


@router.post("/option-cost")
async def get_option_cost(data: OptionCostRequest, db: Session = Depends(get_db)):
    """Return cached option costs instantly. Uncached items are fetched sequentially."""
    _invalidate_cache_if_stale()

    if not data.items:
        return {}

    cached = {}
    uncached_items = []
    for item in data.items:
        if item.security_id in _option_cost_cache:
            cached[item.security_id] = _option_cost_cache[item.security_id]
        else:
            uncached_items.append(item)

    if not uncached_items:
        return cached

    client = DhanClient()
    try:
        expiries = await client.get_expiry_list(uncached_items[0].security_id, "NSE_EQ")
        nearest_expiry = _get_nearest_expiry(expiries) if expiries else None
        if not nearest_expiry:
            await client.close()
            return cached

        for i, item in enumerate(uncached_items):
            try:
                if i > 0:
                    await asyncio.sleep(1.5)
                instrument = db.query(Instrument).filter(
                    Instrument.security_id == item.security_id
                ).first()
                lot_size = instrument.lot_size if instrument and instrument.lot_size > 1 else 1
                chain = await client.get_option_chain(item.security_id, "NSE_EQ", nearest_expiry)
                if chain:
                    result = _extract_atm_cost(chain, item.direction, lot_size)
                    if result:
                        result["expiry"] = nearest_expiry
                        _option_cost_cache[item.security_id] = result
                        cached[item.security_id] = result
            except Exception as e:
                logger.error(f"Option cost error for {item.security_id}: {e}")

        await client.close()
        return cached
    except Exception as e:
        logger.error(f"Option cost error: {e}")
        await client.close()
        return cached


@router.post("/option-cost-stream")
async def stream_option_cost(data: OptionCostRequest, db: Session = Depends(get_db)):
    """SSE stream — emits each stock's option cost as it's fetched, so the UI updates progressively."""
    import json

    _invalidate_cache_if_stale()

    cached_items = []
    uncached_items = []
    for item in data.items:
        if item.security_id in _option_cost_cache:
            cached_items.append(item)
        else:
            uncached_items.append(item)

    instrument_map = {}
    for item in data.items:
        inst = db.query(Instrument).filter(Instrument.security_id == item.security_id).first()
        instrument_map[item.security_id] = inst.lot_size if inst and inst.lot_size > 1 else 1

    async def event_generator():
        for item in cached_items:
            payload = json.dumps({item.security_id: _option_cost_cache[item.security_id]})
            yield f"data: {payload}\n\n"

        if not uncached_items:
            yield 'data: {"done": true}\n\n'
            return

        client = DhanClient()
        try:
            expiries = await client.get_expiry_list(uncached_items[0].security_id, "NSE_EQ")
            nearest_expiry = _get_nearest_expiry(expiries) if expiries else None

            if not nearest_expiry:
                yield 'data: {"done": true}\n\n'
                await client.close()
                return

            for i, item in enumerate(uncached_items):
                try:
                    if i > 0:
                        await asyncio.sleep(1.5)
                    chain = await client.get_option_chain(item.security_id, "NSE_EQ", nearest_expiry)
                    if chain:
                        result = _extract_atm_cost(chain, item.direction, instrument_map[item.security_id])
                        if result:
                            result["expiry"] = nearest_expiry
                            _option_cost_cache[item.security_id] = result
                            payload = json.dumps({item.security_id: result})
                            yield f"data: {payload}\n\n"
                except Exception as e:
                    logger.error(f"SSE option cost error for {item.security_id}: {e}")

            await client.close()
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
            await client.close()

        yield 'data: {"done": true}\n\n'

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class ManualAddRequest(BaseModel):
    security_id: str
    symbol: str


@router.post("/manual-add")
async def manual_add_stock(data: ManualAddRequest, db: Session = Depends(get_db)):
    """Manually add a stock to F&O tracking — system will analyze and populate scores."""
    from backend.services.fno_scanner import scan_fno_manual_add
    from backend.services.sector_mapping import get_sectors_for_stock

    existing = db.query(Signal).filter(
        Signal.security_id == data.security_id,
        Signal.section == "fno",
        Signal.status == "active",
    ).first()

    if existing:
        return {"message": f"{data.symbol} is already being tracked", "signal_id": existing.id}

    result = await scan_fno_manual_add(db, data.security_id, data.symbol)

    past_count = db.query(Signal).filter(
        Signal.security_id == data.security_id,
    ).count()

    if result:
        sectors = get_sectors_for_stock(data.symbol)
        signal = Signal(
            security_id=data.security_id,
            symbol=data.symbol,
            section="fno",
            signal_type=result["direction"],
            occurrence_number=past_count + 1,
            first_detected_date=date.today(),
            last_updated_date=date.today(),
            current_score=result["score"],
            peak_score=result["score"],
            status="active",
            sector=result["sector"],
            close_price_at_detection=result["close_price"],
        )
    else:
        sectors = get_sectors_for_stock(data.symbol)
        signal = Signal(
            security_id=data.security_id,
            symbol=data.symbol,
            section="fno",
            signal_type="manual",
            occurrence_number=past_count + 1,
            first_detected_date=date.today(),
            last_updated_date=date.today(),
            current_score=0.0,
            peak_score=0.0,
            status="active",
            sector=sectors[0] if sectors else "Other",
            close_price_at_detection=0.0,
        )

    db.add(signal)
    db.commit()
    db.refresh(signal)

    return {
        "message": f"{data.symbol} added to F&O tracking",
        "signal_id": signal.id,
        "analysis": result,
    }


@router.post("/{signal_id}/track")
def toggle_track(signal_id: int, db: Session = Depends(get_db)):
    """Mark/unmark a signal as tracked (user wants to watch this setup)."""
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Signal not found")

    if signal.status == "tracking":
        signal.status = "active"
        msg = f"{signal.symbol} unmarked from tracking"
    else:
        signal.status = "tracking"
        msg = f"{signal.symbol} marked for tracking"

    db.commit()
    return {"message": msg, "status": signal.status}


@router.post("/{signal_id}/dismiss")
def dismiss_signal(signal_id: int, db: Session = Depends(get_db)):
    """Manually dismiss a signal."""
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Signal not found")

    signal.status = "dismissed"
    db.commit()
    return {"message": f"Signal {signal_id} dismissed", "symbol": signal.symbol}


@router.post("/{signal_id}/reactivate")
def reactivate_signal(signal_id: int, db: Session = Depends(get_db)):
    """Reactivate a dismissed or invalidated signal."""
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Signal not found")

    signal.status = "active"
    signal.invalidation_reason = None
    db.commit()
    return {"message": f"Signal {signal_id} reactivated", "symbol": signal.symbol}


def _get_liquidity_warnings(recommended: dict, all_strikes: list[dict]) -> list[dict]:
    """Generate warnings about liquidity issues on the recommended strike."""
    warnings = []
    if recommended["oi"] == 0:
        warnings.append({
            "level": "high",
            "message": "Zero Open Interest — no market depth, very hard to exit",
        })
    elif recommended["oi"] < 10000:
        warnings.append({
            "level": "medium",
            "message": f"Low OI ({recommended['oi']:,}) — thin liquidity, may face slippage",
        })

    if recommended["volume"] == 0:
        warnings.append({
            "level": "high",
            "message": "Zero Volume today — no active trading on this strike",
        })
    elif recommended["volume"] < 1000:
        warnings.append({
            "level": "medium",
            "message": f"Low volume ({recommended['volume']:,}) — limited trading activity",
        })

    if recommended["spread_pct"] > 5.0:
        warnings.append({
            "level": "high",
            "message": f"Very wide spread ({recommended['spread_pct']:.1f}%) — high slippage cost",
        })
    elif recommended["spread_pct"] > 2.0:
        warnings.append({
            "level": "medium",
            "message": f"Wide spread ({recommended['spread_pct']:.1f}%) — consider limit orders",
        })

    max_oi_strike = max(all_strikes, key=lambda x: x["oi"]) if all_strikes else None
    if max_oi_strike and max_oi_strike["oi"] > recommended["oi"] * 10 and max_oi_strike["strike"] != recommended["strike"]:
        warnings.append({
            "level": "info",
            "message": f"Highest OI is at {max_oi_strike['strike']} strike ({max_oi_strike['oi']:,} OI) — consider for better liquidity",
        })

    return warnings


@router.get("/{signal_id}/option-analysis")
async def get_option_analysis(
    signal_id: int,
    expiry: str = Query(None, description="Specific expiry date (YYYY-MM-DD). If omitted, picks optimal expiry."),
    db: Session = Depends(get_db),
):
    """
    Full option chain analysis for a signal.
    Returns Greeks, IV surface, recommended strike, SL, target, and hold duration.
    """
    import math

    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    instrument = db.query(Instrument).filter(
        Instrument.security_id == signal.security_id
    ).first()
    lot_size = instrument.lot_size if instrument and instrument.lot_size > 1 else 1

    client = DhanClient()
    try:
        expiries = await client.get_expiry_list(signal.security_id, "NSE_EQ")
        if not expiries:
            raise HTTPException(status_code=404, detail="No expiries found")

        today = date.today()
        valid_expiries = []
        for exp_str in sorted(expiries):
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                if exp_date >= today:
                    dte = (exp_date - today).days
                    valid_expiries.append({"date": exp_str, "dte": dte})
            except ValueError:
                continue

        if not valid_expiries:
            raise HTTPException(status_code=404, detail="No valid expiries")

        if expiry:
            target_exp = next((e for e in valid_expiries if e["date"] == expiry), None)
            if not target_exp:
                raise HTTPException(status_code=400, detail=f"Expiry {expiry} not available")
        else:
            # Stock options only have monthly expiries — nearest month is always first
            target_exp = valid_expiries[0]

        chain = None
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(3)
            chain = await client.get_option_chain(
                signal.security_id, "NSE_EQ", target_exp["date"]
            )
            if chain and chain.get("data", {}).get("oc"):
                break
        await client.close()

        if not chain or not chain.get("data", {}).get("oc"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=502,
                content={
                    "detail": f"Option chain unavailable for {target_exp['date']} — try a different expiry.",
                    "available_expiries": valid_expiries[:6],
                    "failed_expiry": target_exp["date"],
                },
            )

        chain_data = chain.get("data", {})
        spot_price = chain_data.get("last_price", 0)
        oc = chain_data.get("oc", {})

        if not spot_price:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=502,
                content={
                    "detail": f"No spot price data for {target_exp['date']} — try a different expiry.",
                    "available_expiries": valid_expiries[:6],
                    "failed_expiry": target_exp["date"],
                },
            )

        direction = signal.signal_type
        side = "ce" if direction in ("CE", "manual", "consolidation") else "pe"
        dte = target_exp["dte"]

        strikes_data = []
        for strike_str, strike_data in oc.items():
            try:
                strike_val = float(strike_str)
            except ValueError:
                continue
            opt = strike_data.get(side, {})
            if not opt or not opt.get("last_price"):
                continue

            greeks = opt.get("greeks", {})
            iv = opt.get("implied_volatility", 0)
            premium = opt.get("last_price", 0)
            oi = opt.get("oi", 0)
            volume = opt.get("volume", 0)
            bid = opt.get("top_bid_price", 0)
            ask = opt.get("top_ask_price", 0)
            spread = round(ask - bid, 2) if ask and bid else 0
            spread_pct = round((spread / premium * 100), 1) if premium > 0 else 0

            moneyness = (strike_val - spot_price) / spot_price * 100
            if side == "pe":
                moneyness = -moneyness

            intrinsic = max(0, spot_price - strike_val) if side == "ce" else max(0, strike_val - spot_price)
            time_value = max(0, premium - intrinsic)

            opt_security_id = str(opt.get("security_id", ""))

            strikes_data.append({
                "strike": strike_val,
                "option_security_id": opt_security_id,
                "premium": round(premium, 2),
                "lot_cost": round(premium * lot_size, 2),
                "iv": round(iv, 2) if iv else None,
                "delta": round(greeks.get("delta", 0), 4) if greeks else None,
                "gamma": round(greeks.get("gamma", 0), 6) if greeks else None,
                "theta": round(greeks.get("theta", 0), 4) if greeks else None,
                "vega": round(greeks.get("vega", 0), 4) if greeks else None,
                "oi": oi,
                "volume": volume,
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "spread_pct": spread_pct,
                "moneyness": round(moneyness, 2),
                "intrinsic": round(intrinsic, 2),
                "time_value": round(time_value, 2),
            })

        strikes_data.sort(key=lambda x: x["strike"])

        atm_strike = min(strikes_data, key=lambda x: abs(x["moneyness"]))

        max_oi = max((x["oi"] for x in strikes_data), default=1) or 1
        max_vol = max((x["volume"] for x in strikes_data), default=1) or 1
        total_oi = sum(x["oi"] for x in strikes_data) or 1

        def _score_strike(s):
            """
            Smart money strike scoring — liquidity is king.
            Weights: OI 25, Volume 20, Delta 20, Spread 10, IV 10, Moneyness 10, Leverage 5 = 100
            """
            score = 0
            abs_delta = abs(s["delta"]) if s["delta"] else 0

            # --- OI (25 pts) — where is institutional money positioned? ---
            oi_pct = s["oi"] / max_oi if max_oi > 0 else 0
            oi_concentration = s["oi"] / total_oi if total_oi > 0 else 0
            score += 20 * oi_pct
            if oi_concentration >= 0.15:
                score += 5
            elif oi_concentration >= 0.05:
                score += 3

            # --- Volume (20 pts) — active trading = easy entry/exit ---
            vol_pct = s["volume"] / max_vol if max_vol > 0 else 0
            score += 15 * vol_pct
            if s["volume"] > 0 and s["oi"] > 0:
                score += 5
            elif s["volume"] == 0 and s["oi"] == 0:
                score -= 10

            # --- Delta (20 pts) — sweet spot 0.40-0.60 ---
            if 0.40 <= abs_delta <= 0.60:
                score += 20
            elif 0.30 <= abs_delta <= 0.70:
                score += 14
            elif abs_delta > 0.70:
                score += 8

            # --- Spread (10 pts) — tighter spread = less slippage ---
            if s["spread_pct"] < 1.0:
                score += 10
            elif s["spread_pct"] < 3.0:
                score += 6
            elif s["spread_pct"] < 5.0:
                score += 3

            # --- IV (10 pts) — prefer near-ATM IV, avoid inflated strikes ---
            if s["iv"] and atm_strike["iv"] and atm_strike["iv"] > 0:
                iv_ratio = s["iv"] / atm_strike["iv"]
                if 0.90 <= iv_ratio <= 1.10:
                    score += 10
                elif 0.80 <= iv_ratio <= 1.20:
                    score += 6
                elif iv_ratio < 0.85:
                    score += 8

            # --- Moneyness (10 pts) — slight OTM to ATM preferred ---
            abs_m = abs(s["moneyness"])
            if abs_m <= 2:
                score += 10
            elif abs_m <= 5:
                score += 7
            elif abs_m <= 8:
                score += 3

            # --- Leverage (5 pts) — capital efficiency ---
            if s["premium"] > 0:
                leverage = spot_price / (s["premium"] * lot_size) if lot_size > 0 else 0
                if leverage > 0:
                    score += min(5, leverage * 0.5)

            return round(score, 1)

        for s in strikes_data:
            s["recommendation_score"] = _score_strike(s)

        recommended = max(strikes_data, key=lambda x: x["recommendation_score"])

        abs_delta = abs(recommended["delta"]) if recommended["delta"] else 0.5
        abs_moneyness = abs(recommended["moneyness"])
        strike = recommended["strike"]
        premium = recommended["premium"]

        # Fetch daily candles for support/resistance and ATR
        client2 = DhanClient()
        daily_df = await client2.get_historical_daily_data(
            security_id=signal.security_id,
            exchange_segment="NSE_EQ",
            instrument="EQUITY",
            from_date=today - timedelta(days=90),
            to_date=today,
        )
        await client2.close()

        support = round(spot_price * 0.95, 2)
        resistance = round(spot_price * 1.05, 2)
        atr_val = spot_price * 0.02
        swing_low = support
        swing_high = resistance

        if daily_df is not None and len(daily_df) >= 20:
            closes = daily_df["close"].astype(float).tolist()
            highs = daily_df["high"].astype(float).tolist()
            lows = daily_df["low"].astype(float).tolist()

            # ATR (14-period)
            trs = []
            for i in range(1, len(closes)):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                trs.append(tr)
            atr_val = sum(trs[-14:]) / 14 if len(trs) >= 14 else sum(trs) / len(trs)

            swing_low = min(lows[-20:])
            swing_high = max(highs[-20:])

            # Support: max of (swing low, spot - 1.5×ATR)
            support = round(max(swing_low, spot_price - 1.5 * atr_val), 2)
            # Resistance: min of (swing high, spot + 2×ATR)
            resistance = round(min(swing_high, spot_price + 2.0 * atr_val), 2)

        atr_val = round(atr_val, 2)

        # --- Target (stock price) ---
        if side == "ce":
            target_price = round(max(strike * 1.02, resistance, spot_price + 2.0 * atr_val), 2)
            # SL: below recent support OR 1.5×ATR below spot, whichever is tighter
            sl_price = round(max(support, spot_price - 1.5 * atr_val), 2)
        else:
            target_price = round(min(strike * 0.98, support, spot_price - 2.0 * atr_val), 2)
            sl_price = round(min(resistance, spot_price + 1.5 * atr_val), 2)

        # --- Buy range: ±8% around current premium (human-friendly range) ---
        buy_range_low = round(premium * 0.92, 2)
        buy_range_high = round(premium * 1.08, 2)

        # --- Premium target using Greeks ---
        stock_move = abs(target_price - spot_price)
        gamma_boost = 0.5 * abs(recommended["gamma"] or 0) * stock_move ** 2 if recommended["gamma"] else 0
        premium_gain = abs_delta * stock_move + gamma_boost
        hold_est_days = min(dte * 0.35, 10)
        theta_cost = abs(recommended["theta"] or 0) * hold_est_days
        premium_target = round(max(premium * 1.4, premium + premium_gain - theta_cost), 2)

        # --- SL premium: what the option would be worth if stock hits SL ---
        sl_stock_move = abs(spot_price - sl_price)
        sl_premium_loss = abs_delta * sl_stock_move
        premium_sl = round(max(0.5, premium - sl_premium_loss), 2)

        # --- Partial profit booking levels ---
        partial_1_stock = round(spot_price + (target_price - spot_price) * 0.5, 2) if side == "ce" else round(spot_price - (spot_price - target_price) * 0.5, 2)
        partial_1_move = abs(partial_1_stock - spot_price)
        partial_1_premium = round(premium + abs_delta * partial_1_move, 2)

        if dte <= 7:
            hold_days_min, hold_days_max = 1, 3
        elif dte <= 15:
            hold_days_min, hold_days_max = 2, 5
        elif dte <= 30:
            hold_days_min, hold_days_max = 3, 8
        else:
            hold_days_min, hold_days_max = 3, 12

        theta_daily_cost = abs(recommended["theta"] or 0) * lot_size
        max_profit = round((premium_target - premium) * lot_size, 2) if premium_target > premium else 0
        max_loss = round((premium - premium_sl) * lot_size, 2) if premium > premium_sl else round(premium * 0.3 * lot_size, 2)
        risk_reward = round(max_profit / max_loss, 2) if max_loss > 0 else 0

        # --- Trade advice summary ---
        if risk_reward >= 3:
            conviction = "High"
            conviction_reason = f"R:R of 1:{risk_reward} with strong technical setup"
        elif risk_reward >= 2:
            conviction = "Medium"
            conviction_reason = f"Decent R:R of 1:{risk_reward}, watch for momentum confirmation"
        else:
            conviction = "Low"
            conviction_reason = f"Weak R:R of 1:{risk_reward}, consider a closer strike or wait for better entry"

        analysis = {
            "signal": {
                "id": signal.id,
                "symbol": signal.symbol,
                "direction": direction,
                "score": signal.current_score,
                "sector": signal.sector,
                "detected_at": signal.close_price_at_detection,
                "status": signal.status,
            },
            "spot_price": spot_price,
            "lot_size": lot_size,
            "expiry": target_exp["date"],
            "dte": dte,
            "side": side.upper(),
            "available_expiries": valid_expiries[:6],

            "technicals": {
                "atr": atr_val,
                "support": support,
                "resistance": resistance,
                "swing_low": round(swing_low, 2),
                "swing_high": round(swing_high, 2),
            },

            "recommendation": {
                "strike": recommended["strike"],
                "option_security_id": recommended.get("option_security_id", ""),
                "premium": recommended["premium"],
                "lot_cost": recommended["lot_cost"],
                "buy_range": f"₹{buy_range_low} – ₹{buy_range_high}",
                "buy_range_low": buy_range_low,
                "buy_range_high": buy_range_high,
                "delta": recommended["delta"],
                "gamma": recommended["gamma"],
                "theta": recommended["theta"],
                "vega": recommended["vega"],
                "iv": recommended["iv"],
                "oi": recommended["oi"],
                "volume": recommended["volume"],
                "score": recommended["recommendation_score"],
                "moneyness": recommended["moneyness"],
                "spread_pct": recommended["spread_pct"],
                "target_stock_price": target_price,
                "sl_stock_price": sl_price,
                "sl_reason": f"Below {atr_val:.1f} ATR support at {support} / swing low {round(swing_low,2)}" if side == "ce" else f"Above {atr_val:.1f} ATR resistance at {resistance} / swing high {round(swing_high,2)}",
                "target_premium": premium_target,
                "sl_premium": premium_sl,
                "partial_book_at": partial_1_stock,
                "partial_book_premium": partial_1_premium,
                "hold_days": f"{hold_days_min}-{hold_days_max}",
                "theta_daily_cost": round(theta_daily_cost, 2),
                "max_profit": max_profit,
                "max_loss": max_loss,
                "risk_reward": risk_reward,
                "conviction": conviction,
                "conviction_reason": conviction_reason,
            },

            "strikes": [s for s in strikes_data if abs(s["moneyness"]) <= 12],
            "atm_strike": atm_strike["strike"],
            "atm_iv": atm_strike["iv"],
            "liquidity_warnings": _get_liquidity_warnings(recommended, strikes_data),
        }

        # Check for open trade on this signal
        open_trade = (
            db.query(Trade)
            .filter(Trade.signal_id == signal_id, Trade.status == "open")
            .order_by(Trade.id.desc())
            .first()
        )
        if open_trade:
            analysis["active_trade"] = {
                "id": open_trade.id,
                "entry_premium": open_trade.entry_price,
                "strike": open_trade.strike_price,
                "side": open_trade.option_type,
                "expiry": open_trade.expiry_date.strftime("%Y-%m-%d") if open_trade.expiry_date else "",
                "lots": (open_trade.quantity // open_trade.lot_size) if open_trade.lot_size else 1,
                "quantity": open_trade.quantity,
                "entry_date": open_trade.entry_date.strftime("%Y-%m-%d") if open_trade.entry_date else "",
                "sl_price": open_trade.sl_price,
                "target_price": open_trade.target_price,
            }

        return analysis

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Option analysis error for signal {signal_id}: {e}")
        await client.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{signal_id}/expiry-scores")
async def get_expiry_scores(
    signal_id: int,
    db: Session = Depends(get_db),
):
    """
    Quick-score all available expiries for a signal.
    Returns conviction (high/medium/low) for each expiry based on
    best strike's liquidity, spread, and risk:reward.
    """
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    instrument = db.query(Instrument).filter(
        Instrument.security_id == signal.security_id
    ).first()
    lot_size = instrument.lot_size if instrument and instrument.lot_size > 1 else 1

    client = DhanClient()
    try:
        expiries = await client.get_expiry_list(signal.security_id, "NSE_EQ")
        if not expiries:
            return {"scores": {}}

        today = date.today()
        valid_expiries = []
        for exp_str in sorted(expiries):
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                if exp_date >= today:
                    dte = (exp_date - today).days
                    valid_expiries.append({"date": exp_str, "dte": dte})
            except ValueError:
                continue

        direction = signal.signal_type
        side = "ce" if direction in ("CE", "manual", "consolidation") else "pe"

        scores = {}
        for i, exp in enumerate(valid_expiries[:6]):
            if i > 0:
                await asyncio.sleep(3.2)

            try:
                chain = await client.get_option_chain(
                    signal.security_id, "NSE_EQ", exp["date"]
                )
                if not chain or not isinstance(chain.get("data"), dict):
                    scores[exp["date"]] = {"conviction": "none", "reason": "No data"}
                    continue

                chain_data = chain["data"]
                spot = chain_data.get("last_price", 0)
                oc = chain_data.get("oc", {})
                if not spot or not oc:
                    scores[exp["date"]] = {"conviction": "none", "reason": "No data"}
                    continue

                best_score = 0
                best_spread = 999
                best_oi = 0
                best_premium = 0
                total_oi = sum(
                    int(sd.get(side, {}).get("oi", 0))
                    for sd in oc.values() if isinstance(sd, dict)
                )
                max_oi = max(
                    (int(sd.get(side, {}).get("oi", 0)) for sd in oc.values() if isinstance(sd, dict)),
                    default=0,
                )

                for strike_str, strike_data in oc.items():
                    try:
                        strike_val = float(strike_str)
                    except (ValueError, TypeError):
                        continue
                    opt = strike_data.get(side, {})
                    if not opt or not opt.get("last_price"):
                        continue

                    premium = opt.get("last_price", 0)
                    oi = int(opt.get("oi", 0))
                    volume = int(opt.get("volume", 0))
                    bid = opt.get("top_bid_price", 0) or 0
                    ask = opt.get("top_ask_price", 0) or 0
                    spread = round(ask - bid, 2) if ask and bid else 0
                    spread_pct = round((spread / premium * 100), 1) if premium > 0 else 99
                    greeks = opt.get("greeks", {})
                    delta = abs(float(greeks.get("delta", 0) or 0))
                    moneyness = abs((strike_val - spot) / spot * 100)

                    if moneyness > 10:
                        continue

                    # Quick scoring: OI + delta sweet spot + spread
                    oi_score = (oi / max_oi * 25) if max_oi > 0 else 0
                    delta_score = 20 if 0.35 <= delta <= 0.65 else (12 if 0.25 <= delta <= 0.75 else 5)
                    spread_score = 10 if spread_pct < 2 else (5 if spread_pct < 5 else 0)
                    vol_score = 10 if volume > 0 else 0
                    total = oi_score + delta_score + spread_score + vol_score

                    if total > best_score:
                        best_score = total
                        best_spread = spread_pct
                        best_oi = oi
                        best_premium = premium

                scores[exp["date"]] = {
                    "best_score": round(best_score, 1),
                    "best_spread": best_spread,
                    "best_oi": best_oi,
                    "dte": exp["dte"],
                }
            except Exception as exc:
                logger.warning(f"Expiry score error for {exp['date']}: {exc}")
                scores[exp["date"]] = {"conviction": "none", "reason": "Error", "best_score": 0}

        await client.close()

        # Relative conviction: rank expiries against each other
        scored = [k for k, v in scores.items() if v.get("best_score", 0) > 0]
        if scored:
            top_score = max(scores[k]["best_score"] for k in scored)
            for k in scored:
                s = scores[k]
                ratio = s["best_score"] / top_score if top_score > 0 else 0
                if ratio >= 0.85:
                    s["conviction"] = "high"
                    s["reason"] = f"Best setup — score {s['best_score']}, spread {s['best_spread']:.1f}%"
                elif ratio >= 0.6:
                    s["conviction"] = "medium"
                    s["reason"] = f"Decent — score {s['best_score']}, spread {s['best_spread']:.1f}%"
                else:
                    s["conviction"] = "low"
                    s["reason"] = f"Weaker — score {s['best_score']}, spread {s['best_spread']:.1f}%"

            # If only 1 expiry scored, use absolute thresholds as tiebreaker
            if len(scored) == 1:
                s = scores[scored[0]]
                if s["best_score"] >= 30:
                    s["conviction"] = "high"
                elif s["best_score"] >= 15:
                    s["conviction"] = "medium"

        return {"scores": scores}

    except Exception as e:
        logger.error(f"Expiry scores error for signal {signal_id}: {e}")
        await client.close()
        return {"scores": {}}


@router.get("/{signal_id}/live-advisory")
async def live_advisory(
    signal_id: int,
    entry_premium: float = Query(..., description="Premium at which you entered"),
    strike: float = Query(..., description="Strike price of your position"),
    side: str = Query("CE", description="CE or PE"),
    expiry: str = Query(..., description="Expiry date YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    Live advisory for an active option position.
    Fetches current market data and gives HOLD / EXIT / BOOK PROFIT advice.
    """
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    instrument = db.query(Instrument).filter(
        Instrument.security_id == signal.security_id
    ).first()
    lot_size = instrument.lot_size if instrument and instrument.lot_size > 1 else 1

    today = date.today()
    exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
    dte = max(0, (exp_date - today).days)

    # Check market hours
    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    mkt_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    is_market_open = (now_ist.weekday() < 5 and mkt_open <= now_ist <= mkt_close)

    if not is_market_open:
        return {
            "symbol": signal.symbol,
            "market_closed": True,
            "message": f"Market is closed. Live advisory available Mon-Fri 9:15 AM - 3:30 PM IST.",
            "entry_premium": entry_premium,
            "strike": strike,
            "side": side.upper(),
            "expiry": expiry,
            "dte": dte,
        }

    client = DhanClient()
    try:
        # Fetch current option chain for live premium and Greeks
        chain = await client.get_option_chain(signal.security_id, "NSE_EQ", expiry)
        if not chain or "data" not in chain or not isinstance(chain["data"], dict):
            raise HTTPException(status_code=502, detail="Could not fetch live option chain")

        chain_data = chain["data"]
        spot_price = float(chain_data.get("last_price", 0))
        if not spot_price:
            quote = await client.get_market_quote_ohlc([signal.security_id], "NSE_EQ")
            if quote and "data" in quote and isinstance(quote["data"], dict):
                for seg_key, seg_data in quote["data"].items():
                    if isinstance(seg_data, dict):
                        for sec_id, price_data in seg_data.items():
                            if isinstance(price_data, dict):
                                spot_price = float(price_data.get("last_price", 0))
                                if spot_price:
                                    break
                    if spot_price:
                        break

        # Find our strike in the chain (oc is dict keyed by strike string)
        oc = chain_data.get("oc", {})
        opt_key = "ce" if side.upper() == "CE" else "pe"
        our_strike_data = None

        for strike_str, strike_data in oc.items():
            try:
                strike_val = float(strike_str)
            except (ValueError, TypeError):
                continue
            if abs(strike_val - strike) < 0.01:
                opt = strike_data.get(opt_key, {})
                if not opt:
                    continue
                greeks = opt.get("greeks", {})
                our_strike_data = {
                    "ltp": float(opt.get("last_price", 0)),
                    "delta": float(greeks.get("delta", 0) or 0),
                    "theta": float(greeks.get("theta", 0) or 0),
                    "gamma": float(greeks.get("gamma", 0) or 0),
                    "iv": float(opt.get("implied_volatility", 0) or 0),
                    "oi": int(opt.get("oi", 0)),
                    "volume": int(opt.get("volume", 0)),
                    "bid": float(opt.get("top_bid_price", 0) or 0),
                    "ask": float(opt.get("top_ask_price", 0) or 0),
                }
                break

        if not our_strike_data:
            raise HTTPException(status_code=404, detail=f"Strike {strike} not found in chain")

        current_premium = our_strike_data["ltp"]
        pnl_per_lot = round((current_premium - entry_premium) * lot_size, 2)
        pnl_pct = round(((current_premium - entry_premium) / entry_premium) * 100, 1) if entry_premium > 0 else 0

        # Fetch daily data for ATR & support/resistance
        client2 = DhanClient()
        daily_df = await client2.get_historical_daily_data(
            security_id=signal.security_id,
            exchange_segment="NSE_EQ",
            instrument="EQUITY",
            from_date=today - timedelta(days=60),
            to_date=today,
        )
        await client2.close()

        atr_val = spot_price * 0.02
        support = round(spot_price * 0.95, 2)
        resistance = round(spot_price * 1.05, 2)
        day_high = spot_price
        day_low = spot_price

        if daily_df is not None and len(daily_df) >= 14:
            closes = daily_df["close"].astype(float).tolist()
            highs = daily_df["high"].astype(float).tolist()
            lows_list = daily_df["low"].astype(float).tolist()

            trs = []
            for i in range(1, len(closes)):
                tr = max(highs[i] - lows_list[i], abs(highs[i] - closes[i-1]), abs(lows_list[i] - closes[i-1]))
                trs.append(tr)
            atr_val = sum(trs[-14:]) / 14

            swing_low = min(lows_list[-20:]) if len(lows_list) >= 20 else min(lows_list)
            swing_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            support = round(max(swing_low, spot_price - 1.5 * atr_val), 2)
            resistance = round(min(swing_high, spot_price + 2.0 * atr_val), 2)
            day_high = round(highs[-1], 2) if highs else spot_price
            day_low = round(lows_list[-1], 2) if lows_list else spot_price

        # --- Decision Engine ---
        action = "HOLD"
        urgency = "normal"
        reasons = []

        abs_delta = abs(our_strike_data["delta"])
        theta = abs(our_strike_data["theta"])
        theta_daily_impact = round(theta / entry_premium * 100, 1) if entry_premium > 0 else 0

        # 1. Profit thresholds
        if pnl_pct >= 80:
            action = "EXIT — BOOK FULL PROFIT"
            urgency = "high"
            reasons.append(f"Premium up {pnl_pct}% — exceptional gain, lock it in")
        elif pnl_pct >= 50:
            action = "BOOK PARTIAL — Trail SL"
            urgency = "medium"
            reasons.append(f"Up {pnl_pct}% — book 50%, trail SL to entry for rest")
        elif pnl_pct >= 30:
            action = "HOLD — Move SL to cost"
            urgency = "normal"
            reasons.append(f"Up {pnl_pct}% — move SL to entry premium (₹{entry_premium})")

        # 2. Loss thresholds (stock-level SL)
        if side.upper() == "CE" and spot_price < support:
            action = "EXIT — SL HIT"
            urgency = "high"
            reasons.append(f"Stock broke support at {support}")
        elif side.upper() == "PE" and spot_price > resistance:
            action = "EXIT — SL HIT"
            urgency = "high"
            reasons.append(f"Stock broke resistance at {resistance}")

        if pnl_pct <= -40:
            action = "EXIT — CUT LOSS"
            urgency = "high"
            reasons.append(f"Down {abs(pnl_pct)}% — cut loss before further decay")
        elif pnl_pct <= -25:
            reasons.append(f"Down {abs(pnl_pct)}% — approaching SL zone")

        # 3. Theta burn risk
        if dte <= 3 and pnl_pct < 20:
            action = "EXIT — Expiry too close"
            urgency = "high"
            reasons.append(f"Only {dte} day(s) to expiry — theta will accelerate")
        elif dte <= 5 and theta_daily_impact > 5:
            reasons.append(f"Theta burning {theta_daily_impact}% of entry per day — time risk is real")

        # 4. Delta / momentum
        if side.upper() == "CE":
            if abs_delta >= 0.7:
                reasons.append(f"Delta {abs_delta:.2f} — deep ITM, moves almost 1:1 with stock. Strong position")
            elif abs_delta >= 0.4:
                reasons.append(f"Delta {abs_delta:.2f} — good momentum capture")
            elif abs_delta < 0.2:
                reasons.append(f"Delta {abs_delta:.2f} — very low. Stock needs a big move to recover")
        else:
            if abs_delta >= 0.7:
                reasons.append(f"Delta {abs_delta:.2f} — deep ITM, strong put position")
            elif abs_delta < 0.2:
                reasons.append(f"Delta {abs_delta:.2f} — very low. Uphill battle from here")

        # 5. If no reasons yet, give a holding reason
        if not reasons:
            if side.upper() == "CE":
                reasons.append(f"Stock at {spot_price} — above support {support}, below resistance {resistance}. Thesis intact")
            else:
                reasons.append(f"Stock at {spot_price} — above support {support}, below resistance {resistance}. Thesis intact")

        # Current SL premium (using delta × distance to support)
        if side.upper() == "CE":
            sl_move = max(0, spot_price - support)
        else:
            sl_move = max(0, resistance - spot_price)
        trail_sl_premium = round(max(0.5, current_premium - abs_delta * sl_move), 2)

        return {
            "symbol": signal.symbol,
            "spot_price": spot_price,
            "strike": strike,
            "side": side.upper(),
            "expiry": expiry,
            "dte": dte,
            "entry_premium": entry_premium,
            "current_premium": current_premium,
            "lot_size": lot_size,
            "pnl_per_lot": pnl_per_lot,
            "pnl_pct": pnl_pct,
            "action": action,
            "urgency": urgency,
            "reasons": reasons,
            "greeks": {
                "delta": our_strike_data["delta"],
                "theta": our_strike_data["theta"],
                "gamma": our_strike_data["gamma"],
                "iv": our_strike_data["iv"],
            },
            "market": {
                "bid": our_strike_data["bid"],
                "ask": our_strike_data["ask"],
                "oi": our_strike_data["oi"],
                "volume": our_strike_data["volume"],
                "day_high": day_high,
                "day_low": day_low,
                "support": support,
                "resistance": resistance,
                "atr": round(atr_val, 2),
            },
            "trail_sl_premium": trail_sl_premium,
            "theta_daily_pct": theta_daily_impact,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Live advisory error for signal {signal_id}: {e}")
        await client.close()
        raise HTTPException(status_code=500, detail=str(e))


class PlaceTradeRequest(BaseModel):
    signal_id: int
    option_security_id: str
    strike: float
    side: str  # CE or PE
    expiry: str  # YYYY-MM-DD
    lots: int = 1
    limit_price: float
    sl_trigger_price: float
    target_price: float
    lot_size: int


@router.post("/place-trade")
async def place_trade(req: PlaceTradeRequest, db: Session = Depends(get_db)):
    """
    Place a limit buy order + forever SL order + forever target order.
    All three are placed; if buy fails, SL/target are not placed.
    """
    signal = db.query(Signal).filter(Signal.id == req.signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    quantity = req.lots * req.lot_size
    option_type = "CALL" if req.side.upper() == "CE" else "PUT"
    import re
    safe_strike = str(int(req.strike)) if req.strike == int(req.strike) else str(req.strike).replace(".", "p")
    correlation_base = re.sub(r'[^a-zA-Z0-9_-]', '', f"ill-{signal.symbol[:8]}-{safe_strike}")

    # Detect market hours (NSE: 9:15 AM - 3:30 PM IST, Mon-Fri)
    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    is_market_open = (now_ist.weekday() < 5 and market_open <= now_ist <= market_close)

    client = DhanClient()
    results = {"buy": None, "sl": None, "target": None, "errors": [], "is_amo": not is_market_open}

    try:
        # 1. Place LIMIT BUY order (AMO if market is closed)
        buy_result = await client.place_order(
            security_id=req.option_security_id,
            exchange_segment="NSE_FNO",
            transaction_type="BUY",
            product_type="MARGIN",
            order_type="LIMIT",
            quantity=quantity,
            price=req.limit_price,
            validity="DAY",
            correlation_id=f"{correlation_base}-buy",
            drv_expiry_date=req.expiry,
            drv_option_type=option_type,
            drv_strike_price=req.strike,
            after_market_order=not is_market_open,
        )

        if buy_result and buy_result.get("error"):
            results["errors"].append(f"Buy order failed: {buy_result.get('detail', 'Unknown error')}")
            await client.close()
            return JSONResponse(status_code=400, content=results)

        results["buy"] = buy_result

        # 2. Place Forever SL order (SELL when premium drops to SL)
        sl_result = await client.create_forever_order(
            security_id=req.option_security_id,
            exchange_segment="NSE_FNO",
            transaction_type="SELL",
            product_type="MARGIN",
            order_type="MARKET",
            order_flag="SINGLE",
            quantity=quantity,
            price=0,
            trigger_price=req.sl_trigger_price,
            correlation_id=f"{correlation_base}-sl",
        )

        if sl_result and sl_result.get("error"):
            results["errors"].append(f"SL order failed: {sl_result.get('detail', 'Unknown error')} — Place manually!")
        else:
            results["sl"] = sl_result

        # 3. Place Forever Target order (SELL when premium rises to target)
        target_result = await client.create_forever_order(
            security_id=req.option_security_id,
            exchange_segment="NSE_FNO",
            transaction_type="SELL",
            product_type="MARGIN",
            order_type="LIMIT",
            order_flag="SINGLE",
            quantity=quantity,
            price=req.target_price,
            trigger_price=req.target_price,
            correlation_id=f"{correlation_base}-tgt",
        )

        if target_result and target_result.get("error"):
            results["errors"].append(f"Target order failed: {target_result.get('detail', 'Unknown error')} — Place manually!")
        else:
            results["target"] = target_result

        await client.close()

        # 4. Record trade in DB
        trade = Trade(
            signal_id=req.signal_id,
            security_id=req.option_security_id,
            symbol=signal.symbol,
            trade_type="option",
            strike_price=req.strike,
            expiry_date=datetime.strptime(req.expiry, "%Y-%m-%d").date(),
            option_type=req.side.upper(),
            premium=req.limit_price,
            lot_size=req.lot_size,
            entry_price=req.limit_price,
            sl_price=req.sl_trigger_price,
            target_price=req.target_price,
            quantity=quantity,
            entry_date=date.today(),
            status="open",
            buy_order_id=buy_result.get("orderId", ""),
            sl_order_id=sl_result.get("orderId", "") if sl_result else "",
            target_order_id=target_result.get("orderId", "") if target_result else "",
            notes=f"Buy: {buy_result.get('orderId', '?')} | SL: {sl_result.get('orderId', '?') if sl_result else 'FAILED'} | Target: {target_result.get('orderId', '?') if target_result else 'FAILED'}",
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)

        results["trade_id"] = trade.id
        results["message"] = "Trade placed successfully" if not results["errors"] else "Buy placed, but some orders need attention"
        return results

    except Exception as e:
        logger.error(f"Place trade error: {e}")
        await client.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades/{trade_id}/close")
async def close_trade(
    trade_id: int,
    reason: str = Query("cancelled", description="closed / cancelled / sl_hit / target_hit"),
    exit_price: float = Query(0, description="Exit premium (0 if not exited on market)"),
    db: Session = Depends(get_db),
):
    """Mark a trade as closed/cancelled."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    trade.status = reason
    if exit_price > 0:
        trade.exit_price = exit_price
        trade.exit_date = date.today()
        trade.pnl = round((exit_price - trade.entry_price) * trade.quantity, 2)
        trade.pnl_percentage = round(((exit_price - trade.entry_price) / trade.entry_price) * 100, 1) if trade.entry_price else 0
        if trade.entry_date:
            trade.days_held = (date.today() - trade.entry_date).days
    else:
        trade.exit_date = date.today()
        if trade.entry_date:
            trade.days_held = (date.today() - trade.entry_date).days

    db.commit()
    return {"status": "ok", "trade_id": trade_id, "new_status": reason}


@router.post("/trades/{trade_id}/exit-now")
async def exit_trade_now(
    trade_id: int,
    db: Session = Depends(get_db),
):
    """
    Exit a trade immediately:
    1. Place MARKET SELL order
    2. Cancel SL and Target GTT orders
    3. Update trade in DB
    """
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "open":
        raise HTTPException(status_code=400, detail=f"Trade is already {trade.status}")

    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    mkt_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    is_market_open = (now_ist.weekday() < 5 and mkt_open <= now_ist <= mkt_close)

    if not is_market_open:
        raise HTTPException(status_code=400, detail="Market is closed. Exit manually on Dhan or wait for market hours.")

    option_type = "CALL" if trade.option_type == "CE" else "PUT"
    client = DhanClient()
    results = {"sell": None, "sl_cancelled": None, "target_cancelled": None, "errors": []}

    try:
        # 1. Place MARKET SELL
        sell_result = await client.place_order(
            security_id=trade.security_id,
            exchange_segment="NSE_FNO",
            transaction_type="SELL",
            product_type="MARGIN",
            order_type="MARKET",
            quantity=trade.quantity,
            price=0,
            validity="DAY",
            drv_expiry_date=trade.expiry_date.strftime("%Y-%m-%d") if trade.expiry_date else "",
            drv_option_type=option_type,
            drv_strike_price=trade.strike_price or 0,
        )

        if sell_result and sell_result.get("error"):
            results["errors"].append(f"Sell failed: {sell_result.get('detail', 'Unknown')}")
            await client.close()
            return JSONResponse(status_code=400, content=results)

        results["sell"] = sell_result

        # 2. Cancel SL GTT
        if trade.sl_order_id:
            try:
                sl_cancel = await client.cancel_forever_order(trade.sl_order_id)
                results["sl_cancelled"] = sl_cancel
            except Exception as e:
                results["errors"].append(f"SL GTT cancel failed: {e}")

        # 3. Cancel Target GTT
        if trade.target_order_id:
            try:
                tgt_cancel = await client.cancel_forever_order(trade.target_order_id)
                results["target_cancelled"] = tgt_cancel
            except Exception as e:
                results["errors"].append(f"Target GTT cancel failed: {e}")

        await client.close()

        # 4. Update trade in DB
        trade.status = "closed"
        trade.exit_date = date.today()
        if trade.entry_date:
            trade.days_held = (date.today() - trade.entry_date).days
        trade.notes = (trade.notes or "") + f" | EXIT: {sell_result.get('orderId', '?')}"
        db.commit()

        results["trade_id"] = trade.id
        results["message"] = "Position closed" + (" (some GTT cancels failed)" if results["errors"] else "")
        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Exit trade error: {e}")
        await client.close()
        raise HTTPException(status_code=500, detail=str(e))
