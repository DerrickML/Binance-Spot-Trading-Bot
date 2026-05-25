"""Repository layer for database CRUD operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.persistence.models import (
    AccountSnapshot,
    ApprovedCombination,
    BacktestRun,
    BacktestTrade,
    Candle,
    GridEvent,
    GridState,
    Incident,
    Notification,
    Position,
    SelectedStrategy,
    Signal,
    Trade,
)

logger = get_logger(__name__)

T = TypeVar("T")


class CandleRepository:
    """CRUD operations for candle data."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, candle: Candle) -> Candle:
        """Insert or update a candle record."""
        existing = (
            self.session.query(Candle)
            .filter_by(symbol=candle.symbol, interval=candle.interval, open_time=candle.open_time)
            .first()
        )
        if existing:
            existing.open = candle.open
            existing.high = candle.high
            existing.low = candle.low
            existing.close = candle.close
            existing.volume = candle.volume
            existing.quote_volume = candle.quote_volume
            existing.trade_count = candle.trade_count
            existing.close_time = candle.close_time
            self.session.flush()
            return existing
        self.session.add(candle)
        self.session.flush()
        return candle

    def get_candles(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
        latest: bool = True,
        closed_only: bool = True,
    ) -> list[Candle]:
        """Fetch candles for a symbol and interval in chronological order.

        When ``latest`` is true, the limit is applied to the newest rows first,
        then the result is reversed so replay/backtest callers still process
        candles oldest-to-newest.
        By default, unclosed/future candles are excluded.
        """
        query = (
            self.session.query(Candle)
            .filter_by(symbol=symbol, interval=interval)
        )
        if start:
            query = query.filter(Candle.open_time >= start)
        if end:
            query = query.filter(Candle.open_time <= end)
        if closed_only:
            query = query.filter(Candle.close_time <= datetime.now(timezone.utc))
        if latest:
            rows = query.order_by(Candle.open_time.desc()).limit(limit).all()
            return list(reversed(rows))
        return query.order_by(Candle.open_time).limit(limit).all()


class BacktestRepository:
    """CRUD operations for backtest data."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_run(self, run: BacktestRun) -> BacktestRun:
        self.session.add(run)
        self.session.flush()
        return run

    def save_trade(self, trade: BacktestTrade) -> BacktestTrade:
        self.session.add(trade)
        self.session.flush()
        return trade

    def get_runs(self, strategy_name: str | None = None) -> list[BacktestRun]:
        query = self.session.query(BacktestRun).order_by(BacktestRun.created_at.desc())
        if strategy_name:
            query = query.filter_by(strategy_name=strategy_name)
        return query.all()

    def get_trades(self, backtest_run_id: int) -> list[BacktestTrade]:
        return (
            self.session.query(BacktestTrade)
            .filter_by(backtest_run_id=backtest_run_id)
            .order_by(BacktestTrade.entry_time)
            .all()
        )


class TradeRepository:
    """CRUD operations for live/paper trades."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, trade: Trade) -> Trade:
        self.session.add(trade)
        self.session.flush()
        return trade

    def get_trades(
        self,
        mode: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        query = self.session.query(Trade).order_by(Trade.created_at.desc())
        if mode:
            query = query.filter_by(mode=mode)
        if symbol:
            query = query.filter_by(symbol=symbol)
        return query.limit(limit).all()

    def get_daily_trades(self, mode: str, date: datetime) -> list[Trade]:
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = date.replace(hour=23, minute=59, second=59, microsecond=999999)
        return (
            self.session.query(Trade)
            .filter(Trade.mode == mode, Trade.created_at >= start, Trade.created_at <= end)
            .all()
        )


class PositionRepository:
    """CRUD operations for positions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, position: Position) -> Position:
        self.session.add(position)
        self.session.flush()
        return position

    def get_open_positions(self, mode: str | None = None) -> list[Position]:
        query = self.session.query(Position).filter_by(status="OPEN")
        if mode:
            query = query.filter_by(mode=mode)
        return query.all()

    def close_position(self, position_id: int, exit_price: float, realized_pnl: float) -> None:
        position = self.session.get(Position, position_id)
        if position:
            position.status = "CLOSED"
            position.current_price = exit_price
            position.realized_pnl = realized_pnl
            position.closed_at = datetime.now(timezone.utc)
            self.session.flush()


class GridStateRepository:
    """CRUD operations for Grid/DCA basket state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, state: GridState) -> GridState:
        existing = (
            self.session.query(GridState)
            .filter_by(
                mode=state.mode,
                symbol=state.symbol,
                interval=state.interval,
                grid_id=state.grid_id,
            )
            .first()
        )
        if existing:
            existing.strategy_name = state.strategy_name
            existing.status = state.status
            existing.anchor_price = state.anchor_price
            existing.average_entry_price = state.average_entry_price
            existing.quantity = state.quantity
            existing.allocated_notional = state.allocated_notional
            existing.allocation_pct = state.allocation_pct
            existing.filled_levels_json = state.filled_levels_json
            existing.params_json = state.params_json
            existing.updated_at = datetime.now(timezone.utc)
            existing.closed_at = state.closed_at
            self.session.flush()
            return existing
        self.session.add(state)
        self.session.flush()
        return state

    def get_open(self, mode: str, symbol: str, interval: str) -> GridState | None:
        return (
            self.session.query(GridState)
            .filter_by(mode=mode, symbol=symbol, interval=interval, status="OPEN")
            .order_by(GridState.updated_at.desc())
            .first()
        )


