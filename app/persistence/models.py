"""SQLAlchemy ORM models for the trading platform.

Schema is designed to be migration-friendly for future PostgreSQL support.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class Candle(Base):
    """OHLCV candle data."""

    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(5), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    quote_volume: Mapped[float] = mapped_column(Float, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_candles_symbol_interval_time", "symbol", "interval", "open_time", unique=True),
    )


class StrategyDefinition(Base):
    """Registered strategy definition."""

    __tablename__ = "strategy_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    class_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class BacktestRun(Base):
    """Record of a backtest execution."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(5), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    final_equity: Mapped[float] = mapped_column(Float, nullable=False)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    total_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    sortino_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    avg_trade_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_backtest_strategy_symbol", "strategy_name", "symbol"),
    )


class BacktestTrade(Base):
    """Individual trade within a backtest."""

    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(50), default="signal")


class Signal(Base):
    """Strategy signal record."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    strength: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    reject_reason: Mapped[str] = mapped_column(String(200), default="")


class Trade(Base):
    """Executed trade (paper or live)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)  # paper | live
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    requested_price: Mapped[float] = mapped_column(Float, nullable=False)
    filled_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss_price: Mapped[float] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exchange_order_id: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_trades_symbol_mode", "symbol", "mode"),
    )


class Position(Base):
    """Open or closed position."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss_price: Mapped[float] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN")
    strategy_name: Mapped[str] = mapped_column(String(100), default="")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class GridState(Base):
    """Current or historical paper Grid/DCA basket state."""

    __tablename__ = "grid_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(5), nullable=False)
    grid_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    anchor_price: Mapped[float] = mapped_column(Float, default=0.0)
    average_entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    allocated_notional: Mapped[float] = mapped_column(Float, default=0.0)
    allocation_pct: Mapped[float] = mapped_column(Float, default=0.0)
    filled_levels_json: Mapped[str] = mapped_column(Text, default="[]")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_grid_state_lookup", "mode", "symbol", "interval", "grid_id"),
    )


class GridEvent(Base):
    """Append-only Grid/DCA basket event log."""

    __tablename__ = "grid_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(5), nullable=False)
    grid_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), default="")
    grid_level: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    notional: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_grid_event_lookup", "mode", "symbol", "interval", "grid_id"),
    )


class AccountSnapshot(Base):
    """Point-in-time account state."""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    total_equity: Mapped[float] = mapped_column(Float, nullable=False)
    available_balance: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Notification(Base):
    """Sent notification record."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # telegram
    message_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str] = mapped_column(Text, default="")


class Incident(Base):
    """Error or incident record for auditability."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # INFO|WARNING|ERROR|CRITICAL
    component: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class SelectedStrategy(Base):
    """Persisted best-strategy selection from a backtest run.

    Stores the winner chosen by the ranking pipeline so that
    paper-trade can auto-load it without re-running backtests.
    Includes qualification status and validation context.
    """

    __tablename__ = "selected_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(5), nullable=False)

    # Ranking metrics
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    total_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    sortino_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)

    # Qualification
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    qualification_failures: Mapped[str] = mapped_column(Text, default="[]")  # JSON

    # Benchmark comparison
    benchmark_return_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Walk-forward validation
    oos_consistency: Mapped[float] = mapped_column(Float, default=0.0)
    degradation_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    validation_windows: Mapped[int] = mapped_column(Integer, default=0)
    validation_context: Mapped[str] = mapped_column(Text, default="{}")  # JSON

    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_selected_strategy_symbol", "symbol", "interval"),
    )


class ApprovedCombination(Base):
    """Persisted dataset-specific approval from optimization.

    Each row represents one strategy+params+symbol+interval decision:
    approved or rejected, with reasons and context.
    """

    __tablename__ = "approved_combinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(5), nullable=False)

    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    robustness_score: Mapped[float] = mapped_column(Float, default=0.0)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # Context
    regime_tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    regime_state: Mapped[str] = mapped_column(String(20), default="unknown")
    qualification_failures: Mapped[str] = mapped_column(Text, default="[]")  # JSON

    # Walk-forward
    oos_return: Mapped[float] = mapped_column(Float, default=0.0)
    degradation_ratio: Mapped[float] = mapped_column(Float, default=0.0)

    optimization_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_approved_combo_lookup", "strategy_name", "symbol", "interval"),
    )
