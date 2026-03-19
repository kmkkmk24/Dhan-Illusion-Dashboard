"""
Sector-to-Stock mapping for NSE sectoral indices.

Maps each Nifty sectoral index to its constituent stocks.
This mapping is used to:
  1. Tag each stock with its sector in the instruments table
  2. After sector momentum analysis, only scan stocks in top sectors

The constituent lists are based on NSE's official sectoral index compositions.
These are updated periodically (~quarterly) by NSE when index rebalancing happens.
You can update them by modifying the lists below or by adding a CSV import.
"""

import logging
from sqlalchemy.orm import Session

from backend.models.tables import Instrument

logger = logging.getLogger(__name__)

# Nifty sectoral index constituents (symbol -> sector)
# Updated as of March 2026. These can be refreshed manually.
SECTOR_CONSTITUENTS: dict[str, list[str]] = {
    "Nifty Bank": [
        "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
        "INDUSINDBK", "BANKBARODA", "PNB", "FEDERALBNK", "IDFCFIRSTB",
        "AUBANK", "BANDHANBNK",
    ],
    "Nifty IT": [
        "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
        "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS",
    ],
    "Nifty Pharma": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
        "LUPIN", "AUROPHARMA", "TORNTPHARM", "ZYDUSLIFE", "BIOCON",
    ],
    "Nifty Auto": [
        "M&M", "MARUTI", "TATAMOTORS", "BAJAJ-AUTO", "HEROMOTOCO",
        "EICHERMOT", "TVSMOTOR", "ASHOKLEY", "BALKRISIND", "BHARATFORG",
        "BOSCHLTD", "MOTHERSON", "MRF", "EXIDEIND", "TIINDIA",
    ],
    "Nifty FMCG": [
        "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM",
        "GODREJCP", "DABUR", "MARICO", "COLPAL", "VBL",
        "PGHH", "EMAMILTD", "UBL",
    ],
    "Nifty Metal": [
        "TATASTEEL", "HINDALCO", "JSWSTEEL", "ADANIENT", "VEDL",
        "COALINDIA", "NMDC", "SAIL", "JINDALSTEL", "NATIONALUM",
        "APLAPOLLO", "RATNAMANI", "HINDCOPPER",
    ],
    "Nifty Realty": [
        "DLF", "GODREJPROP", "OBEROIRLTY", "PHOENIXLTD", "PRESTIGE",
        "BRIGADE", "LODHA", "SOBHA", "SUNTECK",
        "MAHLIFE",
    ],
    "Nifty Energy": [
        "RELIANCE", "NTPC", "ONGC", "POWERGRID", "ADANIGREEN",
        "ADANIPOWER", "TATAPOWER", "IOC", "BPCL", "GAIL",
        "NHPC", "SJVN", "PETRONET",
    ],
    "Nifty Infra": [
        "LARSEN", "ULTRACEMCO", "ADANIPORTS", "GRASIM", "AMBUJACEM",
        "SHREECEM", "SIEMENS", "ABB", "HAVELLS", "CUMMINSIND",
        "BHEL", "IRB",
    ],
    "Nifty PSU Bank": [
        "SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK",
        "INDIANB", "IOB", "BANKINDIA", "CENTRALBK", "MAHABANK",
    ],
    "Nifty Media": [
        "ZEE MEDIA CORPORATION LTD", "PVR INOX LIMITED", "SUN TV NETWORK LIMITED", 
        "NETWORK18 MEDIA & INV LTD", "TV TODAY NETWORK LTD",
        "DISH TV INDIA LTD.", "NAZARA TECHNOLOGIES LTD", "TIPS FILMS LIMITED",
    ],
    "Nifty Fin Services": [
        "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
        "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE", "ICICIPRULI",
        "MUTHOOTFIN", "CHOLAFIN", "SHRIRAMFIN", "PFC", "RECLTD",
        "ICICIGI", "LICHSGFIN", "MANAPPURAM",
    ],
    "Nifty Healthcare": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "APOLLOHOSP", "DIVISLAB",
        "MAXHEALTH", "FORTIS", "LAURUSLABS", "IPCALAB", "GLENMARK",
        "METROPOLIS", "LALPATHLAB",
    ],
    "Nifty Oil & Gas": [
        "RELIANCE", "ONGC", "IOC", "BPCL", "GAIL",
        "HINDPETRO", "PETRONET", "MGL", "IGL", "GUJGASLTD",
        "CASTROLIND", "OIL",
    ],
    "Nifty Consumer Durables": [
        "TITAN", "HAVELLS", "VOLTAS", "BLUESTARLT", "CROMPTON",
        "WHIRLPOOL", "BATAINDIA", "PAGEIND", "RELAXO", "VGUARD",
        "KAJARIACER", "CENTURYPLY",
    ],
    "Nifty Private Bank": [
        "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
        "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK", "RBLBANK",
    ],
    "Nifty Commodities": [
        "RELIANCE", "ONGC", "COALINDIA", "TATASTEEL", "HINDALCO",
        "JSWSTEEL", "VEDL", "NMDC", "GRASIM", "UPL",
        "ADANIENT", "SAIL", "JINDALSTEL", "NATIONALUM", "HINDPETRO",
    ],
}

# Build reverse mapping: stock symbol -> list of sectors
def _build_stock_to_sectors() -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for sector, stocks in SECTOR_CONSTITUENTS.items():
        for symbol in stocks:
            if symbol not in mapping:
                mapping[symbol] = []
            mapping[symbol].append(sector)
    return mapping

STOCK_TO_SECTORS = _build_stock_to_sectors()


def get_all_sector_names() -> list[str]:
    """Return all tracked sector names."""
    return list(SECTOR_CONSTITUENTS.keys())


def get_stocks_in_sector(sector_name: str) -> list[str]:
    """Return stock symbols belonging to a sector."""
    return SECTOR_CONSTITUENTS.get(sector_name, [])


def get_stocks_in_sectors(sector_names: list[str]) -> list[str]:
    """Return unique stock symbols across multiple sectors."""
    symbols = set()
    for sector in sector_names:
        symbols.update(SECTOR_CONSTITUENTS.get(sector, []))
    return list(symbols)


def get_sectors_for_stock(symbol: str) -> list[str]:
    """Return which sectors a stock belongs to."""
    return STOCK_TO_SECTORS.get(symbol, [])


def update_instrument_sectors(db: Session) -> int:
    """
    Update the sector field for all instruments based on the sector mapping.
    Matches using trading_symbol (NSE short symbol like HDFCBANK, RELIANCE).
    Returns the number of instruments updated.
    """
    updated = 0
    for symbol, sectors in STOCK_TO_SECTORS.items():
        primary_sector = sectors[0]
        instrument = db.query(Instrument).filter(Instrument.trading_symbol == symbol).first()
        if instrument and instrument.sector != primary_sector:
            instrument.sector = primary_sector
            updated += 1

    db.commit()
    logger.info(f"Updated sector mapping for {updated} instruments")
    return updated


def get_sector_instruments(db: Session, sector_names: list[str]) -> list[Instrument]:
    """Get Instrument objects for stocks in the given sectors."""
    symbols = get_stocks_in_sectors(sector_names)
    return db.query(Instrument).filter(
        Instrument.trading_symbol.in_(symbols),
        Instrument.exchange == "NSE",
    ).all()
