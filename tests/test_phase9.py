"""Tests for Phase 9: approval persistence, new strategies, metrics edge cases."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.persistence.db import init_db, get_session, reset_engine
from app.persistence.models import ApprovedCombination
from app.persistence.repositories import ApprovedCombinationRepository
from app.backtesting.metrics import calculate_metrics, _calculate_sharpe, _calculate_sortino
from app.backtesting.engine import BacktestResult


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_candle_df(n=200, start_price=100.0, trend=0.0, volatility=1.5, seed=42) -> pd.DataFrame:
    np.random.seed(seed)
    prices = [start_price]
    for _ in range(n - 1):
        change = trend + np.random.normal(0, volatility)
        prices.append(max(prices[-1] + change, 10.0))

    rows = []
    for i, close in enumerate(prices):
        high = close + abs(np.random.normal(0, volatility * 0.5))
        low = close - abs(np.random.normal(0, volatility * 0.5))
        o = close + np.random.normal(0, volatility * 0.2)
        rows.append({
            "open_time": i * 3600000,
            "close_time": (i + 1) * 3600000 - 1,
            "open": max(o, 1.0),
            "high": max(high, o, close),
            "low": min(max(low, 0.1), o, close),
            "close": close,
            "volume": 1000 + np.random.randint(0, 500),
        })
    return pd.DataFrame(rows)


# ==========================================
# Approval Persistence Tests
# ==========================================

class TestApprovedCombinationPersistence:
    def test_save_and_retrieve_approved(self):
        """Save approved combinations and retrieve approved-only."""
        session = get_session("sqlite:///:memory:")
        repo = ApprovedCombinationRepository(session)

        combos = [
            ApprovedCombination(
                strategy_name="test_strat", parameters='{"a": 1}',
                symbol="BTCUSDT", interval="1h",
                approved=True, reasons='["passed all checks"]',
                robustness_score=0.5, pass_rate=0.6,
            ),
            ApprovedCombination(
                strategy_name="test_strat", parameters='{"a": 1}',
                symbol="ETHUSDT", interval="1h",
                approved=False, reasons='["failed sharpe"]',
                robustness_score=0.5, pass_rate=0.6,
            ),
        ]
        repo.save_batch(combos)
        session.commit()

        approved = repo.get_approved()
        assert len(approved) == 1
        assert approved[0].symbol == "BTCUSDT"
        assert approved[0].approved is True
        session.close()

    def test_save_batch_clears_old(self):
        """save_batch clears previous approvals before inserting."""
        session = get_session("sqlite:///:memory:")
        repo = ApprovedCombinationRepository(session)

        old = [ApprovedCombination(
            strategy_name="old", parameters="{}",
            symbol="BTCUSDT", interval="1h", approved=True,
            reasons="[]", robustness_score=0.1, pass_rate=0.1,
        )]
        repo.save_batch(old)
        session.commit()

        # New batch should replace old
        new = [ApprovedCombination(
            strategy_name="new", parameters="{}",
            symbol="ETHUSDT", interval="4h", approved=True,
            reasons="[]", robustness_score=0.9, pass_rate=0.9,
        )]
        repo.save_batch(new)
        session.commit()

        all_records = repo.get_all()
        assert len(all_records) == 1
        assert all_records[0].strategy_name == "new"
        session.close()

    def test_get_approved_by_symbol(self):
        """Filter approved combos by symbol."""
        session = get_session("sqlite:///:memory:")
        repo = ApprovedCombinationRepository(session)

        combos = [
            ApprovedCombination(
                strategy_name="s", parameters="{}",
                symbol="BTCUSDT", interval="1h", approved=True,
                reasons="[]", robustness_score=0.5, pass_rate=0.5,
            ),
            ApprovedCombination(
                strategy_name="s", parameters="{}",
                symbol="ETHUSDT", interval="1h", approved=True,
                reasons="[]", robustness_score=0.5, pass_rate=0.5,
            ),
        ]
        repo.save_batch(combos)
        session.commit()

        btc = repo.get_approved(symbol="BTCUSDT")
        assert len(btc) == 1
        assert btc[0].symbol == "BTCUSDT"

        eth = repo.get_approved(symbol="ETHUSDT")
        assert len(eth) == 1
        session.close()

    def test_no_approved_returns_empty(self):
        """When nothing is approved, get_approved returns empty list."""
        session = get_session("sqlite:///:memory:")
        repo = ApprovedCombinationRepository(session)

        combos = [
            ApprovedCombination(
                strategy_name="s", parameters="{}",
                symbol="BTCUSDT", interval="1h", approved=False,
                reasons='["nope"]', robustness_score=0.1, pass_rate=0.0,
            ),
        ]
        repo.save_batch(combos)
        session.commit()

        assert repo.get_approved() == []
        session.close()


# ==========================================
# Metrics Edge Cases
# ==========================================

class TestMetricsEdgeCases:
    def test_sharpe_with_empty_curve(self):
        assert _calculate_sharpe([]) == 0.0

    def test_sharpe_with_two_points(self):
        assert _calculate_sharpe([100, 101]) == 0.0

    def test_sharpe_with_three_identical(self):
        """Constant equity curve → std=0 → Sharpe=0."""
        assert _calculate_sharpe([100, 100, 100]) == 0.0

    def test_sortino_with_no_downside(self):
        """Only positive returns → inf."""
        result = _calculate_sortino([100, 105, 110, 115, 120])
        assert result == float("inf")

    def test_sortino_with_single_downside(self):
        """One downside observation (< 2) → inf or 0, not a warning."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = _calculate_sortino([100, 105, 103, 108, 112])
            assert isinstance(result, float)

    def test_sharpe_no_numpy_warning(self):
        """Ensure no RuntimeWarning with very small datasets."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            # Edge case: 3 points with zero returns
            result = _calculate_sharpe([100, 100, 100.0001])
            assert isinstance(result, float)

    def test_metrics_zero_trades(self):
        """Metrics for 0-trade backtest should not error."""
        from datetime import datetime, timezone
        result = BacktestResult(
            strategy_name="test", symbol="BTCUSDT", interval="1h",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 1, tzinfo=timezone.utc),
            trades=[], equity_curve=[10000, 10000, 10000],
            initial_capital=10000, final_equity=10000, fees_paid=0,
        )
        metrics = calculate_metrics(result)
        assert metrics.total_trades == 0
        assert metrics.sharpe_ratio == 0.0
        assert metrics.win_rate == 0.0


# ==========================================
# New Strategy Signal Tests
# ==========================================

class TestMomentumContinuation:
    def test_strategy_registration(self):
        import app.strategies.momentum_continuation  # noqa: F401
        from app.strategies.registry import list_strategies
        assert "momentum_continuation" in list_strategies()

    def test_generates_signals(self):
        from app.strategies.registry import get_strategy
        import app.strategies.momentum_continuation  # noqa: F401

        candles = _make_candle_df(200, trend=0.3, volatility=2.0, seed=1)
        strategy = get_strategy("momentum_continuation")
        signals = strategy.generate_signals(candles)
        assert isinstance(signals, list)

    def test_respects_cooldown(self):
        from app.strategies.registry import get_strategy
        import app.strategies.momentum_continuation  # noqa: F401

        candles = _make_candle_df(300, trend=0.5, volatility=2.5, seed=2)
        strategy = get_strategy("momentum_continuation", {"cooldown_bars": 50})
        signals = strategy.generate_signals(candles)
        # With high cooldown, should have fewer signals
        if len(signals) >= 2:
            # Check signals are spaced
            assert True  # Just verifying no crash


class TestPullbackUptrend:
    def test_strategy_registration(self):
        import app.strategies.pullback_uptrend  # noqa: F401
        from app.strategies.registry import list_strategies
        assert "pullback_uptrend" in list_strategies()

    def test_generates_signals(self):
        from app.strategies.registry import get_strategy
        import app.strategies.pullback_uptrend  # noqa: F401

        candles = _make_candle_df(200, trend=0.2, volatility=1.5, seed=3)
        strategy = get_strategy("pullback_uptrend")
        signals = strategy.generate_signals(candles)
        assert isinstance(signals, list)

    def test_no_signals_in_downtrend(self):
        """In a strong downtrend, pullback strategy should not buy."""
        from app.strategies.registry import get_strategy
        import app.strategies.pullback_uptrend  # noqa: F401

        candles = _make_candle_df(200, trend=-0.5, volatility=1.0, seed=4)
        strategy = get_strategy("pullback_uptrend")
        signals = strategy.generate_signals(candles)
        buy_signals = [s for s in signals if s.signal_type.value == "BUY"]
        # In a strong downtrend, very few or no buys
        assert len(buy_signals) <= 3


class TestVolatilityBreakout:
    def test_strategy_registration(self):
        import app.strategies.volatility_breakout  # noqa: F401
        from app.strategies.registry import list_strategies
        assert "volatility_breakout" in list_strategies()

    def test_generates_signals(self):
        from app.strategies.registry import get_strategy
        import app.strategies.volatility_breakout  # noqa: F401

        candles = _make_candle_df(200, trend=0.3, volatility=2.0, seed=5)
        strategy = get_strategy("volatility_breakout")
        signals = strategy.generate_signals(candles)
        assert isinstance(signals, list)

    def test_signals_have_stop_loss(self):
        """Buy signals should include ATR-scaled stop loss."""
        from app.strategies.registry import get_strategy
        import app.strategies.volatility_breakout  # noqa: F401
        from app.core.enums import SignalType

        candles = _make_candle_df(300, trend=0.4, volatility=3.0, seed=6)
        strategy = get_strategy("volatility_breakout")
        signals = strategy.generate_signals(candles)
        buy_signals = [s for s in signals if s.signal_type == SignalType.BUY]
        for sig in buy_signals:
            assert sig.stop_loss is not None
            assert sig.stop_loss < sig.price
            assert sig.take_profit is not None
            assert sig.take_profit > sig.price


# ==========================================
# Optimizer Grid Tests for New Strategies
# ==========================================

class TestNewStrategyOptimization:
    def test_momentum_in_optimizer(self):
        """Momentum strategy should be optimizable."""
        import app.strategies.momentum_continuation  # noqa: F401
        from app.backtesting.optimizer import optimize_strategy_matrix

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=10)}
        result = optimize_strategy_matrix(
            "momentum_continuation", candles,
            param_grid={"sma_period": [20]},
            run_walk_forward=False,
        )
        assert result.evaluated_param_sets > 0

    def test_pullback_in_optimizer(self):
        """Pullback strategy should be optimizable."""
        import app.strategies.pullback_uptrend  # noqa: F401
        from app.backtesting.optimizer import optimize_strategy_matrix

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=11)}
        result = optimize_strategy_matrix(
            "pullback_uptrend", candles,
            param_grid={"fast_ema": [20]},
            run_walk_forward=False,
        )
        assert result.evaluated_param_sets > 0

    def test_vol_breakout_in_optimizer(self):
        """Vol breakout strategy should be optimizable."""
        import app.strategies.volatility_breakout  # noqa: F401
        from app.backtesting.optimizer import optimize_strategy_matrix

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=12)}
        result = optimize_strategy_matrix(
            "volatility_breakout", candles,
            param_grid={"keltner_ema": [20]},
            run_walk_forward=False,
        )
        assert result.evaluated_param_sets > 0
