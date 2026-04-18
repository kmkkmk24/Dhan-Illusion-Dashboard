from datetime import datetime, date

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    Text,
    ForeignKey,
    Boolean,
    Index,
)
from sqlalchemy.orm import relationship

from backend.models.database import Base


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    security_id = Column(String(20), unique=True, nullable=False, index=True)
    exchange = Column(String(10), nullable=False)
    segment = Column(String(5), nullable=False)
    symbol = Column(String(50), nullable=False, index=True)
    trading_symbol = Column(String(30), index=True)
    display_name = Column(String(100))
    instrument_type = Column(String(30))
    lot_size = Column(Integer, default=1)
    isin = Column(String(20))
    is_fno = Column(Boolean, default=False, index=True)
    sector = Column(String(50))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_instrument_segment_symbol", "segment", "symbol"),
    )


class Watchlist(Base):
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    section = Column(String(10), nullable=False)  # "swing" or "fno"
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id"), nullable=False)
    security_id = Column(String(20), nullable=False)
    symbol = Column(String(50), nullable=False)
    added_manually = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)

    watchlist = relationship("Watchlist", back_populates="items")

    __table_args__ = (
        Index("idx_watchlist_security", "watchlist_id", "security_id", unique=True),
    )


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    security_id = Column(String(20), nullable=False, index=True)
    symbol = Column(String(50), nullable=False)
    section = Column(String(10), nullable=False)  # "swing" or "fno"
    signal_type = Column(String(20), nullable=False)  # "consolidation" or "explosion"
    occurrence_number = Column(Integer, default=1)
    first_detected_date = Column(Date, nullable=False, default=date.today)
    last_updated_date = Column(Date, nullable=False, default=date.today)
    current_score = Column(Float, default=0.0)
    peak_score = Column(Float, default=0.0)
    trend_score = Column(Float)
    consol_score = Column(Float)
    breakout_score = Column(Float)
    status = Column(String(20), default="active", index=True)  # active / exploded / invalidated / dismissed
    invalidation_reason = Column(String(200))
    sector = Column(String(50))
    close_price_at_detection = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Trade tracking fields for index trading
    is_tracked = Column(Boolean, default=False)
    track_status = Column(String(20), default="NONE")  # NONE, HOLD, EARLY-EXIT, SL-HIT, TARGET-HIT
    track_status_reason = Column(String(200))
    track_entry_price = Column(Float)
    track_target_price = Column(Float)
    track_sl_price = Column(Float)
    track_current_price = Column(Float)
    track_last_updated = Column(DateTime)
    track_option_security_id = Column(String(30))
    track_expiry = Column(String(20))
    track_direction = Column(String(5))
    track_entry_oi = Column(Float)
    track_entry_volume = Column(Float)
    track_entry_spot = Column(Float)
    track_support = Column(Float)
    track_resistance = Column(Float)

    history = relationship("SignalHistory", back_populates="signal", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="signal")

    __table_args__ = (
        Index("idx_signal_status_section", "status", "section"),
        Index("idx_signal_security_status", "security_id", "status"),
    )


class SignalHistory(Base):
    __tablename__ = "signal_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), nullable=False)
    date = Column(Date, nullable=False)
    close_price = Column(Float)
    bb_width = Column(Float)
    bb_percentile = Column(Float)
    atr = Column(Float)
    atr_ratio = Column(Float)
    price_range_ratio = Column(Float)
    volume = Column(Float)
    volume_ratio = Column(Float)
    combined_score = Column(Float)

    signal = relationship("Signal", back_populates="history")

    __table_args__ = (
        Index("idx_signal_history_date", "signal_id", "date", unique=True),
    )


class SectorScore(Base):
    __tablename__ = "sector_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_name = Column(String(50), nullable=False)
    date = Column(Date, nullable=False)
    relative_strength = Column(Float)
    daily_rs = Column(Float)
    weekly_rs = Column(Float)
    monthly_rs = Column(Float)
    price_action_score = Column(Float)
    combined_score = Column(Float)
    is_above_20ema = Column(Boolean)
    is_above_50ema = Column(Boolean)
    is_making_higher_highs = Column(Boolean)
    is_making_higher_lows = Column(Boolean)
    is_ema_aligned = Column(Boolean)

    __table_args__ = (
        Index("idx_sector_date", "sector_name", "date", unique=True),
    )


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), nullable=True)
    security_id = Column(String(20), nullable=False)
    symbol = Column(String(50), nullable=False)
    trade_type = Column(String(10), nullable=False)  # "swing" or "option"

    # Option-specific fields
    strike_price = Column(Float)
    expiry_date = Column(Date)
    option_type = Column(String(5))  # "CE" or "PE"
    premium = Column(Float)
    lot_size = Column(Integer)

    # Trade details
    entry_price = Column(Float)
    exit_price = Column(Float)
    sl_price = Column(Float)
    target_price = Column(Float)
    quantity = Column(Integer)
    entry_date = Column(Date)
    exit_date = Column(Date)

    # Calculated
    pnl = Column(Float)
    pnl_percentage = Column(Float)
    risk_reward_ratio = Column(Float)
    days_held = Column(Integer)

    status = Column(String(20), default="open")  # open / closed / sl_hit / target_hit / cancelled
    notes = Column(Text)

    # Dhan order IDs for lifecycle management
    buy_order_id = Column(String(30))
    sl_order_id = Column(String(30))
    target_order_id = Column(String(30))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    signal = relationship("Signal", back_populates="trades")


class ValueCandidate(Base):
    __tablename__ = "value_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    security_id = Column(String(20), index=True)
    symbol = Column(String(50), index=True)
    display_name = Column(String(120))
    exchange = Column(String(10))
    sector = Column(String(50))
    isin = Column(String(20))

    score = Column(Float, default=0.0)
    valuation_score = Column(Float)
    quality_score = Column(Float)
    growth_score = Column(Float)
    ownership_score = Column(Float)

    market_cap = Column(Float)
    pe = Column(Float)
    pb = Column(Float)
    roce = Column(Float)
    roe = Column(Float)
    debt_to_eq = Column(Float)
    operating_margin = Column(Float)
    revenue_growth = Column(Float)
    eps_growth = Column(Float)
    promoter_holding = Column(Float)

    source = Column(String(30), default="tapetide")
    source_id = Column(String(30))
    source_slug = Column(String(120))
    raw_data = Column(Text)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_value_symbol_exchange", "symbol", "exchange", unique=True),
        Index("idx_value_score", "score"),
    )
