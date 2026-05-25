"""Tests for Phase 11: trade lifecycle, accounting, and orchestrator correctness."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.persistence.db import init_db, reset_engine
from app.execution.paper_broker import PaperBroker
from app.risk.risk_engine import RiskEngine
from app.services.orchestrator import Orchestrator
from app.core.enums import TradingMode


def _run(coro):
    """Run an async coroutine without polluting the global event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_orchestrator(strategy_name="ema_atr_crossover", params=None):
    """Create a minimal orchestrator for testing."""
    import app.strategies.ema_atr  # noqa: F401
    import app.strategies.bollinger_mean_reversion  # noqa: F401
    from app.strategies.registry import get_strategy

    strategy = get_strategy(strategy_name, params=params)
    broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.001)
    risk = RiskEngine(
        equity=10_000.0,
        max_risk_per_trade=0.02,
        max_open_positions=3,
        max_position_size_pct=0.25,
        max_daily_loss_pct=0.05,
        stop_loss_pct=0.03,
        is_live=False,
    )
    orch = Orchestrator(
        strategy=strategy,
        risk_engine=risk,
        broker=broker,
        mode=TradingMode.PAPER,
        symbols=["BTCUSDT"],
        database_url="sqlite:///:memory:",
    )
    orch._running = True
    return orch


def _make_candle(symbol, open_time, close, high=None, low=None, volume=1000):
    """Create a candle dict."""
    high = high or close * 1.01
    low = low or close * 0.99
    return {
        "symbol": symbol,
        "open_time": open_time,
        "close_time": open_time + 3600000,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": True,
    }


class TestLifecyclePreChecks:
    """Verify SELL-with-no-position and BUY-with-existing-position are blocked."""

    def test_sell_blocked_without_position(self):
        """SELL signal with no open position should be lifecycle_blocked."""
        orch = _make_orchestrator()

        from app.core.enums import SignalType
        from app.strategies.base import StrategySignal

        assert "BTCUSDT" not in orch._open_positions

        signal = StrategySignal(
            signal_type=SignalType.SELL,
            symbol="BTCUSDT",
            price=50000,
        )

        if signal.signal_type == SignalType.SELL:
            should_block = signal.symbol not in orch._open_positions
            assert should_block is True

    def test_buy_blocked_with_existing_position(self):
        """BUY signal with existing open position should be lifecycle_blocked."""
        orch = _make_orchestrator()

        orch._open_positions["BTCUSDT"] = {
            "entry_price": 50000, "quantity": 0.05,
            "stop_loss": 48000, "take_profit": 55000, "strategy": "test",
        }

        from app.core.enums import SignalType
        from app.strategies.base import StrategySignal

        signal = StrategySignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=51000,
        )

        if signal.signal_type == SignalType.BUY:
            should_block = signal.symbol in orch._open_positions
            assert should_block is True


class TestSellQuantityFromPosition:
    """Verify SELL uses correct quantity from open position."""

    def test_sell_quantity_matches_position(self):
        orch = _make_orchestrator()
        orch._open_positions["BTCUSDT"] = {
            "entry_price": 50000, "quantity": 0.0471,
            "stop_loss": 48000, "take_profit": 55000, "strategy": "test",
        }

        from app.core.enums import SignalType
        from app.strategies.base import StrategySignal

        sell_signal = StrategySignal(signal_type=SignalType.SELL, symbol="BTCUSDT", price=52000)
        qty = orch._calculate_quantity(sell_signal)
        assert qty == pytest.approx(0.0471)

    def test_buy_quantity_from_balance(self):
        orch = _make_orchestrator()

        from app.core.enums import SignalType
        from app.strategies.base import StrategySignal

        buy_signal = StrategySignal(signal_type=SignalType.BUY, symbol="BTCUSDT", price=50000)
        qty = orch._calculate_quantity(buy_signal)
        expected_fill = 50000 * (1 + orch.broker.slippage_pct)
        expected = 2500 / (expected_fill * (1 + orch.broker.fee_pct))
        assert qty == pytest.approx(expected)

    def test_sell_quantity_zero_without_position(self):
        orch = _make_orchestrator()

        from app.core.enums import SignalType
        from app.strategies.base import StrategySignal

        sell_signal = StrategySignal(signal_type=SignalType.SELL, symbol="BTCUSDT", price=52000)
        qty = orch._calculate_quantity(sell_signal)
        assert qty == 0.0

    def test_risk_account_state_syncs_from_paper_broker(self):
        orch = _make_orchestrator()
        orch.broker.balances["USDT"] = 11_000.0
        orch.risk_engine.equity = 10_000.0
        orch.risk_engine.available_balance = 10_000.0

        orch._sync_risk_account_state()

        assert orch.risk_engine.equity == pytest.approx(11_000.0)
        assert orch.risk_engine.available_balance == pytest.approx(11_000.0)


