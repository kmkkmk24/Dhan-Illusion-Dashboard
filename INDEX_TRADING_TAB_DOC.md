# Index Trading Tab — Step‑by‑Step

This document explains what happens in the **Index Trading** tab.

## 1) When the tab opens
**Frontend**: `loadIndexSignals()`
- Calls **GET** `/api/index-trading/signals?mode=intraday` (or positional).
- Renders two sections: **Nifty** and **Sensex**.

**Backend**: `backend/api/index_trading.py` → `get_index_signals()`
- Returns cached scan results if available.
- Merges tracking status from DB (`is_tracked`, `track_status`, `track_status_reason`).

## 2) Scan buttons (Intraday / Positional)
**Frontend**: `runIndexScan('intraday' | 'positional')`
- Calls **POST** `/api/index-trading/scan/run?mode=intraday|positional`.

**Backend**: `backend/services/index_trading_scanner.py`
- Intraday mode (15m): trend + mean‑reversion hybrid.
- Positional mode: daily EMA trend + breakout/pullback logic.
- Strike selection uses option chain, then derives entry/exit/SL ranges.

## 3) Entry/exit logic (S/R + ATR)
- Intraday S/R uses swing highs/lows + EMA/VWAP fallback.
- Entry/exit/SL ranges are built using ATR buffers.
- Converted to option premium ranges using delta.

## 4) Tracking trades (HOLD / EARLY‑EXIT / SL / TARGET)
**Frontend**: Track button
- **POST** `/api/index-trading/track/{signal_id}`

**Backend**: `backend/api/index_trading.py` + `backend/services/trade_tracker.py`
- Every 2 minutes: **POST** `/api/index-trading/update-tracking`
- Uses option chain + index intraday candles to decide:
  - `HOLD` (blue)
  - `EARLY-EXIT` (orange)
  - `SL-HIT` (red)
  - `TARGET-HIT` (green)

---

## `config.yaml` settings used by this tab
```
index_trading:
  interval_minutes: 15
  lookback_days: 5
  request_delay: 0.6
  max_signals_per_day: 30

  ema_fast: 20
  ema_slow: 50
  ema_slope_lookback: 5
  pullback_pct: 0.003
  vwap_pullback_pct: 0.003

  rsi_period: 14
  rsi_overbought: 70
  rsi_oversold: 30
  vwap_dev_pct: 0.002
  flat_trend_pct: 0.0015
  flat_slope_pct: 0.0006
  mean_revert_allow_trend: true
  reversal_wick_pct: 0.6
  allow_mean_revert_without_candle: true
  mean_revert_candle_override_rsi: 30
  mean_revert_candle_override_rsi_high: 70
  allow_trend_continuation: true
  trend_rsi_min: 55
  trend_rsi_max: 45

  atr_period: 14
  sl_atr_trend: 1.0
  target_atr_trend: 2.0
  sl_atr_mean_revert: 0.8
  target_atr_mean_revert: 1.2

  sr_lookback_bars: 20
  sr_swing_window: 3
  entry_atr_buffer: 0.25
  exit_atr_buffer: 0.4
  sl_atr_buffer: 0.6

  min_liquidity_oi: 100000
  max_spread_pct: 5.0
  moneyness_limit_pct: 2.0
  min_option_premium: 10.0
  min_delta: 0.25

  trade_start_time: "09:30"
  trade_end_time: "15:30"
  skip_open_minutes: 15
  skip_lunch: false
  lunch_start: "12:00"
  lunch_end: "13:30"
  allow_after_close: true

  expiry_policy: weekly_nearest
  positional_expiry_policy: next_week

  indices:
    - name: "NIFTY 50"
      security_id: "13"
      exchange_segment: "IDX_I"
      instrument: "INDEX"
      option_segment: "IDX_I"
      lot_size: 65
    - name: "SENSEX"
      security_id: "51"
      exchange_segment: "IDX_I"
      instrument: "INDEX"
      option_segment: "IDX_I"
      lot_size: 20
```

---

## API Summary
- **GET** `/api/index-trading/signals?mode=intraday|positional`
- **POST** `/api/index-trading/scan/run?mode=intraday|positional`
- **POST** `/api/index-trading/track/{signal_id}`
- **POST** `/api/index-trading/update-tracking`

---

If you want, I can add a troubleshooting section (no‑signal days, skips, etc.).
