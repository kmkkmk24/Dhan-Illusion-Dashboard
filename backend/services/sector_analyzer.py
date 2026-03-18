"""
Sector Momentum Analyzer

Identifies sectors with strong momentum using:
  1. Multi-timeframe Relative Strength vs Nifty 50 (Daily, Weekly, Monthly)
  2. Price Action (higher highs, above key EMAs)

Combined score ranks sectors for prioritization.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import SectorScore, Signal
from backend.services.dhan_client import DhanClient
from backend.services.sector_mapping import get_stocks_in_sector, STOCK_TO_SECTORS

logger = logging.getLogger(__name__)

# Nifty sectoral index security IDs on Dhan (from api-scrip-master-detailed.csv)
SECTOR_INDICES = {
    "Nifty Auto": "14",
    "Nifty Bank": "25",
    "Nifty Commodities": "39",
    "Nifty Consumer Durables": "466",
    "Nifty Energy": "42",
    "Nifty Fin Services": "27",
    "Nifty FMCG": "28",
    "Nifty Healthcare": "447",
    "Nifty Infra": "43",
    "Nifty IT": "29",
    "Nifty Media": "30",
    "Nifty Metal": "31",
    "Nifty Oil & Gas": "470",
    "Nifty PSU Bank": "33",
    "Nifty Pharma": "32",
    "Nifty Private Bank": "15",
    "Nifty Realty": "34",
}

NIFTY_50_SECURITY_ID = "13"

# Multi-timeframe period groupings
TIMEFRAME_PERIODS = {
    "daily": [1, 3, 5],
    "weekly": [5, 10, 20],
    "monthly": [20, 30, 50],
}


class SectorAnalyzer:
    def __init__(self, db: Session):
        self.db = db
        self.config = get_config()["sector"]
        self.dhan = DhanClient()

    async def analyze_sectors(self) -> list[dict]:
        """
        Analyze all sector indices with multi-timeframe scoring.

        Returns a list of sector scores sorted by combined score (descending).
        """
        today = date.today()
        from_date = today - timedelta(days=180)

        nifty_df = await self._fetch_index_data(NIFTY_50_SECURITY_ID, from_date, today)
        if nifty_df is None:
            logger.error("Could not fetch Nifty 50 data for sector analysis")
            return []

        results = []

        for i, (sector_name, security_id) in enumerate(SECTOR_INDICES.items()):
            try:
                if i > 0:
                    await asyncio.sleep(0.35)
                sector_df = await self._fetch_index_data(security_id, from_date, today)
                if sector_df is None or len(sector_df) < 50:
                    continue

                # Multi-timeframe relative strength
                daily_rs = self._compute_rs_for_periods(sector_df, nifty_df, TIMEFRAME_PERIODS["daily"])
                weekly_rs = self._compute_rs_for_periods(sector_df, nifty_df, TIMEFRAME_PERIODS["weekly"])
                monthly_rs = self._compute_rs_for_periods(sector_df, nifty_df, TIMEFRAME_PERIODS["monthly"])

                pa_score, pa_details = self._compute_price_action(sector_df)

                # Overall RS is weighted average of all timeframes
                overall_rs = 0.3 * daily_rs + 0.4 * weekly_rs + 0.3 * monthly_rs
                combined = 0.5 * overall_rs + 0.5 * pa_score

                # Trend classification
                trend = self._classify_trend(daily_rs, weekly_rs, monthly_rs, pa_details)

                # Stock count in this sector
                stocks = get_stocks_in_sector(sector_name)
                active_signals = self._count_active_signals(stocks)

                sector_result = {
                    "sector_name": sector_name,
                    "daily_rs": round(daily_rs, 4),
                    "weekly_rs": round(weekly_rs, 4),
                    "monthly_rs": round(monthly_rs, 4),
                    "relative_strength": round(overall_rs, 4),
                    "price_action_score": round(pa_score, 4),
                    "combined_score": round(combined, 4),
                    "is_above_20ema": pa_details["above_20ema"],
                    "is_above_50ema": pa_details["above_50ema"],
                    "is_making_higher_highs": pa_details["higher_highs"],
                    "is_making_higher_lows": pa_details["higher_lows"],
                    "is_ema_aligned": pa_details["ema_aligned"],
                    "trend": trend,
                    "stock_count": len(stocks),
                    "active_signals": active_signals,
                }
                results.append(sector_result)

                self._save_sector_score(sector_result, today)

            except Exception as e:
                logger.error(f"Error analyzing sector {sector_name}: {e}")

        self.db.commit()
        await self.dhan.close()

        results.sort(key=lambda x: x["combined_score"], reverse=True)

        top_n = self.config["top_sectors"]
        logger.info(
            f"Sector analysis complete. Top {top_n}: "
            + ", ".join(f"{r['sector_name']} ({r['combined_score']:.2f})" for r in results[:top_n])
        )

        return results

    async def _fetch_index_data(
        self, security_id: str, from_date: date, to_date: date
    ) -> Optional[pd.DataFrame]:
        """Fetch daily OHLCV data for an index."""
        return await self.dhan.get_historical_daily_data(
            security_id=security_id,
            exchange_segment="IDX_I",
            instrument="INDEX",
            from_date=from_date,
            to_date=to_date,
        )

    def _compute_rs_for_periods(
        self, sector_df: pd.DataFrame, nifty_df: pd.DataFrame, periods: list[int]
    ) -> float:
        """Compute average relative strength across a set of lookback periods."""
        sector_close = sector_df["close"].values
        nifty_close = nifty_df["close"].values

        min_len = min(len(sector_close), len(nifty_close))
        sector_close = sector_close[-min_len:]
        nifty_close = nifty_close[-min_len:]

        scores = []
        for period in periods:
            if min_len < period + 1:
                continue

            sector_return = (sector_close[-1] - sector_close[-period - 1]) / sector_close[-period - 1]
            nifty_return = (nifty_close[-1] - nifty_close[-period - 1]) / nifty_close[-period - 1]

            excess = sector_return - nifty_return
            normalized = (excess + 0.05) / 0.10
            scores.append(min(1.0, max(0.0, normalized)))

        if not scores:
            return 0.0

        weights = list(range(1, len(scores) + 1))
        return sum(s * w for s, w in zip(scores, weights)) / sum(weights)

    def _compute_price_action(self, df: pd.DataFrame) -> tuple[float, dict]:
        """
        Evaluate sector price action quality.

        Checks above 20/50 EMA, higher highs/lows, EMA alignment.
        Returns (score, details_dict).
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        ema_20 = pd.Series(close).ewm(span=20, adjust=False).mean().values
        ema_50 = pd.Series(close).ewm(span=50, adjust=False).mean().values

        above_20ema = bool(close[-1] > ema_20[-1])
        above_50ema = bool(close[-1] > ema_50[-1])

        if len(high) >= 40:
            higher_highs = bool(np.max(high[-20:]) > np.max(high[-40:-20]))
            higher_lows = bool(np.min(low[-20:]) > np.min(low[-40:-20]))
        else:
            higher_highs = False
            higher_lows = False

        ema_aligned = bool(ema_20[-1] > ema_50[-1])

        checks = [above_20ema, above_50ema, higher_highs, higher_lows, ema_aligned]
        score = sum(checks) / len(checks)

        details = {
            "above_20ema": above_20ema,
            "above_50ema": above_50ema,
            "higher_highs": higher_highs,
            "higher_lows": higher_lows,
            "ema_aligned": ema_aligned,
        }
        return score, details

    def _classify_trend(
        self, daily_rs: float, weekly_rs: float, monthly_rs: float, pa_details: dict
    ) -> str:
        """Classify sector trend as Strong / Moderate / Weak / Bearish."""
        avg_rs = (daily_rs + weekly_rs + monthly_rs) / 3
        bullish_checks = sum([
            pa_details["above_20ema"],
            pa_details["above_50ema"],
            pa_details["ema_aligned"],
            pa_details["higher_highs"],
        ])

        if avg_rs >= 0.65 and bullish_checks >= 3:
            return "Strong"
        elif avg_rs >= 0.45 and bullish_checks >= 2:
            return "Moderate"
        elif avg_rs >= 0.3:
            return "Weak"
        else:
            return "Bearish"

    def _count_active_signals(self, stock_symbols: list[str]) -> int:
        """Count how many stocks in the list have active VCP signals."""
        if not stock_symbols:
            return 0
        return self.db.query(Signal).filter(
            Signal.symbol.in_(stock_symbols),
            Signal.status == "active",
        ).count()

    def _save_sector_score(self, result: dict, today: date) -> None:
        """Save or update a sector score record for today."""
        existing = self.db.query(SectorScore).filter(
            SectorScore.sector_name == result["sector_name"],
            SectorScore.date == today,
        ).first()

        fields = {
            "relative_strength": result["relative_strength"],
            "daily_rs": result["daily_rs"],
            "weekly_rs": result["weekly_rs"],
            "monthly_rs": result["monthly_rs"],
            "price_action_score": result["price_action_score"],
            "combined_score": result["combined_score"],
            "is_above_20ema": result["is_above_20ema"],
            "is_above_50ema": result["is_above_50ema"],
            "is_making_higher_highs": result["is_making_higher_highs"],
            "is_making_higher_lows": result.get("is_making_higher_lows", False),
            "is_ema_aligned": result.get("is_ema_aligned", False),
        }

        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
        else:
            score = SectorScore(
                sector_name=result["sector_name"],
                date=today,
                **fields,
            )
            self.db.add(score)


def get_top_sectors(db: Session, top_n: int = 5) -> list[SectorScore]:
    """Get the latest top N sectors by combined score."""
    latest_date = db.query(SectorScore.date).order_by(SectorScore.date.desc()).first()
    if not latest_date:
        return []

    return (
        db.query(SectorScore)
        .filter(SectorScore.date == latest_date[0])
        .order_by(SectorScore.combined_score.desc())
        .limit(top_n)
        .all()
    )
