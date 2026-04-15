import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from Fundamentals.MoneyControl import MoneyControl  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    MoneyControl = None


class MoneycontrolClient:
    def __init__(self):
        if MoneyControl is None:
            raise RuntimeError("Moneycontrol client unavailable. Install Bharat-sm-data.")
        self.api = MoneyControl()

    def get_overview(self, symbol: str) -> Optional[list[dict[str, Any]]]:
        ticker = self._resolve_ticker(symbol)
        if not ticker:
            return None
        try:
            df = self.api.get_overview_mini_statement(ticker)
            return df.to_dict(orient="records")
        except Exception as e:
            logger.warning("Moneycontrol overview failed for %s: %s", symbol, e)
            return None

    def get_ratios(self, symbol: str) -> Optional[list[dict[str, Any]]]:
        ticker = self._resolve_ticker(symbol)
        if not ticker:
            return None
        try:
            df = self.api.get_ratios_mini_statement(ticker)
            return df.to_dict(orient="records")
        except Exception as e:
            logger.warning("Moneycontrol ratios failed for %s: %s", symbol, e)
            return None

    def _resolve_ticker(self, symbol: str) -> Optional[str]:
        try:
            ticker, _url = self.api.get_ticker(symbol)
        except Exception as e:
            logger.warning("Moneycontrol search failed for %s: %s", symbol, e)
            return None
        return ticker
