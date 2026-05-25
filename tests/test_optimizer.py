"""Tests for matrix-wide parameter optimization and strategy filter improvements."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.backtesting.optimizer import (
    DEFAULT_PARAM_GRIDS,
    OptimizationResult,
    ParamSetResult,
    compute_robustness_score,
    generate_param_combinations,
    get_param_grid,
    optimize_strategy_matrix,
)
from app.backtesting.validation import QualificationThresholds
from app.persistence.db import init_db, reset_engine


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_candle_df(n=300, start_price=100.0, trend=0.0, volatility=1.0, seed=42) -> pd.DataFrame:
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
            "open": max(o, 0.1),
            "high": max(high, o, close),
            "low": min(low, o, close),
            "close": close,
            "volume": 1000 + np.random.randint(0, 500),
        })
    return pd.DataFrame(rows)


# ---------- Parameter grid generation ----------

class TestParamGridGeneration:
    def test_generate_combinations(self):
        grid = {"a": [1, 2], "b": [10, 20]}
        combos = generate_param_combinations(grid)
        assert len(combos) == 4
        assert {"a": 1, "b": 10} in combos
        assert {"a": 2, "b": 20} in combos

    def test_single_param_grid(self):
        grid = {"x": [1, 2, 3]}
        combos = generate_param_combinations(grid)
        assert len(combos) == 3

    def test_empty_grid(self):
        combos = generate_param_combinations({})
        assert len(combos) == 1  # One empty dict

    def test_default_grids_exist(self):
        assert "ema_atr_crossover" in DEFAULT_PARAM_GRIDS
        assert "rsi_mean_reversion" in DEFAULT_PARAM_GRIDS
        assert "bollinger_mean_reversion" in DEFAULT_PARAM_GRIDS
        assert "breakout" in DEFAULT_PARAM_GRIDS
        assert "regime_adaptive" in DEFAULT_PARAM_GRIDS
        assert "hybrid_grid_dca" in DEFAULT_PARAM_GRIDS

    def test_hybrid_grid_dca_profile_sizes(self):
        fast = len(generate_param_combinations(get_param_grid("hybrid_grid_dca", "fast")))
        standard = len(generate_param_combinations(get_param_grid("hybrid_grid_dca", "standard")))
        deep = len(generate_param_combinations(get_param_grid("hybrid_grid_dca", "deep")))

        assert fast > 0
        assert fast <= standard < deep
        assert get_param_grid("hybrid_grid_dca", "standard")["max_grid_levels"] == [2, 3]

    def test_unknown_optimizer_profile_rejected(self):
        with pytest.raises(ValueError):
            get_param_grid("hybrid_grid_dca", "unknown")

    def test_default_grid_sizes_reasonable(self):
        for name, grid in DEFAULT_PARAM_GRIDS.items():
            total = 1
            for vals in grid.values():
                total *= len(vals)
            assert total <= 500, f"{name} grid too large: {total}"
            assert total >= 3, f"{name} grid too small: {total}"


# ---------- Robustness scoring ----------

class TestRobustnessScoring:
    def test_perfect_score(self):
        psr = ParamSetResult(
            strategy_name="test", params={},
            datasets_evaluated=5, consistency_score=1.0,
            avg_sharpe=2.0, max_drawdown=0.05, avg_alpha=0.10,
            total_trades=200,
        )
        score = compute_robustness_score(psr)
        assert score > 0.5  # Good strategy should score well

    def test_bad_score(self):
        psr = ParamSetResult(
            strategy_name="test", params={},
            datasets_evaluated=5, consistency_score=0.0,
            avg_sharpe=-2.0, max_drawdown=0.40, avg_alpha=-0.10,
            total_trades=5,
        )
        score = compute_robustness_score(psr)
        assert score < 0.1

    def test_consistency_dominates(self):
        """Consistency (40% weight) should be the largest factor."""
        high_consistency = ParamSetResult(
            strategy_name="test", params={},
            consistency_score=1.0, avg_sharpe=0.5, max_drawdown=0.10,
            avg_alpha=0.0, total_trades=50,
        )
        low_consistency = ParamSetResult(
            strategy_name="test", params={},
            consistency_score=0.0, avg_sharpe=0.5, max_drawdown=0.10,
            avg_alpha=0.0, total_trades=50,
        )
        assert compute_robustness_score(high_consistency) > compute_robustness_score(low_consistency)

    def test_drawdown_penalty(self):
        low_dd = ParamSetResult(
            strategy_name="test", params={},
            consistency_score=0.5, avg_sharpe=1.0, max_drawdown=0.05,
            avg_alpha=0.0, total_trades=50,
        )
        high_dd = ParamSetResult(
            strategy_name="test", params={},
            consistency_score=0.5, avg_sharpe=1.0, max_drawdown=0.30,
            avg_alpha=0.0, total_trades=50,
        )
        assert compute_robustness_score(low_dd) > compute_robustness_score(high_dd)


# ---------- Matrix-wide optimization ----------

class TestMatrixOptimization:
    def test_optimize_single_strategy(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, trend=0.05, seed=42),
        }
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [8, 12], "slow_ema": [21, 26]},
        )
        assert result.strategy_name == "ema_atr_crossover"
        assert result.total_param_sets == 4
        assert len(result.param_results) == 4
        assert result.best_robust is not None

    def test_parallel_workers_preserve_result_count(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(120, trend=0.03, seed=42),
        }
        grid = {"fast_ema": [8, 12], "slow_ema": [21, 26]}
        serial = optimize_strategy_matrix(
            "ema_atr_crossover",
            candles,
            param_grid=grid,
            run_walk_forward=False,
            workers=1,
        )
        parallel = optimize_strategy_matrix(
            "ema_atr_crossover",
            candles,
            param_grid=grid,
            run_walk_forward=False,
            workers=2,
        )

        assert len(parallel.param_results) == len(serial.param_results) == 4
        assert parallel.total_param_sets == serial.total_param_sets == 4

    def test_hybrid_grid_diagnostics_in_optimizer_summary(self):
        import app.strategies.hybrid_grid_dca  # noqa: F401

        rows = []
        for idx in range(180):
            cycle = idx % 30
            close = 100.0
            if 12 <= cycle <= 16:
                close = 100.0 - (cycle - 11) * 1.5
            elif 17 <= cycle <= 20:
                close = 95.0 + (cycle - 16) * 1.8
            rows.append({
                "open_time": idx * 3600000,
                "close_time": (idx + 1) * 3600000 - 1,
                "open": close,
                "high": close * 1.025,
                "low": close * 0.975,
                "close": close,
                "volume": 1000,
            })
        candles = {
            ("BTCUSDT", "1h"): pd.DataFrame(rows),
        }
        result = optimize_strategy_matrix(
            "hybrid_grid_dca",
            candles,
            param_grid={
                "anchor_period": [20],
                "trend_filter_period": [30],
                "grid_spacing_pct": [0.01],
                "max_grid_levels": [2],
                "base_order_pct": [0.05],
                "dca_size_multiplier": [1.25],
                "take_profit_pct": [0.01],
                "stop_loss_pct": [0.12],
                "max_grid_allocation_pct": [0.25],
                "cooldown_bars": [1],
                "min_volatility_pct": [0.0],
                "atr_period": [14],
                "atr_grid_spacing_mult": [0.5],
                "min_trend_slope_pct": [-1.0],
                "trend_slope_lookback": [5],
                "max_anchor_deviation_pct": [0.20],
                "take_profit_fee_buffer_pct": [0.0],
                "stop_cooldown_bars": [1],
                "scale_in_requires_below_average": [True],
                "min_periods": [30],
            },
            run_walk_forward=False,
        )

        assert result.param_results
        summary = result.param_results[0].summary_dict()
        assert "grid_diagnostics" in summary
        assert "signals" in summary["grid_diagnostics"]

    def test_optimize_multiple_datasets(self):
        import app.strategies.bollinger_mean_reversion  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, trend=0.05, seed=42),
            ("ETHUSDT", "1h"): _make_candle_df(200, trend=-0.02, seed=43),
        }
        result = optimize_strategy_matrix(
            "bollinger_mean_reversion", candles,
            param_grid={"bb_period": [15, 20], "bb_std_dev": [1.8, 2.0]},
        )
        assert result.datasets_used == 2
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert psr.datasets_evaluated <= 2

    def test_ranking_by_robustness(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, trend=0.05, seed=42),
        }
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [8, 12, 16], "slow_ema": [21, 26]},
        )
        # Results should be sorted by robustness score
        scores = [p.robustness_score for p in result.param_results]
        assert scores == sorted(scores, reverse=True)

    def test_no_param_grid(self):
        result = optimize_strategy_matrix(
            "nonexistent_strategy", {},
        )
        assert result.total_param_sets == 0

    def test_max_combinations_cap(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, seed=42),
        }
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": list(range(5, 25)), "slow_ema": list(range(20, 50))},
            max_combinations=10,
        )
        # Should cap at max_combinations
        assert len(result.param_results) <= 10

    def test_qualification_across_datasets(self):
        import app.strategies.ema_atr  # noqa: F401

        # Lenient thresholds
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
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [8, 12]},
            thresholds=lenient,
        )
        # With lenient thresholds, some should qualify
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert psr.all_qualified is True


# ---------- Strategy v2 filter behavior ----------

class TestStrategyV2Filters:
    def test_ema_atr_cooldown_reduces_signals(self):
        import app.strategies.ema_atr  # noqa: F401
        from app.strategies.registry import get_strategy

        candles = _make_candle_df(300, volatility=2.0, seed=42)

        # No cooldown
        strat_fast = get_strategy("ema_atr_crossover", params={"cooldown_bars": 0, "min_slope_pct": 0})
        signals_fast = strat_fast.generate_signals(candles)

        # With cooldown
        strat_slow = get_strategy("ema_atr_crossover", params={"cooldown_bars": 10, "min_slope_pct": 0})
        signals_slow = strat_slow.generate_signals(candles)

        assert len(signals_slow) <= len(signals_fast)

    def test_rsi_cooldown_reduces_signals(self):
        from app.strategies.rsi_mean_reversion import RsiMeanReversionStrategy

        candles = _make_candle_df(300, volatility=2.0, seed=42)

        strat1 = RsiMeanReversionStrategy(params={"cooldown_bars": 0, "adx_max_for_mr": 100})
        strat2 = RsiMeanReversionStrategy(params={"cooldown_bars": 15, "adx_max_for_mr": 100})

        sig1 = strat1.generate_signals(candles)
        sig2 = strat2.generate_signals(candles)
        assert len(sig2) <= len(sig1)

    def test_breakout_min_distance_filter(self):
        from app.strategies.breakout import BreakoutStrategy

        candles = _make_candle_df(300, volatility=2.0, seed=42)

        strat_easy = BreakoutStrategy(params={"min_breakout_atr": 0.0, "cooldown_bars": 0})
        strat_strict = BreakoutStrategy(params={"min_breakout_atr": 1.0, "cooldown_bars": 0})

        sig1 = strat_easy.generate_signals(candles)
        sig2 = strat_strict.generate_signals(candles)
        assert len(sig2) <= len(sig1)

    def test_bollinger_rsi_confirmation(self):
        from app.strategies.bollinger_mean_reversion import BollingerMeanReversionStrategy

        candles = _make_candle_df(300, volatility=2.0, seed=42)

        # Very lenient RSI (almost no filtering)
        strat_lenient = BollingerMeanReversionStrategy(params={
            "rsi_oversold_confirm": 99, "rsi_overbought_confirm": 1, "cooldown_bars": 0
        })
        # Strict RSI requirement
        strat_strict = BollingerMeanReversionStrategy(params={
            "rsi_oversold_confirm": 20, "rsi_overbought_confirm": 80, "cooldown_bars": 0
        })

        sig1 = strat_lenient.generate_signals(candles)
        sig2 = strat_strict.generate_signals(candles)
        assert len(sig2) <= len(sig1)

    def test_regime_hysteresis(self):
        from app.strategies.regime_strategy import RegimeStrategy

        candles = _make_candle_df(300, volatility=2.0, seed=42)

        # Narrow hysteresis band (same as threshold)
        strat_no_hyst = RegimeStrategy(params={
            "adx_trend_threshold": 25, "adx_range_threshold": 25, "cooldown_bars": 0
        })
        # Wide hysteresis band
        strat_hyst = RegimeStrategy(params={
            "adx_trend_threshold": 30, "adx_range_threshold": 15, "cooldown_bars": 0
        })

        sig1 = strat_no_hyst.generate_signals(candles)
        sig2 = strat_hyst.generate_signals(candles)
        # Both should produce signals, hysteresis may reduce
        assert isinstance(sig1, list)
        assert isinstance(sig2, list)


# ---------- Report/export structure ----------

class TestOptimizationReportStructure:
    def test_param_set_summary_dict(self):
        psr = ParamSetResult(
            strategy_name="test", params={"a": 1, "b": 2},
            datasets_evaluated=3, datasets_qualified=2,
            avg_return=0.05, avg_sharpe=1.2, avg_alpha=0.02,
            max_drawdown=0.08, consistency_score=0.67,
            robustness_score=0.55, all_qualified=False,
        )
        d = psr.summary_dict()
        assert d["strategy_name"] == "test"
        assert "params" in d
        assert d["robustness_score"] == 0.55
        assert d["all_qualified"] is False

    def test_optimization_result_structure(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [8, 12]},
        )
        assert isinstance(result, OptimizationResult)
        assert result.strategy_name == "ema_atr_crossover"
        assert result.datasets_used == 1
        assert isinstance(result.param_results, list)
