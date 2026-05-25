"""Tests for multi-dataset matrix evaluation, config-driven thresholds, and symbol parsing."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.backtesting.metrics import PerformanceMetrics
from app.backtesting.validation import (
    QualificationThresholds,
    check_qualification,
)
from app.backtesting.matrix_eval import (
    StrategyMatrixResult,
    evaluate_matrix,
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


def _make_candle_df(n=500, start_price=100.0, trend=0.0, volatility=1.0, seed=42) -> pd.DataFrame:
    np.random.seed(seed)
    prices = [start_price]
    for _ in range(n - 1):
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


def _winning_metrics(name="good") -> PerformanceMetrics:
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


# ---------- Multi-symbol input parsing ----------

class TestConfigParsing:
    def test_backtest_symbols_csv(self):
        from app.config.settings import Settings
        s = Settings(
            _env_file=None,
            backtest_symbols="BTCUSDT,ETHUSDT,BNBUSDT",
            backtest_intervals="1h,4h",
        )
        assert s.backtest_symbols == ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        assert s.backtest_intervals == ["1h", "4h"]

    def test_backtest_symbols_json(self):
        from app.config.settings import Settings
        s = Settings(
            _env_file=None,
            backtest_symbols='["BTCUSDT","ETHUSDT"]',
            backtest_intervals='["15m","1h"]',
        )
        assert s.backtest_symbols == ["BTCUSDT", "ETHUSDT"]
        assert s.backtest_intervals == ["15m", "1h"]

    def test_backtest_symbols_list(self):
        from app.config.settings import Settings
        s = Settings(
            _env_file=None,
            backtest_symbols=["BTCUSDT", "ETHUSDT"],
            backtest_intervals=["1h"],
        )
        assert s.backtest_symbols == ["BTCUSDT", "ETHUSDT"]
        assert s.backtest_intervals == ["1h"]

    def test_invalid_interval_rejected(self):
        from app.config.settings import Settings
        with pytest.raises(Exception):
            Settings(_env_file=None, backtest_intervals="1hr,4h")

    def test_qualification_defaults(self):
        from app.config.settings import Settings
        s = Settings(_env_file=None)
        assert s.qual_min_return_pct == 0.0
        assert s.qual_min_sharpe == 0.0
        assert s.qual_min_trades == 5
        assert s.qual_max_drawdown_pct == 0.30
        assert s.qual_min_profit_factor == 0.8
        assert s.qual_min_oos_consistency == 0.0
        assert s.qual_min_benchmark_alpha_pct == 0.0

    def test_qualification_custom_values(self):
        from app.config.settings import Settings
        s = Settings(
            _env_file=None,
            qual_min_return_pct=0.05,
            qual_min_sharpe=1.0,
            qual_min_trades=10,
            qual_max_drawdown_pct=0.15,
            qual_min_benchmark_alpha_pct=0.02,
        )
        assert s.qual_min_return_pct == 0.05
        assert s.qual_min_sharpe == 1.0
        assert s.qual_min_trades == 10
        assert s.qual_max_drawdown_pct == 0.15
        assert s.qual_min_benchmark_alpha_pct == 0.02


# ---------- Benchmark alpha threshold ----------

class TestBenchmarkAlphaThreshold:
    def test_alpha_pass(self):
        metrics = _winning_metrics()
        metrics.total_return_pct = 0.10
        thresholds = QualificationThresholds(min_benchmark_alpha_pct=0.02)
        result = check_qualification(metrics, thresholds=thresholds, benchmark_return_pct=0.05)
        assert result.qualified is True

    def test_alpha_fail(self):
        metrics = _winning_metrics()
        metrics.total_return_pct = 0.03
        thresholds = QualificationThresholds(min_benchmark_alpha_pct=0.05)
        result = check_qualification(metrics, thresholds=thresholds, benchmark_return_pct=0.10)
        assert result.qualified is False
        assert any("Alpha" in f for f in result.failures)

    def test_alpha_zero_threshold_skipped(self):
        """When min_benchmark_alpha_pct=0, alpha check is skipped."""
        metrics = _winning_metrics()
        metrics.total_return_pct = -0.05
        thresholds = QualificationThresholds(min_benchmark_alpha_pct=0.0)
        result = check_qualification(metrics, thresholds=thresholds, benchmark_return_pct=0.10)
        # Should fail for return but NOT for alpha
        assert not any("Alpha" in f for f in result.failures)


# ---------- Matrix evaluation ----------

class TestMatrixEvaluation:
    def test_matrix_eval_basic(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, trend=0.05, seed=42),
            ("ETHUSDT", "1h"): _make_candle_df(200, trend=-0.02, seed=43),
        }

        result = evaluate_matrix(
            candles, run_walk_forward=False, initial_capital=10000,
        )
        assert len(result.strategies) > 0
        assert result.total_datasets == 2
        assert result.symbols_evaluated == ["BTCUSDT", "ETHUSDT"]
        assert result.intervals_evaluated == ["1h"]
        assert result.best_ranked is not None

    def test_matrix_eval_per_dataset(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, trend=0.05, seed=42),
        }

        result = evaluate_matrix(candles, run_walk_forward=False)
        for sr in result.strategies:
            assert sr.datasets_evaluated >= 1
            assert len(sr.per_dataset) == sr.datasets_evaluated
            for dr in sr.per_dataset:
                assert dr.symbol == "BTCUSDT"
                assert dr.interval == "1h"
                assert dr.benchmark is not None
                assert dr.qualification is not None

    def test_matrix_eval_with_walk_forward(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(500, trend=0.05, seed=42),
        }

        result = evaluate_matrix(candles, run_walk_forward=True, wf_windows=2)
        assert result.total_datasets == 1
        # Should have WF results for strategies that had enough data
        for sr in result.strategies:
            for dr in sr.per_dataset:
                if dr.wf_result:
                    assert dr.wf_result.total_windows >= 1

    def test_matrix_eval_empty_data(self):
        result = evaluate_matrix({})
        assert len(result.strategies) == 0 or all(s.datasets_evaluated == 0 for s in result.strategies)

    def test_matrix_eval_small_dataset_skipped(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(20, seed=42),
        }

        result = evaluate_matrix(candles, run_walk_forward=False)
        for sr in result.strategies:
            # Datasets too small should be skipped
            assert sr.datasets_evaluated == 0

    def test_matrix_ranking_sorted(self):
        import app.strategies.ema_atr  # noqa: F401
        import app.strategies.rsi_mean_reversion  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(300, trend=0.05, seed=42),
            ("ETHUSDT", "1h"): _make_candle_df(300, trend=-0.01, seed=43),
        }

        result = evaluate_matrix(candles, run_walk_forward=False)
        # Should be sorted by consistency then sharpe then return
        scores = [s.consistency_score for s in result.strategies]
        assert scores == sorted(scores, reverse=True)


# ---------- Cross-dataset qualification ----------

class TestCrossDatasetQualification:
    def test_all_qualified(self):
        import app.strategies.ema_atr  # noqa: F401

        # Use very lenient thresholds so strategies can pass
        lenient = QualificationThresholds(
            min_total_return_pct=-1.0,
            min_sharpe_ratio=-10.0,
            min_total_trades=0,
            max_drawdown_pct=1.0,
            min_profit_factor=0.0,
        )
        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, seed=42),
        }

        result = evaluate_matrix(candles, thresholds=lenient, run_walk_forward=False)
        for sr in result.strategies:
            if sr.datasets_evaluated > 0:
                assert sr.all_qualified is True

    def test_none_qualified_strict(self):
        import app.strategies.ema_atr  # noqa: F401

        strict = QualificationThresholds(
            min_total_return_pct=5.0,    # 500% return requirement
            min_sharpe_ratio=10.0,
        )
        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, seed=42),
        }

        result = evaluate_matrix(candles, thresholds=strict, run_walk_forward=False)
        assert result.best_qualified is None
        for sr in result.strategies:
            assert sr.all_qualified is False

    def test_partial_qualification(self):
        import app.strategies.ema_atr  # noqa: F401

        # One easy dataset, one hard dataset
        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, trend=0.5, seed=42),   # strong trend
            ("ETHUSDT", "1h"): _make_candle_df(200, trend=-0.5, seed=43),   # strong downtrend
        }

        result = evaluate_matrix(candles, run_walk_forward=False)
        # It is possible all or none qualify; make sure the count is bounded.
        for sr in result.strategies:
            assert sr.datasets_qualified <= sr.datasets_evaluated


# ---------- Paper-trade auto-selection with config thresholds ----------

class TestAutoSelectionConfigThresholds:
    def test_qualified_winner_persisted_and_loaded(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="good_strat", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=True, composite_score=0.9,
            total_return_pct=0.05, sharpe_ratio=1.5,
        ))
        session.commit()

        winner = repo.get_latest_qualified_winner()
        assert winner is not None
        assert winner.strategy_name == "good_strat"
        assert winner.qualified is True
        session.close()

    def test_unqualified_blocked_from_auto(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="bad_strat", parameters="{}",
            symbol="BTCUSDT", interval="1h",
            qualified=False, composite_score=0.3,
            qualification_failures='["Return -5.00% < min 0.00%"]',
        ))
        session.commit()

        assert repo.get_latest_qualified_winner() is None
        assert repo.get_latest_winner() is not None
        session.close()


# ---------- Summary dict ----------

class TestStrategyMatrixResult:
    def test_summary_dict(self):
        sr = StrategyMatrixResult(
            strategy_name="test",
            datasets_evaluated=3,
            datasets_qualified=2,
            avg_return=0.05,
            avg_sharpe=1.2,
            avg_drawdown=0.08,
            max_drawdown=0.12,
            avg_alpha=0.02,
            consistency_score=0.67,
            all_qualified=False,
        )
        d = sr.summary_dict()
        assert d["strategy_name"] == "test"
        assert d["datasets_evaluated"] == 3
        assert d["avg_return"] == 0.05
        assert d["all_qualified"] is False
