"""
Dhan HQ API client for fetching historical candle data and instrument information.

API Reference: https://dhanhq.co/docs/v2/
"""

import logging
from datetime import date, datetime
from typing import Optional
import pyotp
import os

import httpx
import pandas as pd

from backend.config import get_dhan_credentials

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"

EXCHANGE_SEGMENTS = {
    "NSE_EQ": "NSE_EQ",
    "NSE_FNO": "NSE_FNO",
    "BSE_EQ": "BSE_EQ",
    "BSE_FNO": "BSE_FNO",
}

INSTRUMENT_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


class DhanClient:
    def __init__(self):
        creds = get_dhan_credentials()
        self.client_id = creds["client_id"]
        self.api_key = creds["api_key"]
        self.api_secret = creds["api_secret"]
        self.access_token = creds.get("access_token")  # Can be None initially
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def headers(self) -> dict:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers=self.headers,
                timeout=30.0,
            )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def get_historical_daily_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_date: date,
        to_date: date,
        expiry_code: int = 0,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch daily OHLCV candle data for a security.

        Returns a DataFrame with columns: timestamp, open, high, low, close, volume
        """
        client = await self._get_client()
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d"),
            "expiryCode": expiry_code,
        }

        try:
            response = await client.post("/charts/historical", json=payload)
            response.raise_for_status()
            data = response.json()

            if not data or "open" not in data:
                logger.warning(f"No data returned for {security_id}")
                return None

            df = pd.DataFrame({
                "timestamp": [datetime.fromtimestamp(ts) for ts in data["timestamp"]],
                "open": data["open"],
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data["volume"],
            })
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching data for {security_id}: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Error fetching data for {security_id}: {e}")
            return None

    async def get_intraday_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch intraday OHLCV candle data.
        interval: 1, 5, 15, 25, or 60 (minutes). Max 90 days per request.
        from_date/to_date format: "YYYY-MM-DD HH:MM:SS"
        """
        client = await self._get_client()
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": str(interval),
            "fromDate": from_date,
            "toDate": to_date,
        }

        try:
            response = await client.post("/charts/intraday", json=payload)
            response.raise_for_status()
            data = response.json()

            if not data or "open" not in data or not data["open"]:
                return None

            df = pd.DataFrame({
                "timestamp": [datetime.fromtimestamp(ts) for ts in data["timestamp"]],
                "open": data["open"],
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data["volume"],
            })
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching intraday for {security_id}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error fetching intraday for {security_id}: {e}")
            return None

    async def get_market_quote_ohlc(
        self,
        security_ids: list[str],
        exchange_segment: str,
    ) -> Optional[dict]:
        """Fetch current OHLC data for up to 1000 instruments."""
        client = await self._get_client()

        instruments = {exchange_segment: security_ids}
        try:
            response = await client.post(
                "/marketfeed/ohlc",
                json=instruments,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching market quotes: {e}")
            return None

    async def get_expiry_list(
        self,
        security_id: str,
        exchange_segment: str = "NSE_EQ",
    ) -> Optional[list[str]]:
        """Fetch all active expiry dates for an underlying instrument."""
        client = await self._get_client()
        payload = {
            "UnderlyingScrip": int(security_id),
            "UnderlyingSeg": exchange_segment,
        }
        try:
            response = await client.post("/optionchain/expirylist", json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("data", [])
        except Exception as e:
            logger.error(f"Error fetching expiry list for {security_id}: {e}")
            return None

    async def get_option_chain(
        self,
        security_id: str,
        exchange_segment: str = "NSE_EQ",
        expiry: str = "",
    ) -> Optional[dict]:
        """
        Fetch full option chain for an underlying at a specific expiry.
        Rate limit: 1 unique request per 3 seconds.
        """
        client = await self._get_client()
        payload = {
            "UnderlyingScrip": int(security_id),
            "UnderlyingSeg": exchange_segment,
            "Expiry": expiry,
        }
        try:
            response = await client.post("/optionchain", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching option chain for {security_id}: {e}")
            return None

    async def get_market_quote_ltp(
        self,
        security_ids: list[str],
        exchange_segment: str,
    ) -> Optional[dict]:
        """Fetch last traded price for up to 1000 instruments."""
        client = await self._get_client()

        int_ids = [int(sid) for sid in security_ids if sid.isdigit()]
        if not int_ids:
            return None
        instruments = {exchange_segment: int_ids}
        try:
            response = await client.post(
                "/marketfeed/ltp",
                json=instruments,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching LTP: {e}")
            return None


    # ── Order Management APIs ──

    async def place_order(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        product_type: str,
        order_type: str,
        quantity: int,
        price: float = 0,
        trigger_price: float = 0,
        validity: str = "DAY",
        correlation_id: str = "",
        drv_expiry_date: str = None,
        drv_option_type: str = None,
        drv_strike_price: float = None,
        after_market_order: bool = False,
    ) -> Optional[dict]:
        """Place a regular order. Returns {"orderId": ..., "orderStatus": ...}"""
        client = await self._get_client()
        payload = {
            "dhanClientId": self.client_id,
            "transactionType": transaction_type,
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": order_type,
            "validity": validity,
            "securityId": security_id,
            "quantity": quantity,
            "price": price,
            "triggerPrice": trigger_price,
            "afterMarketOrder": after_market_order,
        }
        if after_market_order:
            payload["amoTime"] = "OPEN"
        if correlation_id:
            payload["correlationId"] = correlation_id[:30]
        if drv_expiry_date:
            payload["drvExpiryDate"] = drv_expiry_date
        if drv_option_type:
            payload["drvOptionType"] = drv_option_type
        if drv_strike_price is not None:
            payload["drvStrikePrice"] = drv_strike_price

        try:
            response = await client.post("/orders", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Order placement failed: {e.response.status_code} - {e.response.text}")
            return {"error": True, "status_code": e.response.status_code, "detail": e.response.text}
        except Exception as e:
            logger.error(f"Order placement error: {e}")
            return {"error": True, "detail": str(e)}

    async def create_forever_order(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        product_type: str,
        order_type: str,
        order_flag: str,
        quantity: int,
        price: float,
        trigger_price: float,
        validity: str = "DAY",
        correlation_id: str = "",
        price1: float = 0,
        trigger_price1: float = 0,
        quantity1: int = 0,
    ) -> Optional[dict]:
        """Create a forever (GTT) order. order_flag: SINGLE or OCO."""
        client = await self._get_client()
        payload = {
            "dhanClientId": self.client_id,
            "orderFlag": order_flag,
            "transactionType": transaction_type,
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": order_type,
            "validity": validity,
            "securityId": security_id,
            "quantity": quantity,
            "price": price,
            "triggerPrice": trigger_price,
        }
        if correlation_id:
            payload["correlationId"] = correlation_id[:30]
        if order_flag == "OCO":
            payload["price1"] = price1
            payload["triggerPrice1"] = trigger_price1
            payload["quantity1"] = quantity1

        try:
            response = await client.post("/forever/orders", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Forever order failed: {e.response.status_code} - {e.response.text}")
            return {"error": True, "status_code": e.response.status_code, "detail": e.response.text}
        except Exception as e:
            logger.error(f"Forever order error: {e}")
            return {"error": True, "detail": str(e)}

    async def cancel_forever_order(self, order_id: str) -> Optional[dict]:
        """Cancel a forever (GTT) order."""
        client = await self._get_client()
        try:
            response = await client.delete(f"/forever/orders/{order_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Forever order cancel error: {e}")
            return {"error": True, "detail": str(e)}

    async def get_order_by_id(self, order_id: str) -> Optional[dict]:
        """Get order status by order ID."""
        client = await self._get_client()
        try:
            response = await client.get(f"/orders/{order_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Get order error: {e}")
            return None

    async def get_order_book(self) -> Optional[list]:
        """Get all orders for the day."""
        client = await self._get_client()
        try:
            response = await client.get("/orders")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Order book error: {e}")
            return None

    async def get_forever_orders(self) -> Optional[list]:
        """Get all active forever/GTT orders."""
        client = await self._get_client()
        try:
            response = await client.get("/forever/all")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Forever orders list error: {e}")
            return None

    async def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel a pending regular order."""
        client = await self._get_client()
        try:
            response = await client.delete(f"/orders/{order_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Cancel order error: {e}")
            return {"error": True, "detail": str(e)}

    async def renew_token(self) -> Optional[dict]:
        """Renew the access token using Dhan's RenewToken endpoint."""
        client = await self._get_client()
        try:
            response = await client.post(
                "/RenewToken",
                headers={
                    "access-token": self.access_token,
                    "dhanClientId": self.client_id,
                }
            )
            response.raise_for_status()
            result = response.json()
            
            if "accessToken" in result:
                new_token = result["accessToken"]
                logger.info("Successfully renewed Dhan access token")
                return {"success": True, "new_token": new_token, "expires_in": "24h"}
            else:
                logger.error(f"Token renewal failed: {result}")
                return {"success": False, "error": "No accessToken in response"}
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Token renewal HTTP error: {e.response.status_code} - {e.response.text}")
            return {"success": False, "error": f"HTTP {e.response.status_code}", "detail": e.response.text}
        except Exception as e:
            logger.error(f"Token renewal error: {e}")
            return {"success": False, "error": str(e)}

    async def get_profile(self) -> Optional[dict]:
        """Get user profile and token validity."""
        client = await self._get_client()
        try:
            response = await client.get("/profile")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Profile fetch error: {e}")
            return None

    async def generate_consent(self) -> Optional[dict]:
        """Step 1: Generate consent using API key and secret (OAuth flow)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"https://auth.dhan.co/app/generate-consent?client_id={self.client_id}",
                    headers={
                        "app_id": self.api_key,
                        "app_secret": self.api_secret,
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                if "consentAppId" in result:
                    consent_id = result["consentAppId"]
                    logger.info("Successfully generated consent for OAuth flow")
                    return {"success": True, "consent_id": consent_id}
                else:
                    logger.error(f"Consent generation failed: {result}")
                    return {"success": False, "error": "No consentAppId in response"}
                    
            except httpx.HTTPStatusError as e:
                logger.error(f"Consent generation HTTP error: {e.response.status_code} - {e.response.text}")
                return {"success": False, "error": f"HTTP {e.response.status_code}", "detail": e.response.text}
            except Exception as e:
                logger.error(f"Consent generation error: {e}")
                return {"success": False, "error": str(e)}

    async def generate_access_token_oauth(self, token_id: str) -> Optional[dict]:
        """Step 3: Generate access token using tokenId from OAuth flow."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"https://auth.dhan.co/app/consumeApp-consent?tokenId={token_id}",
                    headers={
                        "app_id": self.api_key,
                        "app_secret": self.api_secret,
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                if "accessToken" in result:
                    new_token = result["accessToken"]
                    logger.info("Successfully generated access token via OAuth flow")
                    return {"success": True, "new_token": new_token, "expires_in": "24h"}
                else:
                    logger.error(f"OAuth token generation failed: {result}")
                    return {"success": False, "error": "No accessToken in response"}
                    
            except httpx.HTTPStatusError as e:
                logger.error(f"OAuth token generation HTTP error: {e.response.status_code} - {e.response.text}")
                return {"success": False, "error": f"HTTP {e.response.status_code}", "detail": e.response.text}
            except Exception as e:
                logger.error(f"OAuth token generation error: {e}")
                return {"success": False, "error": str(e)}

    async def generate_access_token_with_totp(self, pin: str, totp_secret: str) -> Optional[dict]:
        """Generate access token using PIN and TOTP (requires TOTP enabled on account)."""
        try:
            # Generate TOTP code
            totp = pyotp.TOTP(totp_secret)
            totp_code = totp.now()
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"https://auth.dhan.co/app/generateAccessToken?dhanClientId={self.client_id}&pin={pin}&totp={totp_code}"
                )
                response.raise_for_status()
                result = response.json()
                
                if "accessToken" in result:
                    new_token = result["accessToken"]
                    logger.info("Successfully generated access token with TOTP")
                    return {"success": True, "new_token": new_token, "expires_in": "24h"}
                else:
                    logger.error(f"TOTP token generation failed: {result}")
                    return {"success": False, "error": "No accessToken in response"}
                    
        except Exception as e:
            logger.error(f"TOTP token generation error: {e}")
            return {"success": False, "error": str(e)}


async def download_scrip_master() -> pd.DataFrame:
    """
    Download the complete Dhan instrument master CSV.
    This is a public URL and doesn't require authentication.
    """
    logger.info("Downloading Dhan scrip master CSV...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(INSTRUMENT_SCRIP_MASTER_URL)
        response.raise_for_status()

    from io import StringIO
    df = pd.read_csv(StringIO(response.text))
    logger.info(f"Downloaded {len(df)} instruments from scrip master")
    return df
