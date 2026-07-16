import logging
from pathlib import Path

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.schemas import BackupInfo, BackupConfigUpdate
from backend.services import instrument_manager, backup_manager
from backend.services.settings_manager import settings_manager
from backend.services.scanner_engine import ScannerEngine
from backend.services.sector_analyzer import SectorAnalyzer, get_top_sectors
from backend.services.sector_mapping import (
    get_sector_instruments,
    update_instrument_sectors,
    get_all_sector_names,
    get_stocks_in_sector,
)
from backend.models.tables import Signal, Instrument

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger(__name__)


@router.post("/instruments/refresh")
async def refresh_instruments(db: Session = Depends(get_db)):
    """Download latest instrument master from Dhan and refresh the database."""
    summary = await instrument_manager.refresh_instruments(db)
    sectors_updated = update_instrument_sectors(db)
    return {"message": "Instruments refreshed", "sectors_mapped": sectors_updated, **summary}


@router.get("/instruments/search")
def search_instruments(q: str, db: Session = Depends(get_db)):
    """Search instruments by symbol or name."""
    results = instrument_manager.search_instruments(db, q)
    return [
        {
            "security_id": r.security_id,
            "symbol": r.symbol,
            "display_name": r.display_name,
            "is_fno": r.is_fno,
            "lot_size": r.lot_size,
        }
        for r in results
    ]


@router.get("/instruments/fno")
def list_fno_stocks(db: Session = Depends(get_db)):
    """List all F&O eligible stocks."""
    stocks = instrument_manager.get_fno_stocks(db)
    return [
        {
            "security_id": s.security_id,
            "symbol": s.symbol,
            "display_name": s.display_name,
            "lot_size": s.lot_size,
        }
        for s in stocks
    ]


