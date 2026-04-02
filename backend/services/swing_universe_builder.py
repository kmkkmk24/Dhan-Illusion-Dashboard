"""
Rebuild swing universe CSV (filters 1–4) from Dhan data.

Used by Swing Scan (auto before VCP) and optionally scripts/swing_universe_bench.py.
Writes data/swing_universe_passed_<date>.csv and data/swing_universe_latest.csv.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from backend.config import DATA_DIR, get_config, get_dhan_credentials
from backend.models.database import get_session_factory
from backend.models.tables import Instrument
from backend.services.dhan_client import DhanClient

logger = logging.getLogger(__name__)


@dataclass
class SwingUniverseParams:
    min_ltp: float = 300.0
    max_ltp: float = 5000.0
    min_avg_daily_value_inr: float = 40_000_000.0
    turnover_lookback_days: int = 20
    history_calendar_days: int = 300
    min_daily_rows: int = 115
    hist_min_interval: float = 0.28
    hist_chunk_pause: float = 0.18
    ltp_batch_pause_sec: float = 2.0
    ltp_batch_size: int = 1000
    max_after_ltp: int = 0  # 0 = no cap (full run)


def load_swing_universe_params() -> SwingUniverseParams:
    c = get_config().get("swing_universe") or {}
    return SwingUniverseParams(
        min_ltp=float(c.get("min_ltp", 300)),
        max_ltp=float(c.get("max_ltp", 5000)),
        min_avg_daily_value_inr=float(c.get("min_avg_daily_value_inr", 40_000_000)),
        turnover_lookback_days=int(c.get("turnover_lookback_days", 20)),
        history_calendar_days=int(c.get("history_calendar_days", 300)),
        min_daily_rows=int(c.get("min_daily_rows", 115)),
        hist_min_interval=float(c.get("hist_min_interval", 0.28)),
        hist_chunk_pause=float(c.get("hist_chunk_pause", 0.18)),
        ltp_batch_pause_sec=float(c.get("ltp_batch_pause_sec", 2.0)),
        ltp_batch_size=int(c.get("ltp_batch_size", 1000)),
        max_after_ltp=int(c.get("max_after_ltp", 0)),
    )


def _parse_ltp_map(payload: dict | None) -> dict[str, float]:
    """Parse /marketfeed/ltp response (same shape as swing_universe_bench)."""
    if not payload:
        return {}
    data_obj = payload.get("data", payload)
    out: dict[str, float] = {}
    if not isinstance(data_obj, dict):
        return out
    for _seg_key, segment_data in data_obj.items():
        if not isinstance(segment_data, dict):
            continue
        for sec_id, price_data in segment_data.items():
            if not isinstance(price_data, dict):
                continue
            raw = price_data.get(
                "last_price",
                price_data.get("ltp", price_data.get("lastTradedPrice")),
            )
            if raw is not None:
                try:
                    out[str(sec_id)] = float(raw)
                except (TypeError, ValueError):
                    continue
    return out


def passes_history_filters_df(
    df: pd.DataFrame,
    p: SwingUniverseParams,
) -> tuple[bool, dict[str, Any], str]:
    """Filters 3–4 on daily OHLCV; returns (passed, metrics, fail_reason)."""
    if df is None or df.empty or len(df) < p.min_daily_rows:
        return False, {}, f"rows_lt_{p.min_daily_rows}"

    df = df.sort_values("timestamp").reset_index(drop=True)
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    last = float(close.iloc[-1])
    if last < p.min_ltp:
        return False, {}, "close_lt_min"
    if last > p.max_ltp:
        return False, {}, "close_gt_max"

    lookback = p.turnover_lookback_days
    tail = df.tail(lookback)
    if len(tail) < lookback:
        return False, {}, "turnover_window_short"
    daily_value = tail["close"].astype(float) * tail["volume"].astype(float)
    avg_daily_value = float(daily_value.mean())
    if avg_daily_value < p.min_avg_daily_value_inr:
        return False, {}, "avg_value_lt_threshold"

    sma100_series = close.rolling(100, min_periods=100).mean()
    s_now = float(sma100_series.iloc[-1])
    s_10ago = float(sma100_series.iloc[-11])
    if pd.isna(s_now) or pd.isna(s_10ago):
        return False, {}, "sma100_nan"
    if last <= s_now:
        return False, {}, "not_above_sma100"
    if s_now <= s_10ago:
        return False, {}, "sma100_not_rising"

    metrics = {
        "close": last,
        "sma100": s_now,
        "sma100_10bars_ago": s_10ago,
        "avg_daily_value_inr": avg_daily_value,
        "stage2_ok": True,
    }
    return True, metrics, "ok"


def _write_passed_csv(
    out_path: Path,
    passed: list[dict[str, Any]],
    ltp_map: dict[str, float],
    *,
    session_factory: Callable[[], Session],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "security_id",
        "symbol",
        "trading_symbol",
        "display_name",
        "ltp",
        "avg_daily_value_inr",
        "close",
        "sma100",
        "sma100_10bars_ago",
        "stage2_ok",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        db = session_factory()
        try:
            for row in passed:
                sid = str(row["security_id"])
                inst = (
                    db.query(Instrument)
                    .filter(Instrument.security_id == sid)
                    .first()
                )
                sym = inst.symbol if inst else ""
                ts = (inst.trading_symbol or "") if inst else ""
                dn = (inst.display_name or "") if inst else ""
                w.writerow(
                    {
                        "security_id": sid,
                        "symbol": sym,
                        "trading_symbol": ts,
                        "display_name": dn,
                        "ltp": ltp_map.get(sid, row.get("ltp")),
                        "avg_daily_value_inr": row.get("avg_daily_value_inr"),
                        "close": row.get("close"),
                        "sma100": row.get("sma100"),
                        "sma100_10bars_ago": row.get("sma100_10bars_ago"),
                        "stage2_ok": row.get("stage2_ok"),
                    }
                )
        finally:
            db.close()


async def rebuild_swing_universe_csv(
    db: Session,
    *,
    params: Optional[SwingUniverseParams] = None,
    session_factory: Optional[Callable[[], Session]] = None,
    write_latest: bool = True,
) -> dict[str, Any]:
    """
    Fetch LTP + history for NSE cash ES names, apply bench filters, write dated + latest CSV.

    Uses the same Dhan credential path as the rest of the app (UI + env + config).
    """
    p = params or load_swing_universe_params()
    sf = session_factory or get_session_factory()

    t0 = time.perf_counter()
    out: dict[str, Any] = {
        "ok": False,
        "error": None,
        "passed": 0,
        "failed": 0,
        "skipped_ltp": 0,
        "dated_csv": None,
        "latest_csv": None,
        "seconds": 0.0,
        "fail_reasons": {},
        "total_candidates": 0,
    }

    try:
        creds = get_dhan_credentials()
    except ValueError as e:
        out["error"] = f"credentials: {e}"
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out

    if not creds.get("access_token"):
        out["error"] = "dhan_not_connected"
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out

    client = DhanClient(
        hist_min_interval=p.hist_min_interval,
        hist_chunk_pause=p.hist_chunk_pause,
    )
    try:
        rows = (
            db.query(Instrument.security_id)
            .filter(
                Instrument.exchange == "NSE",
                Instrument.segment == "E",
                Instrument.instrument_type == "ES",
            )
            .distinct()
            .all()
        )
        sec_ids = [str(r[0]) for r in rows if r[0]]
        sec_ids.sort(key=lambda x: int(x) if x.isdigit() else x)
        total = len(sec_ids)
        out["total_candidates"] = total

        logger.info(
            "Swing universe rebuild: %d ES candidates (LTP batches + history)",
            total,
        )

        ltp_map: dict[str, float] = {}
        bs = max(1, min(p.ltp_batch_size, 1000))
        for i in range(0, total, bs):
            if i > 0:
                await asyncio.sleep(p.ltp_batch_pause_sec)
            batch = sec_ids[i : i + bs]
            try:
                raw = await client.get_market_quote_ltp(batch, "NSE_EQ")
            except Exception as e:
                logger.warning(
                    "LTP batch failed (%s..%s): %s",
                    batch[0],
                    batch[-1],
                    e,
                )
                continue
            ltp_map.update(_parse_ltp_map(raw))

        out["skipped_ltp"] = sum(1 for s in sec_ids if s not in ltp_map)

        ltp_pass = [
            s
            for s in sec_ids
            if (ltp_px := ltp_map.get(s)) is not None
            and p.min_ltp <= ltp_px <= p.max_ltp
        ]
        if p.max_after_ltp > 0:
            ltp_pass = ltp_pass[: p.max_after_ltp]

        to_date = date.today()
        from_date = to_date - timedelta(days=p.history_calendar_days)

        passed_rows: list[dict[str, Any]] = []
        fail_reasons: dict[str, int] = {}
        hist_errors = 0

        for n, sid in enumerate(ltp_pass, start=1):
            try:
                df = await client.get_historical_daily_data(
                    security_id=sid,
                    exchange_segment="NSE_EQ",
                    instrument="EQUITY",
                    from_date=from_date,
                    to_date=to_date,
                )
            except Exception:
                hist_errors += 1
                out["failed"] += 1
                fail_reasons["fetch_exception"] = (
                    fail_reasons.get("fetch_exception", 0) + 1
                )
                continue

            if df is None or df.empty:
                out["failed"] += 1
                fail_reasons["fetch_empty"] = fail_reasons.get("fetch_empty", 0) + 1
                continue

            ok, metrics, reason = passes_history_filters_df(df, p)
            if ok:
                passed_rows.append(
                    {
                        "security_id": sid,
                        "ltp": ltp_map.get(sid),
                        **metrics,
                    }
                )
                out["passed"] += 1
            else:
                out["failed"] += 1
                fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

            if n % 25 == 0 or n == len(ltp_pass):
                logger.info(
                    "Swing universe rebuild progress: %d/%d (passed=%d)",
                    n,
                    len(ltp_pass),
                    out["passed"],
                )

        if hist_errors:
            fail_reasons["fetch_exception_total"] = hist_errors

        today = datetime.now().strftime("%Y-%m-%d")
        dated = DATA_DIR / f"swing_universe_passed_{today}.csv"
        latest = DATA_DIR / "swing_universe_latest.csv"

        _write_passed_csv(dated, passed_rows, ltp_map, session_factory=sf)
        out["dated_csv"] = str(dated)
        if write_latest:
            _write_passed_csv(latest, passed_rows, ltp_map, session_factory=sf)
            out["latest_csv"] = str(latest)
        else:
            out["latest_csv"] = None
        out["fail_reasons"] = fail_reasons
        out["ok"] = True
        out["seconds"] = round(time.perf_counter() - t0, 2)
        logger.info(
            "Swing universe rebuild done: passed=%d failed=%d skipped_ltp=%d in %.1fs",
            out["passed"],
            out["failed"],
            out["skipped_ltp"],
            out["seconds"],
        )
        return out
    finally:
        await client.close()
