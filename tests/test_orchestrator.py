"""Tests for the orchestrator paper trading pipeline."""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.core.enums import SignalType, TradingMode
from app.execution.paper_broker import PaperBroker
from app.persistence.db import get_session, init_db, reset_engine
from app.persistence.models import AccountSnapshot, Signal, Trade
from app.risk.risk_engine import RiskEngine
from app.services.orchestrator import Orchestrator
from app.strategies.base import BaseStrategy, StrategySignal


# ---------- Minimal test strategy ----------

class AlwaysBuyStrategy(BaseStrategy):
    """Strategy that always emits a BUY signal for testing."""

    name = "test_always_buy"
    description = "Test strategy"

    def default_params(self):
        return {"min_periods": 2}

    def generate_signals(self, candles):
        if len(candles) < 2:
            return []
        close = float(candles.iloc[-1]["close"])
        return [StrategySignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=close,
            stop_loss=close * 0.97,
            take_profit=close * 1.05,
        )]


class HistoricalOneBuyStrategy(BaseStrategy):
    """Full-history strategy with one indexed BUY on bar 49."""

    name = "test_historical_one_buy"

    def default_params(self):
        return {"min_periods": 2}

    def generate_signals(self, candles):
        if len(candles) < 50:
            return []
        row = candles.iloc[49]
        close = float(row["close"])
        return [StrategySignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=close,
            stop_loss=close * 0.5,
            take_profit=close * 2.0,
            metadata={"_bar_index": 49},
        )]


class StaleTimestampSignalStrategy(BaseStrategy):
    """Strategy that keeps returning an old timestamped signal."""

    name = "test_stale_timestamp_signal"

    def default_params(self):
        return {"min_periods": 2}

    def generate_signals(self, candles):
        if len(candles) < 50:
            return []
        first = candles.iloc[0]
        close = float(first["close"])
        return [StrategySignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=close,
            stop_loss=close * 0.5,
            take_profit=close * 2.0,
            timestamp=first.get("close_time"),
        )]


class NeverSignalStrategy(BaseStrategy):
    """Strategy that never emits a signal."""

    name = "test_never_signal"

    def default_params(self):
        return {"min_periods": 2}

    def generate_signals(self, candles):
        return []


class ErrorStrategy(BaseStrategy):
    """Strategy that raises an error."""

    name = "test_error"

    def default_params(self):
        return {"min_periods": 2}

    def generate_signals(self, candles):
        raise ValueError("Test strategy error")


class FakeTelegram:
    """Captures notification calls without network access."""

    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.stop_losses = 0

    async def notify_startup(self, _mode, _symbols):
        return True

    async def notify_shutdown(self, _reason):
        return True

    async def notify_trade_opened(self, _trade_info):
        self.opened += 1
        return True

    async def notify_trade_closed(self, _trade_info):
        self.closed += 1
        return True

    async def notify_stop_loss_hit(self, _trade_info):
        self.stop_losses += 1
        return True


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def reset_db():
    """Reset DB between tests."""
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_risk_engine(**kwargs):
    """Create risk engine with no symbol cooldown for deterministic testing."""
    from app.risk.rules import (
        KillSwitchRule, MaxOpenPositionsRule, MaxDailyLossRule,
        MaxPositionSizeRule, StopLossRequiredRule,
        ConsecutiveLossCooldownRule, DisabledSymbolRule, ErrorHaltRule,
    )
    defaults = {
        "equity": 10_000,
        "is_live": False,
        "rules": [
            KillSwitchRule(), MaxOpenPositionsRule(), MaxDailyLossRule(),
            MaxPositionSizeRule(), StopLossRequiredRule(),
            ConsecutiveLossCooldownRule(), DisabledSymbolRule(), ErrorHaltRule(),
        ],
    }
    defaults.update(kwargs)
    return RiskEngine(**defaults)


def _make_orchestrator(
    strategy=None,
    risk_engine=None,
    broker=None,
    mode=TradingMode.PAPER,
    symbols=None,
    interval="1h",
    telegram=None,
):
    return Orchestrator(
        strategy=strategy or AlwaysBuyStrategy(),
        risk_engine=risk_engine or _make_risk_engine(),
        broker=broker or PaperBroker(initial_balance=10_000),
        telegram=telegram,
        mode=mode,
        symbols=symbols or ["BTCUSDT"],
        interval=interval,
        database_url="sqlite:///:memory:",
    )


def _make_candle(
    symbol="BTCUSDT",
    open_time=1000,
    close=100.0,
    high=105.0,
    low=95.0,
    is_closed=True,
):
    return {
        "symbol": symbol,
        "open_time": open_time,
        "close_time": open_time + 1,
        "open": close - 1,
        "high": high,
        "low": low,
        "close": close,
        "volume": 500.0,
        "is_closed": is_closed,
    }


