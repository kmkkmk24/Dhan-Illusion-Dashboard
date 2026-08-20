from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta
import json
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.models.tables import MarketDirectionPrediction, SectorScore
from backend.services.dhan_client import DhanClient
from backend.services.telegram_notifier import send_telegram_message


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def _macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = _ema(series, 12)
    slow = _ema(series, 26)
    macd = fast - slow
    signal = _ema(macd, 9)
    hist = macd - signal
    return macd, signal, hist


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


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[(plus_dm < 0) | (plus_dm < minus_dm)] = 0
    minus_dm[(minus_dm < 0) | (minus_dm < plus_dm)] = 0

    tr = pd.concat(
        [(high - low).abs(), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.rolling(period).mean().fillna(15)
    return adx


def _safe_last(series: pd.Series, default: float = 0.0) -> float:
    try:
        if series is None or len(series) == 0:
            return default
        val = float(series.iloc[-1])
        if np.isnan(val):
            return default
        return val
    except Exception:
        return default


def _score_to_bias(score: float) -> str:
    if score >= 0.55:
        return "STRONGLY BULLISH"
    if score >= 0.2:
        return "BULLISH"
    if score <= -0.55:
        return "STRONGLY BEARISH"
    if score <= -0.2:
        return "BEARISH"
    return "SIDEWAYS / RANGE-BOUND"


def _probabilities_from_score(score: float) -> dict[str, float]:
    bull = max(0.0, score)
    bear = max(0.0, -score)
    side = max(0.0, 1.0 - abs(score) * 1.25)
    total = bull + bear + side
    if total <= 0:
        return {"bullish": 33.3, "sideways": 33.4, "bearish": 33.3}
    return {
        "bullish": round(100 * bull / total, 1),
        "sideways": round(100 * side / total, 1),
        "bearish": round(100 * bear / total, 1),
    }


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    if df is None or df.empty or "timestamp" not in df.columns:
        return None
    try:
        src = df.copy()
        src["timestamp"] = pd.to_datetime(src["timestamp"])
        src = src.set_index("timestamp").sort_index()
        out = src.resample(rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        out = out.dropna().reset_index()
        return out if not out.empty else None
    except Exception:
        return None


@dataclass
class IndexUniverse:
    name: str
    security_id: str


class MarketDirectionPredictor:
    def __init__(self, db: Session):
        self.db = db
        self.indices = [
            IndexUniverse("NIFTY 50", "13"),
            IndexUniverse("SENSEX", "51"),
        ]

    async def build_dashboard(self) -> dict[str, Any]:
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        today = now_ist.date()
        tomorrow = self._next_trading_day(today)

        today_prediction = await self.predict_for_target(today, horizon="today")
        tomorrow_prediction = await self.predict_for_target(tomorrow, horizon="tomorrow")

        if self._has_valid_index_data(today_prediction):
            self._upsert_prediction(today_prediction, horizon="today")
        else:
            latest_today = self._latest_prediction(today, "today")
            if latest_today:
                today_prediction = latest_today

        if not self._has_valid_index_data(tomorrow_prediction) and self._has_valid_index_data(today_prediction):
            tomorrow_prediction = self._derive_tomorrow_from_today(today_prediction, tomorrow)

        if self._has_valid_index_data(tomorrow_prediction):
            self._upsert_prediction(tomorrow_prediction, horizon="tomorrow")
        else:
            latest_tomorrow = self._latest_prediction(tomorrow, "tomorrow")
            if latest_tomorrow:
                tomorrow_prediction = latest_tomorrow

        await self._evaluate_history()
        history_rows = self._history_rows(limit=90)
        streak = self._current_hit_streak(horizon="tomorrow")

        return {
            "as_of": now_ist.isoformat(timespec="seconds"),
            "today": today_prediction,
            "tomorrow": tomorrow_prediction,
            "history": history_rows,
            "streak": streak,
            "timing": {
                "today_reliable_after_ist": "09:45",
                "note": "Today prediction is most reliable after first 30 minutes of market open.",
            },
        }

    async def predict_today(self) -> dict[str, Any]:
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        return await self.predict_for_target(today, horizon="today")

    async def predict_for_target(self, target_date: date, *, horizon: str) -> dict[str, Any]:
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        client = DhanClient()
        try:
            index_rows = []
            for idx in self.indices:
                index_rows.append(await self._analyze_index(client, idx, horizon=horizon))

            valid = [r for r in index_rows if r.get("analysis_ok")]
            if not valid:
                return {
                    "target_date": target_date.isoformat(),
                    "horizon": horizon,
                    "as_of": now_ist.isoformat(timespec="seconds"),
                    "analysis_ok": False,
                    "market_bias": "SIDEWAYS / RANGE-BOUND",
                    "confidence": 0,
                    "probabilities": {"bullish": 33.3, "sideways": 33.4, "bearish": 33.3},
                    "indices": index_rows,
                    "note": "No sufficient data available for prediction.",
                }

            mean_score = float(np.mean([float(r.get("score", 0.0)) for r in valid]))
            agree_dir = np.mean([np.sign(float(r.get("score", 0.0))) == np.sign(mean_score) for r in valid])
            confidence = int(min(95, max(35, abs(mean_score) * 70 + float(agree_dir) * 30)))

            breadth = self._sector_breadth_snapshot()
            breadth_adj = 0.06 if breadth["strong_share"] >= 0.45 else -0.04 if breadth["strong_share"] <= 0.2 else 0.0
            final_score = float(np.clip(mean_score + breadth_adj, -1.0, 1.0))

            market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
            reliable_after = market_open + timedelta(minutes=30)
            if horizon == "today" and now_ist < reliable_after:
                confidence = max(30, confidence - 12)

            return {
                "target_date": target_date.isoformat(),
                "horizon": horizon,
                "as_of": now_ist.isoformat(timespec="seconds"),
                "analysis_ok": True,
                "market_bias": _score_to_bias(final_score),
                "confidence": confidence,
                "probabilities": _probabilities_from_score(final_score),
                "score": round(final_score, 4),
                "breadth": breadth,
                "indices": index_rows,
                "model_note": (
                    "Tomorrow uses higher-timeframe weighted bias and end-of-session structure."
                    if horizon == "tomorrow"
                    else "Today uses mixed higher + intraday timeframe blend."
                ),
                "disclaimer": "Probabilistic model output. Not a guaranteed outcome or financial advice.",
            }
        finally:
            await client.close()

    async def _analyze_index(self, client: DhanClient, idx: IndexUniverse, *, horizon: str) -> dict[str, Any]:
        today = date.today()
        from_daily = today - timedelta(days=420)
        daily = await client.get_historical_daily_data(
            security_id=idx.security_id,
            exchange_segment="IDX_I",
            instrument="INDEX",
            from_date=from_daily,
            to_date=today,
        )
        if daily is None or len(daily) < 80:
            return {
                "index": idx.name,
                "analysis_ok": False,
                "reason": "insufficient_daily_data",
                "score": 0.0,
                "bias": "SIDEWAYS / RANGE-BOUND",
                "confidence": 0,
            }

        now = datetime.now()
        from_intraday = now - timedelta(days=12)
        from_s = from_intraday.strftime("%Y-%m-%d %H:%M:%S")
        to_s = now.strftime("%Y-%m-%d %H:%M:%S")

        h1 = await client.get_intraday_data(idx.security_id, "IDX_I", "INDEX", 60, from_s, to_s)
        m15 = await client.get_intraday_data(idx.security_id, "IDX_I", "INDEX", 15, from_s, to_s)
        m5 = await client.get_intraday_data(idx.security_id, "IDX_I", "INDEX", 5, from_s, to_s)
        h4 = _resample_ohlcv(h1, "4h") if h1 is not None else None
        weekly = _resample_ohlcv(daily, "W")
        monthly = _resample_ohlcv(daily, "ME")

        tf_weights: dict[str, float] = self._timeframe_weights(horizon)
        tf_data: dict[str, Optional[pd.DataFrame]] = {
            "monthly": monthly,
            "weekly": weekly,
            "daily": daily,
            "4h": h4,
            "1h": h1,
            "15m": m15,
            "5m": m5,
        }

        tf_scores: dict[str, float] = {}
        weighted_score = 0.0
        weight_used = 0.0
        for tf_name, wt in tf_weights.items():
            df = tf_data.get(tf_name)
            if df is None or len(df) < 40:
                continue
            s = self._timeframe_score(df)
            tf_scores[tf_name] = round(s, 4)
            weighted_score += s * wt
            weight_used += wt

        score = weighted_score / weight_used if weight_used > 0 else 0.0
        if horizon == "tomorrow":
            score = float(np.clip(score + self._tomorrow_adjustment(daily), -1.0, 1.0))
        bias = _score_to_bias(score)
        signs = [np.sign(v) for v in tf_scores.values() if abs(v) > 1e-6]
        agree = 0.5 if not signs else float(sum(1 for v in signs if v == np.sign(score)) / len(signs))
        confidence = int(min(95, max(30, abs(score) * 70 + agree * 30)))

        d_close = daily["close"].astype(float)
        ema20 = _ema(d_close, 20)
        ema50 = _ema(d_close, 50)
        ema200 = _ema(d_close, 200)
        rsi = _rsi(d_close, 14)
        macd, macd_signal, macd_hist = _macd(d_close)
        atr = _atr(daily["high"].astype(float), daily["low"].astype(float), d_close, 14)
        adx = _adx(daily["high"].astype(float), daily["low"].astype(float), d_close, 14)
        prev_close = float(d_close.iloc[-2]) if len(d_close) > 1 else float(d_close.iloc[-1])
        last_close = float(d_close.iloc[-1])
        session_context = self._build_session_context(prev_close, m5)

        return {
            "index": idx.name,
            "analysis_ok": True,
            "score": round(float(score), 4),
            "bias": bias,
            "confidence": confidence,
            "probabilities": _probabilities_from_score(float(score)),
            "timeframe_scores": tf_scores,
            "technical_snapshot": {
                "last_close": round(last_close, 2),
                "prev_close": round(prev_close, 2),
                "gap_pct": round(((last_close - prev_close) / prev_close) * 100, 2) if prev_close else 0.0,
                "ema20": round(_safe_last(ema20), 2),
                "ema50": round(_safe_last(ema50), 2),
                "ema200": round(_safe_last(ema200), 2),
                "rsi14": round(_safe_last(rsi, 50), 1),
                "macd": round(_safe_last(macd), 3),
                "macd_signal": round(_safe_last(macd_signal), 3),
                "macd_hist": round(_safe_last(macd_hist), 3),
                "adx14": round(_safe_last(adx, 15), 1),
                "atr14": round(_safe_last(atr, 0), 2),
            },
            "session_context": session_context,
            "notes": self._build_evidence_notes(last_close, ema20, ema50, ema200, rsi, adx),
        }

    def _timeframe_weights(self, horizon: str) -> dict[str, float]:
        if horizon == "tomorrow":
            return {
                "monthly": 0.24,
                "weekly": 0.24,
                "daily": 0.26,
                "4h": 0.14,
                "1h": 0.08,
                "15m": 0.04,
                "5m": 0.0,
            }
        return {
            "monthly": 0.22,
            "weekly": 0.22,
            "daily": 0.20,
            "4h": 0.14,
            "1h": 0.10,
            "15m": 0.08,
            "5m": 0.04,
        }

    def _tomorrow_adjustment(self, daily: pd.DataFrame) -> float:
        if daily is None or daily.empty:
            return 0.0
        try:
            row = daily.iloc[-1]
            op = float(row["open"])
            hi = float(row["high"])
            lo = float(row["low"])
            cl = float(row["close"])
            rng = max(hi - lo, 1e-6)

            close_loc = (cl - lo) / rng  # 0 near low, 1 near high
            day_ret = ((cl - op) / op) if op else 0.0

            adj = 0.0
            if close_loc >= 0.75:
                adj += 0.07
            elif close_loc <= 0.25:
                adj -= 0.07

            adj += float(np.clip(day_ret * 6.0, -0.06, 0.06))
            return float(np.clip(adj, -0.14, 0.14))
        except Exception:
            return 0.0

    def _timeframe_score(self, df: pd.DataFrame) -> float:
        close = df["close"].astype(float)
        ema20 = _ema(close, 20)
        ema50 = _ema(close, 50)
        rsi = _rsi(close, 14)
        macd, macd_signal, _ = _macd(close)

        c = float(close.iloc[-1])
        e20 = _safe_last(ema20)
        e50 = _safe_last(ema50)
        r = _safe_last(rsi, 50)
        m = _safe_last(macd)
        ms = _safe_last(macd_signal)

        trend = 0.0
        if c > e20 > e50:
            trend = 1.0
        elif c < e20 < e50:
            trend = -1.0
        elif c > e20:
            trend = 0.4
        elif c < e20:
            trend = -0.4

        momentum = np.clip((r - 50.0) / 20.0, -1.0, 1.0)
        macd_score = 0.5 if m > ms else -0.5
        score = 0.55 * trend + 0.3 * momentum + 0.15 * macd_score
        return float(np.clip(score, -1.0, 1.0))

    def _build_evidence_notes(
        self,
        close: float,
        ema20: pd.Series,
        ema50: pd.Series,
        ema200: pd.Series,
        rsi: pd.Series,
        adx: pd.Series,
    ) -> list[str]:
        e20 = _safe_last(ema20)
        e50 = _safe_last(ema50)
        e200 = _safe_last(ema200)
        rs = _safe_last(rsi, 50)
        ax = _safe_last(adx, 15)

        notes: list[str] = []
        if close > e20 > e50:
            notes.append("Price above EMA20 and EMA50 (bullish structure)")
        elif close < e20 < e50:
            notes.append("Price below EMA20 and EMA50 (bearish structure)")
        else:
            notes.append("Mixed EMA structure (range risk)")

        if close > e200:
            notes.append("Price above EMA200 (higher-timeframe support)")
        else:
            notes.append("Price below EMA200 (higher-timeframe pressure)")

        if rs >= 60:
            notes.append("RSI momentum strong")
        elif rs <= 40:
            notes.append("RSI momentum weak")
        else:
            notes.append("RSI neutral band")

        if ax >= 22:
            notes.append("ADX indicates trend strength")
        else:
            notes.append("ADX suggests weak/sideways trend")
        return notes

    def _sector_breadth_snapshot(self) -> dict[str, Any]:
        latest_date = self.db.query(SectorScore.date).order_by(SectorScore.date.desc()).first()
        if not latest_date:
            return {"strong_share": 0.0, "strong_count": 0, "total": 0}
        rows = (
            self.db.query(SectorScore)
            .filter(SectorScore.date == latest_date[0])
            .all()
        )
        total = len(rows)
        if total == 0:
            return {"strong_share": 0.0, "strong_count": 0, "total": 0}
        strong = sum(1 for r in rows if float(r.combined_score or 0) >= 0.6)
        return {
            "strong_share": round(strong / total, 3),
            "strong_count": strong,
            "total": total,
            "as_of": str(latest_date[0]),
        }

    def _next_trading_day(self, d: date) -> date:
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def _upsert_prediction(self, prediction: dict[str, Any], *, horizon: str) -> None:
        try:
            target = date.fromisoformat(str(prediction.get("target_date")))
        except Exception:
            return
        probs = prediction.get("probabilities") or {}
        row = (
            self.db.query(MarketDirectionPrediction)
            .filter(
                MarketDirectionPrediction.target_date == target,
                MarketDirectionPrediction.horizon == horizon,
            )
            .first()
        )
        if row is None:
            row = MarketDirectionPrediction(target_date=target, horizon=horizon)
            self.db.add(row)

        row.generated_at = datetime.utcnow()
        row.market_bias = str(prediction.get("market_bias") or "SIDEWAYS / RANGE-BOUND")
        row.confidence = float(prediction.get("confidence") or 0)
        row.score = float(prediction.get("score") or 0)
        row.bullish_prob = float(probs.get("bullish") or 0)
        row.sideways_prob = float(probs.get("sideways") or 0)
        row.bearish_prob = float(probs.get("bearish") or 0)
        row.details_json = json.dumps(
            {
                "breadth": prediction.get("breadth"),
                "indices": prediction.get("indices"),
                "as_of": prediction.get("as_of"),
            },
            separators=(",", ":"),
        )
        self.db.commit()

    async def _evaluate_history(self) -> None:
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        today = now_ist.date()
        close_cutoff = now_ist.replace(hour=15, minute=35, second=0, microsecond=0)
        eval_through = today if now_ist >= close_cutoff else (today - timedelta(days=1))
        pending = (
            self.db.query(MarketDirectionPrediction)
            .filter(
                MarketDirectionPrediction.target_date <= eval_through,
                MarketDirectionPrediction.actual_bias.is_(None),
            )
            .order_by(MarketDirectionPrediction.target_date.asc())
            .limit(45)
            .all()
        )
        if not pending:
            return
        client = DhanClient()
        changed = False
        try:
            for row in pending:
                actual = await self._actual_market_bias_for_date(client, row.target_date)
                if not actual:
                    continue
                row.actual_bias = actual["bias"]
                row.actual_score = float(actual["score"])
                row.hit = self._is_prediction_hit(row.market_bias, actual["bias"])
                row.evaluated_at = datetime.utcnow()
                changed = True
        finally:
            await client.close()
        if changed:
            self.db.commit()

    async def _actual_market_bias_for_date(self, client: DhanClient, target_date: date) -> Optional[dict[str, Any]]:
        returns = []
        used_sources: list[str] = []
        for idx in self.indices:
            op: Optional[float] = None
            cl: Optional[float] = None
            df = await client.get_historical_daily_data(
                security_id=idx.security_id,
                exchange_segment="IDX_I",
                instrument="INDEX",
                from_date=target_date - timedelta(days=6),
                to_date=target_date + timedelta(days=1),
            )
            if df is not None and not df.empty:
                data = df.copy().sort_values("timestamp")
                data["d"] = pd.to_datetime(data["timestamp"]).dt.date
                row = data[data["d"] == target_date]
                if not row.empty:
                    last = row.iloc[-1]
                    op = float(last["open"])
                    cl = float(last["close"])
                    used_sources.append("daily")

            # Fallback for same-day evaluation when EOD daily candle isn't published yet.
            if op is None or cl is None:
                from_s = f"{target_date.isoformat()} 09:15:00"
                to_s = f"{target_date.isoformat()} 15:30:00"
                intraday = await client.get_intraday_data(
                    idx.security_id,
                    "IDX_I",
                    "INDEX",
                    5,
                    from_s,
                    to_s,
                )
                if intraday is not None and not intraday.empty:
                    day = intraday.copy().sort_values("timestamp")
                    op = float(day.iloc[0]["open"])
                    cl = float(day.iloc[-1]["close"])
                    used_sources.append("intraday_5m")

            if op is not None and cl is not None and op > 0:
                returns.append((cl - op) / op)
        if not returns:
            return None
        avg_ret = float(np.mean(returns))
        if avg_ret >= 0.007:
            bias = "STRONGLY BULLISH"
        elif avg_ret >= 0.002:
            bias = "BULLISH"
        elif avg_ret <= -0.007:
            bias = "STRONGLY BEARISH"
        elif avg_ret <= -0.002:
            bias = "BEARISH"
        else:
            bias = "SIDEWAYS / RANGE-BOUND"
        source = "daily" if used_sources and all(s == "daily" for s in used_sources) else "mixed_with_intraday_fallback"
        return {"bias": bias, "score": avg_ret, "source": source}

    def _is_prediction_hit(self, predicted_bias: str, actual_bias: str) -> bool:
        p = self._bias_bucket(predicted_bias)
        a = self._bias_bucket(actual_bias)
        if p == "flat":
            return a == "flat"
        return p == a

    def _bias_bucket(self, bias: str) -> str:
        b = str(bias or "").upper()
        if "BULLISH" in b:
            return "bull"
        if "BEARISH" in b:
            return "bear"
        return "flat"

    def _history_rows(self, limit: int = 90) -> list[dict[str, Any]]:
        rows = (
            self.db.query(MarketDirectionPrediction)
            .order_by(MarketDirectionPrediction.target_date.desc())
            .limit(limit)
            .all()
        )
        out = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "target_date": r.target_date.isoformat() if r.target_date else None,
                    "horizon": r.horizon,
                    "generated_at": r.generated_at.isoformat() if r.generated_at else None,
                    "predicted_bias": r.market_bias,
                    "confidence": round(float(r.confidence or 0), 1),
                    "actual_bias": r.actual_bias,
                    "result": "HIT" if r.hit is True else "MISS" if r.hit is False else "PENDING",
                }
            )
        return out

    async def notify_history_row_to_telegram(self, prediction_id: int, *, include_today_outcome: bool = False) -> dict[str, Any]:
        row = (
            self.db.query(MarketDirectionPrediction)
            .filter(MarketDirectionPrediction.id == int(prediction_id))
            .first()
        )
        if row is None:
            return {"ok": False, "message": "Prediction row not found."}

        index_lines: list[str] = []
        reason_lines: list[str] = []
        try:
            payload = json.loads(row.details_json or "{}")
            indices = payload.get("indices") if isinstance(payload, dict) else None
            if isinstance(indices, list):
                for idx in indices:
                    if not isinstance(idx, dict):
                        continue
                    name = str(idx.get("index") or "").strip().upper()
                    if not name:
                        continue
                    bias = str(idx.get("bias") or "").strip()
                    if not bias:
                        continue
                    index_lines.append(f"{name}: {bias}")
                    notes = idx.get("notes")
                    if isinstance(notes, list) and notes:
                        top_note = str(notes[0]).strip()
                        if top_note:
                            reason_lines.append(f"- {name}: {top_note}")
                    tf_scores = idx.get("timeframe_scores")
                    if isinstance(tf_scores, dict) and tf_scores:
                        # Highlight broader-vs-intraday tone for quick reasoning.
                        daily_like = []
                        intraday_like = []
                        for k, v in tf_scores.items():
                            try:
                                s = float(v)
                            except Exception:
                                continue
                            if k in {"monthly", "weekly", "daily", "4h"}:
                                daily_like.append(s)
                            elif k in {"1h", "15m", "5m"}:
                                intraday_like.append(s)
                        if daily_like:
                            d_avg = float(np.mean(daily_like))
                            d_lbl = "bullish" if d_avg > 0.1 else "bearish" if d_avg < -0.1 else "sideways"
                            reason_lines.append(f"- {name}: higher-timeframe bias {d_lbl}")
                        if intraday_like:
                            i_avg = float(np.mean(intraday_like))
                            i_lbl = "bullish" if i_avg > 0.1 else "bearish" if i_avg < -0.1 else "sideways"
                            reason_lines.append(f"- {name}: intraday tone {i_lbl}")
        except Exception:
            index_lines = []
            reason_lines = []

        target = row.target_date.strftime("%d %b %Y") if row.target_date else "-"
        lines = [
            f"🤖 <b>AI Prediction: {target}</b>",
            f"Bias: <b>{row.market_bias}</b> | Confidence: <b>{int(round(float(row.confidence or 0)))}%</b>",
        ]
        if include_today_outcome:
            today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
            today_row = (
                self.db.query(MarketDirectionPrediction)
                .filter(
                    MarketDirectionPrediction.target_date == today_ist,
                    MarketDirectionPrediction.horizon == "today",
                )
                .order_by(MarketDirectionPrediction.generated_at.desc())
                .first()
            )
            if today_row is not None:
                today_result = "HIT" if today_row.hit is True else "MISS" if today_row.hit is False else "PENDING"
                lines.append(f"Today Result: <b>{today_result}</b>")
        streak = self._current_hit_streak(horizon="tomorrow")
        lines.append(f"Current HIT Streak: <b>{int(streak.get('current', 0))} day(s)</b>")
        lines.extend(index_lines)
        if reason_lines:
            lines.append("")
            lines.append("<b>Why this prediction</b>")
            lines.extend(reason_lines[:6])
        message = "\n".join(lines)
        result = await send_telegram_message(message, parse_mode="HTML")
        if not result.get("ok"):
            return {
                "ok": False,
                "message": result.get("error") or result.get("reason") or "Telegram send failed",
            }
        return {"ok": True, "message": "Prediction sent to Telegram."}

    async def notify_latest_prediction_to_telegram(self, *, horizon: str = "tomorrow") -> dict[str, Any]:
        today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        target_date = self._next_trading_day(today_ist) if horizon == "tomorrow" else today_ist
        row = (
            self.db.query(MarketDirectionPrediction)
            .filter(
                MarketDirectionPrediction.target_date == target_date,
                MarketDirectionPrediction.horizon == horizon,
            )
            .order_by(MarketDirectionPrediction.generated_at.desc())
            .first()
        )
        if row is None:
            await self.build_dashboard()
            row = (
                self.db.query(MarketDirectionPrediction)
                .filter(
                    MarketDirectionPrediction.target_date == target_date,
                    MarketDirectionPrediction.horizon == horizon,
                )
                .order_by(MarketDirectionPrediction.generated_at.desc())
                .first()
            )
        if row is None:
            return {"ok": False, "message": "No prediction available to notify."}
        return await self.notify_history_row_to_telegram(
            int(row.id),
            include_today_outcome=(horizon == "tomorrow"),
        )

    def _has_valid_index_data(self, prediction: dict[str, Any]) -> bool:
        indices = prediction.get("indices") if isinstance(prediction, dict) else None
        if not isinstance(indices, list) or not indices:
            return False
        return any(bool(i.get("analysis_ok")) for i in indices if isinstance(i, dict))

    def _derive_tomorrow_from_today(self, today_prediction: dict[str, Any], target_date: date) -> dict[str, Any]:
        out = dict(today_prediction)
        out["target_date"] = target_date.isoformat()
        out["horizon"] = "tomorrow"
        out["as_of"] = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")
        out["note"] = "Tomorrow prediction derived from latest reliable session structure."
        return out

    def _latest_prediction(self, target_date: date, horizon: str) -> Optional[dict[str, Any]]:
        row = (
            self.db.query(MarketDirectionPrediction)
            .filter(
                MarketDirectionPrediction.target_date == target_date,
                MarketDirectionPrediction.horizon == horizon,
            )
            .order_by(MarketDirectionPrediction.generated_at.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "target_date": row.target_date.isoformat() if row.target_date else None,
            "horizon": row.horizon,
            "as_of": row.generated_at.isoformat() if row.generated_at else None,
            "analysis_ok": True,
            "market_bias": row.market_bias,
            "confidence": int(round(float(row.confidence or 0))),
            "probabilities": {
                "bullish": round(float(row.bullish_prob or 0), 1),
                "sideways": round(float(row.sideways_prob or 0), 1),
                "bearish": round(float(row.bearish_prob or 0), 1),
            },
            "score": round(float(row.score or 0), 4),
            "breadth": {},
            "indices": [],
            "disclaimer": "Loaded from last successful stored prediction.",
            "note": "Using last successful stored prediction due temporary data gap.",
        }

    def _build_session_context(self, prev_close: float, intraday_5m: Optional[pd.DataFrame]) -> dict[str, Any]:
        if intraday_5m is None or intraday_5m.empty:
            return {
                "open_type": "UNKNOWN",
                "session_structure": "UNKNOWN",
                "day_open": None,
                "day_move_pct": None,
            }

    def _current_hit_streak(self, horizon: str = "tomorrow") -> dict[str, Any]:
        rows = (
            self.db.query(MarketDirectionPrediction)
            .filter(
                MarketDirectionPrediction.horizon == horizon,
                MarketDirectionPrediction.hit.is_not(None),
            )
            .order_by(MarketDirectionPrediction.target_date.desc())
            .limit(365)
            .all()
        )
        streak = 0
        for r in rows:
            if r.hit is True:
                streak += 1
            else:
                break
        last_result = None
        if rows:
            last_result = "HIT" if rows[0].hit is True else "MISS"
        return {
            "horizon": horizon,
            "current": streak,
            "evaluated_rows": len(rows),
            "last_result": last_result,
        }
        try:
            df = intraday_5m.copy()
            ts = pd.to_datetime(df["timestamp"], errors="coerce")
            if getattr(ts.dt, "tz", None) is not None:
                ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            df["ts_local"] = ts
            today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
            today_rows = df[df["ts_local"].dt.date == today]
            if today_rows.empty:
                today_rows = df
            day_open = float(today_rows.iloc[0]["open"])
            day_close = float(today_rows.iloc[-1]["close"])
            day_high = float(today_rows["high"].max())
            day_low = float(today_rows["low"].min())

            gap_pct = ((day_open - prev_close) / prev_close) * 100 if prev_close else 0.0
            move_pct = ((day_close - day_open) / day_open) * 100 if day_open else 0.0
            range_pct = ((day_high - day_low) / day_open) * 100 if day_open else 0.0

            if gap_pct >= 0.45:
                open_type = "GAP UP"
            elif gap_pct >= 0.10:
                open_type = "MILD GAP UP"
            elif gap_pct <= -0.45:
                open_type = "GAP DOWN"
            elif gap_pct <= -0.10:
                open_type = "MILD GAP DOWN"
            else:
                open_type = "FLAT OPEN"

            if abs(move_pct) >= 0.60 and abs(move_pct) >= (range_pct * 0.45):
                session_structure = "TREND DAY"
            elif range_pct <= 0.90 or abs(move_pct) <= 0.25:
                session_structure = "SIDEWAYS / RANGE"
            else:
                session_structure = "BALANCED / ROTATIONAL"

            return {
                "open_type": open_type,
                "session_structure": session_structure,
                "gap_pct": round(gap_pct, 2),
                "day_open": round(day_open, 2),
                "day_move_pct": round(move_pct, 2),
                "day_range_pct": round(range_pct, 2),
            }
        except Exception:
            return {
                "open_type": "UNKNOWN",
                "session_structure": "UNKNOWN",
                "day_open": None,
                "day_move_pct": None,
            }
