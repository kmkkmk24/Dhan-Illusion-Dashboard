"""
Trade Tracker — HOLD / EARLY-EXIT / SL-HIT / TARGET-HIT analysis.

Called every 2 minutes for each actively tracked index option trade.

Decision logic (in order of priority):
1. SL-HIT   : current option premium <= sl_price
2. TARGET-HIT: current option premium >= target_price
3. EARLY-EXIT: strength weakening signals (any one of):
     a. OI dropped >25% vs entry OI (smart money unwinding)
     b. Volume spike opposite to trade direction (sudden reversal pressure)
     c. Index candle closed opposite to direction and price crossed back through S/R
     d. Premium decayed >15% from entry but no progress toward target
4. HOLD     : everything looks normal, continue holding
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from backend.models.tables import Signal
from backend.services.dhan_client import DhanClient

logger = logging.getLogger(__name__)

OI_UNWIND_THRESHOLD = 0.25      # >25% OI drop from entry = unwinding
PREMIUM_DECAY_THRESHOLD = 0.15  # >15% premium decay with no target progress = exit
VOLUME_SPIKE_RATIO = 2.0        # 2× average volume in opposite candle = danger
SR_BREACH_BUFFER_PCT = 0.002    # 0.2% inside S/R to count as a breach


async def evaluate_hold_status(signal: Signal) -> dict:
    """Fetch live data and return status + reason for the tracked signal."""
    dhan = DhanClient()
    try:
        result = await _run_evaluation(dhan, signal)
        return result
    except Exception as e:
        logger.error("trade_tracker evaluate error signal=%s: %s", signal.id, e)
        return {"status": "HOLD", "reason": f"Data fetch error: {e}", "current_price": None}
    finally:
        await dhan.close()


async def _run_evaluation(dhan: DhanClient, signal: Signal) -> dict:
    direction = (signal.track_direction or signal.signal_type or "CE").upper()
    entry_price = signal.track_entry_price or 0.0
    sl_price = signal.track_sl_price
    target_price = signal.track_target_price
    entry_oi = signal.track_entry_oi
    security_id = signal.security_id
    option_sec_id = signal.track_option_security_id
    expiry = signal.track_expiry
    support = signal.track_support
    resistance = signal.track_resistance

    # ── 1. Fetch option chain for current premium, OI, volume ──────────────
    current_premium: Optional[float] = None
    current_oi: Optional[float] = None
    current_volume: Optional[float] = None
    chain_ok = False

    if option_sec_id and expiry:
        chain = await dhan.get_option_chain(security_id, "IDX_I", expiry)
        if chain:
            current_premium, current_oi, current_volume = _extract_option_stats(
                chain, int(option_sec_id), direction
            )
            chain_ok = True

    # Fallback: LTP-only for option
    if current_premium is None and option_sec_id:
        ltp_data = await dhan.get_market_quote_ltp([option_sec_id], "NSE_FNO")
        if ltp_data:
            current_premium = _extract_ltp(ltp_data, option_sec_id)

    # ── 2. Fetch index intraday data (15m) for candle analysis ────────────
    spot_price: Optional[float] = None
    df_intraday: Optional[pd.DataFrame] = None

    try:
        from datetime import datetime, timedelta
        now = datetime.now()
        from_str = (now - timedelta(days=1)).strftime("%Y-%m-%d 09:15:00")
        to_str = now.strftime("%Y-%m-%d %H:%M:00")

        df_intraday = await dhan.get_intraday_data(
            security_id=security_id,
            exchange_segment="IDX_I",
            instrument="INDEX",
            interval=15,
            from_date=from_str,
            to_date=to_str,
        )
        if df_intraday is not None and not df_intraday.empty:
            df_intraday = df_intraday.reset_index(drop=True)
            spot_price = float(df_intraday["close"].iloc[-1])
    except Exception as e:
        logger.warning("trade_tracker intraday fetch failed: %s", e)

    # ── 3. SL / Target checks ─────────────────────────────────────────────
    if current_premium is not None:
        if sl_price and current_premium <= sl_price:
            return {
                "status": "SL-HIT",
                "reason": f"Premium ₹{current_premium:.1f} hit SL ₹{sl_price:.1f}",
                "current_price": current_premium,
            }
        if target_price and current_premium >= target_price:
            return {
                "status": "TARGET-HIT",
                "reason": f"Premium ₹{current_premium:.1f} hit target ₹{target_price:.1f}",
                "current_price": current_premium,
            }

    # ── 4. Weakness checks → EARLY-EXIT ───────────────────────────────────
    warnings: list[str] = []

    # 4a. OI unwinding
    if chain_ok and entry_oi and entry_oi > 0 and current_oi is not None:
        oi_drop = (entry_oi - current_oi) / entry_oi
        if oi_drop > OI_UNWIND_THRESHOLD:
            warnings.append(
                f"OI dropped {oi_drop*100:.0f}% from entry ({int(entry_oi):,} → {int(current_oi):,}) — smart money unwinding"
            )

    # 4b. Volume spike in opposite candle
    if df_intraday is not None and len(df_intraday) >= 5:
        avg_vol = float(df_intraday["volume"].iloc[-6:-1].mean())
        last_candle = df_intraday.iloc[-1]
        last_candle_bull = float(last_candle["close"]) > float(last_candle["open"])
        last_candle_bear = float(last_candle["close"]) < float(last_candle["open"])
        last_vol = float(last_candle["volume"]) if last_candle["volume"] else 0.0
        if avg_vol > 0 and last_vol > avg_vol * VOLUME_SPIKE_RATIO:
            if direction == "CE" and last_candle_bear:
                warnings.append(
                    f"Volume spike {last_vol/avg_vol:.1f}× on bearish candle — reversal pressure for CE trade"
                )
            elif direction == "PE" and last_candle_bull:
                warnings.append(
                    f"Volume spike {last_vol/avg_vol:.1f}× on bullish candle — reversal pressure for PE trade"
                )

    # 4c. S/R breach: price crossed back through the entry S/R level
    if df_intraday is not None and len(df_intraday) >= 2 and spot_price is not None:
        if direction == "CE" and support is not None:
            breach_level = support * (1 + SR_BREACH_BUFFER_PCT)
            if spot_price < breach_level:
                warnings.append(
                    f"Index closed below support ₹{support:.0f} — CE trade invalidated"
                )
        if direction == "PE" and resistance is not None:
            breach_level = resistance * (1 - SR_BREACH_BUFFER_PCT)
            if spot_price > breach_level:
                warnings.append(
                    f"Index closed above resistance ₹{resistance:.0f} — PE trade invalidated"
                )

    # 4d. Premium decayed with no target progress
    if current_premium is not None and entry_price > 0 and target_price and target_price > 0:
        decay = (entry_price - current_premium) / entry_price
        target_progress = (current_premium - entry_price) / (target_price - entry_price) if target_price != entry_price else 0
        if decay > PREMIUM_DECAY_THRESHOLD and target_progress < 0:
            warnings.append(
                f"Premium decayed {decay*100:.0f}% from entry with no target progress — consider cutting loss"
            )

    if warnings:
        reason = " | ".join(warnings)
        return {
            "status": "EARLY-EXIT",
            "reason": reason,
            "current_price": current_premium,
        }

    # ── 5. HOLD — compose a positive confirmation ─────────────────────────
    hold_notes: list[str] = []
    if current_premium is not None and entry_price > 0:
        pct = (current_premium - entry_price) / entry_price * 100
        hold_notes.append(f"Premium at ₹{current_premium:.1f} ({pct:+.1f}% vs entry)")
    if chain_ok and entry_oi and current_oi is not None:
        oi_chg = (current_oi - entry_oi) / entry_oi * 100
        hold_notes.append(f"OI {oi_chg:+.0f}% (structure intact)")
    if spot_price:
        hold_notes.append(f"Spot ₹{spot_price:,.0f}")

    reason = " · ".join(hold_notes) if hold_notes else "Monitoring…"
    return {
        "status": "HOLD",
        "reason": reason,
        "current_price": current_premium,
    }


def _extract_option_stats(
    chain: dict, option_sec_id: int, direction: str
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Pull premium, OI, volume for a specific option from the chain response."""
    try:
        data = chain.get("data") or chain
        if isinstance(data, list):
            for row in data:
                for side in ("CE", "PE"):
                    opts = row.get(side) or {}
                    if int(opts.get("security_id", -1)) == option_sec_id:
                        ltp = opts.get("last_price") or opts.get("ltp") or opts.get("LTP")
                        oi = opts.get("open_interest") or opts.get("oi") or opts.get("OI")
                        vol = opts.get("volume") or opts.get("Vol")
                        return (
                            float(ltp) if ltp is not None else None,
                            float(oi) if oi is not None else None,
                            float(vol) if vol is not None else None,
                        )
        # Flat dict format
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, list):
                    for row in val:
                        sec = row.get("security_id") or row.get("scrip_id")
                        if sec and int(str(sec)) == option_sec_id:
                            ltp = row.get("last_price") or row.get("ltp")
                            oi = row.get("open_interest") or row.get("oi")
                            vol = row.get("volume")
                            return (
                                float(ltp) if ltp is not None else None,
                                float(oi) if oi is not None else None,
                                float(vol) if vol is not None else None,
                            )
    except Exception as e:
        logger.warning("_extract_option_stats error: %s", e)
    return None, None, None


def _extract_ltp(ltp_data: dict, security_id: str) -> Optional[float]:
    """Extract LTP value from Dhan LTP response."""
    try:
        for segment_data in ltp_data.values():
            if isinstance(segment_data, dict):
                for sid, info in segment_data.items():
                    if str(sid) == str(security_id):
                        return float(info.get("last_price") or info.get("ltp") or 0)
            elif isinstance(segment_data, list):
                for item in segment_data:
                    if str(item.get("security_id", "")) == str(security_id):
                        return float(item.get("last_price") or item.get("ltp") or 0)
    except Exception as e:
        logger.warning("_extract_ltp error: %s", e)
    return None
