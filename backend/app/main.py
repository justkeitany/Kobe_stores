import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import init_db
from app.redis_client import get_redis, close_redis
from app.websocket import stats_sender
from app.routers import auth, streams, categories, bouquets, epg, server, settings as settings_router, domain as domain_router
from app.routers.users import router as users_router
from app.routers.xtream import router as xtream_router
from app.routers.proxy import router as proxy_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting IPTV Panel backend...")
    os.makedirs(settings.HLS_OUTPUT_DIR, exist_ok=True)
    await init_db()
    await get_redis()
    logger.info("Database and Redis initialized")

    import asyncio
    from app.source_health import health_loop
    health_task = asyncio.create_task(health_loop())

    yield
    # Shutdown
    logger.info("Shutting down...")
    health_task.cancel()
    from app.ffmpeg_manager import ffmpeg_manager
    await ffmpeg_manager.stop_all()
    await close_redis()


app = FastAPI(
    title="IPTV Panel API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — self-hosted panel is reachable on any IP or domain the operator points
# at it, so reflect whatever Origin the request came from.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(users_router)
app.include_router(streams.router)
app.include_router(categories.router)
app.include_router(bouquets.router)
app.include_router(epg.router)
app.include_router(server.router)
app.include_router(settings_router.router)
app.include_router(domain_router.router)

# Xtream Codes API (no PHP — pure FastAPI)
app.include_router(xtream_router)

# YouTube live-stream proxy (/proxy/stream)
app.include_router(proxy_router)

# ── WebSocket ──────────────────────────────────────────────────────────────
@app.websocket("/ws/stats")
async def ws_stats(websocket: WebSocket):
    await stats_sender(websocket)


# ── Health check ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "iptv-panel"}


@app.get("/api/health")
async def api_health():
    return {"status": "ok"}
