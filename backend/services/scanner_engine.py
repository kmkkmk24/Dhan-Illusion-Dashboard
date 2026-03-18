"""
Consolidation-Explosion Scanner Engine

Detects Volatility Contraction Patterns (VCP) using three layers:
  1. Bollinger Band Width (BBW) squeeze
  2. ATR compression
  3. Price range narrowing

Also detects breakout/explosion events on previously consolidated stocks.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import Signal, SignalHistory, Instrument
from backend.services.dhan_client import DhanClient
from backend.services.sector_mapping import get_sectors_for_stock

logger = logging.getLogger(__name__)


class ScannerEngine:
    def __init__(self, db: Session):
        self.db = db
        self.config = get_config()["scanner"]
        self.dhan = DhanClient()

    async def scan_stocks(self, instruments: list[Instrument], section: str) -> dict:
        """
        Run the full consolidation-explosion scan on a list of instruments.

        Args:
            instruments: List of Instrument objects to scan
            section: "swing" or "fno"

        Returns:
            Summary dict with counts of new, updated, exploded, invalidated signals
        """
        summary = {"scanned": 0, "new_signals": 0, "updated": 0, "exploded": 0, "invalidated": 0, "errors": 0}

        to_date = date.today()
        lookback_days = max(
            self.config["bb_lookback_days"],
            self.config["range_long_period"],
            self.config["atr_avg_period"],
        ) + self.config["bb_period"] + 50  # extra buffer for indicators
        from_date = to_date - timedelta(days=int(lookback_days * 1.5))  # account for weekends/holidays

        for i, instrument in enumerate(instruments):
            try:
                if i > 0:
                    await asyncio.sleep(0.35)
                df = await self.dhan.get_historical_daily_data(
                    security_id=instrument.security_id,
                    exchange_segment="NSE_EQ",
                    instrument="EQUITY",
                    from_date=from_date,
                    to_date=to_date,
                )
                if df is None or len(df) < self.config["bb_lookback_days"]:
                    continue

                summary["scanned"] += 1
                result = self._analyze_stock(df, instrument, section)

                if result:
                    self._process_result(result, instrument, section)
                    if result["is_new"]:
                        summary["new_signals"] += 1
                    elif result["is_explosion"]:
                        summary["exploded"] += 1
                    else:
                        summary["updated"] += 1

            except Exception as e:
                logger.error(f"Error scanning {instrument.symbol}: {e}")
                summary["errors"] += 1

        invalidated = self._invalidate_stale_signals(section)
        summary["invalidated"] = invalidated

        self.db.commit()
        await self.dhan.close()

        logger.info(f"Scan complete ({section}): {summary}")
        return summary

    def _analyze_stock(self, df: pd.DataFrame, instrument: Instrument, section: str) -> Optional[dict]:
        """Analyze a single stock's historical data for VCP patterns."""
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values

        # Layer 1: Bollinger Band Width squeeze
        bb_result = self._compute_bb_squeeze(close)

        # Layer 2: ATR compression
        atr_result = self._compute_atr_compression(high, low, close)

        # Layer 3: Price range narrowing
        range_result = self._compute_range_narrowing(high, low)

        # Combined score
        w = self.config
        combined_score = (
            w["weight_bbw"] * bb_result["score"]
            + w["weight_atr"] * atr_result["score"]
            + w["weight_range"] * range_result["score"]
        )

        # Volume dry-up bonus (current volume below average = more consolidation)
        vol_avg = np.mean(volume[-self.config["volume_avg_period"]:])
        current_vol = volume[-1]
        volume_ratio = current_vol / vol_avg if vol_avg > 0 else 1.0
        if volume_ratio < 0.6:
            combined_score = min(1.0, combined_score + 0.05)

        # Check for explosion on existing active signals
        is_explosion = self._check_explosion(close, high, volume, bb_result)

        existing_signal = self.db.query(Signal).filter(
            Signal.security_id == instrument.security_id,
            Signal.status == "active",
        ).first()

        if existing_signal:
            return {
                "is_new": False,
                "is_explosion": is_explosion,
                "signal": existing_signal,
                "combined_score": round(combined_score, 4),
                "bb_width": round(bb_result["bb_width"], 6),
                "bb_percentile": round(bb_result["percentile"], 2),
                "atr": round(atr_result["atr"], 4),
                "atr_ratio": round(atr_result["ratio"], 4),
                "price_range_ratio": round(range_result["ratio"], 4),
                "volume_ratio": round(volume_ratio, 4),
                "close_price": float(close[-1]),
                "volume": float(current_vol),
            }

        if combined_score >= self.config["signal_threshold"]:
            return {
                "is_new": True,
                "is_explosion": False,
                "signal": None,
                "combined_score": round(combined_score, 4),
                "bb_width": round(bb_result["bb_width"], 6),
                "bb_percentile": round(bb_result["percentile"], 2),
                "atr": round(atr_result["atr"], 4),
                "atr_ratio": round(atr_result["ratio"], 4),
                "price_range_ratio": round(range_result["ratio"], 4),
                "volume_ratio": round(volume_ratio, 4),
                "close_price": float(close[-1]),
                "volume": float(current_vol),
            }

        return None

    def _compute_bb_squeeze(self, close: np.ndarray) -> dict:
        """
        Compute Bollinger Band Width and its percentile rank.

        BBW = (Upper - Lower) / Middle
        Squeeze detected when BBW is in the lowest percentile of its history.
        """
        period = self.config["bb_period"]
        std_dev = self.config["bb_std_dev"]
        lookback = self.config["bb_lookback_days"]

        sma = pd.Series(close).rolling(period).mean().values
        std = pd.Series(close).rolling(period).std().values

        upper = sma + std_dev * std
        lower = sma - std_dev * std
        bb_width = (upper - lower) / np.where(sma > 0, sma, 1)

        current_bbw = bb_width[-1]
        historical_bbw = bb_width[-lookback:]
        historical_bbw = historical_bbw[~np.isnan(historical_bbw)]

        if len(historical_bbw) == 0:
            return {"score": 0.0, "bb_width": 0.0, "percentile": 100.0}

        percentile = (np.sum(historical_bbw < current_bbw) / len(historical_bbw)) * 100

        threshold = self.config["bb_squeeze_percentile"]
        if percentile <= threshold:
            score = 1.0 - (percentile / threshold)
        else:
            score = max(0, 1.0 - (percentile / 50))

        return {
            "score": min(1.0, max(0.0, score)),
            "bb_width": float(current_bbw) if not np.isnan(current_bbw) else 0.0,
            "percentile": float(percentile),
        }

    def _compute_atr_compression(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict:
        """
        Compute ATR and compare to its moving average.

        Compression detected when ATR is significantly below its average.
        """
        period = self.config["atr_period"]
        avg_period = self.config["atr_avg_period"]

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )
        tr = np.concatenate([[high[0] - low[0]], tr])

        atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
        atr_avg = pd.Series(atr).rolling(avg_period).mean().values

        current_atr = atr[-1]
        current_avg = atr_avg[-1]

        if np.isnan(current_avg) or current_avg == 0:
            return {"score": 0.0, "atr": 0.0, "ratio": 1.0}

        ratio = current_atr / current_avg
        threshold = self.config["atr_compression_ratio"]

        if ratio <= threshold:
            score = 1.0 - (ratio / threshold)
        else:
            score = max(0, 1.0 - ((ratio - threshold) / (1.0 - threshold)))

        return {
            "score": min(1.0, max(0.0, score)),
            "atr": float(current_atr),
            "ratio": float(ratio),
        }

    def _compute_range_narrowing(self, high: np.ndarray, low: np.ndarray) -> dict:
        """
        Compare recent price range to longer-term range.

        Narrowing detected when short-term range is much smaller than long-term range.
        """
        short = self.config["range_short_period"]
        long = self.config["range_long_period"]

        short_range = np.max(high[-short:]) - np.min(low[-short:])
        long_range = np.max(high[-long:]) - np.min(low[-long:])

        if long_range == 0:
            return {"score": 0.0, "ratio": 1.0}

        ratio = short_range / long_range
        threshold = self.config["range_narrowing_ratio"]

        if ratio <= threshold:
            score = 1.0 - (ratio / threshold)
        else:
            score = max(0, 1.0 - ((ratio - threshold) / (1.0 - threshold)))

        return {
            "score": min(1.0, max(0.0, score)),
            "ratio": float(ratio),
        }

    def _check_explosion(
        self, close: np.ndarray, high: np.ndarray, volume: np.ndarray, bb_result: dict
    ) -> bool:
        """
        Check if a breakout/explosion is occurring.

        Conditions:
          - Price closes above the upper Bollinger Band
          - Volume spikes above the threshold multiple of the average
        """
        period = self.config["bb_period"]
        std_dev = self.config["bb_std_dev"]

        sma = np.mean(close[-period:])
        std = np.std(close[-period:])
        upper_band = sma + std_dev * std

        vol_avg = np.mean(volume[-self.config["volume_avg_period"]:])
        current_vol = volume[-1]
        vol_ratio = current_vol / vol_avg if vol_avg > 0 else 0

        price_above_upper = close[-1] > upper_band
        volume_spike = vol_ratio >= self.config["volume_spike_ratio"]

        return price_above_upper and volume_spike

    def _process_result(self, result: dict, instrument: Instrument, section: str) -> None:
        """Create or update a signal based on scan results."""
        today = date.today()

        if result["is_new"]:
            # Count past occurrences for recurrence tracking
            past_count = self.db.query(Signal).filter(
                Signal.security_id == instrument.security_id,
            ).count()

            display_sym = instrument.trading_symbol or instrument.symbol
            sector = instrument.sector
            if not sector:
                sectors = get_sectors_for_stock(display_sym)
                sector = sectors[0] if sectors else "Other"

            signal = Signal(
                security_id=instrument.security_id,
                symbol=display_sym,
                section=section,
                signal_type="consolidation",
                occurrence_number=past_count + 1,
                first_detected_date=today,
                last_updated_date=today,
                current_score=result["combined_score"],
                peak_score=result["combined_score"],
                status="active",
                sector=sector,
                close_price_at_detection=result["close_price"],
            )
            self.db.add(signal)
            self.db.flush()

            if past_count > 0:
                logger.info(
                    f"Recurrence: {instrument.symbol} detected for the "
                    f"{past_count + 1}{'st' if past_count + 1 == 1 else 'nd' if past_count + 1 == 2 else 'rd' if past_count + 1 == 3 else 'th'} time"
                )
        else:
            signal = result["signal"]
            signal.current_score = result["combined_score"]
            signal.peak_score = max(signal.peak_score, result["combined_score"])
            signal.last_updated_date = today

            if not signal.sector:
                display_sym = instrument.trading_symbol or instrument.symbol
                sectors = get_sectors_for_stock(display_sym)
                signal.sector = sectors[0] if sectors else "Other"

            if result["is_explosion"]:
                signal.signal_type = "explosion"
                signal.status = "exploded"
                logger.info(f"EXPLOSION detected: {instrument.symbol} (score: {result['combined_score']})")

        # Record daily snapshot
        existing_history = self.db.query(SignalHistory).filter(
            SignalHistory.signal_id == signal.id,
            SignalHistory.date == today,
        ).first()

        if not existing_history:
            history = SignalHistory(
                signal_id=signal.id,
                date=today,
                close_price=result["close_price"],
                bb_width=result["bb_width"],
                bb_percentile=result["bb_percentile"],
                atr=result["atr"],
                atr_ratio=result["atr_ratio"],
                price_range_ratio=result["price_range_ratio"],
                volume=result["volume"],
                volume_ratio=result["volume_ratio"],
                combined_score=result["combined_score"],
            )
            self.db.add(history)

    def _invalidate_stale_signals(self, section: str) -> int:
        """
        Mark active signals as invalidated if the setup has broken down.

        A signal is invalidated when its score drops below 0.3 (setup lost its tightness).
        """
        invalidation_threshold = 0.3
        active_signals = self.db.query(Signal).filter(
            Signal.section == section,
            Signal.status == "active",
        ).all()

        invalidated_count = 0
        for signal in active_signals:
            if signal.current_score < invalidation_threshold:
                signal.status = "invalidated"
                signal.invalidation_reason = (
                    f"Score dropped to {signal.current_score:.2f} (below {invalidation_threshold})"
                )
                invalidated_count += 1
                logger.info(f"Signal invalidated: {signal.symbol} (score: {signal.current_score})")

        return invalidated_count
