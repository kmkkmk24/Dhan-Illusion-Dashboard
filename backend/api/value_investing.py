import logging
import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import ValueCandidate
from backend.services.value_investing_service import ValueInvestingService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/value-investing", tags=["value-investing"])


def _clean_json(value):
    if isinstance(value, dict):
        return {k: _clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_json(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    return value


@router.post("/refresh")
async def refresh_value_investing(db: Session = Depends(get_db)):
    service = ValueInvestingService(db)
    try:
        summary = await service.refresh()
        summary["refreshed_at"] = datetime.utcnow().isoformat()
        return summary
    except Exception as e:
        logger.exception("Value investing refresh failed: %s", e)
        return {"ok": False, "message": f"Refresh failed: {str(e)}"}


@router.get("/stocks")
def list_value_candidates(
    min_score: float = Query(0.0),
    limit: int = Query(200),
    db: Session = Depends(get_db),
):
    service = ValueInvestingService(db)
    query = (
        db.query(ValueCandidate)
        .order_by(ValueCandidate.score.desc())
        .limit(max(1, min(limit, 1000)))
    )
    if min_score > 0:
        query = query.filter(ValueCandidate.score >= min_score)

    rows = query.all()
    items = []
    for r in rows:
        raw = service._parse_raw_data(r.raw_data)
        items.append(
            {
                "id": r.id,
                "security_id": r.security_id,
                "symbol": r.symbol,
                "display_name": r.display_name,
                "exchange": r.exchange,
                "sector": r.sector,
                "isin": r.isin,
                "score": r.score,
                "valuation_score": r.valuation_score,
                "quality_score": r.quality_score,
                "growth_score": r.growth_score,
                "ownership_score": r.ownership_score,
                "market_cap": r.market_cap,
                "ltp": raw.get("Ltp"),
                "ltp_source": raw.get("LtpSource"),
                "pe": r.pe,
                "pb": r.pb,
                "roce": r.roce,
                "roe": r.roe,
                "debt_to_eq": r.debt_to_eq,
                "operating_margin": r.operating_margin,
                "revenue_growth": r.revenue_growth,
                "eps_growth": r.eps_growth,
                "promoter_holding": r.promoter_holding,
                "fair_value": service._compute_fair_value_from_candidate(r),
                "mini_dcf": service._compute_mini_dcf_from_candidate(r),
                "time_to_fair": service._estimate_time_to_fair_from_candidate(r),
                "source": r.source,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return _clean_json(items)


@router.get("/stocks/{candidate_id}")
async def value_candidate_detail(
    candidate_id: int,
    db: Session = Depends(get_db),
):
    service = ValueInvestingService(db)
    try:
        candidate = db.query(ValueCandidate).filter(ValueCandidate.id == candidate_id).first()
        details = await service.get_details(candidate_id)
        if not details:
            raise HTTPException(status_code=404, detail="Value candidate not found")
        if candidate:
            details["fair_value"] = service._compute_fair_value_from_candidate(candidate)
            details["mini_dcf"] = service._compute_mini_dcf_from_candidate(candidate)
            details["time_to_fair"] = service._estimate_time_to_fair_from_candidate(candidate)
        return _clean_json(details)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Value candidate detail failed: %s", e)
        return {"error": True, "message": f"Detail load failed: {str(e)}"}
