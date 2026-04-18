# Swing Trades Tab — Step‑by‑Step

This document explains what happens in the **Swing Trades** tab from UI → API → scan pipeline → UI updates.

## 1) When the tab opens
**Frontend**: `frontend/js/app.js`
- `loadSignals('swing')` is called.
- Fetches **GET** `/api/signals?section=swing`.
- Renders the swing table, score tooltips, and last‑updated info.
- Status/score filter pills apply client‑side filters (`loadSignals('swing', { statusFilters, scoreFilters })`).

**Backend**: `backend/api/scanner.py` → `list_signals()`
- Reads `Signal` rows for `section = swing`.
- Adds F&O flags and lot size from `Instrument`.
- Returns a flat list of signals for the UI.

## 2) When you click “Scan Swing”
**Frontend**: `runSwingScan()`
- Calls **POST** `/api/settings/scan/run?section=swing`.
- On success, refreshes the swing list via `loadSignals('swing')`.

**Backend**: `backend/api/settings.py` → `run_scan(section='swing')`
The swing scan is a **VCP (Volatility Contraction Pattern)** pipeline with sector‑first prioritization:

1. **Sector analysis**
   - Runs `SectorAnalyzer.analyze_sectors()` to get ranked sectors.
   - Uses `sector.top_sectors` from `config.yaml` to select top momentum sectors.

2. **Universe selection**
   - Loads all NSE cash equities (`instrument_manager.get_nse_cash_equity_shares`).
   - If `swing_universe.auto_rebuild_before_scan = true`, rebuilds `data/swing_universe_latest.csv` (liquidity‑filtered universe).
   - If `vcp_scanner.swing_universe_csv` exists, restricts the scan to those security IDs (CSV‑based universe).

3. **VCP scan**
   - Calls `VCPScanner.scan_vcp_patterns(all_instruments, top_sectors)`.
   - Stores signals in the DB and returns summary stats.

4. **Result payload**
   - Returns `message` and `summary` (count scanned, signals found, skip reasons, etc.).

## 3) What the Swing table shows
Each row is a signal with:
- Symbol + occurrence number (1st/2nd/3rd time)
- Signal type (VCP / Hybrid‑VCP / Explosion, etc.)
- Score (combined from trend + contraction + volume + position)
- Dates: first detected + last updated
- Status (Active/Exploded/Invalidated/Dismissed)

Hovering the score shows a **score breakdown tooltip** (trend/contraction/volume/position weights).

## 4) Signal detail panel
Clicking a row opens the signal detail drawer:
- Shows price levels, OHLC stats, and historical occurrences.
- Uses **GET** `/api/signals/{id}` to fetch full signal history.

---

## `config.yaml` settings used by Swing Trades

### 1) `sector` (used to choose top sectors for swing scan)
```
sector:
  top_sectors: 5
  rs_periods: [5, 10, 20, 50]
```
- `top_sectors`: number of top momentum sectors to prioritize in the swing scan.
- `rs_periods`: currently **not used** in code; RS timeframes are hard‑coded in `sector_analyzer.py`.

### 2) `swing_universe` (liquidity + universe rebuild)
```
swing_universe:
  auto_rebuild_before_scan: true
  min_ltp: 300
  max_ltp: 5000
  min_avg_daily_value_inr: 40000000
  turnover_lookback_days: 20
  history_calendar_days: 300
  min_daily_rows: 115
  hist_min_interval: 0.28
  hist_chunk_pause: 0.18
  ltp_batch_pause_sec: 2.0
  ltp_batch_size: 1000
  max_after_ltp: 0
```
- `auto_rebuild_before_scan`: rebuilds `swing_universe_latest.csv` before each scan.
- `min_ltp` / `max_ltp`: price band for eligible stocks.
- `min_avg_daily_value_inr`: minimum turnover (liquidity filter).
- `turnover_lookback_days`: how many days to average turnover.
- `history_calendar_days`: history window to fetch for filtering.
- `min_daily_rows`: minimum candle count required.
- `hist_min_interval` / `hist_chunk_pause`: throttling for Dhan historical calls.
- `ltp_batch_pause_sec` / `ltp_batch_size`: pacing for LTP batch requests.
- `max_after_ltp`: cap on post‑LTP trimming (0 = no cap).

### 3) `vcp_scanner` (core Swing scoring logic)
```
vcp_scanner:
  swing_scan_mode: chartink
  swing_universe_csv: "data/swing_universe_latest.csv"

  chartink_near_high_threshold: 0.85
  chartink_min_checks_to_pass: 0
  chartink_must_pass_checks: []
  chartink_weekly_range_max: 0.08
  chartink_sma10_period: 10
  chartink_sma15_period: 15
  chartink_sma30_period: 30
  chartink_sma40_period: 40
  chartink_week_freq: W-FRI
  chartink_drop_last_weekly_bar: true
  chartink_volume_use_avg5: true
  chartink_require_sma30_lag4: false

  min_price: 300
  max_price: 5000
  min_volume: 5000
  near_high_threshold: 0.85
  ma_fast: 12
  ma_mid: 24
  ma_slow: 48
  contraction_thresholds: [0.15, 0.10, 0.05]
  min_contractions_weekly: 3
  min_contractions_daily: 3
  contraction_lookback_weeks: 20
  contraction_lookback_days: 30
  volume_surge_threshold: 1.4
  volume_dryup_threshold: 0.8
  rs_threshold: 70
  min_score_threshold: 0.45
  weight_trend: 0.22
  weight_contraction: 0.32
  weight_volume: 0.18
  weight_price_position: 0.18
  weight_rs: 0.10
```
Key notes:
- `swing_scan_mode`: `chartink` (default) or `minervini` logic.
- `swing_universe_csv`: CSV used to restrict the universe (if present).
- `chartink_*` fields: thresholds for the ChartInk‑style VCP screen.
- `min_price`/`max_price`/`min_volume`: additional filters in the Minervini mode.
- `contraction_*`, `volume_*`, `weight_*`: scoring logic for VCP quality.

---

## API Summary
- **GET** `/api/signals?section=swing` → list swing signals
- **GET** `/api/signals/{id}` → signal detail + history
- **POST** `/api/settings/scan/run?section=swing` → run swing scan

---
