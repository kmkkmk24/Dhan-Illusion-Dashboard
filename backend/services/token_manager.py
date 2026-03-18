"""
Auto-renewal service for Dhan access tokens.
Tokens expire every 24 hours — this service auto-renews them.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.services.dhan_client import DhanClient

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def check_and_renew_token():
    """Check token expiry and renew if needed."""
    try:
        client = DhanClient()
        profile = await client.get_profile()
        await client.close()

        if not profile:
            logger.warning("Could not fetch profile — token may be invalid")
            return

        token_validity = profile.get("tokenValidity", "")
        logger.info(f"Token valid until: {token_validity}")

        # Check if token expires soon (within 2 hours)
        # tokenValidity format: "05/03/2026 15:37"
        try:
            expiry = datetime.strptime(token_validity, "%d/%m/%Y %H:%M")
            now = datetime.now()
            hours_left = (expiry - now).total_seconds() / 3600

            if hours_left < 2:
                logger.info(f"Token expires in {hours_left:.1f}h — renewing now")
                await _renew_and_save_token()
            else:
                logger.info(f"Token valid for {hours_left:.1f}h — no renewal needed")
        except ValueError:
            logger.warning(f"Could not parse token validity: {token_validity}")

    except Exception as e:
        logger.error(f"Token check error: {e}")


async def _renew_and_save_token():
    """Renew token and update config.yaml."""
    try:
        client = DhanClient()
        result = await client.renew_token()
        await client.close()

        if not result or not result.get("accessToken"):
            logger.error("Token renewal failed — no new token returned")
            return

        new_token = result["accessToken"]
        expiry = result.get("expiryTime", "")
        logger.info(f"New token obtained, expires: {expiry}")

        # Update config.yaml
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        if not config_path.exists():
            logger.error(f"Config file not found: {config_path}")
            return

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        config["dhan"]["access_token"] = new_token

        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        logger.info("✅ Token renewed and saved to config.yaml")

    except Exception as e:
        logger.error(f"Token renewal error: {e}")


def start_token_manager():
    """Start the token renewal scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        logger.info("Token manager already running")
        return

    _scheduler = AsyncIOScheduler()

    # Check token on startup
    _scheduler.add_job(
        check_and_renew_token,
        "date",
        run_date=datetime.now(),
        id="token_startup_check",
    )

    # Daily renewal at 6:00 AM (before market opens at 9:15 AM)
    _scheduler.add_job(
        check_and_renew_token,
        "cron",
        hour=6,
        minute=0,
        id="token_daily_renewal",
    )

    _scheduler.start()
    logger.info("Token manager started — daily renewal at 6:00 AM")


def stop_token_manager():
    """Stop the token renewal scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Token manager stopped")
