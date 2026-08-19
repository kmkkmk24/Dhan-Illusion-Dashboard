"""
Index Trading Scanner (intraday options on NIFTY / SENSEX).

Hybrid strategy:
- Trend pullback: EMA20/EMA50 alignment + pullback to EMA/VWAP + candle confirmation
- Mean reversion: VWAP deviation + RSI extremes when trend is flat

Produces CE/PE signals with strike selection from option chain.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import Signal
from backend.services.dhan_client import DhanClient

logger = logging.getLogger(__name__)


_INDEX_SCAN_CACHE: dict[str, dict[str, Any]] = {
    "intraday": {"as_of": None, "signals": [], "summary": {}},
    "positional": {"as_of": None, "signals": [], "summary": {}},
}


def _cache_scan_result(mode: str, signals: list[dict], summary: dict) -> dict:
    payload = _INDEX_SCAN_CACHE.get(mode) or _INDEX_SCAN_CACHE["intraday"]
    if _is_cache_stale(payload):
        payload = _reset_cache(mode)

    # Intraday desk behavior: keep same-day suggestions and append fresh scans.
    if mode == "intraday":
        existing = payload.get("signals") or []
        if signals:
            payload["signals"] = signals + existing
        else:
            payload["signals"] = existing
    else:
        payload["signals"] = signals

    payload["as_of"] = datetime.now().isoformat(timespec="seconds")
    payload["summary"] = summary
    return payload


def _reset_cache(mode: str) -> dict:
    payload = _INDEX_SCAN_CACHE.get(mode) or _INDEX_SCAN_CACHE["intraday"]
    payload["as_of"] = None
    payload["signals"] = []
    payload["summary"] = {}
    return payload


def _is_cache_stale(payload: dict) -> bool:
    as_of = payload.get("as_of")
    if not as_of:
        return False
    try:
        cached = datetime.fromisoformat(as_of)
    except ValueError:
        return False
    return cached.date() != date.today()


def get_index_scan_cache(mode: str = "intraday") -> dict:
    payload = _INDEX_SCAN_CACHE.get(mode) or _INDEX_SCAN_CACHE["intraday"]
    if _is_cache_stale(payload):
        return _reset_cache(mode)
    return payload


def _parse_time(s: str, fallback: time) -> time:
    try:
        return datetime.strptime(s, "%H:%M").time()
    except Exception:
        return fallback


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _vwap_by_session(df: pd.DataFrame) -> pd.Series:
    ts = pd.to_datetime(df["timestamp"])
    session = ts.dt.date
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan)
    cum_vol = vol.groupby(session).cumsum()
    vwap = (typical * vol).groupby(session).cumsum() / cum_vol
    return vwap.fillna(df["close"])


def _last_trade_row(df: pd.DataFrame) -> pd.Series:
    """Use the last row with non-zero volume to avoid flat/empty bars."""
    if "volume" in df.columns:
        non_zero = df[df["volume"].fillna(0) > 0]
        if not non_zero.empty:
            return non_zero.iloc[-1]
    return df.iloc[-1]


@dataclass
class IndexSpec:
    name: str
    security_id: str
    exchange_segment: str
    instrument: str
    option_segment: str
    option_quote_segment: str
    lot_size: int


class IndexTradingScanner:
    def __init__(self, db: Session):
        self.db = db
        cfg = get_config().get("index_trading") or {}
        self.cfg = cfg

        self.interval = int(cfg.get("interval_minutes", 15))
        self.lookback_days = int(cfg.get("lookback_days", 5))
        self.sleep_between = float(cfg.get("request_delay", 0.6))

        self.ema_fast = int(cfg.get("ema_fast", 20))
        self.ema_slow = int(cfg.get("ema_slow", 50))
        self.ema_slope_lookback = int(cfg.get("ema_slope_lookback", 5))
        self.pullback_pct = float(cfg.get("pullback_pct", 0.003))
        self.vwap_pullback_pct = float(cfg.get("vwap_pullback_pct", 0.003))

        self.rsi_period = int(cfg.get("rsi_period", 14))
        self.rsi_overbought = float(cfg.get("rsi_overbought", 70))
        self.rsi_oversold = float(cfg.get("rsi_oversold", 30))
        self.vwap_dev_pct = float(cfg.get("vwap_dev_pct", 0.006))
        self.flat_trend_pct = float(cfg.get("flat_trend_pct", 0.0015))
        self.flat_slope_pct = float(cfg.get("flat_slope_pct", 0.0006))
        self.mean_revert_allow_trend = bool(cfg.get("mean_revert_allow_trend", True))
        self.reversal_wick_pct = float(cfg.get("reversal_wick_pct", 0.6))
        self.allow_mean_revert_without_candle = bool(cfg.get("allow_mean_revert_without_candle", True))
        self.mean_revert_candle_override_rsi = float(cfg.get("mean_revert_candle_override_rsi", 30))
        self.mean_revert_candle_override_rsi_high = float(cfg.get("mean_revert_candle_override_rsi_high", 70))
        self.allow_trend_continuation = bool(cfg.get("allow_trend_continuation", True))
        self.trend_rsi_min = float(cfg.get("trend_rsi_min", 55))
        self.trend_rsi_max = float(cfg.get("trend_rsi_max", 45))
        self.trend_setup_rsi_min = float(cfg.get("trend_setup_rsi_min", 52))
        self.trend_setup_rsi_max = float(cfg.get("trend_setup_rsi_max", 48))
        self.trend_require_vwap_reclaim = bool(cfg.get("trend_require_vwap_reclaim", True))
        self.trend_min_slope_pct = float(cfg.get("trend_min_slope_pct", 0.0004))
        self.mean_revert_two_bar_confirm = bool(cfg.get("mean_revert_two_bar_confirm", True))
        self.mean_revert_min_atr_overshoot = float(cfg.get("mean_revert_min_atr_overshoot", 1.2))
        self.mean_revert_max_slope_pct = float(cfg.get("mean_revert_max_slope_pct", 0.0006))

        self.atr_period = int(cfg.get("atr_period", 14))
        self.sl_atr_trend = float(cfg.get("sl_atr_trend", 1.0))
        self.target_atr_trend = float(cfg.get("target_atr_trend", 2.0))
        self.sl_atr_mean_revert = float(cfg.get("sl_atr_mean_revert", 0.8))
        self.target_atr_mean_revert = float(cfg.get("target_atr_mean_revert", 1.2))
        self.sr_lookback_bars = int(cfg.get("sr_lookback_bars", 20))
        self.sr_swing_window = int(cfg.get("sr_swing_window", 3))
        self.max_sr_distance_atr = float(cfg.get("max_sr_distance_atr", 1.5))
        self.entry_atr_buffer = float(cfg.get("entry_atr_buffer", 0.25))
        self.exit_atr_buffer = float(cfg.get("exit_atr_buffer", 0.4))
        self.sl_atr_buffer = float(cfg.get("sl_atr_buffer", 0.6))

        self.min_liquidity_oi = int(cfg.get("min_liquidity_oi", 100000))
        self.max_spread_pct = float(cfg.get("max_spread_pct", 5.0))
        self.moneyness_limit_pct = float(cfg.get("moneyness_limit_pct", 2.0))
        self.min_option_premium = float(cfg.get("min_option_premium", 10.0))
        self.min_delta = float(cfg.get("min_delta", 0.25))
        self.depth_filter_enabled = bool(cfg.get("depth_filter_enabled", True))
        self.depth_max_spread_pct = float(cfg.get("depth_max_spread_pct", 3.0))
        self.depth_min_top_qty = int(cfg.get("depth_min_top_qty", 100))
        self.depth_min_total_qty = int(cfg.get("depth_min_total_qty", 500))
        self.depth_min_imbalance = float(cfg.get("depth_min_imbalance", 0.0))
        self.depth_require_data = bool(cfg.get("depth_require_data", False))

        self.max_signals_per_day = int(cfg.get("max_signals_per_day", 4))

        self.trade_start = _parse_time(cfg.get("trade_start_time", "09:30"), time(9, 30))
        self.trade_end = _parse_time(cfg.get("trade_end_time", "15:10"), time(15, 10))
        self.skip_open_minutes = int(cfg.get("skip_open_minutes", 15))
        self.lunch_start = _parse_time(cfg.get("lunch_start", "12:00"), time(12, 0))
        self.lunch_end = _parse_time(cfg.get("lunch_end", "13:30"), time(13, 30))
        self.use_lunch_filter = bool(cfg.get("skip_lunch", True))
        self.allow_after_close = bool(cfg.get("allow_after_close", False))

        self.expiry_policy = str(cfg.get("expiry_policy", "weekly_nearest"))
        self.positional_expiry_policy = str(cfg.get("positional_expiry_policy", "next_week"))

        self.pos_lookback_days = int(cfg.get("positional_lookback_days", 180))
        self.pos_ema_fast = int(cfg.get("positional_ema_fast", 20))
        self.pos_ema_mid = int(cfg.get("positional_ema_mid", 50))
        self.pos_ema_slow = int(cfg.get("positional_ema_slow", 100))
        self.pos_pullback_pct = float(cfg.get("positional_pullback_pct", 0.004))
        self.pos_pullback_mid_pct = float(cfg.get("positional_pullback_mid_pct", 0.012))
        self.pos_breakout_lookback = int(cfg.get("positional_breakout_lookback", 20))
        self.pos_slope_lookback = int(cfg.get("positional_slope_lookback", 5))
        self.pos_sl_atr = float(cfg.get("positional_sl_atr", 1.5))
        self.pos_target_atr = float(cfg.get("positional_target_atr", 3.0))
        self.pos_allow_trend_continuation = bool(cfg.get("positional_allow_trend_continuation", True))
        self.pos_trend_rsi_min = float(cfg.get("positional_trend_rsi_min", 55))
        self.pos_trend_rsi_max = float(cfg.get("positional_trend_rsi_max", 45))
        self.pos_pullback_rsi_min = float(cfg.get("positional_pullback_rsi_min", 40))
        self.pos_pullback_rsi_max = float(cfg.get("positional_pullback_rsi_max", 60))

        self.indices = self._load_indices(cfg.get("indices") or [])

    def _load_indices(self, raw: list[dict]) -> list[IndexSpec]:
        out: list[IndexSpec] = []
        for item in raw:
            if not item.get("security_id"):
                continue
            out.append(
                IndexSpec(
                    name=str(item.get("name") or item.get("symbol") or "INDEX"),
                    security_id=str(item["security_id"]),
                    exchange_segment=str(item.get("exchange_segment") or "IDX_I"),
                    instrument=str(item.get("instrument") or "INDEX"),
                    option_segment=str(item.get("option_segment") or "IDX_I"),
                    option_quote_segment=str(
                        item.get("option_quote_segment")
                        or item.get("option_marketfeed_segment")
                        or "NSE_FNO"
                    ),
                    lot_size=int(item.get("lot_size") or 1),
                )
            )
        return out

    async def scan_indices(self, mode: str = "intraday") -> dict:
        mode = "positional" if mode == "positional" else "intraday"
        if not self.indices:
            return _cache_scan_result(mode, [], {"error": "no_indices_configured"})

        summary = {
            "mode": mode,
            "scanned": 0,
            "signals": 0,
            "errors": 0,
            "skip_reason_counts": {},
        }
        signals: list[dict] = []

        today = date.today()
        active_today = (
            self.db.query(Signal)
            .filter(Signal.section == "index", Signal.last_updated_date == today)
            .count()
        )
        if self.max_signals_per_day and active_today >= self.max_signals_per_day:
            summary["skip_reason_counts"]["max_signals_per_day"] = active_today
            return _cache_scan_result(mode, [], summary)

        client = DhanClient()
        try:
            for idx, spec in enumerate(self.indices):
                if idx > 0:
                    await asyncio.sleep(self.sleep_between)
                try:
                    if mode == "positional":
                        result = await self._scan_one_index_positional(spec, client)
                    else:
                        result = await self._scan_one_index_intraday(spec, client)
                    summary["scanned"] += 1
                    if result and result.get("_skip_reason"):
                        reason = result["_skip_reason"]
                        summary["skip_reason_counts"][reason] = summary["skip_reason_counts"].get(reason, 0) + 1
                        if result.get("_debug"):
                            summary.setdefault("debug", []).append(
                                {"index": spec.name, "reason": reason, **result["_debug"]}
                            )
                        continue
                    if result:
                        signals.append(result)
                        summary["signals"] += 1
                except Exception as e:
                    logger.error("Index scan error (%s): %s", spec.name, e)
                    summary["errors"] += 1

            invalidated = self._invalidate_stale_signals()
            if invalidated:
                summary["invalidated"] = invalidated
            self.db.commit()
        finally:
            await client.close()

        return _cache_scan_result(mode, signals, summary)

    async def _scan_one_index_intraday(self, spec: IndexSpec, client: DhanClient) -> Optional[dict]:
        now = datetime.now()
        from_dt = now - timedelta(days=self.lookback_days)
        from_str = from_dt.strftime("%Y-%m-%d 09:15:00")
        to_str = now.strftime("%Y-%m-%d %H:%M:00")

        df = await client.get_intraday_data(
            security_id=spec.security_id,
            exchange_segment=spec.exchange_segment,
            instrument=spec.instrument,
            interval=self.interval,
            from_date=from_str,
            to_date=to_str,
        )
        if df is None or df.empty:
            return {"_skip_reason": "no_data"}

        if len(df) < max(self.ema_slow, self.rsi_period, self.atr_period) + 5:
            return {"_skip_reason": "insufficient_bars"}

        df = df.copy().reset_index(drop=True)
        df["ema_fast"] = _ema(df["close"], self.ema_fast)
        df["ema_slow"] = _ema(df["close"], self.ema_slow)
        df["rsi"] = _rsi(df["close"], self.rsi_period)
        df["atr"] = _atr(df["high"], df["low"], df["close"], self.atr_period)
        df["vwap"] = _vwap_by_session(df)

        last = df.iloc[-1]
        last_time = pd.to_datetime(last["timestamp"]).time()

        if not self._passes_time_filters(last_time):
            return {"_skip_reason": "time_filter"}

        trend = self._detect_trend(df)
        setup = self._detect_setup(df, trend)
        if not setup:
            debug = self._build_intraday_debug(df, trend)
            return {"_skip_reason": "no_setup", "_debug": debug}

        direction = setup["direction"]
        strike_pick = await self._pick_strike(
            client=client,
            security_id=spec.security_id,
            segment=spec.option_segment,
            quote_segment=spec.option_quote_segment,
            direction=direction,
            spot_price=float(last["close"]),
            expiry_policy=self.expiry_policy,
            lot_size=spec.lot_size,
        )
        if not strike_pick:
            debug = self._build_intraday_debug(df, trend)
            return {"_skip_reason": "no_strike", "_debug": debug}

        atr_val = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
        if atr_val <= 0:
            debug = self._build_intraday_debug(df, trend)
            return {"_skip_reason": "atr_missing", "_debug": debug}

        sl_points, target_points = self._risk_points(atr_val, setup["setup_type"])
        spot = float(last["close"])
        if direction == "CE":
            default_sl = spot - sl_points
            default_target = spot + target_points
        else:
            default_sl = spot + sl_points
            default_target = spot - target_points

        support, resistance = self._nearest_intraday_sr(df, spot)
        support, resistance = self._fill_sr_from_confluence(
            direction,
            spot,
            float(last["ema_fast"]),
            float(last["vwap"]),
            support,
            resistance,
        )
        # Ignore stale/far S/R levels. Using a support several ATRs away can produce
        # absurd premium entry/SL ranges (e.g. ₹0-₹2 while option LTP is ₹100+).
        max_sr_dist = self.max_sr_distance_atr * atr_val
        if support is not None and abs(spot - support) > max_sr_dist:
            support = None
        if resistance is not None and abs(resistance - spot) > max_sr_dist:
            resistance = None

        if direction == "CE":
            entry_level = support if support is not None else spot
            target_price = resistance if resistance is not None else default_target
            sl_price = support - self.sl_atr_buffer * atr_val if support is not None else default_sl
        else:
            entry_level = resistance if resistance is not None else spot
            target_price = support if support is not None else default_target
            sl_price = resistance + self.sl_atr_buffer * atr_val if resistance is not None else default_sl

        entry_low = entry_level - self.entry_atr_buffer * atr_val
        entry_high = entry_level + self.entry_atr_buffer * atr_val
        exit_low = target_price - self.exit_atr_buffer * atr_val
        exit_high = target_price + self.exit_atr_buffer * atr_val

        premium = strike_pick["premium"]
        delta = abs(strike_pick.get("delta") or 0.45)
        premium_target = self._premium_from_spot_level(target_price, spot, premium, delta, direction)
        premium_sl = self._premium_from_spot_level(sl_price, spot, premium, delta, direction)
        entry_premium_low, entry_premium_high = self._premium_range_for_spot(
            entry_low,
            entry_high,
            spot,
            premium,
            delta,
            direction,
        )
        exit_premium_low, exit_premium_high = self._premium_range_for_spot(
            exit_low,
            exit_high,
            spot,
            premium,
            delta,
            direction,
        )

        signal_payload = {
            "index": spec.name,
            "security_id": spec.security_id,
            "option_quote_segment": spec.option_quote_segment,
            "direction": direction,
            "setup_type": setup["setup_type"],
            "horizon": "intraday",
            "score": round(setup["score"], 3),
            "spot": round(spot, 2),
            "ema_fast": round(float(last["ema_fast"]), 2),
            "ema_slow": round(float(last["ema_slow"]), 2),
            "rsi": round(float(last["rsi"]), 1),
            "vwap": round(float(last["vwap"]), 2),
            "atr": round(float(atr_val), 2),
            "sl_price": round(sl_price, 2),
            "target_price": round(target_price, 2),
            "premium_target": premium_target,
            "premium_sl": premium_sl,
            "entry_premium_low": entry_premium_low,
            "entry_premium_high": entry_premium_high,
            "exit_premium_low": exit_premium_low,
            "exit_premium_high": exit_premium_high,
            "expiry": strike_pick["expiry"],
            "strike": strike_pick["strike"],
            "premium": premium,
            "delta": strike_pick.get("delta"),
            "oi": strike_pick.get("oi"),
            "volume": strike_pick.get("volume"),
            "spread_pct": strike_pick.get("spread_pct"),
            "lot_size": strike_pick.get("lot_size") or spec.lot_size,
            "option_security_id": strike_pick.get("option_security_id", ""),
        }

        signal_id = self._save_signal(spec, signal_payload)
        signal_payload["signal_id"] = signal_id
        return signal_payload

    async def _scan_one_index_positional(self, spec: IndexSpec, client: DhanClient) -> Optional[dict]:
        to_date = date.today()
        from_date = to_date - timedelta(days=self.pos_lookback_days)

        df = await client.get_historical_daily_data(
            security_id=spec.security_id,
            exchange_segment=spec.exchange_segment,
            instrument=spec.instrument,
            from_date=from_date,
            to_date=to_date,
        )
        if df is None or df.empty:
            return {"_skip_reason": "no_data"}

        if len(df) < max(self.pos_ema_slow, self.atr_period) + 5:
            return {"_skip_reason": "insufficient_bars"}

        df = df.copy().reset_index(drop=True)
        df["ema_fast"] = _ema(df["close"], self.pos_ema_fast)
        df["ema_mid"] = _ema(df["close"], self.pos_ema_mid)
        df["ema_slow"] = _ema(df["close"], self.pos_ema_slow)
        df["rsi"] = _rsi(df["close"], self.rsi_period)
        df["atr"] = _atr(df["high"], df["low"], df["close"], self.atr_period)

        last = df.iloc[-1]
        setup = self._detect_positional_setup(df)
        if not setup:
            debug = self._build_positional_debug(df)
            return {"_skip_reason": "no_setup", "_debug": debug}

        direction = setup["direction"]
        strike_pick = await self._pick_strike(
            client=client,
            security_id=spec.security_id,
            segment=spec.option_segment,
            quote_segment=spec.option_quote_segment,
            direction=direction,
            spot_price=float(last["close"]),
            expiry_policy=self.positional_expiry_policy,
            lot_size=spec.lot_size,
        )
        if not strike_pick:
            debug = self._build_positional_debug(df)
            return {"_skip_reason": "no_strike", "_debug": debug}

        atr_val = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
        if atr_val <= 0:
            debug = self._build_positional_debug(df)
            return {"_skip_reason": "atr_missing", "_debug": debug}

        spot = float(last["close"])
        sl_points = self.pos_sl_atr * atr_val
        target_points = self.pos_target_atr * atr_val
        if direction == "CE":
            sl_price = round(spot - sl_points, 2)
            target_price = round(spot + target_points, 2)
        else:
            sl_price = round(spot + sl_points, 2)
            target_price = round(spot - target_points, 2)

        premium = strike_pick["premium"]
        delta = abs(strike_pick.get("delta") or 0.45)
        premium_target = round(premium + delta * abs(target_price - spot), 2)
        premium_sl = round(max(0.1, premium - delta * abs(spot - sl_price)), 2)

        signal_payload = {
            "index": spec.name,
            "security_id": spec.security_id,
            "option_quote_segment": spec.option_quote_segment,
            "direction": direction,
            "setup_type": setup["setup_type"],
            "horizon": "positional",
            "score": round(setup["score"], 3),
            "spot": round(spot, 2),
            "ema_fast": round(float(last["ema_fast"]), 2),
            "ema_slow": round(float(last["ema_slow"]), 2),
            "rsi": round(float(last["rsi"]), 1),
            "atr": round(float(atr_val), 2),
            "sl_price": sl_price,
            "target_price": target_price,
            "premium_target": premium_target,
            "premium_sl": premium_sl,
            "expiry": strike_pick["expiry"],
            "strike": strike_pick["strike"],
            "premium": premium,
            "delta": strike_pick.get("delta"),
            "oi": strike_pick.get("oi"),
            "volume": strike_pick.get("volume"),
            "spread_pct": strike_pick.get("spread_pct"),
            "lot_size": strike_pick.get("lot_size") or spec.lot_size,
            "option_security_id": strike_pick.get("option_security_id", ""),
        }

        signal_id = self._save_signal(spec, signal_payload)
        signal_payload["signal_id"] = signal_id
        return signal_payload

    def _passes_time_filters(self, last_time: time) -> bool:
        if self.allow_after_close:
            return True
        if last_time < self.trade_start or last_time > self.trade_end:
            return False

        mkt_open = time(9, 15)
        minutes_from_open = (datetime.combine(date.today(), last_time) - datetime.combine(date.today(), mkt_open)).seconds / 60
        if minutes_from_open < self.skip_open_minutes:
            return False

        if self.use_lunch_filter and self.lunch_start <= last_time <= self.lunch_end:
            return False

        return True

    def _detect_trend(self, df: pd.DataFrame) -> dict:
        close = df["close"].astype(float)
        ema_fast = df["ema_fast"].astype(float)
        ema_slow = df["ema_slow"].astype(float)

        cur_close = float(close.iloc[-1])
        cur_fast = float(ema_fast.iloc[-1])
        cur_slow = float(ema_slow.iloc[-1])

        slope_lb = max(2, self.ema_slope_lookback)
        slow_prev = float(ema_slow.iloc[-slope_lb]) if len(ema_slow) > slope_lb else cur_slow
        slope = (cur_slow - slow_prev) / slow_prev if slow_prev else 0

        bull = cur_fast > cur_slow and slope > 0
        bear = cur_fast < cur_slow and slope < 0

        direction = "CE" if bull else "PE" if bear else "neutral"
        ema_gap = abs(cur_fast - cur_slow) / cur_close if cur_close else 0
        return {
            "direction": direction,
            "slope": slope,
            "ema_gap_pct": ema_gap,
            "price_vs_fast": (cur_close - cur_fast) / cur_close if cur_close else 0,
        }

    def _detect_setup(self, df: pd.DataFrame, trend: dict) -> Optional[dict]:
        last = _last_trade_row(df)
        prev = df.iloc[-2] if len(df) > 1 else last

        spot = float(last["close"])
        ema_fast = float(last["ema_fast"])
        vwap = float(last["vwap"])
        rsi = float(last["rsi"])
        atr_val = float(last.get("atr", 0) or 0)

        candle_bull = last["close"] > last["open"]
        candle_bear = last["close"] < last["open"]
        candle_range = max(1e-9, float(last["high"]) - float(last["low"]))
        wick_up = (float(last["close"]) - float(last["low"])) / candle_range
        wick_down = (float(last["high"]) - float(last["close"])) / candle_range
        bullish_reject = wick_up >= self.reversal_wick_pct
        bearish_reject = wick_down >= self.reversal_wick_pct
        bullish_ok = candle_bull or bullish_reject
        bearish_ok = candle_bear or bearish_reject
        prev_range = max(1e-9, float(prev["high"]) - float(prev["low"]))
        prev_wick_up = (float(prev["close"]) - float(prev["low"])) / prev_range
        prev_wick_down = (float(prev["high"]) - float(prev["close"])) / prev_range
        prev_bullish_reject = prev_wick_up >= self.reversal_wick_pct
        prev_bearish_reject = prev_wick_down >= self.reversal_wick_pct

        trend_dir = trend["direction"]
        ema_gap_pct = trend["ema_gap_pct"]
        slope = trend["slope"]

        near_ema = abs(spot - ema_fast) / spot <= self.pullback_pct if spot else False
        near_vwap = abs(spot - vwap) / spot <= self.vwap_pullback_pct if spot else False
        vwap_reclaim_ok = True
        if self.trend_require_vwap_reclaim:
            vwap_reclaim_ok = spot >= vwap if trend_dir == "CE" else spot <= vwap

        trend_slope_ok = (
            slope >= self.trend_min_slope_pct if trend_dir == "CE" else slope <= -self.trend_min_slope_pct
        )
        if trend_dir == "CE" and (near_ema or near_vwap) and bullish_ok and vwap_reclaim_ok:
            if trend_slope_ok and rsi >= self.trend_setup_rsi_min:
                return {"setup_type": "trend", "direction": "CE", "score": 0.7 + min(0.3, ema_gap_pct * 10)}
        if trend_dir == "PE" and (near_ema or near_vwap) and bearish_ok and vwap_reclaim_ok:
            if trend_slope_ok and rsi <= self.trend_setup_rsi_max:
                return {"setup_type": "trend", "direction": "PE", "score": 0.7 + min(0.3, ema_gap_pct * 10)}

        flat = ema_gap_pct <= self.flat_trend_pct and abs(slope) <= self.flat_slope_pct
        trend_strength_ok = abs(slope) <= self.mean_revert_max_slope_pct
        if (flat or self.mean_revert_allow_trend) and trend_strength_ok:
            dev_pct = (spot - vwap) / vwap if vwap else 0
            overshoot_atr = abs(spot - vwap) / atr_val if atr_val > 0 else 0
            overshoot_ok = overshoot_atr >= self.mean_revert_min_atr_overshoot
            override_bull = self.allow_mean_revert_without_candle and rsi <= self.mean_revert_candle_override_rsi
            override_bear = self.allow_mean_revert_without_candle and rsi >= self.mean_revert_candle_override_rsi_high
            bull_confirm = (
                (self.mean_revert_two_bar_confirm and bullish_reject and prev_bullish_reject)
                or (not self.mean_revert_two_bar_confirm and bullish_ok)
                or override_bull
            )
            bear_confirm = (
                (self.mean_revert_two_bar_confirm and bearish_reject and prev_bearish_reject)
                or (not self.mean_revert_two_bar_confirm and bearish_ok)
                or override_bear
            )

            if dev_pct <= -self.vwap_dev_pct and rsi <= self.rsi_oversold and bull_confirm and overshoot_ok:
                return {"setup_type": "mean_revert", "direction": "CE", "score": 0.6 + min(0.4, abs(dev_pct) * 20)}
            if dev_pct >= self.vwap_dev_pct and rsi >= self.rsi_overbought and bear_confirm and overshoot_ok:
                return {"setup_type": "mean_revert", "direction": "PE", "score": 0.6 + min(0.4, abs(dev_pct) * 20)}

        if self.allow_trend_continuation:
            if trend_dir == "CE" and rsi >= self.trend_rsi_min and (candle_bull or bullish_reject):
                score = 0.55 + min(0.25, ema_gap_pct * 8)
                return {"setup_type": "trend_continuation", "direction": "CE", "score": score}
            if trend_dir == "PE" and rsi <= self.trend_rsi_max and (candle_bear or bearish_reject):
                score = 0.55 + min(0.25, ema_gap_pct * 8)
                return {"setup_type": "trend_continuation", "direction": "PE", "score": score}

        return None

    def _build_intraday_debug(self, df: pd.DataFrame, trend: dict) -> dict:
        last = _last_trade_row(df)
        spot = float(last["close"])
        ema_fast = float(last["ema_fast"])
        ema_slow = float(last["ema_slow"])
        vwap = float(last["vwap"])
        rsi = float(last["rsi"])
        atr_val = float(last.get("atr", 0) or 0)

        near_ema = abs(spot - ema_fast) / spot if spot else 0
        near_vwap = abs(spot - vwap) / spot if spot else 0
        dev_vwap = (spot - vwap) / vwap if vwap else 0
        overshoot_atr = abs(spot - vwap) / atr_val if atr_val > 0 else 0
        candle_bull = last["close"] > last["open"]
        candle_bear = last["close"] < last["open"]

        return {
            "spot": round(spot, 2),
            "ema_fast": round(ema_fast, 2),
            "ema_slow": round(ema_slow, 2),
            "vwap": round(vwap, 2),
            "rsi": round(rsi, 1),
            "atr": round(atr_val, 2),
            "trend_dir": trend.get("direction"),
            "ema_gap_pct": round(trend.get("ema_gap_pct", 0) * 100, 3),
            "slope": round(trend.get("slope", 0) * 100, 3),
            "near_ema_pct": round(near_ema * 100, 3),
            "near_vwap_pct": round(near_vwap * 100, 3),
            "vwap_dev_pct": round(dev_vwap * 100, 3),
            "vwap_dev_atr": round(overshoot_atr, 2),
            "candle": "bull" if candle_bull else "bear" if candle_bear else "flat",
            "volume": float(last.get("volume", 0) or 0),
        }

    def _detect_positional_setup(self, df: pd.DataFrame) -> Optional[dict]:
        last = _last_trade_row(df)
        prev = df.iloc[-2]

        close = df["close"].astype(float)
        ema_fast = df["ema_fast"].astype(float)
        ema_mid = df["ema_mid"].astype(float)
        ema_slow = df["ema_slow"].astype(float)

        cur_close = float(last["close"])
        cur_fast = float(last["ema_fast"])
        cur_mid = float(last["ema_mid"])
        cur_slow = float(last["ema_slow"])
        rsi = float(last["rsi"])

        slope_lb = max(2, self.pos_slope_lookback)
        mid_prev = float(ema_mid.iloc[-slope_lb]) if len(ema_mid) > slope_lb else cur_mid
        slope = (cur_mid - mid_prev) / mid_prev if mid_prev else 0

        trend_up = cur_fast >= cur_mid >= cur_slow and slope > 0
        trend_down = cur_fast <= cur_mid <= cur_slow and slope < 0

        candle_bull = last["close"] > last["open"]
        candle_bear = last["close"] < last["open"]
        candle_range = max(1e-9, float(last["high"]) - float(last["low"]))
        wick_up = (float(last["close"]) - float(last["low"])) / candle_range
        wick_down = (float(last["high"]) - float(last["close"])) / candle_range
        bullish_reject = wick_up >= self.reversal_wick_pct
        bearish_reject = wick_down >= self.reversal_wick_pct
        bullish_ok = candle_bull or bullish_reject
        bearish_ok = candle_bear or bearish_reject

        near_fast = abs(cur_close - cur_fast) / cur_close <= self.pos_pullback_pct if cur_close else False
        near_mid = abs(cur_close - cur_mid) / cur_close <= self.pos_pullback_mid_pct if cur_close else False

        lookback = max(5, self.pos_breakout_lookback)
        high_ref = float(df["high"].iloc[-lookback:-1].max())
        low_ref = float(df["low"].iloc[-lookback:-1].min())
        breakout_up = cur_close > high_ref and trend_up
        breakout_down = cur_close < low_ref and trend_down

        if breakout_up:
            score = 0.8 + min(0.2, abs(slope) * 50)
            return {"setup_type": "positional_breakout", "direction": "CE", "score": score}
        if breakout_down:
            score = 0.8 + min(0.2, abs(slope) * 50)
            return {"setup_type": "positional_breakout", "direction": "PE", "score": score}

        if trend_up and (near_fast or near_mid) and (bullish_ok or rsi >= self.pos_pullback_rsi_min):
            score = 0.75 + min(0.2, abs(slope) * 40)
            return {"setup_type": "positional_trend", "direction": "CE", "score": score}
        if trend_down and (near_fast or near_mid) and (bearish_ok or rsi <= self.pos_pullback_rsi_max):
            score = 0.75 + min(0.2, abs(slope) * 40)
            return {"setup_type": "positional_trend", "direction": "PE", "score": score}

        if self.pos_allow_trend_continuation:
            if trend_up and rsi >= self.pos_trend_rsi_min and bullish_ok:
                score = 0.6 + min(0.2, abs(slope) * 40)
                return {"setup_type": "positional_trend", "direction": "CE", "score": score}
            if trend_down and rsi <= self.pos_trend_rsi_max and bearish_ok:
                score = 0.6 + min(0.2, abs(slope) * 40)
                return {"setup_type": "positional_trend", "direction": "PE", "score": score}

        return None

    def _build_positional_debug(self, df: pd.DataFrame) -> dict:
        last = _last_trade_row(df)
        spot = float(last["close"])
        ema_fast = float(last["ema_fast"])
        ema_mid = float(last["ema_mid"])
        ema_slow = float(last["ema_slow"])
        rsi = float(last["rsi"])

        slope_lb = max(2, self.pos_slope_lookback)
        mid_prev = float(df["ema_mid"].iloc[-slope_lb]) if len(df) > slope_lb else ema_mid
        slope = (ema_mid - mid_prev) / mid_prev if mid_prev else 0

        lookback = max(5, self.pos_breakout_lookback)
        high_ref = float(df["high"].iloc[-lookback:-1].max())
        low_ref = float(df["low"].iloc[-lookback:-1].min())

        return {
            "spot": round(spot, 2),
            "ema_fast": round(ema_fast, 2),
            "ema_mid": round(ema_mid, 2),
            "ema_slow": round(ema_slow, 2),
            "rsi": round(rsi, 1),
            "slope": round(slope * 100, 3),
            "breakout_high": round(high_ref, 2),
            "breakout_low": round(low_ref, 2),
            "volume": float(last.get("volume", 0) or 0),
            "near_fast_pct": round(abs(spot - ema_fast) / spot * 100, 3) if spot else 0,
            "near_mid_pct": round(abs(spot - ema_mid) / spot * 100, 3) if spot else 0,
        }

    def _swing_levels(self, df: pd.DataFrame) -> tuple[list[float], list[float]]:
        lookback = max(10, self.sr_lookback_bars)
        window = max(1, self.sr_swing_window)
        if len(df) < lookback + (window * 2 + 1):
            return [], []

        start = max(0, len(df) - lookback)
        highs: list[float] = []
        lows: list[float] = []
        for idx in range(start + window, len(df) - window):
            window_high = float(df["high"].iloc[idx - window: idx + window + 1].max())
            window_low = float(df["low"].iloc[idx - window: idx + window + 1].min())
            high = float(df["high"].iloc[idx])
            low = float(df["low"].iloc[idx])
            if high >= window_high:
                highs.append(round(high, 2))
            if low <= window_low:
                lows.append(round(low, 2))

        return sorted(set(lows)), sorted(set(highs))

    def _nearest_intraday_sr(self, df: pd.DataFrame, spot: float) -> tuple[Optional[float], Optional[float]]:
        lows, highs = self._swing_levels(df)
        support = max((lvl for lvl in lows if lvl <= spot), default=None)
        resistance = min((lvl for lvl in highs if lvl >= spot), default=None)
        return support, resistance

    def _fill_sr_from_confluence(
        self,
        direction: str,
        spot: float,
        ema_fast: float,
        vwap: float,
        support: Optional[float],
        resistance: Optional[float],
    ) -> tuple[Optional[float], Optional[float]]:
        candidates = [level for level in (ema_fast, vwap) if level and not pd.isna(level)]
        if direction == "CE" and support is None:
            below = [level for level in candidates if level <= spot]
            if below:
                support = max(below)
        if direction == "PE" and resistance is None:
            above = [level for level in candidates if level >= spot]
            if above:
                resistance = min(above)
        return support, resistance

    def _premium_from_spot_level(
        self,
        level: float,
        spot: float,
        premium: float,
        delta: float,
        direction: str,
    ) -> float:
        sign = 1 if direction == "CE" else -1
        value = premium + sign * delta * (level - spot)
        return round(max(0.05, value), 2)

    def _premium_range_for_spot(
        self,
        low_level: float,
        high_level: float,
        spot: float,
        premium: float,
        delta: float,
        direction: str,
    ) -> tuple[Optional[float], Optional[float]]:
        if low_level is None or high_level is None:
            return None, None
        p1 = self._premium_from_spot_level(low_level, spot, premium, delta, direction)
        p2 = self._premium_from_spot_level(high_level, spot, premium, delta, direction)
        return (min(p1, p2), max(p1, p2))

    def _risk_points(self, atr_val: float, setup_type: str) -> tuple[float, float]:
        if setup_type == "mean_revert":
            return self.sl_atr_mean_revert * atr_val, self.target_atr_mean_revert * atr_val
        return self.sl_atr_trend * atr_val, self.target_atr_trend * atr_val

    async def _pick_strike(
        self,
        *,
        client: DhanClient,
        security_id: str,
        segment: str,
        quote_segment: str,
        direction: str,
        spot_price: float,
        expiry_policy: str,
        lot_size: int,
    ) -> Optional[dict]:
        expiries = await client.get_expiry_list(security_id, segment)
        exp = self._select_expiry(expiries, expiry_policy)
        if not exp:
            return None

        chain = await client.get_option_chain(security_id, segment, exp)
        if not chain or not isinstance(chain.get("data"), dict):
            return None

        chain_data = chain["data"]
        oc = chain_data.get("oc", {})
        if not oc:
            return None

        side = "ce" if direction == "CE" else "pe"
        strikes: list[dict] = []
        for strike_str, strike_data in oc.items():
            try:
                strike = float(strike_str)
            except (TypeError, ValueError):
                continue
            opt = strike_data.get(side, {}) if isinstance(strike_data, dict) else {}
            if not opt or not opt.get("last_price"):
                continue

            premium = float(opt.get("last_price", 0))
            oi = int(opt.get("oi", 0))
            volume = int(opt.get("volume", 0))
            bid = float(opt.get("top_bid_price", 0) or 0)
            ask = float(opt.get("top_ask_price", 0) or 0)
            spread = round(ask - bid, 2) if ask and bid else 0
            spread_pct = round((spread / premium * 100), 1) if premium > 0 else 99
            iv = opt.get("implied_volatility", 0) or 0
            greeks = opt.get("greeks", {}) or {}
            delta = greeks.get("delta")

            moneyness = (strike - spot_price) / spot_price * 100
            if direction == "PE":
                moneyness = -moneyness

            if abs(moneyness) > self.moneyness_limit_pct:
                continue
            if spread_pct > self.max_spread_pct:
                continue
            if oi < self.min_liquidity_oi:
                continue
            if premium < self.min_option_premium:
                continue
            if delta is not None and abs(float(delta)) < self.min_delta:
                continue

            strikes.append(
                {
                    "strike": strike,
                    "premium": round(premium, 2),
                    "iv": round(float(iv), 2) if iv else None,
                    "delta": round(float(delta), 4) if delta is not None else None,
                    "oi": oi,
                    "volume": volume,
                    "spread_pct": spread_pct,
                    "moneyness": round(moneyness, 2),
                    "option_security_id": str(opt.get("security_id", "")),
                }
            )

        if not strikes:
            return None

        if self.depth_filter_enabled:
            strikes = await self._filter_strikes_by_depth(
                client,
                strikes,
                quote_segment,
                direction,
            )
            if not strikes:
                logger.info(
                    "Index depth filter removed all strikes %s/%s",
                    security_id,
                    direction,
                )
                return None

        max_oi = max(s["oi"] for s in strikes) or 1
        max_vol = max(s["volume"] for s in strikes) or 1
        total_oi = sum(s["oi"] for s in strikes) or 1

        def _score_strike(s: dict) -> float:
            score = 0.0
            abs_delta = abs(s["delta"]) if s["delta"] is not None else 0

            oi_pct = s["oi"] / max_oi if max_oi > 0 else 0
            oi_conc = s["oi"] / total_oi if total_oi > 0 else 0
            score += 20 * oi_pct
            if oi_conc >= 0.15:
                score += 5
            elif oi_conc >= 0.05:
                score += 3

            vol_pct = s["volume"] / max_vol if max_vol > 0 else 0
            score += 15 * vol_pct
            if s["volume"] > 0 and s["oi"] > 0:
                score += 5

            if 0.40 <= abs_delta <= 0.60:
                score += 20
            elif 0.30 <= abs_delta <= 0.70:
                score += 14
            elif abs_delta > 0.70:
                score += 8

            if s["spread_pct"] < 1.0:
                score += 10
            elif s["spread_pct"] < 3.0:
                score += 6
            elif s["spread_pct"] < 5.0:
                score += 3

            abs_m = abs(s["moneyness"])
            if abs_m <= 0.5:
                score += 10
            elif abs_m <= 1.5:
                score += 7
            elif abs_m <= 2.0:
                score += 3

            return round(score, 1)

        for s in strikes:
            s["score"] = _score_strike(s)

        best = max(strikes, key=lambda x: x["score"])
        best["expiry"] = exp
        best["lot_size"] = int(lot_size or 1)
        return best

    async def _filter_strikes_by_depth(
        self,
        client: DhanClient,
        strikes: list[dict],
        quote_segment: str,
        direction: str,
    ) -> list[dict]:
        security_ids = [s["option_security_id"] for s in strikes if s.get("option_security_id")]
        if not security_ids:
            return strikes

        quotes = await client.get_market_quote_quote(security_ids, quote_segment)
        if not quotes or "data" not in quotes:
            logger.warning("Market depth quote missing (%s)", quote_segment)
            return strikes if not self.depth_require_data else []

        segment_data = quotes.get("data", {}).get(quote_segment, {}) or {}
        filtered: list[dict] = []
        for strike in strikes:
            sec_id = strike.get("option_security_id")
            key = str(sec_id) if sec_id is not None else ""
            quote = segment_data.get(key)
            if quote is None and key.isdigit():
                quote = segment_data.get(str(int(key)))
            if not isinstance(quote, dict):
                if self.depth_require_data:
                    continue
                filtered.append(strike)
                continue

            depth = quote.get("depth") or {}
            buy_levels = depth.get("buy") or []
            sell_levels = depth.get("sell") or []
            top_bid = buy_levels[0] if buy_levels else {}
            top_ask = sell_levels[0] if sell_levels else {}

            bid_px = float(top_bid.get("price", 0) or 0)
            ask_px = float(top_ask.get("price", 0) or 0)
            top_bid_qty = int(top_bid.get("quantity", 0) or 0)
            top_ask_qty = int(top_ask.get("quantity", 0) or 0)

            spread_pct = 0.0
            if bid_px and ask_px:
                mid = (bid_px + ask_px) / 2
                spread_pct = round((ask_px - bid_px) / mid * 100, 2) if mid else 0.0

            buy_qty = int(quote.get("buy_quantity", 0) or 0)
            sell_qty = int(quote.get("sell_quantity", 0) or 0)
            total_qty = buy_qty + sell_qty
            imbalance = (buy_qty - sell_qty) / total_qty if total_qty else 0.0

            strike["depth_spread_pct"] = spread_pct
            strike["depth_top_bid_qty"] = top_bid_qty
            strike["depth_top_ask_qty"] = top_ask_qty
            strike["depth_total_qty"] = total_qty
            strike["depth_imbalance"] = round(imbalance, 3)

            if spread_pct and spread_pct > self.depth_max_spread_pct:
                continue
            if top_bid_qty < self.depth_min_top_qty or top_ask_qty < self.depth_min_top_qty:
                continue
            if total_qty < self.depth_min_total_qty:
                continue
            if self.depth_min_imbalance > 0:
                if direction == "CE" and imbalance < self.depth_min_imbalance:
                    continue
                if direction == "PE" and imbalance > -self.depth_min_imbalance:
                    continue

            filtered.append(strike)

        return filtered

    def _select_expiry(self, expiries: list[str] | None, policy: str) -> Optional[str]:
        if not expiries:
            return None
        today = date.today()
        parsed = []
        for exp in sorted(expiries):
            try:
                d = datetime.strptime(exp, "%Y-%m-%d").date()
                if d >= today:
                    parsed.append(d)
            except ValueError:
                continue

        if not parsed:
            return None

        parsed.sort()
        if policy == "next_week" and len(parsed) > 1:
            return parsed[1].isoformat()
        if policy == "monthly":
            first = parsed[0]
            same_month = [d for d in parsed if d.month == first.month and d.year == first.year]
            return same_month[-1].isoformat() if same_month else parsed[0].isoformat()
        return parsed[0].isoformat()

    def _save_signal(self, spec: IndexSpec, payload: dict) -> int:
        today = date.today()
        existing = self.db.query(Signal).filter(
            Signal.security_id == spec.security_id,
            Signal.section == "index",
            Signal.status == "active",
        ).first()

        if existing:
            existing.current_score = payload["score"]
            existing.peak_score = max(existing.peak_score, payload["score"])
            existing.last_updated_date = today
            existing.signal_type = payload["direction"]
            existing.trend_score = payload["score"]
            existing.close_price_at_detection = payload["spot"]
            return existing.id

        past_count = self.db.query(Signal).filter(
            Signal.security_id == spec.security_id,
            Signal.section == "index",
        ).count()

        sig = Signal(
            security_id=spec.security_id,
            symbol=spec.name,
            section="index",
            signal_type=payload["direction"],
            occurrence_number=past_count + 1,
            first_detected_date=today,
            last_updated_date=today,
            current_score=payload["score"],
            peak_score=payload["score"],
            status="active",
            close_price_at_detection=payload["spot"],
            trend_score=payload["score"],
        )
        self.db.add(sig)
        self.db.flush()
        return sig.id

    def _invalidate_stale_signals(self) -> int:
        today = date.today()
        signals = self.db.query(Signal).filter(
            Signal.section == "index",
            Signal.status == "active",
        ).all()
        count = 0
        for sig in signals:
            if sig.last_updated_date != today:
                sig.status = "invalidated"
                sig.invalidation_reason = "stale_intraday"
                sig.last_updated_date = today
                count += 1
        if count:
            self.db.commit()
        return count