def _fill_buffer(orch, count=50):
    """Fill the candle buffer with `count` candles (no signals produced yet)."""
    for i in range(count):
        candle = _make_candle(open_time=i, close=100 + i * 0.1)
        asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))


# ---------- Tests ----------

class TestOrchestratorLifecycle:
    def test_starts_and_stops(self):
        orch = _make_orchestrator()
        orch._running = True
        assert orch.is_running is True
        asyncio.get_event_loop().run_until_complete(orch.stop())
        assert orch.is_running is False

    def test_get_status(self):
        orch = _make_orchestrator()
        orch._running = True
        status = orch.get_status()
        assert status["running"] is True
        assert status["mode"] == "paper"
        assert status["strategy"] == "test_always_buy"
        assert status["interval"] == "1h"

    def test_start_uses_configured_interval(self, monkeypatch):
        called = {}

        class FakeWebSocketClient:
            async def subscribe_klines(self, symbols, interval, callback):
                called["symbols"] = symbols
                called["interval"] = interval
                called["callback"] = callback

            async def stop(self):
                pass

        import app.data.websocket_client as websocket_client

        monkeypatch.setattr(websocket_client, "BinanceWebSocketClient", FakeWebSocketClient)

        orch = _make_orchestrator(interval="4h", symbols=["BTCUSDT", "ETHUSDT"])
        asyncio.get_event_loop().run_until_complete(orch.start())

        assert called["symbols"] == ["BTCUSDT", "ETHUSDT"]
        assert called["interval"] == "4h"


class TestCandleProcessing:
    def test_first_signal_produces_trade(self):
        """The FIRST time the buffer fills, a BUY signal should execute a trade."""
        orch = _make_orchestrator()
        orch._running = True

        # Send 49 candles → all "buffering"
        for i in range(49):
            candle = _make_candle(open_time=i, close=100 + i * 0.1)
            result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
            assert result["action"] == "buffering"

        # 50th candle should trigger first trade
        candle = _make_candle(open_time=49, close=105.0)
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
        assert result["action"] == "trade_executed"
        assert result["signal"] == "BUY"

    def test_process_candle_notifies_when_enabled(self):
        """Live paper processing should still notify when a notifier is wired."""
        telegram = FakeTelegram()
        orch = _make_orchestrator(telegram=telegram)
        orch._running = True

        _fill_buffer(orch, 49)
        candle = _make_candle(open_time=50, close=105.0)
        asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert telegram.opened == 1

    def test_replay_suppresses_telegram_notifications(self):
        """Research replay must not send live notification side effects."""
        telegram = FakeTelegram()
        orch = _make_orchestrator(telegram=telegram)
        candles = [_make_candle(open_time=i, close=100 + i * 0.1) for i in range(55)]

        summary = asyncio.get_event_loop().run_until_complete(orch.replay_candles(candles))

        assert summary["trades_executed"] > 0
        assert telegram.opened == 0
        assert orch._can_notify()

    def test_replay_does_not_persist_runtime_rows_by_default(self):
        """Research replay must not pollute paper runtime tables."""
        orch = _make_orchestrator()
        candles = [_make_candle(open_time=i, close=100 + i * 0.1) for i in range(55)]

        asyncio.get_event_loop().run_until_complete(orch.replay_candles(candles))

        session = get_session("sqlite:///:memory:")
        try:
            assert session.query(Signal).count() == 0
            assert session.query(Trade).count() == 0
            assert session.query(AccountSnapshot).count() == 0
        finally:
            session.close()

    def test_replay_can_persist_when_explicitly_requested(self):
        """Explicit sim persistence remains available for debugging."""
        orch = _make_orchestrator()
        candles = [_make_candle(open_time=i, close=100 + i * 0.1) for i in range(55)]

        asyncio.get_event_loop().run_until_complete(orch.replay_candles(candles, persist=True))

        session = get_session("sqlite:///:memory:")
        try:
            assert session.query(Signal).count() > 0
            assert session.query(Trade).count() > 0
            assert session.query(AccountSnapshot).count() > 0
        finally:
            session.close()

    def test_no_signal_strategy(self):
        orch = _make_orchestrator(strategy=NeverSignalStrategy())
        orch._running = True
        _fill_buffer(orch, 51)

        candle = _make_candle(open_time=51, close=110)
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
        assert result["action"] == "no_signal"

    def test_full_history_signal_not_replayed_on_later_candles(self):
        orch = _make_orchestrator(strategy=HistoricalOneBuyStrategy())
        orch._running = True

        result = None
        for i in range(50):
            candle = _make_candle(open_time=i, close=100 + i * 0.1)
            result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert result["action"] == "trade_executed"

        next_candle = _make_candle(open_time=50, close=106.0, high=107.0, low=105.0)
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(next_candle))

        assert result["action"] == "no_signal"
        assert "BTCUSDT" in orch._open_positions

    def test_stale_timestamped_signal_is_ignored(self):
        orch = _make_orchestrator(strategy=StaleTimestampSignalStrategy())
        orch._running = True

        result = None
        for i in range(50):
            candle = _make_candle(open_time=i, close=100 + i * 0.1)
            result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert result["action"] == "no_signal"
        assert len(orch.broker.orders) == 0

    def test_buffering_until_min_candles(self):
        orch = _make_orchestrator()
        orch._running = True

        candle = _make_candle(open_time=0, close=100)
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
        assert result["action"] == "buffering"
        assert result["buffer_size"] == 1


