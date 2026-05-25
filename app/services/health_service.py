"""Health service — system health checks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger

logger = get_logger(__name__)


class HealthService:
    """Reports on overall system health."""

    def check(self, settings: Any = None) -> dict[str, Any]:
        """Run health checks and return status report."""
        report: dict[str, Any] = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
        }

        # Config check
        if settings:
            report["checks"]["config"] = {
                "status": "ok",
                "trading_mode": str(settings.trading_mode),
                "live_enabled": settings.enable_live_trading,
                "kill_switch": settings.enable_kill_switch,
                "symbols": settings.trade_symbols,
            }
        else:
            report["checks"]["config"] = {"status": "unknown"}

        # Database check
        try:
            from app.persistence.db import get_engine
            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            report["checks"]["database"] = {"status": "ok"}
        except Exception as e:
            report["checks"]["database"] = {"status": "error", "message": str(e)}
            report["status"] = "degraded"

        # Strategies check
        try:
            from app.strategies.registry import list_strategies
            strategies = list_strategies()
            report["checks"]["strategies"] = {
                "status": "ok" if strategies else "warning",
                "count": len(strategies),
                "names": strategies,
            }
        except Exception as e:
            report["checks"]["strategies"] = {"status": "error", "message": str(e)}

        logger.info("health_check", status=report["status"])
        return report
