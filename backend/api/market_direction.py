from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.market_direction import MarketDirectionPredictor

router = APIRouter(prefix="/api/market-direction", tags=["market-direction"])


@router.get("/today")
async def get_market_direction_today(
    force_refresh: bool = False,  # placeholder for future cache controls
    db: Session = Depends(get_db),
):
    _ = force_refresh
    predictor = MarketDirectionPredictor(db)
    return await predictor.predict_today()


@router.get("/dashboard")
async def get_market_direction_dashboard(
    force_refresh: bool = False,  # reserved for future caching strategy
    db: Session = Depends(get_db),
):
    _ = force_refresh
    predictor = MarketDirectionPredictor(db)
    return await predictor.build_dashboard()


@router.post("/history/{prediction_id}/notify")
async def notify_market_direction_history_row(
    prediction_id: int,
    db: Session = Depends(get_db),
):
    predictor = MarketDirectionPredictor(db)
    return await predictor.notify_history_row_to_telegram(prediction_id)
