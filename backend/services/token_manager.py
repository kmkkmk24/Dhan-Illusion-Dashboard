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
        
        # If no access token available, try to generate one
        if not client.access_token:
            logger.info("No access token found — attempting to generate one")
            await _renew_and_save_token()
            return
        
        profile = await client.get_profile()
        await client.close()

        if not profile:
            logger.warning("Could not fetch profile — token may be invalid, attempting renewal")
            await _renew_and_save_token()
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
        # If there's an error, try to generate a new token
        await _renew_and_save_token()


async def _renew_and_save_token():
    """Generate/renew token using TOTP or renewal and update environment."""
    try:
        import os
        client = DhanClient()
        
        # Try to renew existing token first (if available)
        if client.access_token:
            logger.info("Attempting to renew existing token...")
            result = await client.renew_token()
            
            if result.get("success") and result.get("new_token"):
                # Update environment variable for this session
                os.environ["DHAN_ACCESS_TOKEN"] = result["new_token"]
                logger.info("✅ Token renewed successfully")
                await client.close()
                return
            else:
                logger.warning("Token renewal failed, trying TOTP generation...")
        
        # Try UI credentials first
        from backend.services.settings_manager import settings_manager
        
        logger.info("Checking UI credentials...")
        ui_credentials = settings_manager.load_credentials()
        
        if ui_credentials:
            logger.info("Found UI credentials, attempting TOTP generation...")
            result = await client.generate_access_token_with_totp(
                ui_credentials["pin"], 
                ui_credentials["totp_secret"]
            )
            
            if result.get("success") and result.get("new_token"):
                os.environ["DHAN_ACCESS_TOKEN"] = result["new_token"]
                logger.info("✅ Fresh token generated using UI credentials")
                await client.close()
                return
            else:
                logger.warning("UI credentials failed, trying environment variables...")
        
        # Try environment variables as fallback
        dhan_pin = os.environ.get("DHAN_PIN")
        totp_secret = os.environ.get("DHAN_TOTP_SECRET")
        
        if dhan_pin and totp_secret:
            logger.info("Attempting TOTP generation with environment variables...")
            result = await client.generate_access_token_with_totp(dhan_pin, totp_secret)
            
            if result.get("success") and result.get("new_token"):
                os.environ["DHAN_ACCESS_TOKEN"] = result["new_token"]
                logger.info("✅ Fresh token generated using environment variables")
                await client.close()
                return
        
        # Fallback: manual token generation required
        logger.warning("❌ Token generation failed - no credentials available")
        logger.info("Please either:")
        logger.info("1. Configure credentials in Settings UI (recommended), OR")
        logger.info("2. Generate token manually from https://web.dhan.co/")
        
        await client.close()

    except Exception as e:
        logger.error(f"Token generation error: {e}", exc_info=True)


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
