"""FastAPI application factory for the Trading Bot web dashboard.

Serves:
- Static files (HTML/CSS/JS) for the SPA frontend
- REST API for auth, dashboard data, and command execution
- WebSocket for live command output streaming

Run with:
    python -m uvicorn app.web.server:app --host 0.0.0.0 --port 8880
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.logging import get_logger, setup_logging
from app.web.auth import verify_session_token
from app.web.routes.auth import router as auth_router
from app.web.routes.commands import router as commands_router
from app.web.routes.dashboard import router as dashboard_router

logger = get_logger(__name__)

# ---------- Application ----------

app = FastAPI(
    title="Trading Bot Dashboard",
    description="Secure management dashboard for the Binance Spot trading bot",
    version="1.0.0",
    docs_url=None,   # Disable Swagger in production
    redoc_url=None,   # Disable ReDoc in production
)

# ---------- CORS (same-origin only by default) ----------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # No external origins allowed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Static files ----------

_STATIC_DIR = Path(__file__).parent / "static"


if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ---------- Routes ----------

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(commands_router, prefix="/api/commands", tags=["commands"])


# ---------- Auth Middleware ----------

# Paths that don't require authentication
_PUBLIC_PATHS = {
    "/",
    "/api/auth/request-otp",
    "/api/auth/verify-otp",
}
_PUBLIC_PREFIXES = ("/static/",)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Verify JWT on all API routes except auth and static."""
    path = request.url.path

    # Allow public paths
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return await call_next(request)

    # Skip auth for WebSocket upgrade (handled in the WS endpoint itself)
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)

    # Extract token from Authorization header or cookie
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("auth_token")

    if not token:
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )

    # Verify token
    from app.config.settings import get_settings
    settings = get_settings()
    claims = verify_session_token(
        token=token,
        bot_token=settings.telegram_bot_token,
        expected_chat_id=settings.telegram_chat_id,
    )

    if not claims:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired session"},
        )

    # Attach claims to request state
    request.state.chat_id = claims.get("sub")
    return await call_next(request)


# ---------- SPA fallback ----------

@app.get("/")
async def serve_index():
    """Serve the SPA index.html."""
    index = _STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return JSONResponse(
        status_code=404,
        content={"detail": "Frontend not found. Check app/web/static/"},
    )


# ---------- Lifecycle ----------

@app.on_event("startup")
async def startup():
    """Initialize logging and core services on server start."""
    setup_logging()

    # Register strategies (needed for some dashboard queries)
    import app.strategies.ema_atr  # noqa: F401
    import app.strategies.rsi_mean_reversion  # noqa: F401
    import app.strategies.bollinger_mean_reversion  # noqa: F401
    import app.strategies.breakout  # noqa: F401
    import app.strategies.regime_strategy  # noqa: F401
    import app.strategies.momentum_continuation  # noqa: F401
    import app.strategies.pullback_uptrend  # noqa: F401
    import app.strategies.volatility_breakout  # noqa: F401
    import app.strategies.hybrid_grid_dca  # noqa: F401

    # Initialize database
    from app.config.settings import get_settings
    from app.persistence.db import init_db
    settings = get_settings()
    init_db(settings.database_url)

    port = os.environ.get("WEB_PORT", "8880")
    logger.info("web_dashboard_started", port=port)


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on server shutdown."""
    from app.web.command_runner import command_runner
    if command_runner.is_running:
        logger.warning("shutting_down_with_running_command")
        await command_runner.stop()
    logger.info("web_dashboard_stopped")