class GridEventRepository:
    """Append-only CRUD operations for Grid/DCA basket events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, event: GridEvent) -> GridEvent:
        self.session.add(event)
        self.session.flush()
        return event


class SignalRepository:
    """CRUD operations for signals."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, signal: Signal) -> Signal:
        self.session.add(signal)
        self.session.flush()
        return signal


class IncidentRepository:
    """CRUD operations for incidents."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, incident: Incident) -> Incident:
        self.session.add(incident)
        self.session.flush()
        return incident

    def get_unresolved(self) -> list[Incident]:
        return (
            self.session.query(Incident)
            .filter_by(resolved=False)
            .order_by(Incident.timestamp.desc())
            .all()
        )


class AccountSnapshotRepository:
    """CRUD for account snapshots."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        self.session.add(snapshot)
        self.session.flush()
        return snapshot


class NotificationRepository:
    """CRUD for notification records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, notification: Notification) -> Notification:
        self.session.add(notification)
        self.session.flush()
        return notification


class SelectedStrategyRepository:
    """CRUD for persisted strategy selections."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, selection: SelectedStrategy) -> SelectedStrategy:
        self.session.add(selection)
        self.session.flush()
        return selection

    def get_latest_winner(
        self, symbol: str | None = None, interval: str | None = None
    ) -> SelectedStrategy | None:
        """Get the most recent strategy winner (any qualification status)."""
        query = self.session.query(SelectedStrategy).order_by(
            SelectedStrategy.selected_at.desc()
        )
        if symbol:
            query = query.filter_by(symbol=symbol)
        if interval:
            query = query.filter_by(interval=interval)
        return query.first()

    def get_latest_qualified_winner(
        self, symbol: str | None = None, interval: str | None = None
    ) -> SelectedStrategy | None:
        """Get the most recent strategy that passed qualification thresholds."""
        query = (
            self.session.query(SelectedStrategy)
            .filter_by(qualified=True)
            .order_by(SelectedStrategy.selected_at.desc())
        )
        if symbol:
            query = query.filter_by(symbol=symbol)
        if interval:
            query = query.filter_by(interval=interval)
        return query.first()

    def get_all_selections(self, limit: int = 20) -> list[SelectedStrategy]:
        return (
            self.session.query(SelectedStrategy)
            .order_by(SelectedStrategy.selected_at.desc())
            .limit(limit)
            .all()
        )


class ApprovedCombinationRepository:
    """CRUD for per-dataset strategy approval decisions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_batch(self, approvals: list[ApprovedCombination]) -> int:
        """Clear old approvals and save a new batch from optimization.

        Returns the number of approved combinations saved.
        """
        self.session.query(ApprovedCombination).delete()
        for a in approvals:
            self.session.add(a)
        self.session.flush()
        logger.info("approved_combinations_saved", total=len(approvals),
                     approved=sum(1 for a in approvals if a.approved))
        return len(approvals)

    def get_approved(
        self, symbol: str | None = None, interval: str | None = None
    ) -> list[ApprovedCombination]:
        """Get approved combinations, optionally filtered by symbol/interval."""
        query = (
            self.session.query(ApprovedCombination)
            .filter_by(approved=True)
            .order_by(ApprovedCombination.robustness_score.desc())
        )
        if symbol:
            query = query.filter_by(symbol=symbol)
        if interval:
            query = query.filter_by(interval=interval)
        return query.all()

    def get_all(self, limit: int = 100) -> list[ApprovedCombination]:
        """Get all approval records."""
        return (
            self.session.query(ApprovedCombination)
            .order_by(ApprovedCombination.robustness_score.desc())
            .limit(limit)
            .all()
        )
