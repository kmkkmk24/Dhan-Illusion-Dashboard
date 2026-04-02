from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


# --- Instruments ---

class InstrumentOut(BaseModel):
    security_id: str
    exchange: str
    segment: str
    symbol: str
    display_name: Optional[str] = None
    instrument_type: Optional[str] = None
    lot_size: int = 1
    is_fno: bool = False
    sector: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Watchlists ---

class WatchlistCreate(BaseModel):
    name: str
    section: str  # "swing" or "fno"

class WatchlistItemAdd(BaseModel):
    security_id: str
    symbol: str

class WatchlistOut(BaseModel):
    id: int
    name: str
    section: str
    items: list["WatchlistItemOut"] = []

    model_config = {"from_attributes": True}

class WatchlistItemOut(BaseModel):
    id: int
    security_id: str
    symbol: str
    added_manually: bool

    model_config = {"from_attributes": True}


# --- Signals ---

class SignalOut(BaseModel):
    id: int
    security_id: str
    symbol: str
    section: str
    signal_type: str
    occurrence_number: int
    first_detected_date: date
    last_updated_date: date
    current_score: float
    peak_score: float
    trend_score: Optional[float] = None
    consol_score: Optional[float] = None
    breakout_score: Optional[float] = None
    status: str
    invalidation_reason: Optional[str] = None
    sector: Optional[str] = None
    close_price_at_detection: Optional[float] = None
    is_fno: Optional[bool] = None
    lot_size: Optional[int] = None

    model_config = {"from_attributes": True}

class SignalHistoryOut(BaseModel):
    date: date
    close_price: Optional[float] = None
    bb_width: Optional[float] = None
    bb_percentile: Optional[float] = None
    atr: Optional[float] = None
    atr_ratio: Optional[float] = None
    price_range_ratio: Optional[float] = None
    volume: Optional[float] = None
    volume_ratio: Optional[float] = None
    combined_score: Optional[float] = None

    model_config = {"from_attributes": True}

class SignalDetailOut(SignalOut):
    history: list[SignalHistoryOut] = []
    past_occurrences: list[SignalOut] = []

    model_config = {"from_attributes": True}


# --- Trades ---

class TradeCreate(BaseModel):
    signal_id: Optional[int] = None
    security_id: str
    symbol: str
    trade_type: str  # "swing" or "option"
    strike_price: Optional[float] = None
    expiry_date: Optional[date] = None
    option_type: Optional[str] = None
    premium: Optional[float] = None
    lot_size: Optional[int] = None
    entry_price: float
    sl_price: Optional[float] = None
    target_price: Optional[float] = None
    quantity: int
    entry_date: date
    notes: Optional[str] = None

class TradeUpdate(BaseModel):
    entry_price: Optional[float] = None
    entry_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_date: Optional[date] = None
    sl_price: Optional[float] = None
    target_price: Optional[float] = None
    quantity: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class TradeOut(BaseModel):
    id: int
    signal_id: Optional[int] = None
    security_id: str
    symbol: str
    trade_type: str
    strike_price: Optional[float] = None
    expiry_date: Optional[date] = None
    option_type: Optional[str] = None
    premium: Optional[float] = None
    lot_size: Optional[int] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    sl_price: Optional[float] = None
    target_price: Optional[float] = None
    quantity: Optional[int] = None
    entry_date: Optional[date] = None
    exit_date: Optional[date] = None
    pnl: Optional[float] = None
    pnl_percentage: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    days_held: Optional[int] = None
    status: str = "open"
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# --- Sectors ---

class SectorScoreOut(BaseModel):
    sector_name: str
    date: date
    relative_strength: Optional[float] = None
    price_action_score: Optional[float] = None
    combined_score: Optional[float] = None
    is_above_20ema: Optional[bool] = None
    is_above_50ema: Optional[bool] = None
    is_making_higher_highs: Optional[bool] = None

    model_config = {"from_attributes": True}


# --- Backup ---

class BackupInfo(BaseModel):
    filename: str
    path: str
    size_mb: float
    created_at: str

class BackupConfigUpdate(BaseModel):
    path: Optional[str] = None
    schedule: Optional[str] = None
    time: Optional[str] = None
    keep_last: Optional[int] = None
