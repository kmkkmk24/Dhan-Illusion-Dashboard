# Dhan API Opportunity Review

This review compares the DhanHQ v2 API catalog with the current integration
(`DOCUMENTATION/DHAN_API_MAP.md`) and highlights unused endpoints that could
improve execution, risk control, or capital efficiency.

## Inventory Summary (Unused vs Used)

### Market Data
- Used: `/marketfeed/ltp`, `/marketfeed/ohlc`, `/charts/historical`, `/charts/intraday`, `/optionchain`, `/optionchain/expirylist`
- Unused: `/marketfeed/quote` (market depth), Live Market Feed WebSocket

### Orders and Execution
- Used: `POST /orders`, `POST /forever/orders`, `DELETE /forever/orders/{order_id}`
- Unused: `PUT /orders/{order-id}`, `DELETE /orders/{order-id}`, `GET /orders`,
  `GET /orders/{order-id}`, `GET /orders/external/{correlation-id}`,
  `POST /orders/slicing`, `GET /trades`, `GET /trades/{order-id}`,
  `PUT /forever/orders/{order-id}`, `GET /forever/orders`,
  Super Orders (`/super/orders`), Live Order Update WebSocket

### Portfolio and Account
- Unused: `/holdings`, `/positions`, `/positions/convert`, `DELETE /positions`,
  `/fundlimit`, `/margincalculator`, `/margincalculator/multi`

### Statements and Compliance
- Unused: `/ledger`, `/trades/{from-date}/{to-date}/{page}` (trade history),
  EDIS (`/edis/tpin`, `/edis/form`, `/edis/inquire/{isin}`)

### Instruments and Admin
- Used: Instrument master CSV (detailed)
- Unused: `/instrument/{exchangeSegment}`, Static IP APIs (`/ip/*`)

## Unused APIs with Profit-Impact Ideas

| Area | Unused API(s) | How it can help profitability |
| --- | --- | --- |
| Market depth | `POST /marketfeed/quote` | Filter signals by spread/imbalance, avoid illiquid strikes, and size entries based on depth to reduce slippage. |
| Real-time data | Live Market Feed WebSocket | Replace polling with tick/quote/full feed to tighten entry timing, improve trailing exits, and monitor OI/volume intraday. |
| Risk automation | Super Orders (`/super/orders`) | One API call for entry + SL + target + trailing stop to reduce missed exits and enforce discipline. |
| GTT lifecycle | `PUT/GET /forever/orders` | Auto-adjust GTT SL/targets based on new S/R or trailing logic; detect stale/expired GTTs. |
| Order lifecycle | Order modify/cancel/list endpoints | Adaptive execution (reprice on spread widen), manage partial fills, auto-cancel if risk changes. |
| Order slicing | `POST /orders/slicing` | Large orders split over freeze limits to avoid rejections and reduce market impact. |
| Order updates | Order Update WebSocket | Real-time fill/partial fill handling; update tracking state and position sizing immediately. |
| Portfolio-aware risk | `/positions`, `/holdings`, `/positions/convert` | Avoid doubling exposure, hedge existing holdings, and auto-convert intraday to delivery when trend strengthens. |
| Capital efficiency | `/fundlimit`, `/margincalculator`, `/margincalculator/multi` | Dynamic position sizing per signal; reduce idle cash and avoid margin shortfall rejections. |
| Performance analytics | `/ledger`, `/trades/{from-date}/{to-date}/{page}` | Strategy-level P&L attribution, time-of-day edge detection, and scanner ROI ranking. |
| Delivery workflows | EDIS endpoints | Faster exit of delivery/MTF positions if you add longer-horizon strategies. |

## Opportunity Shortlist (Highest Impact First)

1. **Live Market Feed WebSocket**  
   - Benefit: tighter entries/exits and more responsive tracking, especially for intraday index and option trades.  
   - Effort: medium (binary parsing + feed normalization).  
   - Risk/constraint: data plan limits; feed reliability monitoring needed.

2. **Super Orders (entry + SL + target + trailing)**  
   - Benefit: reduces manual error and missed exits; enforces risk on every trade.  
   - Effort: low-medium (API integration + UI mapping).  
   - Constraint: static IP required for order APIs.

3. **Market Depth (`/marketfeed/quote`) for liquidity-aware filters**  
   - Benefit: avoid wide spreads and weak depth, improving realized P&L.  
   - Effort: low (REST call + filter rules).  
   - Constraint: 1 request/sec limit for quote API.

4. **Order Update WebSocket**  
   - Benefit: instant fill/partial fill awareness for trade tracking, faster SL/target updates.  
   - Effort: medium (socket client + mapping).  
   - Constraint: needs resilient reconnect/backoff.

5. **Margin Calculator + Fund Limit**  
   - Benefit: position sizing that maximizes capital while respecting leverage and risk.  
   - Effort: low-medium (call + sizing logic).  
   - Constraint: depends on up-to-date prices.

6. **Positions/Holdings for Exposure Control**  
   - Benefit: stop stacking correlated positions; enforce max exposure per index/sector.  
   - Effort: low.  
   - Constraint: depends on consistent symbol mapping.

7. **Order Modify/Cancel + Slicing**  
   - Benefit: reduce rejection/slippage by adaptive execution.  
   - Effort: low.  
   - Constraint: static IP required for trading APIs.

8. **Trade History + Ledger Analytics**  
   - Benefit: identify most profitable setups and cut low-ROI scans.  
   - Effort: low-medium (reporting pipeline).  
   - Constraint: data volume; needs aggregation storage.

## Constraints to Keep in Mind

- **Static IP required** for order placement/modification/cancellation and super/forever orders.
- **Rate limits**: order APIs 10/sec, data APIs 5/sec, quote APIs 1/sec.
- **WebSockets**: market feed is binary; order updates are JSON.
- **Data plan**: live feed and intraday data depend on subscription status.

## Suggested Next Steps

1. Add a prototype for Live Market Feed + Order Update WebSocket to measure latency gains.
2. Integrate Super Orders for the Index Trading tab first (clear TP/SL).
3. Add market depth filters to option selection to reduce slippage.
4. Introduce margin-aware position sizing using `fundlimit` + `margincalculator`.
5. Build a simple P&L dashboard using `trade history` + `ledger` to rank scanners.
