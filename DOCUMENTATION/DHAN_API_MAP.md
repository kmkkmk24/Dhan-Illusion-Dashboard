# Dhan API Usage Map

This is a quick reference for the Dhan endpoints used by the app and where they are called.
All requests are implemented in `backend/services/dhan_client.py`.

## Base URLs
- **API**: `https://api.dhan.co/v2`
- **Auth**: `https://auth.dhan.co`
- **Public CSV**: `https://images.dhan.co/api-data/api-scrip-master-detailed.csv`

## Market Data
| Endpoint | DhanClient method | Used in (features / modules) |
| --- | --- | --- |
| `POST /charts/historical` | `get_historical_daily_data` | Swing scanner (`backend/services/scanner_engine.py`), VCP scanner (`backend/services/vcp_scanner.py`), Sector analysis (`backend/services/sector_analyzer.py`), Index positional scan (`backend/services/index_trading_scanner.py`), Swing universe builder (`backend/services/swing_universe_builder.py`), Option analysis (`backend/api/scanner.py`) |
| `POST /charts/intraday` | `get_intraday_data` | F&O scanner (`backend/services/fno_scanner.py`), Index intraday scan (`backend/services/index_trading_scanner.py`), Adv/Decl intraday (`backend/services/advdecl_intraday_scanner.py`), Trade tracker (`backend/services/trade_tracker.py`) |
| `POST /marketfeed/ohlc` | `get_market_quote_ohlc` | Option analysis / advisory (`backend/api/scanner.py`), Adv/Decl intraday filters (`backend/services/advdecl_intraday_scanner.py`) |
| `POST /marketfeed/ltp` | `get_market_quote_ltp` | Value Investing LTP (`backend/services/value_investing_service.py`), Swing universe builder (`backend/services/swing_universe_builder.py`), Option analysis / cost calc (`backend/api/scanner.py`), Trade tracker fallback (`backend/services/trade_tracker.py`) |
| `POST /marketfeed/quote` | `get_market_quote_quote` | Index trading depth filter (`backend/services/index_trading_scanner.py`) |

## Derivatives Data
| Endpoint | DhanClient method | Used in (features / modules) |
| --- | --- | --- |
| `POST /optionchain/expirylist` | `get_expiry_list` | Index trading strike selection (`backend/services/index_trading_scanner.py`), Adv/Decl OI boost (`backend/services/advdecl_intraday_scanner.py`), Option analysis (`backend/api/scanner.py`) |
| `POST /optionchain` | `get_option_chain` | Index trading strike selection (`backend/services/index_trading_scanner.py`), Adv/Decl OI boost (`backend/services/advdecl_intraday_scanner.py`), Option analysis (`backend/api/scanner.py`), Trade tracker HOLD logic (`backend/services/trade_tracker.py`) |

## Orders & GTT
| Endpoint | DhanClient method | Used in (features / modules) |
| --- | --- | --- |
| `POST /orders` | `place_order` | Live trade placement (`backend/api/scanner.py`) |
| `POST /forever/orders` | `create_forever_order` | SL/Target GTT creation (`backend/api/scanner.py`) |
| `DELETE /forever/orders/{order_id}` | `cancel_forever_order` | Cancel SL/Target GTT (`backend/api/scanner.py`) |

## Auth & Token Management
| Endpoint | DhanClient method | Used in (features / modules) |
| --- | --- | --- |
| `POST /RenewToken` | `renew_token` | Auto token renewal (`backend/services/token_manager.py`) |
| `GET /profile` | `get_profile` | Token validity check (`backend/services/token_manager.py`) |
| `POST /app/generate-consent` | `generate_consent` | OAuth flow (manual token setup) |
| `POST /app/consumeApp-consent` | `consume_consent` | OAuth flow (manual token setup) |
| `GET /app/generateAccessToken` | `generate_access_token_with_totp` | TOTP auto token generation (`backend/services/token_manager.py`) |

## Public Instrument Master
| Endpoint | DhanClient method | Used in (features / modules) |
| --- | --- | --- |
| `GET api-scrip-master-detailed.csv` | `download_scrip_master` | Adv/Decl scrip master mapping (`backend/services/advdecl_intraday_scanner.py`) |

---
If you want a module-by-module usage table or per-feature mapping, tell me and I’ll add it here.