@router.post("/scan/run")
async def run_scan(section: str = "swing", setup: str | None = None, db: Session = Depends(get_db)):
    """
    Manually trigger a scan.

    For 'swing': First runs sector analysis, then scans only stocks in top momentum sectors.
    For 'fno': Sector-first directional scan using 75m candles for CE/PE setups.
    """
    try:
        if section == "fno":
            from backend.services.fno_scanner import FnoScanner
            from backend.services.advdecl_intraday_scanner import AdvDeclIntradayScanner

            mode = (setup or "all").lower()
            if mode not in ("all", "core", "advdecl"):
                mode = "all"

            summary = {}

            # Step 1: Get F&O instruments (needed for all modes)
            instruments = instrument_manager.get_fno_stocks(db)
            if not instruments:
                return {
                    "message": "No instruments loaded. Run 'Refresh Instruments' first.",
                    "summary": {},
                }

            if mode in ("core", "all"):
                scanner = FnoScanner(db)

                # Revalidate existing active/tracked signals with fresh data
                reval = await scanner.revalidate_signals("fno")

                # Run sector analysis to get top sectors
                analyzer = SectorAnalyzer(db)
                sector_results = await analyzer.analyze_sectors()

                if not sector_results:
                    if mode == "core":
                        return {
                            "message": "Sector analysis returned no results. Check API connection.",
                            "summary": {"revalidation": reval},
                        }
                    summary["revalidation"] = reval
                else:
                    from backend.config import get_config

                    fno_config = get_config().get("fno_scanner", {})
                    top_n = fno_config.get("top_sectors", 4)

                    top_sectors_ce = [s["sector_name"] for s in sector_results[:top_n]]
                    bottom = sector_results[-top_n:]
                    top_sectors_pe = [s["sector_name"] for s in bottom]

                    # Discover new setups (reuses the same scanner/dhan client)
                    scanner_fresh = FnoScanner(db)
                    core_summary = await scanner_fresh.scan_fno(instruments, top_sectors_ce, top_sectors_pe)
                    core_summary["revalidation"] = reval
                    summary.update(core_summary)

            if mode in ("advdecl", "all"):
                adv_scanner = AdvDeclIntradayScanner(db)
                adv_summary = await adv_scanner.scan(instruments)
                summary["advdecl_intraday"] = adv_summary

            if mode == "advdecl":
                return {"message": "Adv/Decl intraday scan complete.", "summary": summary}
            if mode == "core":
                return {"message": "Core F&O scan complete.", "summary": summary}
            return {
                "message": "F&O scan complete.",
                "summary": summary,
            }

        # Swing: Enhanced VCP scanner with sector-first approach
        analyzer = SectorAnalyzer(db)
        sector_results = await analyzer.analyze_sectors()

        if not sector_results:
            return {"message": "Sector analysis returned no results. Check API connection.", "summary": {}}

        from backend.config import PROJECT_ROOT, get_config
        top_n = get_config()["sector"]["top_sectors"]
        top_sectors = [s["sector_name"] for s in sector_results[:top_n]]

        # Cash equities only (ES); other segment-E rows break /charts/historical NSE_EQ.
        all_instruments = instrument_manager.get_nse_cash_equity_shares(db)
        if not all_instruments:
            return {
                "message": "No instruments loaded. Run 'Refresh Instruments' first.",
                "summary": {},
            }

        sucfg = get_config().get("swing_universe") or {}
        rebuild_info: dict | None = None
        if sucfg.get("auto_rebuild_before_scan", True):
            from backend.services.swing_universe_builder import rebuild_swing_universe_csv

            try:
                rebuild_info = await rebuild_swing_universe_csv(db)
                if not rebuild_info.get("ok"):
                    logger.warning(
                        "Swing universe auto-rebuild skipped or failed: %s",
                        rebuild_info.get("error") or rebuild_info,
                    )
            except Exception as e:
                logger.exception("Swing universe auto-rebuild error: %s", e)
                rebuild_info = {"ok": False, "error": str(e)}

        vcfg = get_config().get("vcp_scanner", {})
        csv_rel = (vcfg.get("swing_universe_csv") or "").strip()
        resolved_csv: str | None = None
        universe_mode = "full"
        if csv_rel:
            from backend.services.swing_universe_loader import (
                load_swing_universe_security_ids,
            )

            csv_path = Path(csv_rel)
            if not csv_path.is_absolute():
                csv_path = PROJECT_ROOT / csv_path
            if not csv_path.is_file():
                logger.warning(
                    "swing_universe_csv=%s not found; scanning full ES universe with "
                    "smart filter (enable swing_universe.auto_rebuild_before_scan or "
                    "place swing_universe_latest.csv under data/).",
                    csv_path,
                )
            else:
                ordered_ids = load_swing_universe_security_ids(csv_path)
                if not ordered_ids:
                    logger.warning(
                        "swing_universe_csv=%s has no ids; scanning full universe.",
                        csv_path,
                    )
                else:
                    want = set(ordered_ids)
                    pos = {sid: i for i, sid in enumerate(ordered_ids)}
                    matched = [
                        i for i in all_instruments if str(i.security_id) in want
                    ]
                    matched.sort(
                        key=lambda x: pos.get(str(x.security_id), 10**9)
                    )
                    if not matched:
                        return {
                            "message": (
                                f"No DB instruments matched ids in {csv_path.name} "
                                "(refresh instruments or check security_id column)."
                            ),
                            "summary": {},
                        }
                    all_instruments = matched
                    resolved_csv = str(csv_path)
                    universe_mode = "csv"
                    logger.info(
                        "Swing scan: restricted to %s instruments from %s",
                        len(all_instruments),
                        csv_path.name,
                    )

        # Use original VCP scanner (same as before)
        from backend.services.vcp_scanner import VCPScanner
        vcp_scanner = VCPScanner(db)
        summary = await vcp_scanner.scan_vcp_patterns(
            all_instruments, top_sectors, universe_mode=universe_mode
        )
        summary["top_sectors"] = top_sectors
        summary["total_instruments"] = len(all_instruments)
        if rebuild_info is not None:
            summary["swing_universe_rebuild"] = rebuild_info
        if resolved_csv:
            summary["swing_universe_csv"] = resolved_csv
            summary["universe_source"] = "swing_universe_csv"

        prefix = ""
        if resolved_csv:
            prefix = f"CSV universe ({summary['total_instruments']} stocks): "
        msg = (
            f"{prefix}VCP scan complete: {summary['vcp_signals']} signals found from "
            f"{summary['scanned']} stocks"
        )
        if summary.get("chartink_diagnostics"):
            na = summary.get("chartink_stocks_analyzed", 0)
            worst = sorted(
                summary["chartink_diagnostics"].items(),
                key=lambda x: x[1]["fail"] - x[1]["pass"],
                reverse=True,
            )[:5]
            hint = ", ".join(
                f"{k}={v['pass']}/{v['pass'] + v['fail']} pass" for k, v in worst
            )
            msg += f". ChartInk checks (top bottlenecks, n={na}): {hint}"

        sd = summary.get("scan_debug") or {}
        if sd.get("skip_reason_counts"):
            msg += f". Debug skips: {sd['skip_reason_counts']}"
        if sd.get("chartink_full_analysis_count") == 0 and "chartink" in str(
            get_config().get("vcp_scanner", {}).get("swing_scan_mode", "")
        ):
            msg += " (see server log: no stock reached full ChartInk checks)"

        return {
            "message": msg,
            "summary": summary,
        }
    
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Scan failed with error: {e}")
        logger.error(f"Full traceback: {error_details}")
        
        return {
            "error": True,
            "message": f"Scan failed: {str(e)}",
            "details": error_details,
            "section": section
        }


