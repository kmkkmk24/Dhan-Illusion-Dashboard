from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.schemas import BackupInfo, BackupConfigUpdate
from backend.services import instrument_manager, backup_manager
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
async def run_scan(section: str = "swing", db: Session = Depends(get_db)):
    """
    Manually trigger a scan.

    For 'swing': First runs sector analysis, then scans only stocks in top momentum sectors.
    For 'fno': Sector-first directional scan using 75m candles for CE/PE setups.
    """
    if section == "fno":
        from backend.services.fno_scanner import FnoScanner

        scanner = FnoScanner(db)

        # Step 1: Revalidate existing active/tracked signals with fresh data
        reval = await scanner.revalidate_signals("fno")

        # Step 2: Run sector analysis to get top sectors
        analyzer = SectorAnalyzer(db)
        sector_results = await analyzer.analyze_sectors()

        if not sector_results:
            return {
                "message": "Sector analysis returned no results. Check API connection.",
                "summary": {"revalidation": reval},
            }

        from backend.config import get_config
        fno_config = get_config().get("fno_scanner", {})
        top_n = fno_config.get("top_sectors", 4)

        top_sectors_ce = [s["sector_name"] for s in sector_results[:top_n]]
        bottom = sector_results[-top_n:]
        top_sectors_pe = [s["sector_name"] for s in bottom]

        # Step 3: Get F&O instruments
        instruments = instrument_manager.get_fno_stocks(db)
        if not instruments:
            return {
                "message": "No instruments loaded. Run 'Refresh Instruments' first.",
                "summary": {"revalidation": reval},
            }

        # Step 4: Discover new setups (reuses the same scanner/dhan client)
        scanner_fresh = FnoScanner(db)
        summary = await scanner_fresh.scan_fno(instruments, top_sectors_ce, top_sectors_pe)
        summary["revalidation"] = reval

        return {
            "message": f"F&O scan complete: CE sectors={top_sectors_ce}, PE sectors={top_sectors_pe}",
            "summary": summary,
        }

    # Swing: sector-first approach
    analyzer = SectorAnalyzer(db)
    sector_results = await analyzer.analyze_sectors()

    if not sector_results:
        return {"message": "Sector analysis returned no results. Check API connection.", "summary": {}}

    from backend.config import get_config
    top_n = get_config()["sector"]["top_sectors"]
    top_sectors = [s["sector_name"] for s in sector_results[:top_n]]

    instruments = get_sector_instruments(db, top_sectors)

    if not instruments:
        return {
            "message": f"No instruments found for top sectors: {', '.join(top_sectors)}. Run 'Refresh Instruments' first.",
            "summary": {},
        }

    scanner = ScannerEngine(db)
    summary = await scanner.scan_stocks(instruments, section)
    summary["top_sectors"] = top_sectors
    summary["sector_stocks_count"] = len(instruments)

    return {
        "message": f"Scan complete: {len(instruments)} stocks from top {top_n} sectors ({', '.join(top_sectors)})",
        "summary": summary,
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

    instruments = db.query(Instrument).filter(
        Instrument.trading_symbol.in_(symbols),
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
