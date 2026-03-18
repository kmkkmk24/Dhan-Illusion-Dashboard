from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import Trade
from backend.models.schemas import TradeCreate, TradeUpdate, TradeOut

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/", response_model=list[TradeOut])
def list_trades(
    trade_type: str = Query(None, description="Filter by type: swing or option"),
    status: str = Query(None, description="Filter by status: open, closed, sl_hit, target_hit"),
    db: Session = Depends(get_db),
):
    """List all trades with optional filters."""
    query = db.query(Trade)
    if trade_type:
        query = query.filter(Trade.trade_type == trade_type)
    if status:
        query = query.filter(Trade.status == status)
    return query.order_by(Trade.created_at.desc()).all()


@router.post("/", response_model=TradeOut)
def create_trade(data: TradeCreate, db: Session = Depends(get_db)):
    """Log a new trade."""
    trade = Trade(**data.model_dump())
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


@router.get("/stats")
def get_trade_stats(
    trade_type: str = Query(None),
    db: Session = Depends(get_db),
):
    """Get trade statistics: win rate, total P&L, average P&L, etc."""
    query = db.query(Trade).filter(Trade.status.in_(["closed", "sl_hit", "target_hit"]))
    if trade_type:
        query = query.filter(Trade.trade_type == trade_type)

    closed_trades = query.all()
    if not closed_trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "best_trade": None,
            "worst_trade": None,
            "avg_days_held": 0,
        }

    winning = [t for t in closed_trades if t.pnl and t.pnl > 0]
    losing = [t for t in closed_trades if t.pnl and t.pnl <= 0]
    pnls = [t.pnl for t in closed_trades if t.pnl is not None]

    return {
        "total_trades": len(closed_trades),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(len(winning) / len(closed_trades) * 100, 1) if closed_trades else 0,
        "total_pnl": round(sum(pnls), 2) if pnls else 0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "best_trade": round(max(pnls), 2) if pnls else None,
        "worst_trade": round(min(pnls), 2) if pnls else None,
        "avg_days_held": round(
            sum(t.days_held for t in closed_trades if t.days_held) / len(closed_trades), 1
        ) if closed_trades else 0,
    }


@router.get("/{trade_id}", response_model=TradeOut)
def get_trade(trade_id: int, db: Session = Depends(get_db)):
    """Get a specific trade."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.put("/{trade_id}", response_model=TradeOut)
def update_trade(trade_id: int, data: TradeUpdate, db: Session = Depends(get_db)):
    """Update a trade (close it, update SL/target, add notes)."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(trade, field, value)

    # Auto-calculate P&L if both entry and exit prices are set
    # quantity already includes lots × lot_size
    if trade.entry_price and trade.exit_price and trade.quantity:
        trade.pnl = round((trade.exit_price - trade.entry_price) * trade.quantity, 2)

        if trade.entry_price > 0:
            trade.pnl_percentage = round(
                ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100, 2
            )

    # Auto-calculate risk-reward ratio
    if trade.entry_price and trade.sl_price and trade.target_price:
        risk = abs(trade.entry_price - trade.sl_price)
        reward = abs(trade.target_price - trade.entry_price)
        trade.risk_reward_ratio = round(reward / risk, 2) if risk > 0 else None

    # Auto-calculate days held
    if trade.entry_date and trade.exit_date:
        trade.days_held = (trade.exit_date - trade.entry_date).days

    db.commit()
    db.refresh(trade)
    return trade


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, db: Session = Depends(get_db)):
    """Delete a trade."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    db.delete(trade)
    db.commit()
    return {"message": f"Trade {trade_id} deleted"}
