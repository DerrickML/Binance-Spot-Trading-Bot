"""Tests for walk-forward validation, benchmark, and qualification thresholds."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.backtesting.metrics import PerformanceMetrics
from app.backtesting.validation import (
    QualificationThresholds,
    WalkForwardResult,
    check_qualification,
    compute_benchmark,
    walk_forward_validate,
)
from app.persistence.db import init_db, get_session, reset_engine
from app.persistence.models import SelectedStrategy
from app.persistence.repositories import SelectedStrategyRepository


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_candle_df(n=500, start_price=100.0, trend=0.0, volatility=1.0) -> pd.DataFrame:
    """Generate synthetic OHLCV candle data with controllable trend and volatility."""
    np.random.seed(42)
    prices = [start_price]
    for i in range(n - 1):
        change = trend + np.random.normal(0, volatility)
        prices.append(max(prices[-1] + change, 1.0))

    rows = []
    for i, close in enumerate(prices):
        high = close + abs(np.random.normal(0, volatility * 0.5))
        low = close - abs(np.random.normal(0, volatility * 0.5))
        o = close + np.random.normal(0, volatility * 0.2)
        rows.append({
            "open_time": i * 3600000,
            "close_time": (i + 1) * 3600000 - 1,
            "open": o,
            "high": max(high, o, close),
            "low": min(low, o, close),
            "close": close,
            "volume": 1000 + np.random.randint(0, 500),
        })
    return pd.DataFrame(rows)


def _winning_metrics(name="good_strategy") -> PerformanceMetrics:
    """Create metrics that pass default qualification."""
    return PerformanceMetrics(
        strategy_name=name, symbol="BTCUSDT",
        net_profit=500, total_return_pct=0.05, max_drawdown_pct=0.10,
        sharpe_ratio=1.5, sortino_ratio=2.0, profit_factor=1.8,
        win_rate=0.55, total_trades=20, avg_trade_return_pct=0.003,
        winning_trades=11, losing_trades=9,
        avg_win_pct=0.02, avg_loss_pct=-0.01,
        max_consecutive_losses=3, fees_paid=50,
        initial_capital=10000, final_equity=10500,
    )


def _losing_metrics(name="bad_strategy") -> PerformanceMetrics:
    """Create metrics that fail qualification."""
    return PerformanceMetrics(
        strategy_name=name, symbol="BTCUSDT",
        net_profit=-500, total_return_pct=-0.05, max_drawdown_pct=0.35,
        sharpe_ratio=-0.5, sortino_ratio=-0.3, profit_factor=0.5,
        win_rate=0.30, total_trades=10, avg_trade_return_pct=-0.005,
        winning_trades=3, losing_trades=7,
        avg_win_pct=0.01, avg_loss_pct=-0.02,
        max_consecutive_losses=5, fees_paid=30,
        initial_capital=10000, final_equity=9500,
    )


# ---------- Benchmark Tests ----------

class TestBenchmark:
    def test_basic_benchmark(self):
        df = _make_candle_df(100, start_price=100, trend=0.1)
        bm = compute_benchmark(df, "BTCUSDT")
        assert bm.symbol == "BTCUSDT"
        assert bm.start_price == float(df.iloc[0]["close"])
        assert bm.end_price == float(df.iloc[-1]["close"])
        assert bm.total_return_pct != 0.0

    def test_benchmark_uptrend(self):
        df = _make_candle_df(200, start_price=100, trend=0.5)
        bm = compute_benchmark(df, "BTCUSDT")
        assert bm.total_return_pct > 0.0
        assert bm.max_drawdown_pct >= 0.0

    def test_benchmark_downtrend(self):
        df = _make_candle_df(200, start_price=100, trend=-0.2)
        bm = compute_benchmark(df, "BTCUSDT")
        assert bm.total_return_pct < 0.0

    def test_benchmark_empty_data(self):
        bm = compute_benchmark(pd.DataFrame(), "BTCUSDT")
        assert bm.total_return_pct == 0.0
        assert bm.max_drawdown_pct == 0.0

    def test_benchmark_single_candle(self):
        df = _make_candle_df(1)
        bm = compute_benchmark(df, "BTCUSDT")
        assert bm.total_return_pct == 0.0


# ---------- Qualification Tests ----------

class TestQualification:
    def test_winning_strategy_qualifies(self):
        metrics = _winning_metrics()
        result = check_qualification(metrics)
        assert result.qualified is True
        assert result.failures == []

    def test_losing_strategy_fails(self):
        metrics = _losing_metrics()
        result = check_qualification(metrics)
        assert result.qualified is False
        assert len(result.failures) > 0

    def test_negative_return_fails(self):
        metrics = _winning_metrics()
        metrics.total_return_pct = -0.01
        result = check_qualification(metrics)
        assert result.qualified is False
        assert any("Return" in f for f in result.failures)

    def test_negative_sharpe_fails(self):
        metrics = _winning_metrics()
        metrics.sharpe_ratio = -0.5
        result = check_qualification(metrics)
        assert result.qualified is False
        assert any("Sharpe" in f for f in result.failures)

    def test_low_trade_count_fails(self):
        metrics = _winning_metrics()
        metrics.total_trades = 2
        result = check_qualification(metrics)
        assert result.qualified is False
        assert any("Trades" in f for f in result.failures)

    def test_high_drawdown_fails(self):
        metrics = _winning_metrics()
        metrics.max_drawdown_pct = 0.40
        result = check_qualification(metrics)
        assert result.qualified is False
        assert any("Drawdown" in f for f in result.failures)

    def test_low_profit_factor_fails(self):
        metrics = _winning_metrics()
        metrics.profit_factor = 0.5
        result = check_qualification(metrics)
        assert result.qualified is False
        assert any("Profit factor" in f for f in result.failures)

    def test_custom_thresholds(self):
        metrics = _winning_metrics()
        strict = QualificationThresholds(
            min_total_return_pct=0.10,
            min_sharpe_ratio=2.0,
        )
        result = check_qualification(metrics, thresholds=strict)
        assert result.qualified is False
        assert len(result.failures) >= 1

    def test_oos_consistency_check(self):
        metrics = _winning_metrics()
        wf = WalkForwardResult(
            strategy_name="test", symbol="BTCUSDT", interval="1h",
            oos_consistency=0.3, total_windows=3,
        )
        thresholds = QualificationThresholds(min_oos_consistency=0.5)
        result = check_qualification(metrics, thresholds=thresholds, wf_result=wf)
        assert result.qualified is False
        assert any("OOS" in f for f in result.failures)

    def test_all_failures_listed(self):
        metrics = _losing_metrics()
        result = check_qualification(metrics)
        # Should have multiple failures
        assert len(result.failures) >= 3

    def test_reason_string(self):
        metrics = _winning_metrics()
        result = check_qualification(metrics)
        assert result.reason() == "QUALIFIED"

        metrics2 = _losing_metrics()
        result2 = check_qualification(metrics2)
        assert result2.reason().startswith("UNQUALIFIED:")


# ---------- Walk-Forward Tests ----------

class TestWalkForward:
    def test_walk_forward_basic(self):
        import app.strategies.ema_atr  # noqa: F401
        from app.strategies.registry import get_strategy
        strategy = get_strategy("ema_atr_crossover")
        df = _make_candle_df(500, start_price=100, trend=0.05)

        result = walk_forward_validate(strategy, df, symbol="BTCUSDT", n_windows=3)
        assert result.strategy_name == "ema_atr_crossover"
        assert result.total_windows > 0
        assert len(result.windows) == result.total_windows

    def test_walk_forward_window_structure(self):
        import app.strategies.ema_atr  # noqa: F401
        from app.strategies.registry import get_strategy
        strategy = get_strategy("ema_atr_crossover")
        df = _make_candle_df(500, start_price=100, trend=0.05)

        result = walk_forward_validate(strategy, df, n_windows=2)
        for w in result.windows:
            assert w.train_size > 0
            assert w.test_size > 0
            assert w.train_metrics is not None
            assert w.test_metrics is not None
            assert w.test_benchmark is not None

    def test_walk_forward_insufficient_data(self):
        import app.strategies.ema_atr  # noqa: F401
        from app.strategies.registry import get_strategy
        strategy = get_strategy("ema_atr_crossover")
        df = _make_candle_df(20)

        result = walk_forward_validate(strategy, df)
        assert result.total_windows == 0

    def test_walk_forward_summarize(self):
        import app.strategies.ema_atr  # noqa: F401
        from app.strategies.registry import get_strategy
        strategy = get_strategy("ema_atr_crossover")
        df = _make_candle_df(500, start_price=100, trend=0.05)

        result = walk_forward_validate(strategy, df, n_windows=2)
        summary = result.summarize()
        assert "strategy_name" in summary
        assert "avg_test_return" in summary
        assert "oos_consistency" in summary
        assert "degradation_ratio" in summary


# ---------- Qualified Winner Retrieval ----------

class TestQualifiedWinnerRetrieval:
    def test_qualified_winner_found(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="good_strat", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=True, composite_score=0.8,
        ))
        session.commit()

        winner = repo.get_latest_qualified_winner()
        assert winner is not None
        assert winner.strategy_name == "good_strat"
        assert winner.qualified is True
        session.close()

    def test_unqualified_not_returned_by_qualified(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="bad_strat", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=False, composite_score=0.5,
        ))
        session.commit()

        winner = repo.get_latest_qualified_winner()
        assert winner is None

        # But get_latest_winner still returns it
        any_winner = repo.get_latest_winner()
        assert any_winner is not None
        assert any_winner.qualified is False
        session.close()

    def test_latest_qualified_skips_old_unqualified(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="qualified_old", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=True, composite_score=0.7,
        ))
        session.commit()

        repo.save(SelectedStrategy(
            strategy_name="unqualified_new", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=False, composite_score=0.9,
        ))
        session.commit()

        # get_latest_winner returns the newer unqualified one
        latest = repo.get_latest_winner()
        assert latest.strategy_name == "unqualified_new"

        # get_latest_qualified_winner returns the older qualified one
        qualified = repo.get_latest_qualified_winner()
        assert qualified.strategy_name == "qualified_old"
        session.close()

    def test_no_qualified_winner_returns_none(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)
        assert repo.get_latest_qualified_winner() is None
        session.close()

    def test_qualified_with_fields(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="validated_strat", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=True,
            benchmark_return_pct=0.05,
            oos_consistency=0.67,
            degradation_ratio=0.85,
            validation_windows=3,
            qualification_failures="[]",
        ))
        session.commit()

        winner = repo.get_latest_qualified_winner()
        assert winner.benchmark_return_pct == 0.05
        assert winner.oos_consistency == 0.67
        assert winner.validation_windows == 3
        session.close()


# ---------- Auto-Selection Rejection ----------

class TestAutoSelectionRejection:
    def test_auto_resolution_skips_unqualified(self):
        """When only unqualified winners exist, resolution should fall to default."""
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="bad_strat", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=False,
            qualification_failures='["Return -5.00% < min 0.00%"]',
        ))
        session.commit()

        # Qualified lookup returns None
        assert repo.get_latest_qualified_winner() is None
        # Unqualified lookup returns the record
        assert repo.get_latest_winner() is not None
        session.close()
