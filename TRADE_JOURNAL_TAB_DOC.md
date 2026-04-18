# Trade Journal Tab — Step‑by‑Step

This document explains what happens in the **Trade Journal** tab.

## 1) When the tab opens
**Frontend**: `loadTrades()`
- Calls **GET** `/api/trades/` to load all trades.
- Calls **GET** `/api/trades/stats` to show summary stats.

**Backend**: `backend/api/journal.py`
- `list_trades()` returns trades (optional filters: `trade_type`, `status`).
- `get_trade_stats()` aggregates win rate, total P&L, best/worst trade, etc.

## 2) Creating a new trade
**Frontend**: “+ New Trade” → `openTradeModal()`
- Submits **POST** `/api/trades/` with the trade data.

**Backend**: `create_trade()`
- Saves the trade row and returns it.

## 3) Editing trades (inline)
**Frontend**: inline edits on table cells.
- Sends **PUT** `/api/trades/{trade_id}` with updated fields.

**Backend**: `update_trade()`
- Updates fields.
- Auto‑computes P&L, P&L %, risk‑reward, and days held.

## 4) Deleting a trade
**Frontend**: Delete button
- Sends **DELETE** `/api/trades/{trade_id}`.

**Backend**: `delete_trade()`
- Removes the trade row.

---

## API Summary
- **GET** `/api/trades/`
- **GET** `/api/trades/stats`
- **POST** `/api/trades/`
- **PUT** `/api/trades/{trade_id}`
- **DELETE** `/api/trades/{trade_id}`

---

If you want, I can add a sample payload for create/update operations.