@router.post("/scan/run/custom")
async def run_custom_scan(request_data: dict, db: Session = Depends(get_db)):
    """
    Run a custom F&O scan with user-selected sectors.
    
    Expected request_data:
    {
        "section": "fno",
        "ce_sectors": ["Nifty IT", "Nifty Pharma"],
        "pe_sectors": ["Nifty Media", "Nifty Realty"]
    }
    """
    try:
        section = request_data.get("section", "fno")
        ce_sectors = request_data.get("ce_sectors", [])
        pe_sectors = request_data.get("pe_sectors", [])
        
        if section != "fno":
            return {"error": True, "message": "Custom scan currently only supports F&O section"}
        
        if not ce_sectors and not pe_sectors:
            return {"error": True, "message": "Please provide at least one sector for CE or PE"}
        
        from backend.services.fno_scanner import FnoScanner
        
        scanner = FnoScanner(db)
        
        # Step 1: Revalidate existing active/tracked signals with fresh data
        reval = await scanner.revalidate_signals("fno")
        
        # Step 2: Get F&O instruments
        instruments = instrument_manager.get_fno_stocks(db)
        if not instruments:
            return {
                "message": "No instruments loaded. Run 'Refresh Instruments' first.",
                "summary": {"revalidation": reval},
            }
        
        # Step 3: Run custom sector scan
        scanner_fresh = FnoScanner(db)
        summary = await scanner_fresh.scan_fno(instruments, ce_sectors, pe_sectors)
        summary["revalidation"] = reval
        
        return {
            "message": f"Custom F&O scan complete: CE sectors={ce_sectors}, PE sectors={pe_sectors}",
            "summary": summary,
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Custom scan failed with error: {e}")
        logger.error(f"Full traceback: {error_details}")
        
        return {
            "error": True,
            "message": f"Custom scan failed: {str(e)}",
            "details": error_details,
        }


# In-memory cache for the latest sector analysis results (includes multi-timeframe data)
_sector_analysis_cache: list[dict] = []


@router.get("/sectors/{sector_name}/chart")
async def get_sector_chart_data(sector_name: str):
    """Fetch weekly OHLCV data for a sector index with EMAs."""
    import pandas as pd
    from backend.services.sector_analyzer import SECTOR_INDICES
    from backend.services.dhan_client import DhanClient
    from datetime import date, timedelta

    security_id = SECTOR_INDICES.get(sector_name)
    if not security_id:
        return {"candles": [], "ema20": [], "ema50": []}

    client = DhanClient()
    to_date = date.today()
    from_date = to_date - timedelta(days=900)  # ~2.5 years of daily data

    try:
        df = await client.get_historical_daily_data(
            security_id=security_id,
            exchange_segment="IDX_I",
            instrument="INDEX",
            from_date=from_date,
            to_date=to_date,
        )
        await client.close()

        if df is None or df.empty:
            return {"candles": [], "ema20": [], "ema50": []}

        # Aggregate daily -> weekly
        df["week"] = df["timestamp"].dt.to_period("W").apply(lambda p: p.start_time)
        weekly = df.groupby("week").agg(
            o=("open", "first"),
            h=("high", "max"),
            l=("low", "min"),
            c=("close", "last"),
            v=("volume", "sum"),
        ).reset_index()
        weekly = weekly.sort_values("week")

        # Compute EMAs on weekly close
        close_series = pd.Series(weekly["c"].values, dtype=float)
        ema20 = close_series.ewm(span=20, adjust=False).mean().values
        ema50 = close_series.ewm(span=50, adjust=False).mean().values

        candles = []
        ema20_out = []
        ema50_out = []
        for i, (_, row) in enumerate(weekly.iterrows()):
            t = row["week"].strftime("%Y-%m-%d")
            candles.append({
                "t": t,
                "o": round(float(row["o"]), 2),
                "h": round(float(row["h"]), 2),
                "l": round(float(row["l"]), 2),
                "c": round(float(row["c"]), 2),
                "v": int(row["v"]),
            })
            ema20_out.append(round(float(ema20[i]), 2))
            ema50_out.append(round(float(ema50[i]), 2))

        return {"candles": candles, "ema20": ema20_out, "ema50": ema50_out}
    except Exception as e:
        await client.close()
        return {"candles": [], "ema20": [], "ema50": [], "error": str(e)}


@router.post("/scan/sectors")
async def run_sector_analysis(db: Session = Depends(get_db)):
    """Manually trigger sector momentum analysis with multi-timeframe RS."""
    global _sector_analysis_cache
    analyzer = SectorAnalyzer(db)
    results = await analyzer.analyze_sectors()
    _sector_analysis_cache = results
    return {"message": f"Sector analysis complete. {len(results)} sectors analyzed.", "sectors": results}


@router.get("/sectors/top")
def get_top_sector_scores(db: Session = Depends(get_db)):
    """Get the latest top-ranked sectors."""
    if _sector_analysis_cache:
        from backend.config import get_config
        top_n = get_config()["sector"]["top_sectors"]
        return _sector_analysis_cache[:top_n]

    sectors = get_top_sectors(db)
    return [
        {
            "sector_name": s.sector_name,
            "combined_score": s.combined_score,
            "relative_strength": s.relative_strength,
            "price_action_score": s.price_action_score,
            "is_above_20ema": s.is_above_20ema,
            "is_above_50ema": s.is_above_50ema,
            "is_making_higher_highs": s.is_making_higher_highs,
            "date": str(s.date),
        }
        for s in sectors
    ]


@router.get("/sectors/all")
async def get_all_sectors(db: Session = Depends(get_db)):
    """Get all sector scores for the Sector Analysis tab (uses cached multi-timeframe data)."""
    if _sector_analysis_cache:
        return _sector_analysis_cache

    # Fallback to DB data if no cached results
    from backend.models.tables import SectorScore
    latest_date = db.query(SectorScore.date).order_by(SectorScore.date.desc()).first()
    if not latest_date:
        return []

    sectors = (
        db.query(SectorScore)
        .filter(SectorScore.date == latest_date[0])
        .order_by(SectorScore.combined_score.desc())
        .all()
    )
    results = []
    for s in sectors:
        stocks = get_stocks_in_sector(s.sector_name)
        active_signals = db.query(Signal).filter(
            Signal.symbol.in_(stocks), Signal.status == "active"
        ).count() if stocks else 0

        results.append({
            "sector_name": s.sector_name,
            "combined_score": s.combined_score,
            "relative_strength": s.relative_strength,
            "price_action_score": s.price_action_score,
            "daily_rs": s.daily_rs if s.daily_rs is not None else s.relative_strength,
            "weekly_rs": s.weekly_rs if s.weekly_rs is not None else s.relative_strength,
            "monthly_rs": s.monthly_rs if s.monthly_rs is not None else s.relative_strength,
            "is_above_20ema": s.is_above_20ema,
            "is_above_50ema": s.is_above_50ema,
            "is_making_higher_highs": s.is_making_higher_highs,
            "is_making_higher_lows": getattr(s, 'is_making_higher_lows', None),
            "is_ema_aligned": getattr(s, 'is_ema_aligned', None),
            "trend": "—",
            "date": str(s.date),
            "stock_count": len(stocks),
            "active_signals": active_signals,
        })
    return results


@router.get("/sectors/{sector_name}/stocks")
def get_sector_stocks(sector_name: str, db: Session = Depends(get_db)):
    """Get all stocks in a sector with their signal status."""
    symbols = get_stocks_in_sector(sector_name)
    if not symbols:
        return []

    # Search in both trading_symbol and symbol fields to handle different symbol formats
    from sqlalchemy import or_
    instruments = db.query(Instrument).filter(
        or_(
            Instrument.trading_symbol.in_(symbols),
            Instrument.symbol.in_(symbols)
        ),
        Instrument.exchange == "NSE",
    ).all()

    trading_syms = [inst.trading_symbol for inst in instruments if inst.trading_symbol]
    active_signals = db.query(Signal).filter(
        Signal.symbol.in_(trading_syms),
        Signal.status.in_(["active", "exploded"]),
    ).all() if trading_syms else []
    signal_map = {s.symbol: s for s in active_signals}

    results = []
    for inst in instruments:
        tsym = inst.trading_symbol or inst.symbol
        sig = signal_map.get(tsym)
        results.append({
            "symbol": tsym,
            "display_name": inst.display_name,
            "security_id": inst.security_id,
            "is_fno": inst.is_fno,
            "lot_size": inst.lot_size,
            "has_signal": sig is not None,
            "signal_status": sig.status if sig else None,
            "signal_score": sig.current_score if sig else None,
            "signal_type": sig.signal_type if sig else None,
            "occurrence_number": sig.occurrence_number if sig else None,
        })

    results.sort(key=lambda x: (not x["has_signal"], -(x["signal_score"] or 0)))
    return results


@router.post("/reset/signals")
def reset_all_signals(db: Session = Depends(get_db)):
    """Delete all signals and signal history for a fresh start."""
    from backend.models.tables import SignalHistory
    deleted_history = db.query(SignalHistory).delete()
    deleted_signals = db.query(Signal).delete()
    db.commit()
    return {
        "message": "All signals reset",
        "deleted_signals": deleted_signals,
        "deleted_history": deleted_history,
    }


@router.get("/scheduler/status")
def scheduler_status():
    """Get auto-scan scheduler status."""
    from backend.services.scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/scheduler/pause")
async def scheduler_pause():
    """Pause the auto-scan scheduler."""
    from backend.services.scheduler import stop_scheduler
    stop_scheduler()
    return {"message": "Scheduler paused"}


@router.post("/scheduler/resume")
async def scheduler_resume():
    """Resume the auto-scan scheduler."""
    from backend.services.scheduler import start_scheduler
    start_scheduler()
    return {"message": "Scheduler resumed"}


@router.get("/pnl-exit")
async def get_pnl_exit_status():
    """Get the current Dhan P&L based exit configuration for today."""
    from backend.services.dhan_client import DhanClient

    client = DhanClient()
    try:
        result = await client.get_pnl_exit()
        if not result:
            return {"success": False, "message": "No response from Dhan"}
        if result.get("error"):
            return {"success": False, "message": result.get("detail", "Failed to fetch P&L exit")}
        # Dhan can return HTTP 200 with business-level error in body.
        if str(result.get("status", "")).upper() == "ERROR":
            return {"success": False, "message": result.get("message", "P&L exit status error"), "data": result}
        return {"success": True, "data": result}
    finally:
        await client.close()


@router.post("/pnl-exit")
async def configure_pnl_exit(payload: dict):
    """Configure daily max profit/loss auto-exit using Dhan P&L exit API."""
    from backend.services.dhan_client import DhanClient

    max_profit = payload.get("max_profit")
    max_loss = payload.get("max_loss")
    enable_kill_switch = bool(payload.get("enable_kill_switch", False))
    product_type = payload.get("product_type") or ["INTRADAY", "DELIVERY"]

    if max_profit is None and max_loss is None:
        return {"success": False, "message": "Provide at least one of max_profit or max_loss"}

    try:
        if max_profit is not None and float(max_profit) <= 0:
            return {"success": False, "message": "max_profit must be greater than 0"}
        if max_loss is not None and float(max_loss) <= 0:
            return {"success": False, "message": "max_loss must be greater than 0"}
    except (TypeError, ValueError):
        return {"success": False, "message": "max_profit/max_loss must be numeric"}

    # UX-friendly contract: user enters positive max loss in UI, while Dhan expects
    # loss threshold as a negative number.
    profit_value = abs(float(max_profit)) if max_profit is not None else None
    loss_value = -abs(float(max_loss)) if max_loss is not None else None

    if isinstance(product_type, str):
        product_type = [product_type]
    allowed = {"INTRADAY", "DELIVERY"}
    product_type = [str(p).upper() for p in product_type if str(p).upper() in allowed]
    if not product_type:
        product_type = ["INTRADAY", "DELIVERY"]

    client = DhanClient()
    try:
        result = await client.set_pnl_exit(
            profit_value=profit_value,
            loss_value=loss_value,
            product_types=product_type,
            enable_kill_switch=enable_kill_switch,
        )
        if not result:
            return {"success": False, "message": "No response from Dhan"}
        if result.get("error"):
            return {"success": False, "message": result.get("detail", "Failed to configure P&L exit")}
        if str(result.get("status", "")).upper() == "ERROR":
            return {"success": False, "message": result.get("message", "Failed to configure P&L exit"), "data": result}
        return {"success": True, "message": "Daily P&L guard configured", "data": result}
    finally:
        await client.close()


@router.delete("/pnl-exit")
async def disable_pnl_exit():
    """Disable active Dhan P&L based exit configuration."""
    from backend.services.dhan_client import DhanClient

    client = DhanClient()
    try:
        result = await client.stop_pnl_exit()
        if not result:
            return {"success": False, "message": "No response from Dhan"}
        if result.get("error"):
            return {"success": False, "message": result.get("detail", "Failed to disable P&L exit")}
        if str(result.get("status", "")).upper() == "ERROR":
            return {"success": False, "message": result.get("message", "Failed to disable P&L exit"), "data": result}
        return {"success": True, "message": "Daily P&L guard disabled", "data": result}
    finally:
        await client.close()


@router.post("/token/refresh")
async def refresh_dhan_token():
    """Manually refresh/generate the Dhan access token."""
    try:
        from backend.services.dhan_client import DhanClient
        import os
        
        client = DhanClient()
        old_token_preview = "None"
        
        # Try to renew existing token first (if available)
        if client.access_token:
            old_token_preview = client.access_token[:20] + "..."
            result = await client.renew_token()
            
            if result.get("success") and result.get("new_token"):
                # Update environment variable for this session
                os.environ["DHAN_ACCESS_TOKEN"] = result["new_token"]
                new_token_preview = result["new_token"][:20] + "..."
                await client.close()
                return {
                    "success": True,
                    "message": "Token renewed successfully",
                    "old_token": old_token_preview,
                    "new_token": new_token_preview,
                    "expires_in": "24 hours",
                }
        
        # OAuth flow requires browser interaction
        return {
            "success": False,
            "message": "Token renewal failed. OAuth flow requires manual token generation.",
            "instructions": [
                "1. Go to https://web.dhan.co/",
                "2. My Profile → Access DhanHQ APIs → Generate Access Token",
                "3. Set as DHAN_ACCESS_TOKEN environment variable",
                "4. Restart the application"
            ]
        }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Token generation error: {str(e)}",
        }


