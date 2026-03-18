import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import FRONTEND_DIR
from backend.models.database import init_db
from backend.api import scanner, watchlist, journal, settings

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

# API routes
app.include_router(scanner.router)
app.include_router(watchlist.router)
app.include_router(journal.router)
app.include_router(settings.router)


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