class TestDuplicateProtection:
    def test_skips_duplicate_candles(self):
        orch = _make_orchestrator()
        orch._running = True

        candle = _make_candle(open_time=100, close=100)
        asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        # Same open_time should be deduplicated
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
        assert result["action"] == "duplicate"

    def test_allows_different_timestamps(self):
        orch = _make_orchestrator()
        orch._running = True

        c1 = _make_candle(open_time=100, close=100)
        c2 = _make_candle(open_time=200, close=101)

        r1 = asyncio.get_event_loop().run_until_complete(orch.process_candle(c1))
        r2 = asyncio.get_event_loop().run_until_complete(orch.process_candle(c2))

        assert r1["action"] != "duplicate"
        assert r2["action"] != "duplicate"


class TestRiskRejection:
    def test_kill_switch_blocks_processing(self):
        risk = RiskEngine(equity=10_000)
        risk.activate_kill_switch("test")
        orch = _make_orchestrator(risk_engine=risk)
        orch._running = True

        candle = _make_candle(open_time=1)
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
        assert result["action"] == "kill_switch"

    def test_risk_rejects_when_max_positions_hit(self):
        risk = RiskEngine(equity=10_000, max_open_positions=0)
        orch = _make_orchestrator(risk_engine=risk)
        orch._running = True

        for i in range(51):
            candle = _make_candle(open_time=i, close=100 + i)
            result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert result["action"] == "risk_rejected"
        assert "position" in result["reject_reason"].lower()

    def test_daily_loss_halts_trading(self):
        risk = RiskEngine(equity=10_000, max_daily_loss_pct=0.05)
        risk.daily_pnl = -600  # 6% loss
        orch = _make_orchestrator(risk_engine=risk)
        orch._running = True

        for i in range(51):
            candle = _make_candle(open_time=i, close=100 + i)
            result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert result["action"] == "risk_rejected"


class TestTradeExecution:
    def test_balance_decreases_after_buy(self):
        broker = PaperBroker(initial_balance=10_000, fee_pct=0.001, slippage_pct=0.001)
        orch = _make_orchestrator(broker=broker)
        orch._running = True

        for i in range(51):
            candle = _make_candle(open_time=i, close=100 + i * 0.1)
            asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        balance = asyncio.get_event_loop().run_until_complete(broker.get_balance("USDT"))
        assert balance < 10_000  # Some capital deployed

    def test_trade_persisted_to_broker_orders(self):
        broker = PaperBroker(initial_balance=10_000)
        orch = _make_orchestrator(broker=broker)
        orch._running = True

        for i in range(51):
            candle = _make_candle(open_time=i, close=100 + i * 0.1)
            asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert len(broker.orders) > 0


class TestStrategyErrors:
    def test_strategy_error_handled_gracefully(self):
        orch = _make_orchestrator(strategy=ErrorStrategy())
        orch._running = True

        for i in range(51):
            candle = _make_candle(open_time=i, close=100)
            result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert result["action"] == "strategy_error"
        assert "Test strategy error" in result["error"]

    def test_error_increments_risk_engine_error_count(self):
        risk = RiskEngine(equity=10_000)
        orch = _make_orchestrator(strategy=ErrorStrategy(), risk_engine=risk)
        orch._running = True

        for i in range(51):
            candle = _make_candle(open_time=i, close=100)
            asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))

        assert risk.error_count_today > 0


class TestStoppedState:
    def test_stopped_orchestrator_returns_immediately(self):
        orch = _make_orchestrator()
        orch._running = False

        candle = _make_candle()
        result = asyncio.get_event_loop().run_until_complete(orch.process_candle(candle))
        assert result["action"] == "stopped"


class TestOnCandle:
    def test_incomplete_candle_skipped(self):
        orch = _make_orchestrator()
        orch._running = True

        candle = _make_candle(is_closed=False)
        # _on_candle should skip non-closed candles
        asyncio.get_event_loop().run_until_complete(orch._on_candle(candle))
        assert len(orch._processed_candles) == 0

    def test_closed_candle_processed(self):
        orch = _make_orchestrator()
        orch._running = True

        candle = _make_candle(is_closed=True, open_time=999)
        asyncio.get_event_loop().run_until_complete(orch._on_candle(candle))
        assert len(orch._processed_candles) == 1