@router.post("/backup/now", response_model=BackupInfo)
def create_backup_now(custom_path: str = None):
    """Create an immediate backup of the database."""
    result = backup_manager.create_backup(custom_path)
    return result


@router.get("/backup/list", response_model=list[BackupInfo])
def list_backups():
    """List all available backups."""
    return backup_manager.list_backups()


@router.post("/backup/restore")
def restore_from_backup(filename: str):
    """Restore the database from a backup file."""
    result = backup_manager.restore_backup(filename)
    return result


@router.post("/backup/export-csv")
def export_trades_csv():
    """Export the trade journal to a CSV file."""
    csv_path = backup_manager.export_trades_csv()
    return {"message": "Trades exported", "path": csv_path}


# ── Credentials Management ──

@router.get("/credentials/status")
def get_credentials_status():
    """Get current credentials status (masked)."""
    try:
        masked_creds = settings_manager.get_masked_credentials()
        if not masked_creds:
            return {
                "configured": False,
                "message": "No credentials configured"
            }
        
        return {
            "configured": True,
            "credentials": masked_creds,
            "message": "Credentials configured"
        }
        
    except Exception as e:
        return {
            "configured": False,
            "error": str(e),
            "message": "Error loading credentials"
        }


@router.post("/credentials/save")
async def save_credentials(credentials: dict):
    """Save new credentials securely."""
    try:
        # Validate required fields
        required_fields = ["client_id", "api_key", "api_secret", "pin", "totp_secret"]
        missing_fields = [field for field in required_fields if not credentials.get(field)]
        
        if missing_fields:
            return {
                "success": False,
                "message": f"Missing required fields: {', '.join(missing_fields)}"
            }
        
        # Test credentials first
        test_result = settings_manager.test_credentials(credentials)
        if not test_result["success"]:
            return {
                "success": False,
                "message": f"Credential validation failed: {test_result['message']}"
            }
        
        # Save credentials
        if settings_manager.save_credentials(credentials):
            # Update environment variables
            settings_manager.update_environment_variables(credentials)
            
            return {
                "success": True,
                "message": "Credentials saved and validated successfully",
                "totp_test": test_result["totp_code"]
            }
        else:
            return {
                "success": False,
                "message": "Failed to save credentials"
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Error saving credentials: {str(e)}"
        }


