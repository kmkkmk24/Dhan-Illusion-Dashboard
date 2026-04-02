"""
Minervini-style VCP scanner — agreed spec (config-driven)

What we do
----------
1. **Universe** — Liquid equities (price/volume filters), optional ETF exclusions, known sector,
   scan cap (e.g. 200). Prefer non–F&O names; allow up to `max_fno_stocks_for_swing` F&O from
   liquid universe (sorted by top sectors first).

2. **Stage 2 (trend)** — Daily: **close > MA12 > MA24 > MA48** (rising stack = participation
   in an uptrend). Minervini often cites 50/150–200; your stack is faster — good for swing timing;
   we use it as a hard gate + scored for strength.

3. **Near 52-week high** — Default **85%** of 252-day high (`near_high_threshold: 0.85`):
   within ~15% of the high (leader behaviour). Loosen to **0.80** for more names; tighten to
   **0.90** for stricter leaders.

4. **VCP contractions** — **Weekly AND daily**: each timeframe must show at least
   `min_contractions_weekly` / `min_contractions_daily` measured pullback legs (rolling range
   compression). Progressive tightening increases the contraction score.

5. **Volume** — Dry-up during contraction; optional surge near the end (breakout hint).

6. **Relative strength (internal)** — Short-horizon performance vs stock’s own recent baseline
   (proxy until index RS is wired); scored 0–1.

Scoring (weights in config, default sum = 1.0)
----------------------------------------------
- **Trend / Stage 2** — weight_trend: alignment + distance above MAs.
- **Contraction** — weight_contraction: blend of weekly + daily contraction quality.
- **Volume** — weight_volume: dry-up + surge mix.
- **Price position** — weight_price_position: closeness to 52w high (beyond the hard gate).
- **RS** — weight_rs: momentum vs baseline.

**Signal** = passes all **gates** (Stage 2, near-high, weekly/daily contraction counts,
min volume/price) **and** `total_score >= min_score_threshold`. Top-sector names get a small
**sector_weight** boost (capped).
"""

import asyncio
import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional, Dict, List, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import Signal, SignalHistory, Instrument
from backend.services.dhan_client import DhanClient
from backend.services.sector_mapping import get_sectors_for_stock

logger = logging.getLogger(__name__)

# When using partial pass (chartink_min_checks_to_pass < total checks), these must still pass.
# Near-high is NOT here by default (it kills most names the same week); it still counts toward N/14.
# For strict leaders, set chartink_must_pass_checks including "daily_near_250d_high".
_DEFAULT_CHARTINK_HARD_KEYS = (
    "weekly_inside_lower_high",
    "weekly_inside_higher_low",
    "weekly_tight_range",
    "weekly_vol_daily_gt_min",
)


