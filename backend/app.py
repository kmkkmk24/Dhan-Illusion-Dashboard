import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from backend.config import FRONTEND_DIR
from backend.models.database import init_db, get_session_factory
from backend.api import (
    scanner,
    watchlist,
    journal,
    settings,
    index_trading,
    value_investing,
    auth,
    market_direction,
)
from backend.services.auth_service import ensure_seed_users, get_user_by_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Trade Scanner Platform...")
    init_db()
    logger.info("Database initialized.")
    db = get_session_factory()()
    try:
        ensure_seed_users(db)
    finally:
        db.close()

    from backend.services.scheduler import start_scheduler, stop_scheduler
    from backend.services.token_manager import start_token_manager, stop_token_manager

    start_scheduler()
    start_token_manager()

    yield

    stop_scheduler()
    stop_token_manager()
    logger.info("Shutting down Trade Scanner Platform.")


app = FastAPI(
    title="Trade Scanner Platform",
    description="Personal Trade Suggestion & Tracking Platform for Swing Trades and Stock Options",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path or ""
    method = request.method.upper()
    if path.startswith("/css") or path.startswith("/js") or path == "/" or path == "/favicon.ico":
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in {"/api/health", "/api/auth/login", "/api/auth/me", "/api/auth/logout"}:
        return await call_next(request)

    token = request.cookies.get("illusion_session")
    db = get_session_factory()()
    try:
        user = get_user_by_session(db, token)
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        request.state.user = {"id": user.id, "username": user.username, "role": user.role}
        if method not in {"GET", "HEAD", "OPTIONS"} and user.role != "admin":
            return JSONResponse(status_code=403, content={"detail": "Admin access required"})
    finally:
        db.close()
    return await call_next(request)


# API routes
app.include_router(auth.router)
app.include_router(scanner.router)
app.include_router(watchlist.router)
app.include_router(journal.router)
app.include_router(settings.router)
app.include_router(index_trading.router)
app.include_router(value_investing.router)
app.include_router(market_direction.router)


# Health check
@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "0.1.0"}


# Serve frontend static files
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")


@app.get("/")
async def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
