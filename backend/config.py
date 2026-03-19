import os
import logging
from pathlib import Path
from functools import lru_cache
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base dict."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def get_config() -> dict:
    """Load configuration from config.yaml with environment variable overrides."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_PATH}. "
            "Copy config.yaml.example to config.yaml and fill in your credentials."
        )

    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    env_overrides = {
        "dhan": {
            "client_id": os.environ.get("DHAN_CLIENT_ID"),
            "access_token": os.environ.get("DHAN_ACCESS_TOKEN"),
            "api_key": os.environ.get("DHAN_API_KEY"),
            "api_secret": os.environ.get("DHAN_API_SECRET"),
        },
        "telegram": {
            "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN"),
            "chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
        },
    }

    # Only apply non-None env overrides
    cleaned = {}
    for section, values in env_overrides.items():
        cleaned_values = {k: v for k, v in values.items() if v is not None}
        if cleaned_values:
            cleaned[section] = cleaned_values

    if cleaned:
        config = _deep_merge(config, cleaned)

    DATA_DIR.mkdir(exist_ok=True)

    return config


def update_config(new_config: dict):
    """Update the config.yaml file with new values."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(new_config, f, default_flow_style=False, sort_keys=False)
    
    # Clear the cache so get_config() returns the new values
    get_config.cache_clear()


def get_db_path() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / "trades.db"


def get_env_var(var_name: str, required: bool = False) -> Optional[str]:
    """Get environment variable with optional requirement check."""
    value = os.environ.get(var_name)
    if required and not value:
        raise ValueError(f"Required environment variable {var_name} is not set")
    return value


def get_dhan_credentials() -> dict:
    """Get Dhan credentials from UI, environment variables, or config."""
    # Try UI credentials first
    try:
        from backend.services.settings_manager import settings_manager
        ui_credentials = settings_manager.load_credentials()
        if ui_credentials:
            logger.info("Using UI credentials for Dhan client")
            return {
                "client_id": ui_credentials["client_id"],
                "api_key": ui_credentials["api_key"],
                "api_secret": ui_credentials["api_secret"],
                "access_token": get_env_var("DHAN_ACCESS_TOKEN"),  # Access token from env
            }
        else:
            logger.info("No UI credentials found, falling back to environment variables")
    except Exception as e:
        logger.error(f"Error loading UI credentials: {e}")
        # If UI credentials fail, continue to environment variables
    
    config = get_config()
    
    # Try environment variables, then fall back to config
    client_id = get_env_var("DHAN_CLIENT_ID") or config.get("dhan", {}).get("client_id")
    api_key = get_env_var("DHAN_API_KEY") or config.get("dhan", {}).get("api_key")
    api_secret = get_env_var("DHAN_API_SECRET") or config.get("dhan", {}).get("api_secret")
    access_token = get_env_var("DHAN_ACCESS_TOKEN") or config.get("dhan", {}).get("access_token")
    
    if not client_id:
        raise ValueError("DHAN_CLIENT_ID must be set in UI, environment variables, or config.yaml")
    
    # API key is required for production
    if not api_key:
        raise ValueError("DHAN_API_KEY must be set in UI or environment variables for production use")
    
    # API secret might be required for some endpoints
    if not api_secret:
        raise ValueError("DHAN_API_SECRET must be set in UI or environment variables for production use")
    
    return {
        "client_id": client_id,
        "api_key": api_key,
        "api_secret": api_secret,
        "access_token": access_token,  # This can be None initially
    }