class VCPScanner:
    def __init__(self, db: Session):
        self.db = db
        self.config = get_config().get("vcp_scanner", {})
        self.dhan = DhanClient()
        
        # VCP Configuration (Mark Minervini's criteria)
        self.min_price = self.config.get("min_price", 500)
        self.max_price = self.config.get("max_price", 4000)
        self.min_volume = self.config.get("min_volume", 5000)
        self.near_high_threshold = float(self.config.get("near_high_threshold", 0.85))
        # ChartInk can use a different % of 250d high than Minervini (same YAML key near_high_threshold is the fallback).
        self.chartink_near_high_threshold = float(
            self.config.get("chartink_near_high_threshold", self.near_high_threshold)
        )
        self.contraction_thresholds = self.config.get("contraction_thresholds", [0.15, 0.10, 0.05])
        self.min_contractions_weekly = self.config.get("min_contractions_weekly", self.config.get("min_contractions", 3))
        self.min_contractions_daily = self.config.get("min_contractions_daily", 3)
        self.contraction_lookback_weeks = self.config.get("contraction_lookback_weeks", 20)
        self.contraction_lookback_days = self.config.get("contraction_lookback_days", 30)
        self.ma_fast = int(self.config.get("ma_fast", 12))
        self.ma_mid = int(self.config.get("ma_mid", 24))
        self.ma_slow = int(self.config.get("ma_slow", 48))
        self.volume_surge_threshold = self.config.get("volume_surge_threshold", 1.4)
        self.rs_threshold = self.config.get("rs_threshold", 70)
        self.min_score_threshold = float(self.config.get("min_score_threshold", 0.45))
        self.weight_trend = float(self.config.get("weight_trend", 0.22))
        self.weight_contraction = float(self.config.get("weight_contraction", 0.32))
        self.weight_volume = float(self.config.get("weight_volume", 0.18))
        self.weight_price_position = float(self.config.get("weight_price_position", 0.18))
        self.weight_rs = float(self.config.get("weight_rs", 0.10))
        self.sector_weight = float(self.config.get("sector_weight", 0.2))
        self.require_known_sector = self.config.get("require_known_sector", True)
        self.max_fno_stocks_for_swing = int(self.config.get("max_fno_stocks_for_swing", 20))
        self.swing_scan_mode = str(self.config.get("swing_scan_mode", "chartink")).lower().strip()
        self.chartink_weekly_range_max = float(self.config.get("chartink_weekly_range_max", 0.08))
        self.ct_s10 = int(self.config.get("chartink_sma10_period", 10))
        self.ct_s15 = int(self.config.get("chartink_sma15_period", 15))
        self.ct_s30 = int(self.config.get("chartink_sma30_period", 30))
        self.ct_s40 = int(self.config.get("chartink_sma40_period", 40))
        self.min_daily_bars = int(self.config.get("min_daily_bars", 260))
        self.chartink_week_freq = str(self.config.get("chartink_week_freq", "W-FRI"))
        self.chartink_drop_last_weekly = self.config.get("chartink_drop_last_weekly_bar", True)
        self.chartink_volume_avg5 = self.config.get("chartink_volume_use_avg5", True)
        self.chartink_require_sma30_lag4 = self.config.get("chartink_require_sma30_lag4", False)
        self.log_scan_summary = self.config.get("log_scan_summary", True)
        self.log_chartink_failure_samples = int(
            self.config.get("log_chartink_failure_samples", 15)
        )
        self.log_chartink_verbose = self.config.get("log_chartink_verbose", False)
        # ChartInk pass rule: requiring every SMA lag + 85%×250d in one week is very rare.
        # 0 = strict (all checks). N = need at least N checks True + must_pass list (if any).
        self.chartink_min_checks_to_pass = int(
            self.config.get("chartink_min_checks_to_pass", 0) or 0
        )
        self.chartink_must_pass_checks: List[str] = list(
            self.config.get("chartink_must_pass_checks") or []
        )
        # Rate limiting (each symbol may trigger multiple /charts/historical chunks)
        self.vcp_batch_size = max(1, int(self.config.get("batch_size", 5)))
        self.vcp_request_delay = float(self.config.get("request_delay", 0.55))
        self.vcp_batch_delay = float(self.config.get("batch_delay", 3.5))

    def _chartink_bail(
        self, result: Dict, reason: str, **ctx: object
    ) -> Dict:
        """Mark early ChartInk exit (before or instead of full checks)."""
        result["chartink_skip_reason"] = reason
        if ctx:
            result["chartink_skip_ctx"] = ctx
        result["chartink_checks"] = {}
        return result

    async def scan_vcp_patterns(
        self,
        instruments: List[Instrument],
        top_sectors: List[str],
        *,
        universe_mode: str = "full",
    ) -> Dict:
        """
        Enhanced VCP scan with sector-first approach and smart rate limiting.

        Args:
            instruments: List of instruments to scan
            top_sectors: List of top momentum sectors to prioritize
            universe_mode: ``full`` = apply smart filter / cap. ``csv`` = scan this list as-is
                (caller already restricted to bench CSV ids).

        Returns:
            Summary with VCP signals found
        """
        summary = {
            "scanned": 0, "vcp_signals": 0, "updated": 0,
            "invalidated": 0, "errors": 0, "rate_limited": 0,
            "top_sectors": top_sectors,
            "chartink_diagnostics": {},
            "scan_debug": {},
        }
        chartink_diag: Dict[str, Dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
        chartink_analyzed = 0
        skip_reasons: Dict[str, int] = defaultdict(int)
        failure_samples: List[Dict] = []

        if universe_mode == "csv":
            filtered_instruments = list(instruments)
            logger.info(
                "VCP swing: CSV universe — %s instruments (no smart_filter cap)",
                len(filtered_instruments),
            )
        else:
            filtered_instruments = self._smart_filter_instruments(instruments, top_sectors)

        n_fno_u = sum(1 for x in filtered_instruments if x.is_fno)
        logger.info(
            "VCP Scanner: universe %s (non-F&O=%s, F&O=%s) from %s total instruments",
            len(filtered_instruments),
            len(filtered_instruments) - n_fno_u,
            n_fno_u,
            len(instruments),
        )
        
        # Rate limiting: Process in batches with delays (see vcp_scanner.batch_size / *_delay)
        batch_size = self.vcp_batch_size
        delay_between_batches = self.vcp_batch_delay
        delay_between_requests = self.vcp_request_delay

        for i in range(0, len(filtered_instruments), batch_size):
            batch = filtered_instruments[i:i + batch_size]
            
            logger.info(f"Processing batch {i//batch_size + 1}/{(len(filtered_instruments) + batch_size - 1)//batch_size}")
            
            for instrument in batch:
                try:
                    summary["scanned"] += 1
                    
                    # Rate limiting delay
                    await asyncio.sleep(delay_between_requests)
                    
                    # Get historical data (daily for VCP analysis)
                    df = await self._get_historical_data(instrument)
                    sym = instrument.trading_symbol or instrument.symbol or "?"

                    if df is None:
                        skip_reasons["fetch_none_or_empty"] += 1
                        if self.log_chartink_verbose:
                            logger.info("VCP skip %s: no OHLCV dataframe", sym)
                        continue
                    if len(df) < self.min_daily_bars:
                        skip_reasons["daily_rows_below_min"] += 1
                        if self.log_chartink_verbose:
                            logger.info(
                                "VCP skip %s: rows=%s need>=%s",
                                sym,
                                len(df),
                                self.min_daily_bars,
                            )
                        continue

                    # Run VCP analysis (chartink or minervini)
                    vcp_result = self._analyze_vcp_pattern(df, instrument, top_sectors)

                    if self.swing_scan_mode != "minervini":
                        sk = vcp_result.get("chartink_skip_reason")
                        if sk:
                            skip_reasons[sk] += 1
                            if self.log_chartink_verbose:
                                logger.info(
                                    "VCP skip %s: chartink_bail=%s ctx=%s",
                                    sym,
                                    sk,
                                    vcp_result.get("chartink_skip_ctx"),
                                )
                        elif vcp_result.get("chartink_checks"):
                            chartink_analyzed += 1
                            for ck, ok in vcp_result["chartink_checks"].items():
                                if ok:
                                    chartink_diag[ck]["pass"] += 1
                                else:
                                    chartink_diag[ck]["fail"] += 1
                            if (
                                not vcp_result["is_vcp"]
                                and len(failure_samples) < self.log_chartink_failure_samples
                            ):
                                failure_samples.append(
                                    {
                                        "symbol": sym,
                                        "is_fno": bool(instrument.is_fno),
                                        "failed": vcp_result.get("chartink_failed_keys", []),
                                        "score_frac": round(vcp_result.get("total_score", 0), 3),
                                        "debug": vcp_result.get("chartink_numbers", {}),
                                    }
                                )
                            if self.log_chartink_verbose:
                                logger.info(
                                    "VCP %s: is_vcp=%s score=%.2f failed=%s",
                                    sym,
                                    vcp_result["is_vcp"],
                                    vcp_result.get("total_score", 0),
                                    vcp_result.get("chartink_failed_keys"),
                                )

                    if vcp_result["is_vcp"]:
                        # Save or update signal
                        signal_saved = await self._save_vcp_signal(instrument, vcp_result)
                        if signal_saved:
                            summary["vcp_signals"] += 1
                            logger.info(f"VCP signal found: {instrument.symbol} - Score: {vcp_result['total_score']:.2f}")
                    
                except Exception as e:
                    if "Rate_Limit" in str(e) or "429" in str(e):
                        summary["rate_limited"] += 1
                        logger.warning(f"Rate limited on {instrument.symbol}, waiting longer...")
                        await asyncio.sleep(5.0)  # Wait 5 seconds on rate limit
                    else:
                        summary["errors"] += 1
                        logger.error(f"Error scanning {instrument.symbol}: {e}")
            
            # Delay between batches (except for last batch)
            if i + batch_size < len(filtered_instruments):
                await asyncio.sleep(delay_between_batches)
        
        await self.dhan.close()

        summary["scan_debug"] = {
            "skip_reason_counts": dict(sorted(skip_reasons.items(), key=lambda x: -x[1])),
            "chartink_full_analysis_count": chartink_analyzed,
            "failure_samples": failure_samples,
        }

        if self.log_scan_summary:
            logger.info(
                "VCP scan_debug skip_reason_counts: %s",
                summary["scan_debug"]["skip_reason_counts"],
            )
            if chartink_analyzed:
                logger.info(
                    "VCP ChartInk: %s stocks ran all checks; diagnostics: %s",
                    chartink_analyzed,
                    {
                        k: f"{v['pass']}/{v['pass'] + v['fail']}"
                        for k, v in sorted(chartink_diag.items())
                    },
                )
            for s in failure_samples[:5]:
                logger.info(
                    "VCP ChartInk sample FAIL %s fno=%s score=%s failed=%s nums=%s",
                    s["symbol"],
                    s["is_fno"],
                    s["score_frac"],
                    s["failed"],
                    s.get("debug"),
                )
            if len(failure_samples) > 5:
                logger.info(
                    "VCP ChartInk … %s more failure samples in summary.scan_debug.failure_samples",
                    len(failure_samples) - 5,
                )
            if chartink_analyzed == 0 and self.swing_scan_mode != "minervini":
                logger.warning(
                    "VCP ChartInk: NO stock reached full checks — all bailed early. "
                    "See skip_reason_counts above (e.g. weekly_bars_low, sma_nan)."
                )

        if chartink_diag and chartink_analyzed:
            summary["chartink_diagnostics"] = {
                k: dict(v) for k, v in sorted(chartink_diag.items())
            }
            summary["chartink_stocks_analyzed"] = chartink_analyzed

        return summary

    def _smart_filter_instruments(self, instruments: List[Instrument], top_sectors: List[str]) -> List[Instrument]:
        """
        Universe for swing VCP: known sector (if configured), non-F&O first, cap F&O count.
        """
        eligible: List[Tuple[Instrument, bool]] = []  # (instrument, in_top_sector)

        exclude_kw = [k.lower() for k in self.config.get("exclude_keywords", [])]
        exclude_etf = self.config.get("exclude_etfs", True)

        for instrument in instruments:
            symbol = instrument.trading_symbol or instrument.symbol
            sym_l = (symbol or "").lower()
            if exclude_etf and exclude_kw and any(k in sym_l for k in exclude_kw):
                continue
            stock_sectors = get_sectors_for_stock(symbol)
            if self.require_known_sector and not stock_sectors:
                continue
            in_top = bool(top_sectors) and any(sector in top_sectors for sector in stock_sectors)
            eligible.append((instrument, in_top))

        non_fno = [(i, ts) for i, ts in eligible if not i.is_fno]
        fno_only = [(i, ts) for i, ts in eligible if i.is_fno]
        non_fno.sort(key=lambda x: (not x[1],))  # top sectors first
        fno_only.sort(key=lambda x: (not x[1],))

        max_stocks = int(self.config.get("max_stocks_to_scan", 200))
        max_fno = max(0, self.max_fno_stocks_for_swing)

        # Reserve capacity for F&O names — otherwise 200 non-F&O fill the list and
        # GLENMARK-style F&O leaders are never scanned.
        slots_non_prio = max(0, max_stocks - max_fno)
        merged: List[Instrument] = []
        for inst, _ in non_fno:
            if len(merged) >= slots_non_prio:
                break
            merged.append(inst)

        fno_added = 0
        for inst, _ in fno_only:
            if len(merged) >= max_stocks:
                break
            if fno_added >= max_fno:
                break
            merged.append(inst)
            fno_added += 1

        merged_ids = {m.security_id for m in merged}
        for inst, _ in non_fno:
            if inst.security_id in merged_ids:
                continue
            if len(merged) >= max_stocks:
                break
            merged.append(inst)
            merged_ids.add(inst.security_id)

        fn_in = sum(1 for x in merged if x.is_fno)
        logger.info(
            f"VCP universe: {len(merged)} instruments ({len(merged) - fn_in} non-F&O + {fn_in} F&O, cap F&O={max_fno})"
        )
        return merged

    def _prioritize_by_sector(self, instruments: List[Instrument], top_sectors: List[str]) -> List[Instrument]:
        """Prioritize instruments from top momentum sectors."""
        prioritized = []
        remaining = []
        
        for instrument in instruments:
            symbol = instrument.trading_symbol or instrument.symbol
            stock_sectors = get_sectors_for_stock(symbol)
            
            # Check if stock belongs to any top sector
            if any(sector in top_sectors for sector in stock_sectors):
                prioritized.append(instrument)
            else:
                remaining.append(instrument)
        
        # Return prioritized first, then remaining
        return prioritized + remaining

    async def _get_historical_data(self, instrument: Instrument) -> Optional[pd.DataFrame]:
        """Get daily historical data for VCP analysis."""
        try:
            to_date = date.today()
            # 250d high + weekly SMA40 need ~2 years calendar for comfortable weekly bar count
            from_date = to_date - timedelta(days=int(self.config.get("history_calendar_days", 520)))
            
            df = await self.dhan.get_historical_daily_data(
                security_id=instrument.security_id,
                exchange_segment="NSE_EQ",
                instrument="EQUITY",
                from_date=from_date,
                to_date=to_date
            )
            
            if df is not None and not df.empty:
                # Ensure we have required columns
                required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                if all(col in df.columns for col in required_cols):
                    return df.sort_values('timestamp').reset_index(drop=True)
            
            return None
            
        except Exception as e:
            logger.error(f"Error fetching data for {instrument.symbol}: {e}")
            return None

    def _analyze_vcp_pattern(
        self, df: pd.DataFrame, instrument: Instrument, top_sectors: List[str]
    ) -> Dict:
        """Dispatch ChartInk-parity vs Minervini strict mode."""
        if self.swing_scan_mode == "minervini":
            return self._analyze_minervini_pattern(df, instrument, top_sectors)
        return self._analyze_chartink_pattern(df, instrument, top_sectors)

    def _analyze_chartink_pattern(
        self, df: pd.DataFrame, instrument: Instrument, top_sectors: List[str]
    ) -> Dict:
        """
        Mirrors your ChartInk scanner (weekly inside + tight range + weekly SMAs + 85%×250d high + vol).
        See config: chartink_* keys. Uses W-FRI weeks + drops last bucket when configured.
        """
        result: Dict = {
            "is_vcp": False,
            "total_score": 0.0,
            "contraction_score": 0.0,
            "volume_score": 0.0,
            "trend_score": 0.0,
            "relative_strength": 0.0,
            "price_position": 0.0,
            "price_position_score": 0.0,
            "contractions_found": 0,
            "contractions_weekly": 0,
            "contractions_daily": 0,
            "stage2_ok": False,
            "breakout_imminent": False,
            "current_price": 0.0,
            "chartink_checks": {},
            "chartink_skip_reason": None,
            "chartink_failed_keys": [],
            "chartink_numbers": {},
        }
        try:
            if len(df) < self.min_daily_bars:
                return self._chartink_bail(
                    result, "daily_rows_below_min", rows=len(df), need=self.min_daily_bars
                )

            d_close = float(df["close"].iloc[-1])
            d_vol = float(df["volume"].iloc[-1])
            result["current_price"] = d_close

            if d_close < self.min_price or d_close > self.max_price:
                return self._chartink_bail(
                    result,
                    "price_outside_band",
                    close=d_close,
                    min_p=self.min_price,
                    max_p=self.max_price,
                )

            weekly = self._resample_to_weekly(df)
            if weekly is None or weekly.empty:
                return self._chartink_bail(result, "weekly_resample_empty")

            w = weekly.dropna(subset=["high", "low", "close"])
            # Drop last weekly bucket — often a partial Mon–Thu week vs full Fri–Fri NSE week
            if self.chartink_drop_last_weekly and len(w) >= 2:
                w = w.iloc[:-1].copy()

            need_w = self.ct_s40 + 8
            if len(w) < need_w:
                return self._chartink_bail(
                    result,
                    "weekly_bars_low",
                    weeks=len(w),
                    need_weeks=need_w,
                    week_freq=self.chartink_week_freq,
                )

            # Most recent *complete* week (Fri-freq resample + drop partial)
            i = -1
            hi_c = float(w["high"].iloc[i])
            lo_c = float(w["low"].iloc[i])
            cl_c = float(w["close"].iloc[i])
            hi_p = float(w["high"].iloc[i - 1])
            lo_p = float(w["low"].iloc[i - 1])

            sma10 = w["close"].rolling(self.ct_s10, min_periods=self.ct_s10).mean()
            sma15 = w["close"].rolling(self.ct_s15, min_periods=self.ct_s15).mean()
            sma30 = w["close"].rolling(self.ct_s30, min_periods=self.ct_s30).mean()
            sma40 = w["close"].rolling(self.ct_s40, min_periods=self.ct_s40).mean()

            _sma_vals = [
                sma10.iloc[i],
                sma15.iloc[i],
                sma30.iloc[i],
                sma40.iloc[i],
            ]
            if any(pd.isna(v) for v in _sma_vals):
                return self._chartink_bail(
                    result,
                    "weekly_sma_nan",
                    week_rows=len(w),
                    last_week=str(w["timestamp"].iloc[-1]) if "timestamp" in w.columns else "?",
                )

            high_250 = float(df["high"].tail(250).max())
            nh = float(self.chartink_near_high_threshold)

            checks: Dict[str, bool] = {}
            checks["weekly_inside_lower_high"] = hi_c < hi_p
            checks["weekly_inside_higher_low"] = lo_c > lo_p
            checks["weekly_tight_range"] = (
                (hi_c - lo_c) / hi_c < self.chartink_weekly_range_max if hi_c > 0 else False
            )
            checks["weekly_close_gt_sma15"] = cl_c > float(sma15.iloc[i])
            checks["daily_near_250d_high"] = d_close >= nh * high_250 if high_250 > 0 else False
            checks["weekly_sma10_gt_sma30"] = float(sma10.iloc[i]) > float(sma30.iloc[i])
            if self.chartink_volume_avg5:
                v_eff = max(d_vol, float(df["volume"].tail(5).mean()))
            else:
                v_eff = d_vol
            checks["weekly_vol_daily_gt_min"] = v_eff > float(self.min_volume)

            # Weekly SMA10 rising vs 1,2,3 weeks ago (same series, prior bars)
            s10_i = float(sma10.iloc[i])
            checks["sma10_gt_lag1"] = s10_i > float(sma10.iloc[i - 1])
            checks["sma10_gt_lag2"] = s10_i > float(sma10.iloc[i - 2])
            checks["sma10_gt_lag3"] = s10_i > float(sma10.iloc[i - 3])

            s30_i = float(sma30.iloc[i])
            checks["sma30_gt_lag1"] = s30_i > float(sma30.iloc[i - 1])
            checks["sma30_gt_lag2"] = s30_i > float(sma30.iloc[i - 2])
            checks["sma30_gt_lag3"] = s30_i > float(sma30.iloc[i - 3])

            # Longer weekly trend: SMA30 above SMA40 (+ optional lag4 per strict ChartInk)
            checks["sma30_gt_sma40"] = s30_i > float(sma40.iloc[i])
            if self.chartink_require_sma30_lag4:
                checks["sma30_gt_lag4"] = s30_i > float(sma30.iloc[i - 4])

            result["chartink_checks"] = checks
            result["chartink_failed_keys"] = [k for k, v in checks.items() if not v]
            n_ok = sum(1 for v in checks.values() if v)
            total_c = len(checks)
            result["total_score"] = n_ok / max(1, total_c)

            need = (
                total_c
                if self.chartink_min_checks_to_pass <= 0
                else min(self.chartink_min_checks_to_pass, total_c)
            )
            if need >= total_c:
                passed = all(checks.values())
            else:
                must = (
                    self.chartink_must_pass_checks
                    if self.chartink_must_pass_checks
                    else list(_DEFAULT_CHARTINK_HARD_KEYS)
                )
                hard_ok = all(checks.get(k, False) for k in must if k in checks)
                passed = hard_ok and n_ok >= need

            result["is_vcp"] = passed
            result["chartink_pass_rule"] = {
                "need_checks": need,
                "total_checks": total_c,
                "hard_gates": (
                    []
                    if need >= total_c
                    else (
                        self.chartink_must_pass_checks
                        if self.chartink_must_pass_checks
                        else list(_DEFAULT_CHARTINK_HARD_KEYS)
                    )
                ),
            }

            rng_ratio = (hi_c - lo_c) / hi_c if hi_c > 0 else None
            result["chartink_numbers"] = {
                "close": round(d_close, 2),
                "high_250d": round(high_250, 2) if high_250 else None,
                "pct_of_250d_high": round(d_close / high_250, 4) if high_250 else None,
                "need_pct_for_gate": nh,
                "vol_last": int(d_vol),
                "vol_eff": int(max(d_vol, float(df["volume"].tail(5).mean())))
                if self.chartink_volume_avg5
                else int(d_vol),
                "weekly_hi_cur": round(hi_c, 2),
                "weekly_lo_cur": round(lo_c, 2),
                "weekly_hi_prev": round(hi_p, 2),
                "weekly_lo_prev": round(lo_p, 2),
                "weekly_range_ratio": round(rng_ratio, 4) if rng_ratio is not None else None,
                "weekly_range_max_cfg": self.chartink_weekly_range_max,
            }

            # Map into existing score columns for UI / tooltips
            trend_keys = [
                "weekly_sma10_gt_sma30",
                "sma10_gt_lag1",
                "sma10_gt_lag2",
                "sma10_gt_lag3",
                "sma30_gt_lag1",
                "sma30_gt_lag2",
                "sma30_gt_lag3",
                "sma30_gt_sma40",
            ]
            if self.chartink_require_sma30_lag4:
                trend_keys.append("sma30_gt_lag4")
            result["trend_score"] = sum(1 for k in trend_keys if checks.get(k)) / max(
                1, len(trend_keys)
            )
            result["contraction_score"] = (
                1.0
                if checks.get("weekly_inside_lower_high")
                and checks.get("weekly_inside_higher_low")
                and checks.get("weekly_tight_range")
                else 0.35
            )
            result["volume_score"] = 1.0 if checks.get("weekly_vol_daily_gt_min") else 0.0
            result["price_position"] = d_close / high_250 if high_250 > 0 else 0.0
            result["price_position_score"] = result["price_position"]
            result["stage2_ok"] = checks.get("weekly_sma10_gt_sma30", False)
            result["contractions_weekly"] = 3 if result["contraction_score"] >= 0.9 else 0
            result["contractions_daily"] = 0
            result["contractions_found"] = result["contractions_weekly"]

            symbol = instrument.trading_symbol or instrument.symbol
            stock_sectors = get_sectors_for_stock(symbol)
            if stock_sectors and top_sectors and any(
                sector in top_sectors for sector in stock_sectors
            ):
                result["total_score"] = min(1.0, result["total_score"] + self.sector_weight * 0.25)

        except Exception as e:
            logger.error(f"Error in ChartInk analysis for {instrument.symbol}: {e}")
            result["chartink_skip_reason"] = "chartink_exception"
            result["chartink_skip_ctx"] = {"error": str(e)[:200]}
            return result

        return result

    def _analyze_minervini_pattern(
        self, df: pd.DataFrame, instrument: Instrument, top_sectors: List[str]
    ) -> Dict:
        """Minervini-style gates + weighted score (see module docstring)."""
        result: Dict = {
            "is_vcp": False,
            "total_score": 0.0,
            "contraction_score": 0.0,
            "volume_score": 0.0,
            "trend_score": 0.0,
            "relative_strength": 0.0,
            "price_position": 0.0,
            "price_position_score": 0.0,
            "contractions_found": 0,
            "contractions_weekly": 0,
            "contractions_daily": 0,
            "stage2_ok": False,
            "breakout_imminent": False,
            "current_price": 0.0,
        }

        try:
            need = max(self.ma_slow + 5, 252)
            if len(df) < need:
                return result

            current_price = float(df["close"].iloc[-1])
            result["current_price"] = current_price
            if current_price < self.min_price or current_price > self.max_price:
                return result

            avg_volume = float(df["volume"].tail(20).mean())
            if avg_volume < self.min_volume:
                return result

            weekly_df = self._resample_to_weekly(df)
            w_series = self._extract_contraction_pcts(
                weekly_df, window=4, tail=self.contraction_lookback_weeks
            )
            d_series = self._extract_contraction_pcts(
                df, window=5, tail=self.contraction_lookback_days
            )
            w_score, w_count, _ = self._score_contraction_series(w_series)
            d_score, d_count, _ = self._score_contraction_series(d_series)
            contraction_blend = (w_score + d_score) / 2.0 if (w_series or d_series) else 0.0
            result["contraction_score"] = contraction_blend
            result["contractions_weekly"] = w_count
            result["contractions_daily"] = d_count
            result["contractions_found"] = min(w_count, d_count)

            volume_result = self._analyze_volume_behavior(df)
            result.update(volume_result)

            trend_result = self._analyze_stage2_ma_stack(df)
            result.update(trend_result)

            price_ratio = self._calculate_price_position(df)
            result["price_position"] = price_ratio
            nh = float(self.near_high_threshold)
            if price_ratio >= nh:
                result["price_position_score"] = min(
                    1.0, (price_ratio - nh) / max(1e-6, 1.0 - nh)
                )
            else:
                result["price_position_score"] = 0.0

            rs = self._relative_strength_score(df)
            result["relative_strength"] = rs

            symbol = instrument.trading_symbol or instrument.symbol
            stock_sectors = get_sectors_for_stock(symbol)
            in_top_sector = bool(stock_sectors) and bool(top_sectors) and any(
                sector in top_sectors for sector in stock_sectors
            )

            total_score = (
                self.weight_trend * float(result["trend_score"])
                + self.weight_contraction * float(result["contraction_score"])
                + self.weight_volume * float(result.get("volume_score", 0.0))
                + self.weight_price_position * float(result["price_position_score"])
                + self.weight_rs * float(rs)
            )
            if in_top_sector:
                total_score = min(1.0, total_score + self.sector_weight * 0.5)
            result["total_score"] = total_score

            gates_ok = (
                bool(result.get("stage2_ok"))
                and price_ratio >= nh
                and w_count >= self.min_contractions_weekly
                and d_count >= self.min_contractions_daily
            )
            result["is_vcp"] = bool(
                gates_ok and total_score >= self.min_score_threshold
            )

        except Exception as e:
            logger.error(f"Error in VCP analysis for {instrument.symbol}: {e}")
            return result

        return result

    def _extract_contraction_pcts(
        self, ohlc: pd.DataFrame, window: int, tail: int
    ) -> List[float]:
        """Rolling high-low range / high over trailing windows (contraction leg series)."""
        if ohlc is None or ohlc.empty or len(ohlc) < window + 2:
            return []
        contractions: List[float] = []
        start = max(window, len(ohlc) - int(tail))
        for i in range(start, len(ohlc)):
            period_high = float(ohlc["high"].iloc[i - window : i].max())
            period_low = float(ohlc["low"].iloc[i - window : i].min())
            if period_high > 0:
                contractions.append((period_high - period_low) / period_high)
        return contractions

    def _score_contraction_series(
        self, contractions: List[float]
    ) -> Tuple[float, int, int]:
        """Returns (score 0-1, count, progressive_pairs)."""
        if len(contractions) < 3:
            return 0.0, len(contractions), 0
        progressive_count = sum(
            1
            for i in range(1, len(contractions))
            if contractions[i] < contractions[i - 1]
        )
        score = 0.0
        if progressive_count >= 2:
            final_contraction = contractions[-1]
            if final_contraction <= 0.05:
                score = 1.0
            elif final_contraction <= 0.08:
                score = 0.8
            elif final_contraction <= 0.12:
                score = 0.6
            else:
                score = 0.4
        elif len(contractions) >= 3:
            score = 0.25
        return score, len(contractions), progressive_count

    def _analyze_volume_behavior(self, df: pd.DataFrame) -> Dict:
        """
        Analyze volume behavior: dry-up during contraction + surge on breakout.
        """
        try:
            recent_volume = df['volume'].tail(10).mean()
            avg_volume = df['volume'].tail(50).mean()
            
            # Volume dry-up score (lower recent volume is better)
            volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0
            
            if volume_ratio <= 0.7:  # 30% below average
                dryup_score = 1.0
            elif volume_ratio <= 0.8:  # 20% below average
                dryup_score = 0.8
            elif volume_ratio <= 0.9:  # 10% below average
                dryup_score = 0.6
            else:
                dryup_score = 0.2
            
            # Check for recent volume surge (breakout signal)
            latest_volume = df['volume'].iloc[-1]
            surge_ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0
            
            breakout_imminent = surge_ratio >= self.volume_surge_threshold
            
            # Combined volume score
            volume_score = dryup_score * 0.7 + (min(surge_ratio / 2, 1.0) * 0.3)
            
            return {
                "volume_score": min(1.0, volume_score),
                "volume_dryup_ratio": volume_ratio,
                "volume_surge_ratio": surge_ratio,
                "breakout_imminent": breakout_imminent
            }
            
        except Exception as e:
            logger.error(f"Error in volume analysis: {e}")
            return {"volume_score": 0.0, "breakout_imminent": False}

    def _analyze_stage2_ma_stack(self, df: pd.DataFrame) -> Dict:
        """
        Stage 2 gate: close > MA12 > MA24 > MA48 (configurable periods).
        Scores alignment and distance above slow MA.
        """
        try:
            close = df["close"]
            ma_f = close.rolling(self.ma_fast).mean().iloc[-1]
            ma_m = close.rolling(self.ma_mid).mean().iloc[-1]
            ma_s = close.rolling(self.ma_slow).mean().iloc[-1]
            cur = float(close.iloc[-1])
            ma_f, ma_m, ma_s = float(ma_f), float(ma_m), float(ma_s)

            stage2_ok = cur > ma_f > ma_m > ma_s

            score = 0.0
            if cur > ma_f:
                score += 0.35
            if ma_f > ma_m:
                score += 0.25
            if ma_m > ma_s:
                score += 0.25
            if ma_s > 0 and (cur - ma_s) / ma_s > 0.02:
                score += 0.15

            return {
                "trend_score": min(1.0, score),
                "stage2_ok": stage2_ok,
            }
        except Exception as e:
            logger.error(f"Error in Stage 2 MA analysis: {e}")
            return {"trend_score": 0.0, "stage2_ok": False}

    def _relative_strength_score(self, df: pd.DataFrame) -> float:
        """Proxy RS: 20-day return scaled (0–1); index RS can replace later."""
        try:
            close = df["close"]
            if len(close) < 22:
                return 0.0
            base = float(close.iloc[-22])
            if base <= 0:
                return 0.0
            ret20 = float(close.iloc[-1]) / base - 1.0
            return min(1.0, max(0.0, ret20 / 0.15))
        except Exception as e:
            logger.error(f"Error in RS score: {e}")
            return 0.0

    def _calculate_price_position(self, df: pd.DataFrame) -> float:
        """
        Calculate how close the stock is to its 52-week high.
        """
        try:
            current_price = df['close'].iloc[-1]
            year_high = df['high'].tail(252).max()  # 252 trading days ≈ 1 year
            
            if year_high > 0:
                position = current_price / year_high
                return min(1.0, position)
            
            return 0.0
            
        except Exception as e:
            logger.error(f"Error calculating price position: {e}")
            return 0.0

    def _resample_to_weekly(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert daily data to weekly OHLCV (NSE: week ending Friday by default)."""
        try:
            df_copy = df.copy()
            df_copy["timestamp"] = pd.to_datetime(df_copy["timestamp"])
            df_copy.set_index("timestamp", inplace=True)

            freq = getattr(self, "chartink_week_freq", None) or self.config.get(
                "chartink_week_freq", "W-FRI"
            )
            weekly = df_copy.resample(freq, label="right", closed="right").agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            weekly = weekly.dropna(how="any")
            return weekly.reset_index()

        except Exception as e:
            logger.error(f"Error resampling to weekly: {e}")
            return pd.DataFrame()

    async def _save_vcp_signal(self, instrument: Instrument, vcp_result: Dict) -> bool:
        """Save VCP signal to database."""
        try:
            # Check if signal already exists
            existing = self.db.query(Signal).filter(
                Signal.security_id == instrument.security_id,
                Signal.section == "swing",
                Signal.status.in_(["active", "tracking"])
            ).first()
            
            if existing:
                # Update existing signal
                existing.current_score = vcp_result["total_score"]
                existing.last_updated_date = date.today()
                self.db.commit()
                return True
            else:
                # Create new signal
                signal = Signal(
                    security_id=instrument.security_id,
                    symbol=instrument.trading_symbol or instrument.symbol,
                    section="swing",
                    signal_type="VCP",
                    occurrence_number=1,
                    first_detected_date=date.today(),
                    last_updated_date=date.today(),
                    current_score=vcp_result["total_score"],
                    peak_score=vcp_result["total_score"],
                    trend_score=vcp_result["trend_score"],
                    consol_score=vcp_result["contraction_score"],
                    breakout_score=vcp_result["volume_score"],
                    status="active",
                    sector=get_sectors_for_stock(instrument.trading_symbol or instrument.symbol)[0] if get_sectors_for_stock(instrument.trading_symbol or instrument.symbol) else None,
                    close_price_at_detection=vcp_result.get("current_price", 0.0)
                )
                
                self.db.add(signal)
                self.db.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error saving VCP signal for {instrument.symbol}: {e}")
            self.db.rollback()
            return False