@router.post("/credentials/test")
async def test_credentials(credentials: dict):
    """Test credentials without saving."""
    try:
        result = settings_manager.test_credentials(credentials)
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error testing credentials: {str(e)}"
        }


@router.post("/credentials/clear")
async def clear_credentials():
    """Clear all stored credentials."""
    try:
        if settings_manager.clear_credentials():
            return {
                "success": True,
                "message": "Credentials cleared successfully"
            }
        else:
            return {
                "success": False,
                "message": "Failed to clear credentials"
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Error clearing credentials: {str(e)}"
        }


@router.get("/credentials/export")
def export_credentials():
    """Export credentials status for backup."""
    try:
        export_data = settings_manager.export_settings()
        if export_data:
            return {
                "success": True,
                "data": export_data
            }
        else:
            return {
                "success": False,
                "message": "No credentials to export"
            }
            
    except Exception as e:
            return {
                "success": False,
                "message": f"Error exporting credentials: {str(e)}"
            }


@router.post("/credentials/generate-token")
async def generate_token_now():
    """Manually trigger token generation using UI credentials."""
    try:
        from backend.services.token_manager import _renew_and_save_token
        
        # Trigger token generation
        await _renew_and_save_token()
        
        # Check if token was generated
        import os
        access_token = os.environ.get("DHAN_ACCESS_TOKEN")
        
        if access_token:
            return {
                "success": True,
                "message": "Token generated successfully",
                "token_preview": access_token[:20] + "..."
            }
        else:
            return {
                "success": False,
                "message": "Token generation failed - check logs"
            }
            
    except Exception as e:
            return {
                "success": False,
                "message": f"Error generating token: {str(e)}"
            }


