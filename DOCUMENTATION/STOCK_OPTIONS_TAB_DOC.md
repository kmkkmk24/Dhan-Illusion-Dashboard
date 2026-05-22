# Stock Options (F&O) Tab — Step-by-Step

This document explains the **Stock Options** tab flow (F&O scanning + option analysis + trade actions).

## 1) When the tab opens
**Frontend**: `frontend/js/app.js`
- Calls `loadFnoSignals()`.
- Fetches **GET** `/api/signals?section=fno`.
- Displays CE/PE tables with filters and score tooltips.

**Backend**: `backend/api/scanner.py` → `list_signals()`
- Returns signals filtered by `section=fno`.
- Adds `is_fno` and `lot_size` from `Instrument`.

## 2) Scan buttons (Core / Adv-Decl)
**Frontend**: `runFnoScanCore()` / `runFnoScanAdvDecl()`
- Calls **POST** `/api/settings/scan/run?section=fno&setup=core` or `setup=advdecl`.
- Status line shows summary and skips.

**Backend**: `backend/api/settings.py` → `run_scan(section='fno')`
- **Core** scan:
  1. Revalidates existing F&O signals.
  2. Runs sector analysis.
  3. Selects top sectors for CE and bottom sectors for PE.
  4. `FnoScanner.scan_fno()` scans those sectors.
- **Adv/Decl** scan:
  - Uses `AdvDeclIntradayScanner.scan()` for intraday breadth-based setups.

## 3) Custom sector selection
**Frontend**: Custom panel lets you pick CE/PE sectors manually.
- Calls **POST** `/api/settings/scan/run/custom` with `{ ce_sectors, pe_sectors }`.

**Backend**: `backend/api/settings.py` → `run_custom_scan()`
- Runs a scan only for the selected sectors.

## 4) Option Analysis panel
**Frontend**: Clicking “Analyze” on a signal opens the right panel.
- Calls **GET** `/api/signals/{signal_id}/option-analysis`.
- Also calls **GET** `/api/signals/{signal_id}/expiry-scores` for expiry scoring.

**Backend**: `backend/api/scanner.py`
- Uses Dhan option chain + Greeks to build the analysis payload.

## 5) Live Advisory
**Frontend**: “Live Advisory” button calls:
- **GET** `/api/signals/{signal_id}/live-advisory?strike=…&side=…&expiry=…`

**Backend**: `backend/api/scanner.py`
- Fetches real-time option chain and evaluates HOLD / EXIT suggestions.

## 6) Trade actions
**Frontend**:
- Place trade → **POST** `/api/signals/place-trade`
- Exit now → **POST** `/api/signals/trades/{trade_id}/exit-now`
- Cancel trade (dashboard only) → **POST** `/api/signals/trades/{trade_id}/close?reason=cancelled`

**Backend**: `backend/api/scanner.py`
- Places orders, creates GTT SL/Target, and stores the trade in DB.

---

## `config.yaml` settings used by this tab

### 1) `fno_scanner`
```
fno_scanner:
  ema_fast: 20
  ema_slow: 50
  consolidation_period: 10
  trend_lookback: 40
  signal_threshold: 0.55
  top_per_sector: 2
  top_sectors: 4
```
- Controls EMA trend, consolidation checks, and how many signals per sector.
- `top_sectors` controls how many top/bottom sectors are used in core scan.

### 2) `advdecl_intraday`
```
advdecl_intraday:
  interval_minutes: 15
  lookback_days: 5
  request_delay: 0.35
  max_candidates: 20
  gap_exclude_pct: 1.5
  ad_ratio_bull: 1.6
  ad_ratio_bear: 0.6
  ema_fast: 20
  ema_slow: 50
  rsi_period: 14
  rsi_bull: 55
  rsi_bear: 45
  adx_period: 14
  adx_min: 20
  volume_spike_ratio: 1.5
  rs_threshold: 0.2
  oi_weight_options: 0.4
  oi_weight_futures: 0.6
  expiry_policy: weekly_nearest
  min_option_premium: 10.0
  min_delta: 0.25
```
- Breadth thresholds, trend filters, and OI boosts for Adv/Decl setup.

---

## API Summary
- **GET** `/api/signals?section=fno`
- **POST** `/api/settings/scan/run?section=fno&setup=core|advdecl|all`
- **POST** `/api/settings/scan/run/custom`
- **GET** `/api/signals/{signal_id}/option-analysis`
- **GET** `/api/signals/{signal_id}/expiry-scores`
- **GET** `/api/signals/{signal_id}/live-advisory`
- **POST** `/api/signals/place-trade`
- **POST** `/api/signals/trades/{trade_id}/exit-now`
- **POST** `/api/signals/trades/{trade_id}/close?reason=cancelled`

---

If you want, I can add a troubleshooting section for scan skips or empty setups.
