"""
Auto-scan scheduler for F&O directional scanner.

Schedule:
  - Every 30 minutes during market hours (9:45, 10:15, ..., 3:15)
  - Once at 4:00 PM post-market
  - Weekdays only (Mon-Fri)
  - Skips NSE holidays
"""

import asyncio
import logging
from datetime import datetime, date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 17),   # Maha Shivaratri (tentative)
    date(2026, 3, 10),   # Ramadan / Eid (tentative)
    date(2026, 3, 30),   # Holi
    date(2026, 3, 31),   # Holi
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Ambedkar Jayanti
    date(2026, 5, 1),    # May Day
    date(2026, 5, 25),   # Buddha Purnima
    date(2026, 6, 29),   # Eid ul-Adha (tentative)
    date(2026, 7, 29),   # Muharram (tentative)
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 16),   # Janmashtami (tentative)
    date(2026, 9, 28),   # Milad-un-Nabi (tentative)
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 10, 21),  # Dussehra
    date(2026, 11, 9),   # Diwali (Laxmi Puja)
    date(2026, 11, 10),  # Diwali (Balipratipada)
    date(2026, 11, 30),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
}

_scheduler: AsyncIOScheduler | None = None
_last_scan_result: dict = {}
_last_scan_time: datetime | None = None


def is_market_day() -> bool:
    today = date.today()
    if today.weekday() >= 5:
        return False
    if today in NSE_HOLIDAYS_2026:
        return False
    return True


async def _refresh_dhan_token():
    """Generate fresh Dhan access token using API key and update environment."""
    logger.info("=== Token generation starting ===")
    
    try:
        from backend.services.dhan_client import DhanClient
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
                logger.warning("Token renewal failed, generating fresh token...")
        
        # Try TOTP-based token generation if credentials are available
        dhan_pin = os.environ.get("DHAN_PIN")
        totp_secret = os.environ.get("DHAN_TOTP_SECRET")
        
        if dhan_pin and totp_secret:
            logger.info("Attempting TOTP-based token generation...")
            result = await client.generate_access_token_with_totp(dhan_pin, totp_secret)
            
            if result.get("success") and result.get("new_token"):
                os.environ["DHAN_ACCESS_TOKEN"] = result["new_token"]
                logger.info("✅ Fresh token generated using TOTP")
                await client.close()
                return
        
        # Fallback: manual token generation required
        logger.warning("❌ Token renewal failed and no TOTP credentials available")
        logger.info("Please manually generate a new token from Dhan web interface:")
        logger.info("1. Go to https://web.dhan.co/")
        logger.info("2. My Profile → Access DhanHQ APIs → Generate Access Token")
        logger.info("3. Set the token as DHAN_ACCESS_TOKEN environment variable")
        logger.info("")
        logger.info("OR enable TOTP for fully automatic token generation:")
        logger.info("1. Setup TOTP in Dhan web interface")
        logger.info("2. Set DHAN_PIN and DHAN_TOTP_SECRET environment variables")
            
    except Exception as e:
        logger.error(f"Token generation error: {e}", exc_info=True)


async def _run_scheduled_fno_scan():
    """Execute the F&O scan (revalidate + discover) as a background job."""
    global _last_scan_result, _last_scan_time

    if not is_market_day():
        logger.info("Skipping scheduled scan — not a market day")
        return

    logger.info("=== Scheduled F&O scan starting ===")

    from backend.models.database import get_session_factory
    from backend.services.fno_scanner import FnoScanner
    from backend.services.sector_analyzer import SectorAnalyzer
    from backend.services import instrument_manager
    from backend.config import get_config

    session_factory = get_session_factory()
    db = session_factory()

    try:
        scanner = FnoScanner(db)
        reval = await scanner.revalidate_signals("fno")
        logger.info(f"Revalidation: {reval['revalidated']} checked, "
                     f"{reval['invalidated']} invalidated")

        analyzer = SectorAnalyzer(db)
        sector_results = await analyzer.analyze_sectors()

        if not sector_results:
            logger.warning("Scheduled scan: sector analysis returned no results")
            _last_scan_result = {"revalidation": reval, "error": "No sector data"}
            _last_scan_time = datetime.now()
            return

        fno_config = get_config().get("fno_scanner", {})
        top_n = fno_config.get("top_sectors", 4)

        top_sectors_ce = [s["sector_name"] for s in sector_results[:top_n]]
        top_sectors_pe = [s["sector_name"] for s in sector_results[-top_n:]]

        instruments = instrument_manager.get_fno_stocks(db)
        if not instruments:
            logger.warning("Scheduled scan: no instruments loaded")
            _last_scan_result = {"revalidation": reval, "error": "No instruments"}
            _last_scan_time = datetime.now()
            return

        scanner_fresh = FnoScanner(db)
        summary = await scanner_fresh.scan_fno(instruments, top_sectors_ce, top_sectors_pe)
        summary["revalidation"] = reval

        _last_scan_result = summary
        _last_scan_time = datetime.now()

        logger.info(f"=== Scheduled F&O scan complete === "
                     f"CE:{summary.get('ce_signals', 0)} PE:{summary.get('pe_signals', 0)} "
                     f"Invalidated:{reval.get('invalidated', 0)}")

    except Exception as e:
        logger.error(f"Scheduled scan failed: {e}", exc_info=True)
        _last_scan_result = {"error": str(e)}
        _last_scan_time = datetime.now()
    finally:
        db.close()


def start_scheduler():
    """Start the background scheduler with market-hours scan schedule."""
    global _scheduler

    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

    # Every 30 minutes during market hours: 9:45, 10:15, ..., 3:15
    _scheduler.add_job(
        _run_scheduled_fno_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-14",
            minute="15,45",
            timezone="Asia/Kolkata",
        ),
        id="fno_scan_market",
        name="F&O scan (market hours)",
        replace_existing=True,
    )

    # Additional runs at 15:15 (3:15 PM)
    _scheduler.add_job(
        _run_scheduled_fno_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=15,
            timezone="Asia/Kolkata",
        ),
        id="fno_scan_315",
        name="F&O scan (3:15 PM)",
        replace_existing=True,
    )

    # Post-market scan at 4:00 PM
    _scheduler.add_job(
        _run_scheduled_fno_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=0,
            timezone="Asia/Kolkata",
        ),
        id="fno_scan_postmarket",
        name="F&O scan (post-market 4 PM)",
        replace_existing=True,
    )

    # Token refresh every day at 6:00 AM (before market opens)
    _scheduler.add_job(
        _refresh_dhan_token,
        CronTrigger(
            day_of_week="mon-sun",  # 7 days a week
            hour=6,
            minute=0,
            timezone="Asia/Kolkata",
        ),
        id="token_refresh",
        name="Dhan token refresh (6 AM daily)",
        replace_existing=True,
    )

    _scheduler.start()

    jobs = _scheduler.get_jobs()
    next_runs = [f"{j.name}: next at {j.next_run_time}" for j in jobs]
    logger.info(f"Scheduler started with {len(jobs)} jobs: {next_runs}")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


def get_scheduler_status() -> dict:
    """Get current scheduler state for the UI."""
    if not _scheduler:
        return {"running": False, "jobs": [], "last_scan": None}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    return {
        "running": _scheduler.running,
        "jobs": jobs,
        "last_scan_time": str(_last_scan_time) if _last_scan_time else None,
        "last_scan_result": _last_scan_result,
        "is_market_day": is_market_day(),
    }
