# Sector Analysis Tab — Step‑by‑Step

This document explains what happens in the **Sector Analysis** tab from UI → API → data processing → UI updates.

## 1) When the tab opens
**Frontend**: `frontend/js/app.js` → `loadSectorsTab()`
- Calls **GET** `/api/settings/sectors/all`.
- If no data is available, shows the “No sector data yet” message.
- Otherwise renders the sectors table via `renderSectorsTable()`.

**Backend**: `backend/api/settings.py` → `get_all_sectors()`
- Returns cached results if present (`_sector_analysis_cache`).
- Otherwise loads the latest `SectorScore` rows from DB for the most recent date.
- Adds `active_signals` count by checking active signals per sector.

## 2) When you click “Refresh Sectors”
**Frontend**: `frontend/js/app.js` (button handler triggers POST)
- Calls **POST** `/api/settings/scan/sectors`.
- On success, renders the updated table.

**Backend**: `backend/api/settings.py` → `run_sector_analysis()`
- Instantiates `SectorAnalyzer` and runs `analyze_sectors()`.
- Saves results into `_sector_analysis_cache`.
- Returns `{ message, sectors }` to the UI.

## 3) How the sector score is calculated
**Backend**: `backend/services/sector_analyzer.py` → `SectorAnalyzer.analyze_sectors()`

### Data fetch
- Pulls **daily OHLCV** for:
  - Nifty 50 index (`NIFTY_50_SECURITY_ID`) and
  - each sector index from `SECTOR_INDICES`.
- Uses `DhanClient.get_historical_daily_data()`.

### Relative Strength (RS) score
- Calculates **sector return − Nifty 50 return** across multiple lookbacks:
  - Daily: 1, 3, 5 days
  - Weekly: 5, 10, 20 days
  - Monthly: 20, 30, 50 days
- Normalizes to a **0–1 score**, then uses a weighted average.

### Price Action score
Checks (each is 1/0; final score = average of the 5 checks):
- Close above 20‑EMA
- Close above 50‑EMA
- Higher highs (last 20 vs prior 20)
- Higher lows (last 20 vs prior 20)
- EMA alignment (20‑EMA > 50‑EMA)

### Final combined score
- `combined_score = 0.5 * RS + 0.5 * PriceAction`
- Trend label is computed from RS + price action signals.

### Stored/returned fields
Each sector returns:
- `daily_rs`, `weekly_rs`, `monthly_rs`, `relative_strength`
- `price_action_score`, `combined_score`
- `trend` label + EMA/HH/HL flags
- `stock_count` and `active_signals`

## 4) How the table is rendered
**Frontend**: `renderSectorsTable()`
- Shows daily/weekly/monthly RS as percent values.
- Shows trend label + badges (20E, 50E, HH).
- Score tooltip on hover shows full breakdown and formula.
- Table is sortable with `applySortable('sectors-table')`.

## 5) Clicking a sector (drill‑down)
**Frontend**: `expandSector(sectorName)`
- Opens the right‑side drawer.
- Fetches **GET** `/api/settings/sectors/{sector}/stocks`.
- Renders stock list with signal badges if the stock has a signal.

**Backend**: `backend/api/settings.py` → `get_sector_stocks()`
- Uses sector mappings from `backend/services/sector_mapping.py`.
- Pulls instruments for that sector and checks for active signals.

## 6) Sector chart (weekly candles)
**Frontend**: `loadSectorChart(sectorName)`
- Fetches **GET** `/api/settings/sectors/{sector}/chart`.
- Draws a custom mini chart + EMA overlays.

**Backend**: `backend/api/settings.py` → `get_sector_chart_data()`
- Loads ~2.5 years of daily OHLCV for the sector index.
- Aggregates to weekly candles and computes EMA20/EMA50.
- Returns `{ candles, ema20, ema50 }`.

## 7) Data sources and mappings
- **Sector index IDs**: `backend/services/sector_analyzer.py` → `SECTOR_INDICES`
- **Sector → stocks mapping**: `backend/services/sector_mapping.py`
- **Signals** (active/exploded): `backend/models/tables.py` → `Signal`

---

## 8) `config.yaml` settings for this tab
The Sector Analysis tab reads settings from the `sector` block in `config.yaml`.

Recommended block (from `config.yaml.example`):
```
sector:
  # Top N sectors to highlight
  top_sectors: 5
  # Relative strength lookback periods (days)
  rs_periods: [5, 10, 20, 50]
```

How each property is used:
- `top_sectors`: used by `backend/api/settings.py` → `get_top_sector_scores()` to limit the number of sectors returned for “top sector” lists.
- `rs_periods`: **not currently used in code** (RS periods are hard‑coded in `TIMEFRAME_PERIODS` inside `backend/services/sector_analyzer.py`).  
  If you want, I can wire this field into the RS calculation.

If the `sector` block is missing in `config.yaml`, the tab will rely on cached results (if any) but can error on fresh scans. Add the block to avoid that.

---

## API Summary
- **GET** `/api/settings/sectors/all` → full sector list
- **POST** `/api/settings/scan/sectors` → refresh analysis
- **GET** `/api/settings/sectors/{sector}/stocks` → stocks in sector
- **GET** `/api/settings/sectors/{sector}/chart` → weekly chart data

---

If you want this expanded with actual formulas or thresholds from `config.yaml`, tell me and I’ll add them.
