"""
Intraday Advances/Declines scanner for F&O stocks.

Workflow:
1) Compute breadth (A/D) from F&O universe, excluding big gap-ups/downs.
2) If breadth is strong, score momentum stocks and shortlist top N.
3) Apply weighted OI + price relationship boosts (options + futures if available).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import Instrument, Signal
from backend.services.dhan_client import DhanClient, download_scrip_master

logger = logging.getLogger(__name__)


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
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = _atr(high, low, close, period=1)
    tr_smooth = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / tr_smooth.replace(0, np.nan))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.rolling(period).mean().fillna(0)


def _vwap_today(df: pd.DataFrame) -> pd.Series:
    ts = pd.to_datetime(df["timestamp"])
    today = ts.dt.date.max()
    day = df[ts.dt.date == today].copy()
    typical = (day["high"] + day["low"] + day["close"]) / 3.0
    vol = day["volume"].fillna(0)
    cum_vol = vol.cumsum()
    vwap = (typical * vol).cumsum() / cum_vol.replace(0, np.nan)
    return vwap.fillna(day["close"])


def _signal_direction(signal_type: str) -> str:
    st = (signal_type or "").upper()
    if st.startswith("CE"):
        return "CE"
    if st.startswith("PE"):
        return "PE"
    return st


@dataclass
class FutRef:
    security_id: str
    expiry: date


_FUT_CACHE: dict[str, list[FutRef]] = {}


async def _load_futures_map() -> dict[str, list[FutRef]]:
    if _FUT_CACHE:
        return _FUT_CACHE
    df = await download_scrip_master()
    fut = df[
        (df["EXCH_ID"] == "NSE")
        & (df["SEGMENT"] == "D")
        & (df["INSTRUMENT"].isin(["FUTSTK", "FUTIDX"]))
    ].copy()
    out: dict[str, list[FutRef]] = {}
    for _, row in fut.iterrows():
        sym = str(row.get("UNDERLYING_SYMBOL", "")).strip().upper()
        sec_id = str(int(row.get("SECURITY_ID"))) if pd.notna(row.get("SECURITY_ID")) else ""
        exp_raw = row.get("SM_EXPIRY_DATE")
        try:
            exp = datetime.strptime(str(exp_raw), "%Y-%m-%d").date()
        except Exception:
            continue
        if sym and sec_id:
            out.setdefault(sym, []).append(FutRef(security_id=sec_id, expiry=exp))
    for k in out:
        out[k].sort(key=lambda x: x.expiry)
    _FUT_CACHE.update(out)
    return _FUT_CACHE


class AdvDeclIntradayScanner:
    def __init__(self, db: Session):
        self.db = db
        cfg = get_config().get("advdecl_intraday") or {}
        self.cfg = cfg

        self.interval = int(cfg.get("interval_minutes", 15))
        self.lookback_days = int(cfg.get("lookback_days", 5))
        self.sleep_between = float(cfg.get("request_delay", 0.35))
        self.max_candidates = int(cfg.get("max_candidates", 20))

        self.gap_exclude_pct = float(cfg.get("gap_exclude_pct", 1.5))
        self.ad_ratio_bull = float(cfg.get("ad_ratio_bull", 1.6))
        self.ad_ratio_bear = float(cfg.get("ad_ratio_bear", 0.6))

        self.ema_fast = int(cfg.get("ema_fast", 20))
        self.ema_slow = int(cfg.get("ema_slow", 50))
        self.rsi_period = int(cfg.get("rsi_period", 14))
        self.rsi_bull = float(cfg.get("rsi_bull", 55))
        self.rsi_bear = float(cfg.get("rsi_bear", 45))
        self.adx_period = int(cfg.get("adx_period", 14))
        self.adx_min = float(cfg.get("adx_min", 20))
        self.volume_spike_ratio = float(cfg.get("volume_spike_ratio", 1.5))
        self.rs_threshold = float(cfg.get("rs_threshold", 0.2))

        self.oi_weight_options = float(cfg.get("oi_weight_options", 0.4))
        self.oi_weight_futures = float(cfg.get("oi_weight_futures", 0.6))

        self.min_option_premium = float(cfg.get("min_option_premium", 10.0))
        self.min_delta = float(cfg.get("min_delta", 0.25))

        self.expiry_policy = str(cfg.get("expiry_policy", "weekly_nearest"))
        self.index_security_id = str(cfg.get("index_security_id", "13"))
        self.index_segment = str(cfg.get("index_segment", "IDX_I"))

    async def scan(self, instruments: list[Instrument]) -> dict:
        summary = {
            "scanned": 0,
            "signals": 0,
            "errors": 0,
            "advancers": 0,
            "decliners": 0,
            "ad_ratio": 0,
            "bias": "neutral",
        }

        client = DhanClient()
        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=self.lookback_days)
            from_str = from_dt.strftime("%Y-%m-%d 09:15:00")
            to_str = to_dt.strftime("%Y-%m-%d 15:30:00")

            index_change = await self._get_index_change(client, from_str, to_str)

            candidates = []
            for i, inst in enumerate(instruments):
                try:
                    if i > 0:
                        await asyncio.sleep(self.sleep_between)
                    df = await client.get_intraday_data(
                        security_id=inst.security_id,
                        exchange_segment="NSE_EQ",
                        instrument="EQUITY",
                        interval=self.interval,
                        from_date=from_str,
                        to_date=to_str,
                    )
                    if df is None or df.empty:
                        continue
                    summary["scanned"] += 1

                    day, prev_close = self._extract_today(df)
                    if day is None or day.empty or prev_close is None:
                        continue

                    open_today = float(day.iloc[0]["open"])
                    close_now = float(day.iloc[-1]["close"])
                    gap_pct = ((open_today - prev_close) / prev_close) * 100 if prev_close else 0
                    if abs(gap_pct) >= self.gap_exclude_pct:
                        continue

                    vwap_series = _vwap_today(df)
                    vwap = float(vwap_series.iloc[-1]) if not vwap_series.empty else close_now

                    adv = close_now > open_today and close_now > vwap
                    decl = close_now < open_today and close_now < vwap
                    if adv:
                        summary["advancers"] += 1
                    elif decl:
                        summary["decliners"] += 1

                    df["ema_fast"] = _ema(df["close"], self.ema_fast)
                    df["ema_slow"] = _ema(df["close"], self.ema_slow)
                    df["rsi"] = _rsi(df["close"], self.rsi_period)
                    df["adx"] = _adx(df["high"], df["low"], df["close"], self.adx_period)

                    last = day.iloc[-1]
                    ema_fast = float(df["ema_fast"].iloc[-1])
                    ema_slow = float(df["ema_slow"].iloc[-1])
                    rsi = float(df["rsi"].iloc[-1])
                    adx = float(df["adx"].iloc[-1])
                    vol = float(last["volume"])
                    vol_avg = float(day["volume"].tail(10).mean()) if len(day) >= 10 else float(day["volume"].mean())
                    vol_spike = (vol / vol_avg) if vol_avg else 0
                    rs = ((close_now - open_today) / open_today * 100) - index_change

                    candidates.append(
                        {
                            "instrument": inst,
                            "open": open_today,
                            "close": close_now,
                            "vwap": vwap,
                            "adv": adv,
                            "decl": decl,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "adx": adx,
                            "vol_spike": vol_spike,
                            "rs": rs,
                        }
                    )
                except Exception as e:
                    logger.error("Adv/Decl scan error %s: %s", inst.trading_symbol or inst.symbol, e)
                    summary["errors"] += 1

            if summary["decliners"] > 0:
                summary["ad_ratio"] = round(summary["advancers"] / summary["decliners"], 2)
            elif summary["advancers"] > 0:
                summary["ad_ratio"] = 9.99

            if summary["ad_ratio"] >= self.ad_ratio_bull:
                bias = "CE"
            elif summary["ad_ratio"] <= self.ad_ratio_bear and summary["decliners"] > 0:
                bias = "PE"
            else:
                bias = "neutral"
            summary["bias"] = bias

            if bias == "neutral":
                return summary

            shortlist = self._score_candidates(candidates, bias, index_change)
            shortlist.sort(key=lambda x: x["score"], reverse=True)
            shortlist = shortlist[: self.max_candidates]

            if shortlist:
                await self._apply_oi_boosts(client, shortlist, bias)

            for row in shortlist:
                signal = self._save_signal(row, bias)
                if signal:
                    summary["signals"] += 1

            return summary
        finally:
            await client.close()

    async def _get_index_change(self, client: DhanClient, from_str: str, to_str: str) -> float:
        df = await client.get_intraday_data(
            security_id=self.index_security_id,
            exchange_segment=self.index_segment,
            instrument="INDEX",
            interval=self.interval,
            from_date=from_str,
            to_date=to_str,
        )
        if df is None or df.empty:
            return 0.0
        day, prev_close = self._extract_today(df)
        if day is None or day.empty:
            return 0.0
        open_today = float(day.iloc[0]["open"])
        close_now = float(day.iloc[-1]["close"])
        return ((close_now - open_today) / open_today * 100) if open_today else 0.0

    def _extract_today(self, df: pd.DataFrame) -> tuple[Optional[pd.DataFrame], Optional[float]]:
        ts = pd.to_datetime(df["timestamp"])
        today = ts.dt.date.max()
        day = df[ts.dt.date == today].copy()
        prev_days = df[ts.dt.date < today]
        prev_close = float(prev_days.iloc[-1]["close"]) if not prev_days.empty else None
        return day, prev_close

    def _score_candidates(self, rows: list[dict], bias: str, index_change: float) -> list[dict]:
        scored = []
        for r in rows:
            if bias == "CE":
                if not r["adv"]:
                    continue
                if not (r["close"] > r["vwap"] and r["ema_fast"] > r["ema_slow"]):
                    continue
                if r["rsi"] < self.rsi_bull:
                    continue
                if r["adx"] < self.adx_min:
                    continue
                if r["vol_spike"] < self.volume_spike_ratio:
                    continue
                if r["rs"] < self.rs_threshold:
                    continue
            else:
                if not r["decl"]:
                    continue
                if not (r["close"] < r["vwap"] and r["ema_fast"] < r["ema_slow"]):
                    continue
                if r["rsi"] > self.rsi_bear:
                    continue
                if r["adx"] < self.adx_min:
                    continue
                if r["vol_spike"] < self.volume_spike_ratio:
                    continue
                if r["rs"] > -self.rs_threshold:
                    continue

            score = 0.4
            score += min(0.2, max(0, (r["adx"] - self.adx_min) / 20) * 0.2)
            score += min(0.15, max(0, (r["vol_spike"] - 1) / 1.5) * 0.15)
            score += min(0.15, max(0, abs(r["rs"]) / (self.rs_threshold * 2)) * 0.15)
            score += 0.1  # EMA alignment
            score += 0.1 if (bias == "CE" and r["rsi"] > 60) or (bias == "PE" and r["rsi"] < 40) else 0

            r["score"] = round(score, 3)
            scored.append(r)
        return scored

    async def _apply_oi_boosts(self, client: DhanClient, rows: list[dict], bias: str) -> None:
        fut_map = await _load_futures_map()
        for i, r in enumerate(rows):
            if i > 0:
                await asyncio.sleep(3.1)  # option chain rate limit
            try:
                opt_boost = await self._option_chain_oi_boost(client, r, bias)
                fut_boost = await self._futures_oi_boost(client, r, fut_map, bias)
                total_boost = opt_boost * self.oi_weight_options + fut_boost * self.oi_weight_futures
                r["score"] = round(r["score"] + total_boost, 3)
                r["oi_boost"] = round(total_boost, 3)
            except Exception as e:
                logger.warning("OI boost error for %s: %s", r["instrument"].trading_symbol or r["instrument"].symbol, e)

    async def _option_chain_oi_boost(self, client: DhanClient, r: dict, bias: str) -> float:
        inst = r["instrument"]
        expiries = await client.get_expiry_list(inst.security_id, "NSE_EQ")
        exp = self._select_expiry(expiries, self.expiry_policy)
        if not exp:
            return 0.0
        chain = await client.get_option_chain(inst.security_id, "NSE_EQ", exp)
        if not chain or not isinstance(chain.get("data"), dict):
            return 0.0
        oc = chain["data"].get("oc", {})
        spot = r["close"]
        atm = None
        for strike_str, strike_data in oc.items():
            try:
                strike = float(strike_str)
            except (TypeError, ValueError):
                continue
            if atm is None or abs(strike - spot) < abs(atm[0] - spot):
                atm = (strike, strike_data)
        if not atm:
            return 0.0
        strike, data = atm
        ce = (data or {}).get("ce", {}) if isinstance(data, dict) else {}
        pe = (data or {}).get("pe", {}) if isinstance(data, dict) else {}

        if bias == "CE":
            oi_now = float(ce.get("oi", 0))
            oi_prev = float(ce.get("previous_oi", 0))
            opt_price = ce.get("last_price") or ce.get("ltp") or ce.get("close") or 0
            delta = ce.get("delta")
            if delta is None and isinstance(ce.get("greeks"), dict):
                delta = ce["greeks"].get("delta")
        else:
            oi_now = float(pe.get("oi", 0))
            oi_prev = float(pe.get("previous_oi", 0))
            opt_price = pe.get("last_price") or pe.get("ltp") or pe.get("close") or 0
            delta = pe.get("delta")
            if delta is None and isinstance(pe.get("greeks"), dict):
                delta = pe["greeks"].get("delta")

        try:
            opt_price = float(opt_price)
        except (TypeError, ValueError):
            opt_price = 0
        try:
            delta = float(delta) if delta is not None else None
        except (TypeError, ValueError):
            delta = None

        if opt_price and opt_price < self.min_option_premium:
            return 0.0
        if delta is not None and abs(delta) < self.min_delta:
            return 0.0

        oi_delta = oi_now - oi_prev
        price_delta = r["close"] - r["open"]

        if price_delta > 0 and oi_delta > 0:
            return 0.1
        if price_delta > 0 and oi_delta < 0:
            return 0.05
        if price_delta < 0 and oi_delta > 0:
            return -0.1
        if price_delta < 0 and oi_delta < 0:
            return -0.05
        return 0.0

    async def _futures_oi_boost(
        self, client: DhanClient, r: dict, fut_map: dict[str, list[FutRef]], bias: str
    ) -> float:
        inst = r["instrument"]
        sym = (inst.trading_symbol or inst.symbol or "").upper()
        if sym not in fut_map:
            return 0.0
        today = date.today()
        futs = [f for f in fut_map[sym] if f.expiry >= today]
        if not futs:
            return 0.0
        fut_id = futs[0].security_id
        quote = await client.get_market_quote_ohlc([fut_id], "NSE_FNO")
        if not quote or not isinstance(quote.get("data"), dict):
            return 0.0
        oi_now = None
        oi_prev = None
        for seg_data in quote["data"].values():
            if not isinstance(seg_data, dict):
                continue
            for _sid, row in seg_data.items():
                if isinstance(row, dict):
                    oi_now = row.get("oi") or row.get("open_interest")
                    oi_prev = row.get("previous_oi") or row.get("prev_oi")
                    break
        if oi_now is None or oi_prev is None:
            return 0.0
        oi_delta = float(oi_now) - float(oi_prev)
        price_delta = r["close"] - r["open"]
        if price_delta > 0 and oi_delta > 0:
            return 0.1
        if price_delta > 0 and oi_delta < 0:
            return 0.05
        if price_delta < 0 and oi_delta > 0:
            return -0.1
        if price_delta < 0 and oi_delta < 0:
            return -0.05
        return 0.0

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

    def _save_signal(self, r: dict, bias: str) -> Optional[Signal]:
        inst = r["instrument"]
        today = date.today()
        sig_type = f"{bias}_AD"
        existing = (
            self.db.query(Signal)
            .filter(Signal.security_id == inst.security_id, Signal.section == "fno", Signal.status == "active")
            .first()
        )
        if existing:
            existing.current_score = r["score"]
            existing.peak_score = max(existing.peak_score, r["score"])
            existing.last_updated_date = today
            existing.signal_type = sig_type
            existing.trend_score = r["score"]
            existing.close_price_at_detection = r["close"]
            return existing

        past_count = self.db.query(Signal).filter(
            Signal.security_id == inst.security_id, Signal.section == "fno"
        ).count()
        signal = Signal(
            security_id=inst.security_id,
            symbol=inst.trading_symbol or inst.symbol,
            section="fno",
            signal_type=sig_type,
            occurrence_number=past_count + 1,
            first_detected_date=today,
            last_updated_date=today,
            current_score=r["score"],
            peak_score=r["score"],
            status="active",
            close_price_at_detection=r["close"],
            trend_score=r["score"],
        )
        self.db.add(signal)
        return signal