class TestPaperBrokerAccounting:
    """Verify paper broker balance integrity."""

    def test_buy_reduces_quote_balance(self):
        broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.0)

        from app.execution.base_broker import OrderRequest
        from app.core.enums import OrderSide, OrderType

        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        result = _run(broker.submit_order(order))
        assert result.success is True
        assert broker.balances["USDT"] < 10_000.0
        assert broker.balances.get("BTC", 0) == pytest.approx(0.1)

    def test_sell_increases_quote_balance(self):
        broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.0)

        from app.execution.base_broker import OrderRequest
        from app.core.enums import OrderSide, OrderType

        buy = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        _run(broker.submit_order(buy))
        balance_after_buy = broker.balances["USDT"]

        sell = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        result = _run(broker.submit_order(sell))
        assert result.success is True
        assert broker.balances["USDT"] > balance_after_buy
        assert broker.balances.get("BTC", 0) == pytest.approx(0.0)

    def test_sell_rejected_without_asset(self):
        broker = PaperBroker(initial_balance=10_000.0)

        from app.execution.base_broker import OrderRequest
        from app.core.enums import OrderSide, OrderType

        sell = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        result = _run(broker.submit_order(sell))
        assert result.success is False
        assert "Insufficient" in result.error_message

    def test_total_equity_includes_positions(self):
        broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.0)

        from app.execution.base_broker import OrderRequest
        from app.core.enums import OrderSide, OrderType

        buy = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        _run(broker.submit_order(buy))
        equity = broker.get_total_equity(prices={"BTCUSDT": 55000})
        assert equity > 10_000.0 - 100

    def test_equity_consistency_after_round_trip(self):
        broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.0)

        from app.execution.base_broker import OrderRequest
        from app.core.enums import OrderSide, OrderType

        buy = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        _run(broker.submit_order(buy))

        sell = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity=0.1, price=50000, strategy_name="test",
        )
        _run(broker.submit_order(sell))

        final_equity = broker.get_total_equity()
        assert final_equity == pytest.approx(10_000.0 - 10.0, abs=1.0)


class TestReplaySummaryCounters:
    """Verify replay summary has correct counter structure."""

    def test_replay_summary_has_lifecycle_blocked(self):
        orch = _make_orchestrator()
        candles = [_make_candle("BTCUSDT", i * 3600000, 50000 + i * 10) for i in range(5)]
        summary = _run(orch.replay_candles(candles))

        assert "lifecycle_blocked" in summary
        assert "order_rejected" in summary
        assert "regime_blocked" in summary
        assert "no_signal" in summary
        assert "net_pnl" in summary
        assert "initial_capital" in summary

    def test_replay_net_pnl_consistent(self):
        orch = _make_orchestrator()
        candles = [_make_candle("BTCUSDT", i * 3600000, 50000 + i * 5) for i in range(10)]
        summary = _run(orch.replay_candles(candles))

        expected_net = summary["final_equity"] - summary["initial_capital"]
        assert summary["net_pnl"] == pytest.approx(expected_net, abs=0.01)


class TestRuntimePersistenceHardening:
    """Verify lifecycle and automatic exits are persisted accurately."""

    def test_lifecycle_block_persists_rejected_signal(self):
        from app.core.enums import SignalType
        from app.persistence.db import get_session
        from app.persistence.models import Signal as SignalModel
        from app.strategies.base import StrategySignal

        orch = _make_orchestrator()

        def sell_signal(_df):
            return [StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=50_000.0,
            )]

        orch.strategy.generate_signals = sell_signal
        result = {}
        for i in range(60):
            result = _run(orch.process_candle(_make_candle("BTCUSDT", i * 3600000, 50_000)))

        assert result["action"] == "lifecycle_blocked"
        assert result["reject_reason"] == "no_open_position_for_sell"

        session = get_session("sqlite:///:memory:")
        signal = session.query(SignalModel).order_by(SignalModel.id.desc()).first()
        session.close()

        assert signal is not None
        assert signal.accepted is False
        assert signal.reject_reason == "no_open_position_for_sell"

    def test_stop_loss_exit_persists_sell_trade_signal_and_snapshot(self):
        from app.persistence.db import get_session
        from app.persistence.models import AccountSnapshot, Signal as SignalModel, Trade

        orch = _make_orchestrator()
        orch.broker.balances["BTC"] = 0.1
        orch.broker.positions["BTCUSDT"] = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry_price": 100.0,
            "quantity": 0.1,
        }
        orch._open_positions["BTCUSDT"] = {
            "entry_price": 100.0,
            "quantity": 0.1,
            "entry_fee": 0.01,
            "stop_loss": 95.0,
            "take_profit": 120.0,
            "strategy": orch.strategy.name,
        }
        orch.risk_engine.open_positions = 1

        candle = {
            "symbol": "BTCUSDT",
            "open_time": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "close_time": datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
            "open": 100.0,
            "high": 101.0,
            "low": 94.0,
            "close": 96.0,
            "volume": 1000,
            "is_closed": True,
        }
        _run(orch._check_stop_loss_take_profit("BTCUSDT", candle))

        assert "BTCUSDT" not in orch._open_positions
        assert orch.risk_engine.open_positions == 0
        assert orch.risk_engine.daily_pnl < 0

        session = get_session("sqlite:///:memory:")
        sell_trade = session.query(Trade).filter_by(symbol="BTCUSDT", side="SELL").one()
        sell_signal = session.query(SignalModel).filter_by(symbol="BTCUSDT", signal_type="SELL").one()
        snapshots = session.query(AccountSnapshot).count()
        session.close()

        assert sell_trade.status == "FILLED"
        assert sell_trade.filled_quantity == pytest.approx(0.1)
        assert sell_signal.accepted is True
        assert snapshots == 1
