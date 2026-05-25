"""Tests for the hybrid Grid/DCA strategy and runtime integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.core.enums import SignalType, TradingMode
from app.execution.paper_broker import PaperBroker
from app.persistence.db import get_session, init_db, reset_engine
from app.persistence.models import GridEvent, GridState
from app.risk.risk_engine import RiskEngine
from app.services.orchestrator import Orchestrator
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.hybrid_grid_dca import HybridGridDcaStrategy


def _run(coro):
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


def _grid_params(**overrides):
    params = {
        "anchor_period": 3,
        "trend_filter_period": 3,
        "grid_spacing_pct": 0.02,
        "max_grid_levels": 3,
        "base_order_pct": 0.10,
        "dca_size_multiplier": 2.0,
        "take_profit_pct": 0.02,
        "stop_loss_pct": 0.10,
        "max_grid_allocation_pct": 0.35,
        "cooldown_bars": 1,
        "min_volatility_pct": 0.0,
        "atr_period": 3,
        "atr_grid_spacing_mult": 0.0,
        "min_trend_slope_pct": -1.0,
        "trend_slope_lookback": 1,
        "max_anchor_deviation_pct": 0.20,
        "take_profit_fee_buffer_pct": 0.0,
        "stop_cooldown_bars": 1,
        "scale_in_requires_below_average": True,
        "scale_in_requires_level_reclaim": True,
        "entry_momentum_lookback": 1,
        "min_entry_momentum_pct": -1.0,
        "support_lookback": 0,
        "support_buffer_pct": 0.0,
        "max_bearish_streak": 99,
        "volatility_zscore_lookback": 3,
        "max_volatility_zscore": 99.0,
        "require_reversal_confirmation": False,
        "min_periods": 3,
    }
    params.update(overrides)
    return params


def _strategy_candles(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    data = []
    for idx, (close, high, low) in enumerate(rows):
        open_time = start + timedelta(hours=idx)
        data.append({
            "symbol": "BTCUSDT",
            "open_time": open_time,
            "close_time": open_time + timedelta(hours=1),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
        })
    return pd.DataFrame(data)


class TestHybridGridDcaStrategy:
    def test_registered_and_waits_for_enough_candles(self):
        import app.strategies.hybrid_grid_dca  # noqa: F401
        from app.strategies.registry import list_strategies

        strategy = HybridGridDcaStrategy(params=_grid_params(min_periods=10))
        candles = _strategy_candles([(100.0, 101.0, 99.0)] * 9)

        assert "hybrid_grid_dca" in list_strategies()
        assert strategy.generate_signals(candles) == []

    def test_open_scale_in_and_take_profit_metadata(self):
        strategy = HybridGridDcaStrategy(params=_grid_params())
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (99.0, 100.0, 98.0),
            (98.0, 99.0, 97.0),
            (97.0, 98.0, 95.0),
            (97.0, 98.0, 95.0),
            (100.0, 110.0, 99.0),
        ])

        signals = strategy.generate_signals(candles)
        actions = [s.metadata["grid_action"] for s in signals]
        scale_levels = [
            s.metadata["grid_level"]
            for s in signals
            if s.metadata["grid_action"] == "scale_in"
        ]

        assert actions[0] == "open"
        assert "take_profit" in actions
        assert scale_levels == sorted(set(scale_levels))
        assert signals[0].metadata["target_notional_pct"] == pytest.approx(0.10)

        first_scale = next(s for s in signals if s.metadata["grid_action"] == "scale_in")
        assert first_scale.metadata["target_notional_pct"] == pytest.approx(0.20)
        assert first_scale.metadata["projected_grid_notional_pct"] <= 0.35
        assert first_scale.metadata["effective_grid_spacing_pct"] >= 0.02
        assert "trend_slope_pct" in first_scale.metadata
        assert "_bar_index" in first_scale.metadata

    def test_stop_exit_signal_uses_grid_contract(self):
        strategy = HybridGridDcaStrategy(params=_grid_params())
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (99.0, 100.0, 98.0),
            (90.0, 91.0, 88.0),
        ])

        signals = strategy.generate_signals(candles)
        stop = next(s for s in signals if s.metadata["grid_action"] == "stop_exit")

        assert stop.signal_type == SignalType.SELL
        assert stop.metadata["grid_id"]
        assert stop.metadata["max_grid_allocation_pct"] == pytest.approx(0.35)

    def test_anchor_deviation_filter_blocks_deep_falling_entries(self):
        strategy = HybridGridDcaStrategy(params=_grid_params(max_anchor_deviation_pct=0.03))
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (90.0, 91.0, 89.0),
        ])

        assert strategy.generate_signals(candles) == []

    def test_trend_slope_filter_blocks_steep_downtrend(self):
        strategy = HybridGridDcaStrategy(params=_grid_params(
            min_trend_slope_pct=-0.01,
            trend_slope_lookback=1,
        ))
        candles = _strategy_candles([
            (110.0, 111.0, 109.0),
            (106.0, 107.0, 105.0),
            (102.0, 103.0, 101.0),
            (98.0, 99.0, 97.0),
            (94.0, 95.0, 93.0),
        ])

        assert strategy.generate_signals(candles) == []

    def test_support_breakdown_filter_blocks_fresh_lows(self):
        strategy = HybridGridDcaStrategy(params=_grid_params(
            support_lookback=3,
            support_buffer_pct=0.02,
        ))
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (98.0, 99.0, 97.0),
        ])

        assert strategy.generate_signals(candles) == []

    def test_momentum_and_bearish_streak_guards_block_falling_knife(self):
        strategy = HybridGridDcaStrategy(params=_grid_params(
            entry_momentum_lookback=1,
            min_entry_momentum_pct=-0.02,
            max_bearish_streak=2,
        ))
        candles = _strategy_candles([
            (103.0, 104.0, 102.0),
            (101.0, 102.0, 100.0),
            (99.0, 100.0, 98.0),
            (96.0, 97.0, 95.0),
        ])

        assert strategy.generate_signals(candles) == []

    def test_reversal_confirmation_blocks_non_reversal_entry(self):
        strategy = HybridGridDcaStrategy(params=_grid_params(
            require_reversal_confirmation=True,
        ))
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (99.0, 100.0, 98.0),
        ])
        candles.loc[3, "open"] = 100.0

        assert strategy.generate_signals(candles) == []

    def test_scale_in_requires_level_reclaim_by_default(self):
        strategy = HybridGridDcaStrategy(params=_grid_params())
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (99.0, 100.0, 98.0),
            (96.0, 98.0, 95.0),
        ])

        signals = strategy.generate_signals(candles)
        assert [s.metadata["grid_action"] for s in signals] == ["open"]

    def test_scale_in_average_entry_uses_grid_fill_price(self):
        strategy = HybridGridDcaStrategy(params=_grid_params(
            grid_spacing_pct=0.02,
            base_order_pct=0.10,
            dca_size_multiplier=2.0,
            take_profit_pct=0.02,
            scale_in_requires_level_reclaim=True,
        ))
        candles = _strategy_candles([
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (100.0, 101.0, 99.0),
            (99.0, 100.0, 98.0),
            (98.5, 99.0, 97.0),
            (100.0, 110.0, 99.0),
        ])

        signals = strategy.generate_signals(candles)
        scale = next(s for s in signals if s.metadata["grid_action"] == "scale_in")
        take_profit = next(s for s in signals if s.metadata["grid_action"] == "take_profit")

        anchor_price = signals[0].metadata["anchor_price"]
        level_price = anchor_price * 0.98
        expected_average = 0.30 / ((0.10 / 99.0) + (0.20 / level_price))

        assert scale.price == pytest.approx(level_price)
        assert take_profit.metadata["average_entry"] == pytest.approx(expected_average)
        assert take_profit.price == pytest.approx(expected_average * 1.02)


class RuntimeGridStrategy(BaseStrategy):
    """Emits an open, then a scale-in, on the latest replay candles."""

    name = "test_runtime_grid"

    def __init__(self, *, reject_scale: bool = False):
        self.reject_scale = reject_scale
        super().__init__()

    def default_params(self):
        return {
            "min_periods": 2,
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.02,
        }

    def generate_signals(self, candles):
        idx = len(candles) - 1
        close = float(candles.iloc[-1]["close"])
        if idx == 49:
            return [StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=close,
                stop_loss=close * 0.90,
                take_profit=close * 1.02,
                metadata={
                    "_bar_index": idx,
                    "grid_action": "open",
                    "grid_id": "grid-1",
                    "grid_level": 0,
                    "target_notional_pct": 0.10,
                    "projected_grid_notional_pct": 0.10,
                    "max_grid_allocation_pct": 0.20,
                    "anchor_price": close,
                },
            )]
        if idx == 50:
            projected = 0.25 if self.reject_scale else 0.18
            return [StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=close,
                metadata={
                    "_bar_index": idx,
                    "grid_action": "scale_in",
                    "grid_id": "grid-1",
                    "grid_level": 1,
                    "target_notional_pct": 0.08,
                    "projected_grid_notional_pct": projected,
                    "max_grid_allocation_pct": 0.20,
                    "anchor_price": 100.0,
                },
            )]
        return []


class OrdinarySecondBuyStrategy(RuntimeGridStrategy):
    name = "test_ordinary_second_buy"

    def generate_signals(self, candles):
        signals = super().generate_signals(candles)
        idx = len(candles) - 1
        if idx == 50:
            close = float(candles.iloc[-1]["close"])
            return [StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=close,
                metadata={"_bar_index": idx},
            )]
        return signals


class RuntimeGridExitStrategy(RuntimeGridStrategy):
    name = "test_runtime_grid_exit"

    def generate_signals(self, candles):
        idx = len(candles) - 1
        close = float(candles.iloc[-1]["close"])
        if idx == 49:
            return super().generate_signals(candles)
        if idx == 50:
            return [StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=102.0,
                metadata={
                    "_bar_index": idx,
                    "grid_action": "take_profit",
                    "grid_id": "grid-1",
                    "grid_level": 0,
                    "target_notional_pct": 0.0,
                    "projected_grid_notional_pct": 0.10,
                    "max_grid_allocation_pct": 0.20,
                    "anchor_price": close,
                },
            )]
        return []


class RuntimeGridDelayedExitStrategy(RuntimeGridStrategy):
    name = "test_runtime_grid_delayed_exit"

    def generate_signals(self, candles):
        idx = len(candles) - 1
        if idx == 49:
            return super().generate_signals(candles)
        if idx == 51:
            return [StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=102.0,
                metadata={
                    "_bar_index": idx,
                    "grid_action": "take_profit",
                    "grid_id": "grid-1",
                    "grid_level": 0,
                    "target_notional_pct": 0.0,
                    "projected_grid_notional_pct": 0.10,
                    "max_grid_allocation_pct": 0.20,
                    "anchor_price": 100.0,
                },
            )]
        return []


def _runtime_candle(idx: int, close: float = 100.0) -> dict:
    open_time = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=idx)
    return {
        "symbol": "BTCUSDT",
        "open_time": open_time,
        "close_time": open_time + timedelta(hours=1),
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1000.0,
        "is_closed": True,
    }


def _runtime_orchestrator(strategy: BaseStrategy, persist_runtime: bool = True) -> Orchestrator:
    broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.0)
    risk = RiskEngine(
        equity=10_000.0,
        max_open_positions=1,
        max_position_size_pct=0.25,
        is_live=False,
    )
    orch = Orchestrator(
        strategy=strategy,
        risk_engine=risk,
        broker=broker,
        mode=TradingMode.PAPER,
        symbols=["BTCUSDT"],
        interval="1h",
        database_url="sqlite:///:memory:",
        persist_runtime=persist_runtime,
    )
    orch._running = True
    return orch


class TestRuntimeGridLifecycle:
    def test_grid_scale_in_is_allowed_and_does_not_add_open_position(self):
        orch = _runtime_orchestrator(RuntimeGridStrategy())

        first = {}
        second = {}
        for idx in range(51):
            result = _run(orch.process_candle(_runtime_candle(idx)))
            if idx == 49:
                first = result
            if idx == 50:
                second = result

        assert first["action"] == "trade_executed"
        assert first["metadata"]["grid_action"] == "open"
        assert second["action"] == "trade_executed"
        assert second["metadata"]["grid_action"] == "scale_in"
        assert orch.risk_engine.open_positions == 1
        assert orch._open_positions["BTCUSDT"]["quantity"] > first["quantity"]

    def test_ordinary_buy_while_long_remains_blocked(self):
        orch = _runtime_orchestrator(OrdinarySecondBuyStrategy())

        second = {}
        for idx in range(51):
            result = _run(orch.process_candle(_runtime_candle(idx)))
            if idx == 50:
                second = result

        assert second["action"] == "lifecycle_blocked"
        assert second["reject_reason"] == "position_already_open"

    def test_scale_in_exceeding_max_allocation_is_rejected(self):
        orch = _runtime_orchestrator(RuntimeGridStrategy(reject_scale=True))

        second = {}
        for idx in range(51):
            result = _run(orch.process_candle(_runtime_candle(idx)))
            if idx == 50:
                second = result

        assert second["action"] == "lifecycle_blocked"
        assert second["reject_reason"] == "grid_allocation_exceeded"

    def test_same_candle_sl_tp_exit_suppresses_duplicate_strategy_sell(self):
        orch = _runtime_orchestrator(RuntimeGridExitStrategy())

        results = []
        for idx in range(51):
            close = 102.0 if idx == 50 else 100.0
            candle = _runtime_candle(idx, close=close)
            if idx == 50:
                candle["high"] = 103.0
                candle["low"] = 100.0
            results.append(_run(orch.process_candle(candle)))

        assert results[49]["action"] == "trade_executed"
        assert results[49]["metadata"]["grid_action"] == "open"
        assert results[50]["action"] == "trade_executed"
        assert results[50]["signal"] == "SELL"
        assert results[50]["exit_reason"] == "take_profit"
        assert results[50]["metadata"]["source"] == "sl_tp"
        assert results[50]["metadata"]["grid_action"] == "take_profit"
        assert "BTCUSDT" not in orch._open_positions
        assert orch.risk_engine.open_positions == 0

    def test_replay_counts_sl_tp_exit_without_lifecycle_block(self):
        orch = _runtime_orchestrator(RuntimeGridExitStrategy())
        candles = []
        for idx in range(51):
            close = 102.0 if idx == 50 else 100.0
            candle = _runtime_candle(idx, close=close)
            if idx == 50:
                candle["high"] = 103.0
                candle["low"] = 100.0
            candles.append(candle)

        summary = _run(orch.replay_candles(candles, persist=False))

        assert summary["trades_executed"] == 2
        assert summary["lifecycle_blocked"] == 0
        assert summary["grid_actions"]["open"] == 1
        assert summary["grid_actions"]["take_profit"] == 1

    def test_stale_grid_exit_after_broker_close_is_ignored_while_flat(self):
        orch = _runtime_orchestrator(RuntimeGridDelayedExitStrategy())
        candles = []
        for idx in range(52):
            candle = _runtime_candle(idx)
            if idx == 50:
                candle["high"] = 103.0
                candle["low"] = 100.0
                candle["close"] = 102.0
            candles.append(candle)

        summary = _run(orch.replay_candles(candles, persist=False))

        assert summary["trades_executed"] == 2
        assert summary["lifecycle_blocked"] == 0
        assert summary["grid_actions"]["open"] == 1
        assert summary["grid_actions"]["take_profit"] == 1
        assert "BTCUSDT" not in orch._open_positions

    def test_replay_grid_persistence_is_non_polluting_by_default(self):
        orch = _runtime_orchestrator(RuntimeGridStrategy())
        candles = [_runtime_candle(idx) for idx in range(51)]

        summary = _run(orch.replay_candles(candles, persist=False))

        session = get_session("sqlite:///:memory:")
        event_count = session.query(GridEvent).count()
        state_count = session.query(GridState).count()
        session.close()

        assert summary["grid_actions"]["open"] == 1
        assert summary["grid_actions"]["scale_in"] == 1
        assert event_count == 0
        assert state_count == 0

    def test_paper_runtime_persists_grid_state_and_events(self):
        orch = _runtime_orchestrator(RuntimeGridStrategy())

        for idx in range(51):
            _run(orch.process_candle(_runtime_candle(idx)))

        session = get_session("sqlite:///:memory:")
        events = session.query(GridEvent).order_by(GridEvent.id).all()
        state = session.query(GridState).filter_by(grid_id="grid-1").one()
        session.close()

        assert [event.event_type for event in events] == ["open", "scale_in"]
        assert state.status == "OPEN"
        assert state.quantity > 0
        assert state.filled_levels_json == "[0, 1]"
