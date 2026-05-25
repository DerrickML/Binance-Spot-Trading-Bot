"""Dashboard data API routes — reads from the SQLite database."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.persistence.db import get_session

logger = get_logger(__name__)
router = APIRouter()


def _safe_query(query_fn: Any) -> Any:
    """Execute a DB query with proper session lifecycle."""
    settings = get_settings()
    session = get_session(settings.database_url)
    try:
        return query_fn(session)
    finally:
        session.close()


@router.get("/status")
async def get_status() -> dict:
    """System status: mode, config, kill switch state."""
    settings = get_settings()
    return {
        "trading_mode": str(settings.trading_mode),
        "live_enabled": settings.enable_live_trading,
        "kill_switch": settings.enable_kill_switch,
        "app_env": str(settings.app_env),
        "symbols": list(settings.trade_symbols),
        "interval": str(settings.trade_interval),
        "max_risk_per_trade": settings.max_risk_per_trade,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_open_positions": settings.max_open_positions,
        "stop_loss_pct": settings.stop_loss_pct,
        "telegram_enabled": settings.enable_telegram,
        "database_url": settings.database_url,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/equity")
async def get_equity() -> dict:
    """Equity curve from account snapshots."""
    from app.persistence.models import AccountSnapshot

    def query(session: Any) -> list[dict]:
        rows = (
            session.query(AccountSnapshot)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(500)
            .all()
        )
        return [
            {
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "total_equity": row.total_equity,
                "cash_balance": row.cash_balance,
                "unrealized_pnl": getattr(row, "unrealized_pnl", 0.0),
            }
            for row in reversed(rows)
        ]

    try:
        data = _safe_query(query)
    except Exception:
        data = []

    return {"equity_curve": data, "count": len(data)}


@router.get("/trades")
async def get_trades(limit: int = 50) -> dict:
    """Recent trades from the database."""
    from app.persistence.models import Trade

    def query(session: Any) -> list[dict]:
        rows = (
            session.query(Trade)
            .order_by(Trade.timestamp.desc())
            .limit(min(limit, 200))
            .all()
        )
        return [
            {
                "id": row.id,
                "symbol": row.symbol,
                "side": row.side,
                "quantity": row.quantity,
                "price": row.price,
                "pnl": getattr(row, "pnl", None),
                "fee": getattr(row, "fee", None),
                "strategy_name": row.strategy_name,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "exit_reason": getattr(row, "exit_reason", None),
            }
            for row in rows
        ]

    try:
        data = _safe_query(query)
    except Exception:
        data = []

    return {"trades": data, "count": len(data)}


@router.get("/positions")
async def get_positions() -> dict:
    """Current open grid states."""
    from app.persistence.models import GridState

    def query(session: Any) -> list[dict]:
        rows = (
            session.query(GridState)
            .order_by(GridState.updated_at.desc())
            .limit(50)
            .all()
        )
        return [
            {
                "id": row.id,
                "symbol": row.symbol,
                "strategy_name": row.strategy_name,
                "state_json": row.state_json,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]

    try:
        data = _safe_query(query)
    except Exception:
        data = []

    return {"positions": data, "count": len(data)}


@router.get("/approved")
async def get_approved() -> dict:
    """Approved strategy combinations from the optimizer."""
    from app.persistence.models import ApprovedCombination

    def query(session: Any) -> list[dict]:
        rows = (
            session.query(ApprovedCombination)
            .order_by(ApprovedCombination.robustness_score.desc())
            .limit(100)
            .all()
        )
        return [
            {
                "id": row.id,
                "strategy_name": row.strategy_name,
                "symbol": row.symbol,
                "interval": row.interval,
                "approved": row.approved,
                "robustness_score": row.robustness_score,
                "pass_rate": row.pass_rate,
                "parameters": json.loads(row.parameters) if row.parameters else {},
                "reasons": json.loads(row.reasons) if row.reasons else [],
                "regime_state": getattr(row, "regime_state", None),
                "regime_tradable": getattr(row, "regime_tradable", None),
            }
            for row in rows
        ]

    try:
        data = _safe_query(query)
    except Exception:
        data = []

    approved_count = sum(1 for d in data if d.get("approved"))
    return {"combinations": data, "total": len(data), "approved_count": approved_count}


@router.get("/incidents")
async def get_incidents(limit: int = 30) -> dict:
    """Recent incidents and errors."""
    from app.persistence.models import Incident

    def query(session: Any) -> list[dict]:
        rows = (
            session.query(Incident)
            .order_by(Incident.timestamp.desc())
            .limit(min(limit, 100))
            .all()
        )
        return [
            {
                "id": row.id,
                "severity": row.severity,
                "component": row.component,
                "message": row.message,
                "details": getattr(row, "details", None),
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
            for row in rows
        ]

    try:
        data = _safe_query(query)
    except Exception:
        data = []

    return {"incidents": data, "count": len(data)}


@router.get("/winner")
async def get_winner() -> dict:
    """Latest selected strategy winner."""
    from app.persistence.models import SelectedStrategy

    def query(session: Any) -> dict | None:
        row = (
            session.query(SelectedStrategy)
            .order_by(SelectedStrategy.selected_at.desc())
            .first()
        )
        if not row:
            return None
        return {
            "strategy_name": row.strategy_name,
            "symbol": row.symbol,
            "interval": row.interval,
            "composite_score": row.composite_score,
            "total_return_pct": row.total_return_pct,
            "max_drawdown_pct": row.max_drawdown_pct,
            "sharpe_ratio": row.sharpe_ratio,
            "profit_factor": row.profit_factor,
            "win_rate": row.win_rate,
            "total_trades": row.total_trades,
            "qualified": row.qualified,
            "selected_at": row.selected_at.isoformat() if row.selected_at else None,
        }

    try:
        data = _safe_query(query)
    except Exception:
        data = None

    return {"winner": data}
