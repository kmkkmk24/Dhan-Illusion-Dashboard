# Settings Tab — Step-by-Step

This document explains what happens in the **Settings** tab.

## 1) Run Scanner buttons
**Frontend**: “Scan Swing Trades”, “Scan F&O Stocks”, “Sector Analysis Only”
- **POST** `/api/settings/scan/run?section=swing`
- **POST** `/api/settings/scan/run?section=fno`
- **POST** `/api/settings/scan/sectors`

**Backend**: `backend/api/settings.py`
- Swing scan runs sector analysis then VCP scan (see Swing tab doc).
- F&O scan runs sector-first F&O scan or Adv/Decl based on setup.
- Sector analysis only returns fresh sector rankings.

## 2) Instruments refresh / search
- **POST** `/api/settings/instruments/refresh`
  - Downloads Dhan scrip master and updates local DB.
- **GET** `/api/settings/instruments/search?q=...`
  - Finds instruments by symbol/name.
- **GET** `/api/settings/instruments/fno`
  - Returns list of F&O-eligible stocks.

## 3) Scheduler controls
- **GET** `/api/settings/scheduler/status`
- **POST** `/api/settings/scheduler/pause`
- **POST** `/api/settings/scheduler/resume`

## 4) Token management
- **POST** `/api/settings/token/refresh`
  - Triggers token renewal via Dhan.

## 5) Backups
- **POST** `/api/settings/backup/now`
- **GET** `/api/settings/backup/list`
- **POST** `/api/settings/backup/restore`
- **POST** `/api/settings/backup/export-csv`

## 6) Credentials management
- **GET** `/api/settings/credentials/status`
- **POST** `/api/settings/credentials/save`
- **POST** `/api/settings/credentials/test`
- **POST** `/api/settings/credentials/clear`
- **GET** `/api/settings/credentials/export`
- **POST** `/api/settings/credentials/generate-token`

## 7) Reset signals
- **POST** `/api/settings/reset/signals`
  - Clears all signals and signal history (use with care).

---

## API Summary (Settings)
- `/api/settings/scan/run`
- `/api/settings/scan/sectors`
- `/api/settings/instruments/refresh`
- `/api/settings/instruments/search`
- `/api/settings/instruments/fno`
- `/api/settings/scheduler/status`
- `/api/settings/scheduler/pause`
- `/api/settings/scheduler/resume`
- `/api/settings/token/refresh`
- `/api/settings/backup/now`
- `/api/settings/backup/list`
- `/api/settings/backup/restore`
- `/api/settings/backup/export-csv`
- `/api/settings/credentials/*`
- `/api/settings/reset/signals`

---

If you want, I can add example request payloads for each action.