@router.get("/debug/credentials")
async def debug_credentials():
    """Debug endpoint to check credential loading."""
    try:
        from backend.config import get_dhan_credentials
        from backend.services.settings_manager import settings_manager
        import os
        
        # Check UI credentials
        ui_creds = settings_manager.load_credentials()
        
        # Check environment variables
        env_creds = {
            "DHAN_CLIENT_ID": os.environ.get("DHAN_CLIENT_ID"),
            "DHAN_API_KEY": os.environ.get("DHAN_API_KEY"),
            "DHAN_PIN": os.environ.get("DHAN_PIN"),
            "DHAN_TOTP_SECRET": os.environ.get("DHAN_TOTP_SECRET", "")[:10] + "..." if os.environ.get("DHAN_TOTP_SECRET") else None,
            "DHAN_ACCESS_TOKEN": os.environ.get("DHAN_ACCESS_TOKEN", "")[:20] + "..." if os.environ.get("DHAN_ACCESS_TOKEN") else None,
        }
        
        # Check what get_dhan_credentials returns
        try:
            final_creds = get_dhan_credentials()
            final_creds_safe = {
                "client_id": final_creds.get("client_id"),
                "api_key": final_creds.get("api_key", "")[:10] + "..." if final_creds.get("api_key") else None,
                "has_api_secret": bool(final_creds.get("api_secret")),
                "has_access_token": bool(final_creds.get("access_token"))
            }
        except Exception as e:
            final_creds_safe = {"error": str(e)}
        
        return {
            "ui_credentials_exist": bool(ui_creds),
            "ui_credentials": {
                "client_id": ui_creds.get("client_id") if ui_creds else None,
                "has_pin": bool(ui_creds.get("pin")) if ui_creds else False,
                "has_totp_secret": bool(ui_creds.get("totp_secret")) if ui_creds else False,
            } if ui_creds else None,
            "environment_variables": env_creds,
            "final_credentials": final_creds_safe
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": __import__('traceback').format_exc()
        }
