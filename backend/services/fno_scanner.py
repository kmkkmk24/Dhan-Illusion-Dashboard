"""
F&O Directional Scanner Engine

Finds stocks for directional option trades (CE/PE) based on:
  - Trend detection (EMA alignment, higher highs/lows)
  - Consolidation at extremes (tight range near highs or lows)
  - Breakout proximity (how close to breaking the consolidation boundary)
  - Volume behavior during consolidation

Uses 75-minute candles (aggregated from 15-minute data from Dhan API).

CE setup: uptrend + consolidation at highs + about to break out upward
PE setup: downtrend + consolidation at lows + about to break down
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import Signal, SignalHistory, Instrument
from backend.services.dhan_client import DhanClient
from backend.services.sector_mapping import get_sectors_for_stock

logger = logging.getLogger(__name__)


def _aggregate_to_75m(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 15-minute candles into 75-minute candles (5 x 15m)."""
    df = df_15m.copy()
    df["bar_group"] = df.index // 5
    agg = df.groupby("bar_group").agg(
        timestamp=("timestamp", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index(drop=True)
    return agg


class FnoScanner:
    def __init__(self, db: Session):
        self.db = db
        config = get_config()
        self.fno_config = config.get("fno_scanner", {})
        self.dhan = DhanClient()

        self.ema_fast = self.fno_config.get("ema_fast", 20)
        self.ema_slow = self.fno_config.get("ema_slow", 50)
        self.consol_period = self.fno_config.get("consolidation_period", 10)
        self.trend_lookback = self.fno_config.get("trend_lookback", 40)
        self.signal_threshold = self.fno_config.get("signal_threshold", 0.55)
        self.top_per_sector = self.fno_config.get("top_per_sector", 2)
        self.top_sectors = self.fno_config.get("top_sectors", 4)

    async def scan_fno(
        self,
        instruments: list[Instrument],
        ce_sectors: list[str],
        pe_sectors: list[str],
    ) -> dict:
        """
        Full F&O directional scan.
        CE: only looks for bullish setups in ce_sectors (top strongest)
        PE: only looks for bearish setups in pe_sectors (bottom weakest)
        Top N per sector per side.
        """
        summary = {
            "scanned": 0, "ce_signals": 0, "pe_signals": 0,
            "updated": 0, "invalidated": 0, "errors": 0,
            "ce_sectors": ce_sectors, "pe_sectors": pe_sectors,
        }

        ce_sector_insts: dict[str, list[Instrument]] = {}
        pe_sector_insts: dict[str, list[Instrument]] = {}

        for inst in instruments:
            sym = inst.trading_symbol or inst.symbol
            stock_sectors = get_sectors_for_stock(sym)
            for sec in stock_sectors:
                if sec in ce_sectors:
                    ce_sector_insts.setdefault(sec, []).append(inst)
                if sec in pe_sectors:
                    pe_sector_insts.setdefault(sec, []).append(inst)

        all_sector_insts = set()
        for insts in ce_sector_insts.values():
            for inst in insts:
                all_sector_insts.add(inst.security_id)
        for insts in pe_sector_insts.values():
            for inst in insts:
                all_sector_insts.add(inst.security_id)

        if not all_sector_insts:
            logger.warning("No F&O instruments matched CE/PE sectors")
            return summary

        to_dt = datetime.now()
        from_dt = to_dt - timedelta(days=85)
        from_str = from_dt.strftime("%Y-%m-%d 09:15:00")
        to_str = to_dt.strftime("%Y-%m-%d 15:30:00")

        analyzed: dict[str, dict] = {}

        unique_insts = {inst.security_id: inst for insts in
                        list(ce_sector_insts.values()) + list(pe_sector_insts.values())
                        for inst in insts}

        for sid, instrument in unique_insts.items():
            try:
                if summary["scanned"] > 0:
                    await asyncio.sleep(0.35)

                df_15m = await self.dhan.get_intraday_data(
                    security_id=instrument.security_id,
                    exchange_segment="NSE_EQ",
                    instrument="EQUITY",
                    interval=15,
                    from_date=from_str,
                    to_date=to_str,
                )

                if df_15m is None or len(df_15m) < 100:
                    continue

                df_75m = _aggregate_to_75m(df_15m)
                if len(df_75m) < self.trend_lookback + 10:
                    continue

                summary["scanned"] += 1
                analyzed[sid] = {
                    "instrument": instrument,
                    "df_75m": df_75m,
                }

            except Exception as e:
                logger.error(f"Error scanning {instrument.trading_symbol}: {e}")
                summary["errors"] += 1

        # CE: analyze only for bullish setups in strong sectors
        ce_by_sector: dict[str, list] = {}
        for sector_name, insts in ce_sector_insts.items():
            for inst in insts:
                data = analyzed.get(inst.security_id)
                if not data:
                    continue
                result = self._analyze_directional(
                    data["df_75m"], data["instrument"], sector_name, "CE"
                )
                if result and result["score"] >= self.signal_threshold:
                    ce_by_sector.setdefault(sector_name, []).append(result)

        # PE: analyze only for bearish setups in weak sectors
        pe_by_sector: dict[str, list] = {}
        for sector_name, insts in pe_sector_insts.items():
            for inst in insts:
                data = analyzed.get(inst.security_id)
                if not data:
                    continue
                result = self._analyze_directional(
                    data["df_75m"], data["instrument"], sector_name, "PE"
                )
                if result and result["score"] >= self.signal_threshold:
                    pe_by_sector.setdefault(sector_name, []).append(result)

        selected_ce = []
        seen_ce_ids = set()
        for sector, items in ce_by_sector.items():
            items.sort(key=lambda x: x["score"], reverse=True)
            for r in items[:self.top_per_sector]:
                sid = r["instrument"].security_id
                if sid not in seen_ce_ids:
                    seen_ce_ids.add(sid)
                    selected_ce.append(r)

        selected_pe = []
        seen_pe_ids = set()
        for sector, items in pe_by_sector.items():
            items.sort(key=lambda x: x["score"], reverse=True)
            for r in items[:self.top_per_sector]:
                sid = r["instrument"].security_id
                if sid not in seen_pe_ids:
                    seen_pe_ids.add(sid)
                    selected_pe.append(r)

        for r in selected_ce:
            self._save_signal(r, "fno")
            summary["ce_signals"] += 1

        for r in selected_pe:
            self._save_signal(r, "fno")
            summary["pe_signals"] += 1

        invalidated = self._invalidate_stale("fno")
        summary["invalidated"] = invalidated

        self.db.commit()
        await self.dhan.close()

        logger.info(f"F&O scan complete: {summary}")
        return summary

    def _analyze(self, df: pd.DataFrame, instrument: Instrument, sector: str) -> Optional[dict]:
        """Analyze a stock on 75m candles for directional setup (auto-detect direction)."""
        return self._analyze_directional(df, instrument, sector, required_direction=None)

    def _analyze_directional(
        self, df: pd.DataFrame, instrument: Instrument, sector: str,
        required_direction: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Analyze a stock for a specific direction.
        required_direction: "CE", "PE", or None (auto-detect).
        If required and the stock's trend doesn't match, returns None.
        """
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)

        ema_fast = pd.Series(close).ewm(span=self.ema_fast, adjust=False).mean().values
        ema_slow = pd.Series(close).ewm(span=self.ema_slow, adjust=False).mean().values

        trend = self._detect_trend(close, high, low, ema_fast, ema_slow)

        if trend["direction"] == "neutral":
            return None

        if required_direction and trend["direction"] != required_direction:
            return None

        direction = trend["direction"]

        consol = self._detect_consolidation(close, high, low, volume, direction)
        breakout_prox = self._breakout_proximity(close, high, low, direction)

        score = (
            0.35 * trend["score"]
            + 0.35 * consol["score"]
            + 0.30 * breakout_prox["score"]
        )

        if consol["volume_dryup"]:
            score = min(1.0, score + 0.05)

        display_sym = instrument.trading_symbol or instrument.symbol

        return {
            "instrument": instrument,
            "direction": direction,
            "score": round(score, 4),
            "trend_score": round(trend["score"], 4),
            "trend_detail": trend,
            "consol_score": round(consol["score"], 4),
            "consol_detail": consol,
            "breakout_score": round(breakout_prox["score"], 4),
            "breakout_detail": breakout_prox,
            "close_price": float(close[-1]),
            "volume": float(volume[-1]),
            "sector": sector,
            "symbol": display_sym,
        }

    def _detect_trend(self, close, high, low, ema_fast, ema_slow) -> dict:
        """
        Detect trend direction and strength.
        CE (bullish): price > EMA fast > EMA slow, higher highs & higher lows
        PE (bearish): price < EMA fast < EMA slow, lower highs & lower lows
        """
        lb = self.trend_lookback
        recent_close = close[-lb:]
        recent_high = high[-lb:]
        recent_low = low[-lb:]

        cur_price = close[-1]
        cur_ema_f = ema_fast[-1]
        cur_ema_s = ema_slow[-1]

        ema_aligned_bull = cur_price > cur_ema_f > cur_ema_s
        ema_aligned_bear = cur_price < cur_ema_f < cur_ema_s

        mid = lb // 2
        first_half_high = np.max(recent_high[:mid])
        second_half_high = np.max(recent_high[mid:])
        first_half_low = np.min(recent_low[:mid])
        second_half_low = np.min(recent_low[mid:])

        higher_highs = second_half_high > first_half_high
        higher_lows = second_half_low > first_half_low
        lower_highs = second_half_high < first_half_high
        lower_lows = second_half_low < first_half_low

        ema_slope_f = (ema_fast[-1] - ema_fast[-5]) / ema_fast[-5] if ema_fast[-5] != 0 else 0
        ema_slope_s = (ema_slow[-1] - ema_slow[-5]) / ema_slow[-5] if ema_slow[-5] != 0 else 0

        bull_score = 0.0
        bear_score = 0.0

        if ema_aligned_bull:
            bull_score += 0.35
        if higher_highs:
            bull_score += 0.25
        if higher_lows:
            bull_score += 0.25
        if ema_slope_f > 0 and ema_slope_s > 0:
            bull_score += 0.15

        if ema_aligned_bear:
            bear_score += 0.35
        if lower_highs:
            bear_score += 0.25
        if lower_lows:
            bear_score += 0.25
        if ema_slope_f < 0 and ema_slope_s < 0:
            bear_score += 0.15

        if bull_score > bear_score and bull_score >= 0.4:
            return {
                "direction": "CE",
                "score": min(1.0, bull_score),
                "ema_aligned": ema_aligned_bull,
                "higher_highs": higher_highs,
                "higher_lows": higher_lows,
                "ema_slope": round(ema_slope_f * 100, 3),
            }
        elif bear_score > bull_score and bear_score >= 0.4:
            return {
                "direction": "PE",
                "score": min(1.0, bear_score),
                "ema_aligned": ema_aligned_bear,
                "lower_highs": lower_highs,
                "lower_lows": lower_lows,
                "ema_slope": round(ema_slope_f * 100, 3),
            }
        else:
            return {"direction": "neutral", "score": 0.0}

    def _detect_consolidation(self, close, high, low, volume, direction: str) -> dict:
        """
        Detect tight consolidation at highs (CE) or lows (PE).
        Measures range tightness in the consolidation period relative to
        the recent swing range.
        """
        cp = self.consol_period
        lb = self.trend_lookback

        consol_high = np.max(high[-cp:])
        consol_low = np.min(low[-cp:])
        consol_range = consol_high - consol_low

        swing_range = np.max(high[-lb:]) - np.min(low[-lb:])

        if swing_range == 0:
            return {"score": 0.0, "range_ratio": 1.0, "volume_dryup": False}

        range_ratio = consol_range / swing_range

        if direction == "CE":
            recent_peak = np.max(high[-lb:])
            dist_to_peak = abs(consol_high - recent_peak) / swing_range
            position_score = max(0, 1.0 - dist_to_peak * 3)
        else:
            recent_trough = np.min(low[-lb:])
            dist_to_trough = abs(consol_low - recent_trough) / swing_range
            position_score = max(0, 1.0 - dist_to_trough * 3)

        tightness_score = max(0, 1.0 - range_ratio * 2.5)

        score = 0.5 * tightness_score + 0.5 * position_score

        vol_avg = np.mean(volume[-lb:])
        vol_recent = np.mean(volume[-cp:])
        volume_dryup = (vol_recent / vol_avg) < 0.7 if vol_avg > 0 else False

        return {
            "score": min(1.0, max(0.0, score)),
            "range_ratio": round(range_ratio, 4),
            "position_score": round(position_score, 4),
            "tightness": round(tightness_score, 4),
            "volume_dryup": volume_dryup,
        }

    def _breakout_proximity(self, close, high, low, direction: str) -> dict:
        """
        Measure how close the current price is to breaking the consolidation boundary.
        CE: price near the recent consolidation ceiling
        PE: price near the recent consolidation floor
        """
        cp = self.consol_period
        cur_price = close[-1]

        consol_high = np.max(high[-cp:])
        consol_low = np.min(low[-cp:])
        consol_range = consol_high - consol_low

        if consol_range == 0:
            return {"score": 0.0, "distance_pct": 0.0}

        if direction == "CE":
            distance = consol_high - cur_price
            distance_ratio = distance / consol_range
            score = max(0, 1.0 - distance_ratio * 2)
        else:
            distance = cur_price - consol_low
            distance_ratio = distance / consol_range
            score = max(0, 1.0 - distance_ratio * 2)

        distance_pct = (distance / cur_price * 100) if cur_price > 0 else 0

        return {
            "score": min(1.0, max(0.0, score)),
            "distance_pct": round(distance_pct, 3),
            "consol_high": round(consol_high, 2),
            "consol_low": round(consol_low, 2),
        }

    def _save_signal(self, result: dict, section: str) -> None:
        """Create or update an F&O signal."""
        today = date.today()
        instrument = result["instrument"]

        existing = self.db.query(Signal).filter(
            Signal.security_id == instrument.security_id,
            Signal.section == section,
            Signal.status == "active",
        ).first()

        sub_scores = {
            "trend_score": result.get("trend_score"),
            "consol_score": result.get("consol_score"),
            "breakout_score": result.get("breakout_score"),
        }

        if existing:
            existing.current_score = result["score"]
            existing.peak_score = max(existing.peak_score, result["score"])
            existing.last_updated_date = today
            existing.signal_type = result["direction"]
            existing.trend_score = sub_scores["trend_score"]
            existing.consol_score = sub_scores["consol_score"]
            existing.breakout_score = sub_scores["breakout_score"]
            if not existing.sector:
                existing.sector = result["sector"]
        else:
            past_count = self.db.query(Signal).filter(
                Signal.security_id == instrument.security_id,
            ).count()

            signal = Signal(
                security_id=instrument.security_id,
                symbol=result["symbol"],
                section=section,
                signal_type=result["direction"],
                occurrence_number=past_count + 1,
                first_detected_date=today,
                last_updated_date=today,
                current_score=result["score"],
                peak_score=result["score"],
                status="active",
                sector=result["sector"],
                close_price_at_detection=result["close_price"],
                **sub_scores,
            )
            self.db.add(signal)
            self.db.flush()

            if past_count > 0:
                logger.info(f"F&O recurrence: {result['symbol']} ({result['direction']}) {past_count + 1}th time")

    @staticmethod
    def _signal_direction(signal_type: str) -> str:
        st = (signal_type or "").upper()
        if st.startswith("CE"):
            return "CE"
        if st.startswith("PE"):
            return "PE"
        return st

    async def revalidate_signals(self, section: str = "fno") -> dict:
        """
        Re-evaluate all active and tracked signals with fresh market data.
        Updates scores and auto-invalidates broken setups.

        Returns a summary of what changed.
        """
        signals = self.db.query(Signal).filter(
            Signal.section == section,
            Signal.status.in_(["active", "tracking"]),
        ).all()

        if not signals:
            return {"revalidated": 0, "invalidated": 0, "updated": 0, "details": []}

        to_dt = datetime.now()
        from_dt = to_dt - timedelta(days=85)
        from_str = from_dt.strftime("%Y-%m-%d 09:15:00")
        to_str = to_dt.strftime("%Y-%m-%d 15:30:00")

        summary = {"revalidated": 0, "invalidated": 0, "updated": 0, "details": []}
        today = date.today()

        for i, sig in enumerate(signals):
            try:
                if i > 0:
                    await asyncio.sleep(0.35)

                df_15m = await self.dhan.get_intraday_data(
                    security_id=sig.security_id,
                    exchange_segment="NSE_EQ",
                    instrument="EQUITY",
                    interval=15,
                    from_date=from_str,
                    to_date=to_str,
                )

                if df_15m is None or len(df_15m) < 100:
                    continue

                df_75m = _aggregate_to_75m(df_15m)
                if len(df_75m) < self.trend_lookback + 10:
                    continue

                close = df_75m["close"].values.astype(float)
                high = df_75m["high"].values.astype(float)
                low = df_75m["low"].values.astype(float)
                volume = df_75m["volume"].values.astype(float)

                ema_f = pd.Series(close).ewm(span=self.ema_fast, adjust=False).mean().values
                ema_s = pd.Series(close).ewm(span=self.ema_slow, adjust=False).mean().values

                trend = self._detect_trend(close, high, low, ema_f, ema_s)
                direction = self._signal_direction(sig.signal_type)  # CE or PE

                old_score = sig.current_score
                detail = {"symbol": sig.symbol, "old_score": old_score, "action": "ok"}

                # Check 1: trend reversed (CE signal but trend is now bearish, or vice versa)
                if trend["direction"] != "neutral" and trend["direction"] != direction:
                    sig.status = "invalidated"
                    sig.invalidation_reason = f"Trend reversed to {trend['direction']}"
                    sig.last_updated_date = today
                    summary["invalidated"] += 1
                    detail["action"] = "invalidated"
                    detail["reason"] = sig.invalidation_reason
                    summary["details"].append(detail)
                    summary["revalidated"] += 1
                    continue

                # Check 2: trend went neutral (no clear direction anymore)
                if trend["direction"] == "neutral":
                    consol = self._detect_consolidation(close, high, low, volume, direction)
                    breakout = self._breakout_proximity(close, high, low, direction)
                    new_score = 0.35 * 0.0 + 0.35 * consol["score"] + 0.30 * breakout["score"]

                    if new_score < 0.3:
                        sig.status = "invalidated"
                        sig.invalidation_reason = f"Trend lost, score {new_score:.0%}"
                        sig.current_score = round(new_score, 4)
                        sig.last_updated_date = today
                        summary["invalidated"] += 1
                        detail["action"] = "invalidated"
                        detail["reason"] = sig.invalidation_reason
                    else:
                        sig.current_score = round(new_score, 4)
                        sig.last_updated_date = today
                        summary["updated"] += 1
                        detail["action"] = "score_dropped"

                    detail["new_score"] = sig.current_score
                    summary["details"].append(detail)
                    summary["revalidated"] += 1
                    continue

                # Check 3: trend matches — re-score fully
                consol = self._detect_consolidation(close, high, low, volume, direction)
                breakout = self._breakout_proximity(close, high, low, direction)
                new_score = (
                    0.35 * trend["score"]
                    + 0.35 * consol["score"]
                    + 0.30 * breakout["score"]
                )
                if consol.get("volume_dryup"):
                    new_score = min(1.0, new_score + 0.05)

                new_score = round(new_score, 4)
                sig.trend_score = round(trend["score"], 4)
                sig.consol_score = round(consol["score"], 4)
                sig.breakout_score = round(breakout["score"], 4)

                # Check 4: score dropped below survival threshold
                if new_score < 0.35:
                    sig.status = "invalidated"
                    sig.invalidation_reason = f"Setup weakened, score {new_score:.0%}"
                    sig.current_score = new_score
                    sig.last_updated_date = today
                    summary["invalidated"] += 1
                    detail["action"] = "invalidated"
                    detail["reason"] = sig.invalidation_reason
                # Check 5: significant score drop (>40% decline from peak)
                elif sig.peak_score > 0 and new_score < sig.peak_score * 0.6:
                    sig.status = "invalidated"
                    sig.invalidation_reason = f"Score fell from {sig.peak_score:.0%} to {new_score:.0%}"
                    sig.current_score = new_score
                    sig.last_updated_date = today
                    summary["invalidated"] += 1
                    detail["action"] = "invalidated"
                    detail["reason"] = sig.invalidation_reason
                # Check 6: price broke key level
                elif self._price_broke_structure(close, high, low, direction):
                    sig.status = "invalidated"
                    sig.invalidation_reason = "Price broke consolidation structure"
                    sig.current_score = new_score
                    sig.last_updated_date = today
                    summary["invalidated"] += 1
                    detail["action"] = "invalidated"
                    detail["reason"] = sig.invalidation_reason
                else:
                    sig.current_score = new_score
                    sig.peak_score = max(sig.peak_score, new_score)
                    sig.last_updated_date = today
                    summary["updated"] += 1
                    detail["action"] = "updated"

                detail["new_score"] = new_score
                summary["details"].append(detail)
                summary["revalidated"] += 1

            except Exception as e:
                logger.error(f"Error revalidating {sig.symbol}: {e}")

        self.db.commit()
        logger.info(f"Revalidation complete: {summary['revalidated']} checked, "
                     f"{summary['invalidated']} invalidated, {summary['updated']} updated")
        return summary

    def _price_broke_structure(self, close, high, low, direction: str) -> bool:
        """
        Check if price has broken the consolidation structure in the wrong direction.
        CE: price dropped below the consolidation low (bulls lost control)
        PE: price rallied above the consolidation high (bears lost control)
        """
        cp = self.consol_period
        lb = self.trend_lookback
        cur_price = close[-1]

        consol_high = np.max(high[-cp:])
        consol_low = np.min(low[-cp:])
        swing_range = np.max(high[-lb:]) - np.min(low[-lb:])

        if swing_range == 0:
            return False

        if direction == "CE":
            recent_trough = np.min(low[-lb:])
            floor = consol_low - swing_range * 0.05
            return cur_price < floor and cur_price < recent_trough
        else:
            recent_peak = np.max(high[-lb:])
            ceiling = consol_high + swing_range * 0.05
            return cur_price > ceiling and cur_price > recent_peak

    def _invalidate_stale(self, section: str) -> int:
        """Invalidate F&O signals with score below threshold (used after new scan)."""
        threshold = 0.3
        active = self.db.query(Signal).filter(
            Signal.section == section,
            Signal.status == "active",
        ).all()

        count = 0
        for sig in active:
            if sig.current_score < threshold:
                sig.status = "invalidated"
                sig.invalidation_reason = f"Score dropped to {sig.current_score:.2f}"
                count += 1
        return count


async def scan_fno_manual_add(
    db: Session, security_id: str, symbol: str
) -> Optional[dict]:
    """Process a manually added stock: fetch data, analyze, return result."""
    dhan = DhanClient()
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=85)
    from_str = from_dt.strftime("%Y-%m-%d 09:15:00")
    to_str = to_dt.strftime("%Y-%m-%d 15:30:00")

    try:
        df_15m = await dhan.get_intraday_data(
            security_id=security_id,
            exchange_segment="NSE_EQ",
            instrument="EQUITY",
            interval=15,
            from_date=from_str,
            to_date=to_str,
        )
        await dhan.close()

        if df_15m is None or len(df_15m) < 100:
            return None

        df_75m = _aggregate_to_75m(df_15m)

        config = get_config()
        fno_config = config.get("fno_scanner", {})
        ema_fast = fno_config.get("ema_fast", 20)
        ema_slow = fno_config.get("ema_slow", 50)
        trend_lookback = fno_config.get("trend_lookback", 40)

        if len(df_75m) < trend_lookback + 10:
            return None

        close = df_75m["close"].values.astype(float)
        high = df_75m["high"].values.astype(float)
        low = df_75m["low"].values.astype(float)
        volume = df_75m["volume"].values.astype(float)

        ema_f = pd.Series(close).ewm(span=ema_fast, adjust=False).mean().values
        ema_s = pd.Series(close).ewm(span=ema_slow, adjust=False).mean().values

        scanner = FnoScanner.__new__(FnoScanner)
        scanner.ema_fast = ema_fast
        scanner.ema_slow = ema_slow
        scanner.consol_period = fno_config.get("consolidation_period", 10)
        scanner.trend_lookback = trend_lookback

        trend = scanner._detect_trend(close, high, low, ema_f, ema_s)
        direction = trend["direction"] if trend["direction"] != "neutral" else "CE"

        consol = scanner._detect_consolidation(close, high, low, volume, direction)
        breakout = scanner._breakout_proximity(close, high, low, direction)

        score = 0.35 * trend["score"] + 0.35 * consol["score"] + 0.30 * breakout["score"]

        sectors = get_sectors_for_stock(symbol)

        return {
            "direction": direction,
            "score": round(score, 4),
            "trend_score": round(trend["score"], 4),
            "consol_score": round(consol["score"], 4),
            "breakout_score": round(breakout["score"], 4),
            "close_price": float(close[-1]),
            "sector": sectors[0] if sectors else "Other",
            "trend_detail": trend,
            "consol_detail": consol,
            "breakout_detail": breakout,
        }

    except Exception as e:
        logger.error(f"Error analyzing manual add {symbol}: {e}")
        await dhan.close()
        return None
