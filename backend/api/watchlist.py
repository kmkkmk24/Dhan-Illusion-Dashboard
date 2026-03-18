from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.tables import Watchlist, WatchlistItem
from backend.models.schemas import (
    WatchlistCreate,
    WatchlistItemAdd,
    WatchlistOut,
)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


@router.get("/", response_model=list[WatchlistOut])
def list_watchlists(
    section: str = None,
    db: Session = Depends(get_db),
):
    """List all watchlists, optionally filtered by section."""
    query = db.query(Watchlist)
    if section:
        query = query.filter(Watchlist.section == section)
    return query.all()


@router.post("/", response_model=WatchlistOut)
def create_watchlist(data: WatchlistCreate, db: Session = Depends(get_db)):
    """Create a new watchlist."""
    existing = db.query(Watchlist).filter(Watchlist.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Watchlist with this name already exists")

    watchlist = Watchlist(name=data.name, section=data.section)
    db.add(watchlist)
    db.commit()
    db.refresh(watchlist)
    return watchlist


@router.delete("/{watchlist_id}")
def delete_watchlist(watchlist_id: int, db: Session = Depends(get_db)):
    """Delete a watchlist and all its items."""
    watchlist = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    db.delete(watchlist)
    db.commit()
    return {"message": f"Watchlist '{watchlist.name}' deleted"}


@router.post("/{watchlist_id}/items")
def add_item(watchlist_id: int, data: WatchlistItemAdd, db: Session = Depends(get_db)):
    """Add a stock to a watchlist."""
    watchlist = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    existing = db.query(WatchlistItem).filter(
        WatchlistItem.watchlist_id == watchlist_id,
        WatchlistItem.security_id == data.security_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Stock already in watchlist")

    item = WatchlistItem(
        watchlist_id=watchlist_id,
        security_id=data.security_id,
        symbol=data.symbol,
        added_manually=True,
    )
    db.add(item)
    db.commit()
    return {"message": f"{data.symbol} added to {watchlist.name}"}


@router.delete("/{watchlist_id}/items/{item_id}")
def remove_item(watchlist_id: int, item_id: int, db: Session = Depends(get_db)):
    """Remove a stock from a watchlist."""
    item = db.query(WatchlistItem).filter(
        WatchlistItem.id == item_id,
        WatchlistItem.watchlist_id == watchlist_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    db.delete(item)
    db.commit()
    return {"message": "Item removed"}
