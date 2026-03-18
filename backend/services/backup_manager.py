"""
Database backup manager with configurable path and retention policy.

Uses SQLite's online backup API for safe, consistent copies
even while the application is running.
"""

import logging
import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

from backend.config import get_config, get_db_path

logger = logging.getLogger(__name__)


def get_backup_path() -> Path | None:
    """Get the configured backup directory path."""
    config = get_config()
    backup_path = config.get("backup", {}).get("path", "")
    if not backup_path:
        return None
    return Path(backup_path)


def create_backup(custom_path: str | None = None) -> dict:
    """
    Create a backup of the SQLite database.

    Uses sqlite3.backup() for a safe, consistent copy that works
    even while the database is being written to.

    Returns info about the created backup.
    """
    db_path = get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    backup_dir = Path(custom_path) if custom_path else get_backup_path()
    if backup_dir is None:
        raise ValueError(
            "No backup path configured. Set backup.path in config.yaml "
            "or provide a custom_path."
        )

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"trades_{timestamp}.db"
    backup_filepath = backup_dir / backup_filename

    source = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(str(backup_filepath))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    size_mb = backup_filepath.stat().st_size / (1024 * 1024)

    logger.info(f"Backup created: {backup_filepath} ({size_mb:.2f} MB)")

    _enforce_retention_policy(backup_dir)

    return {
        "filename": backup_filename,
        "path": str(backup_filepath),
        "size_mb": round(size_mb, 2),
        "created_at": timestamp,
    }


def _enforce_retention_policy(backup_dir: Path) -> None:
    """Delete old backups beyond the configured retention count."""
    config = get_config()
    keep_last = config.get("backup", {}).get("keep_last", 30)

    backups = sorted(
        [f for f in backup_dir.glob("trades_*.db")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if len(backups) > keep_last:
        for old_backup in backups[keep_last:]:
            old_backup.unlink()
            logger.info(f"Deleted old backup: {old_backup.name}")


def list_backups() -> list[dict]:
    """List all available backups."""
    backup_dir = get_backup_path()
    if backup_dir is None or not backup_dir.exists():
        return []

    backups = sorted(
        backup_dir.glob("trades_*.db"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    return [
        {
            "filename": f.name,
            "path": str(f),
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        for f in backups
    ]


def restore_backup(backup_filename: str) -> dict:
    """
    Restore the database from a backup file.

    Copies the backup file over the current database.
    The current database is first backed up as trades_pre_restore_<timestamp>.db.
    """
    backup_dir = get_backup_path()
    if backup_dir is None:
        raise ValueError("No backup path configured.")

    backup_filepath = backup_dir / backup_filename
    if not backup_filepath.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_filepath}")

    db_path = get_db_path()

    if db_path.exists():
        pre_restore_name = f"trades_pre_restore_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
        pre_restore_path = backup_dir / pre_restore_name
        shutil.copy2(str(db_path), str(pre_restore_path))
        logger.info(f"Pre-restore backup created: {pre_restore_path}")

    shutil.copy2(str(backup_filepath), str(db_path))
    logger.info(f"Database restored from: {backup_filepath}")

    return {
        "restored_from": backup_filename,
        "restored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def export_trades_csv(db_path_override: str | None = None) -> str:
    """Export the trades table to a CSV file in the backup directory."""
    import pandas as pd
    from sqlalchemy import create_engine

    db_path = Path(db_path_override) if db_path_override else get_db_path()
    engine = create_engine(f"sqlite:///{db_path}")

    df = pd.read_sql_table("trades", engine)

    backup_dir = get_backup_path()
    export_dir = backup_dir if backup_dir else db_path.parent

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    csv_path = export_dir / f"trades_export_{timestamp}.csv"
    df.to_csv(csv_path, index=False)

    logger.info(f"Trades exported to CSV: {csv_path}")
    return str(csv_path)
