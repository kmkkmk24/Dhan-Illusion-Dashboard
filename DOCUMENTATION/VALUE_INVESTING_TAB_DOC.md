# Value Investing Tab — Step-by-Step

This document explains what happens in the **Value Investing** tab.

## 1) When the tab opens
**Frontend**: `loadValueInvesting()`
- Calls **GET** `/api/value-investing/stocks`.
- Renders the table with score, fair value, LTP, and DCF columns.

**Backend**: `backend/api/value_investing.py`
- Returns cached candidates from DB.

## 2) When you click “Refresh Fundamentals”
**Frontend**: `refreshValueInvesting()`
- Calls **POST** `/api/value-investing/refresh`.
- On success, reloads the table.

**Backend**: `backend/services/value_investing_service.py`
- Uses Tapetide (screener) to fetch candidates.
- Enriches details from Tickertape + Moneycontrol.
- Computes score, fair value range, mini-DCF, and time-to-fair.

## 3) Detail panel
**Frontend**: Clicking a row opens the detail drawer.
- Calls **GET** `/api/value-investing/stocks/{id}`.
- Shows valuation formulas, moat flags, and “why undervalued” reasons.

---

## `config.yaml` settings used by this tab
```
value_investing:
  sources: ["tapetide", "tickertape", "moneycontrol"]
  max_candidates: 3000
  page_size: 50
  max_pages: 6
  min_score: 0.5

  thresholds:
    min_roce: 15
    min_roe: 12
    max_debt_to_eq: 1
    max_pe: 35
    max_pb: 8
    min_operating_margin: 12
    min_promoter_holding: 25
    min_mcap: 300

  score_caps:
    roce_best: 30
    roe_best: 25
    debt_best: 0.5
    pe_best: 12
    pb_best: 2
    margin_best: 25
    promoter_best: 60

  weights:
    valuation: 0.25
    quality: 0.35
    growth: 0.25
    ownership: 0.15

  valuation_range:
    pe_low: 12
    pe_high: 20
    industry_low_factor: 0.8
    industry_high_factor: 1.0

  dcf:
    years: 5
    discount_rate: 0.12
    terminal_multiple: 12
    growth_floor: 0.04
    growth_cap: 0.20
    growth_buffer: 0.05

  time_to_fair:
    fixed_growth_rate: 0.12
    min_growth: 0.04
    max_growth: 0.25
    max_years: 10
```

---

## API Summary
- **GET** `/api/value-investing/stocks`
- **GET** `/api/value-investing/stocks/{id}`
- **POST** `/api/value-investing/refresh`

---

If you want the exact score formula or weight usage breakdown, I can add it.
