import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from Fundamentals.TickerTape import Tickertape  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Tickertape = None


class TickertapeClient:
    def __init__(self):
        if Tickertape is None:
            raise RuntimeError("Tickertape client unavailable. Install Bharat-sm-data.")
        self.api = Tickertape()

    def get_scorecard(self, symbol: str) -> Optional[list[dict[str, Any]]]:
        try:
            ticker, _raw = self.api.get_ticker(symbol, search_place="stock")
        except Exception as e:
            logger.warning("Tickertape search failed for %s: %s", symbol, e)
            return None
        if not ticker:
            return None
        try:
            df = self.api.get_score_card(ticker)
            return df.to_dict(orient="records")
        except Exception as e:
            logger.warning("Tickertape scorecard failed for %s: %s", symbol, e)
            return None
