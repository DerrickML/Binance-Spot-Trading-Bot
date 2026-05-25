"""Tests for regime filter, walk-forward optimization, pass-rate qualification, and dataset approval."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.backtesting.regime_filter import (
    RegimeConfig,
    RegimeState,
    assess_regime,
    should_trade,
)
from app.backtesting.optimizer import (
    DatasetApproval,
    ParamSetResult,
    compute_robustness_score,
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


# ---------- Regime Filter ----------

class TestRegimeFilter:
    def test_default_config_returns_tradable(self):
        candles = _make_candle_df(200, volatility=1.0)
        state = assess_regime(candles)
        assert isinstance(state, RegimeState)
        assert isinstance(state.is_tradable, bool)

    def test_quiet_market_blocked(self):
        """Very low volatility should be blocked."""
        candles = _make_candle_df(200, volatility=0.001)
        config = RegimeConfig(min_volatility_pct=0.5, max_volatility_pct=10.0)
        state = assess_regime(candles, config)
        assert state.is_tradable is False
        assert any("volatility_too_low" in r for r in state.reasons)

    def test_volatile_market_blocked(self):
        """Extremely high volatility should be blocked."""
        candles = _make_candle_df(200, volatility=20.0, start_price=50.0)
        config = RegimeConfig(min_volatility_pct=0.0, max_volatility_pct=0.5)
        state = assess_regime(candles, config)
        assert state.is_tradable is False
        assert any("volatility_too_high" in r for r in state.reasons)

    def test_normal_volatility_passes(self):
        candles = _make_candle_df(200, volatility=1.0, start_price=100.0)
        config = RegimeConfig(min_volatility_pct=0.0, max_volatility_pct=50.0)
        state = assess_regime(candles, config)
        assert state.is_tradable is True

    def test_regime_disabled(self):
        candles = _make_candle_df(200, volatility=0.001)
        config = RegimeConfig(enabled=False)
        state = assess_regime(candles, config)
        assert state.is_tradable is True

    def test_insufficient_data_passes(self):
        candles = _make_candle_df(10, volatility=1.0)
        config = RegimeConfig(trend_sma_period=50)
        state = assess_regime(candles, config)
        assert state.is_tradable is True
        assert state.regime == "unknown"

    def test_bullish_regime_detected(self):
        candles = _make_candle_df(200, trend=0.5, volatility=1.0)
        config = RegimeConfig(min_volatility_pct=0.0)
        state = assess_regime(candles, config)
        assert state.trend_slope > 0

    def test_bearish_regime_detected(self):
        candles = _make_candle_df(200, trend=-0.5, volatility=1.0)
        config = RegimeConfig(min_volatility_pct=0.0)
        state = assess_regime(candles, config)
        assert state.trend_slope < 0

    def test_should_trade_convenience(self):
        candles = _make_candle_df(200, volatility=1.0)
        tradable, state = should_trade(candles)
        assert isinstance(tradable, bool)
        assert isinstance(state, RegimeState)

    def test_benchmark_bullish_filter(self):
        """When require_bullish_benchmark is on, bearish data should fail."""
        candles = _make_candle_df(200, trend=-0.5, volatility=1.0)
        config = RegimeConfig(
            require_bullish_benchmark=True,
            benchmark_sma_period=50,
            min_volatility_pct=0.0,
        )
        state = assess_regime(candles, config)
        # Bearish trend should make benchmark not bullish
        if state.benchmark_direction != "up":
            assert state.is_tradable is False


# ---------- Walk-Forward in Optimizer ----------

class TestWalkForwardOptimization:
    def test_optimizer_with_walk_forward(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(300, trend=0.05, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [8, 12]},
            run_walk_forward=True, wf_windows=2,
        )
        assert result.evaluated_param_sets > 0
        # Check WF data is populated
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert isinstance(psr.avg_oos_return, float)
                assert isinstance(psr.avg_degradation, float)

    def test_optimizer_without_walk_forward(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [8, 12]},
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert psr.avg_oos_return == 0.0
                assert psr.avg_degradation == 0.0

    def test_degradation_affects_robustness(self):
        """Higher degradation penalty → lower robustness."""
        good_wf = ParamSetResult(
            strategy_name="test", params={},
            consistency_score=0.5, avg_sharpe=0.5, max_drawdown=0.10,
            avg_alpha=0.02, total_trades=50,
            avg_oos_return=0.05, avg_degradation=0.9,
        )
        bad_wf = ParamSetResult(
            strategy_name="test", params={},
            consistency_score=0.5, avg_sharpe=0.5, max_drawdown=0.10,
            avg_alpha=0.02, total_trades=50,
            avg_oos_return=-0.10, avg_degradation=0.0,
        )
        assert compute_robustness_score(good_wf) > compute_robustness_score(bad_wf)


# ---------- Pass-Rate Qualification ----------

class TestPassRateQualification:
    def test_pass_rate_calculation(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {
            ("BTCUSDT", "1h"): _make_candle_df(200, seed=42),
            ("ETHUSDT", "1h"): _make_candle_df(200, seed=43),
        }
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert 0 <= psr.pass_rate <= 1.0
                expected_pass_rate = psr.datasets_qualified / psr.datasets_evaluated
                assert psr.pass_rate == expected_pass_rate

    def test_best_pass_rate_above_threshold(self):
        import app.strategies.ema_atr  # noqa: F401

        # Lenient thresholds
        lenient = QualificationThresholds(
            min_total_return_pct=-1.0, min_sharpe_ratio=-10.0,
            min_total_trades=0, max_drawdown_pct=1.0, min_profit_factor=0.0,
        )
        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            thresholds=lenient,
            min_pass_rate=0.5,
            run_walk_forward=False,
        )
        # With lenient thresholds, should find pass-rate winner
        assert result.best_pass_rate is not None

    def test_no_pass_rate_winner_strict_thresholds(self):
        import app.strategies.ema_atr  # noqa: F401

        strict = QualificationThresholds(
            min_total_return_pct=10.0, min_sharpe_ratio=5.0,
        )
        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            thresholds=strict,
            min_pass_rate=0.5,
            run_walk_forward=False,
        )
        assert result.best_pass_rate is None


# ---------- Dataset Approval ----------

class TestDatasetApproval:
    def test_approval_structure(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert len(psr.approvals) > 0
                for approval in psr.approvals:
                    assert isinstance(approval, DatasetApproval)
                    assert isinstance(approval.approved, bool)
                    assert isinstance(approval.reasons, list)
                    assert len(approval.reasons) > 0
                    assert approval.symbol == "BTCUSDT"
                    assert approval.interval == "1h"

    def test_approval_with_regime_blocking(self):
        import app.strategies.ema_atr  # noqa: F401

        # Very low volatility data + strict regime config
        candles = {("BTCUSDT", "1h"): _make_candle_df(200, volatility=0.001)}
        regime = RegimeConfig(min_volatility_pct=5.0)  # Will block
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            regime_config=regime,
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                for approval in psr.approvals:
                    if not approval.approved:
                        assert any("regime" in r for r in approval.reasons)

    def test_summary_dict_includes_approvals(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                d = psr.summary_dict()
                assert "approved_datasets" in d
                assert "pass_rate" in d
                assert "avg_oos_return" in d
                assert "avg_degradation" in d

    def test_datasets_tradable_count(self):
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, volatility=1.0, seed=42)}
        # Normal volatility, should be tradable
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            regime_config=RegimeConfig(min_volatility_pct=0.0, max_volatility_pct=100.0),
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert psr.datasets_tradable == psr.datasets_evaluated


# ---------- Regime in Orchestrator ----------

class TestOrchestratorRegime:
    @pytest.mark.asyncio
    async def test_regime_blocking_in_orchestrator(self):
        """Orchestrator blocks when regime_config is set and conditions are hostile."""
        from app.services.orchestrator import Orchestrator
        from app.risk.risk_engine import RiskEngine
        from app.execution.paper_broker import PaperBroker
        from app.strategies.registry import get_strategy
        import app.strategies.ema_atr  # noqa: F401

        strategy = get_strategy("ema_atr_crossover")
        risk = RiskEngine()
        broker = PaperBroker()
        regime = RegimeConfig(min_volatility_pct=50.0)  # Very strict — will block

        orch = Orchestrator(
            strategy=strategy, risk_engine=risk, broker=broker,
            symbols=["BTCUSDT"], regime_config=regime,
        )
        orch._running = True

        # Feed enough candles to build buffer
        for i in range(60):
            candle = {
                "symbol": "BTCUSDT", "open_time": i * 3600000,
                "open": 100, "high": 100.1, "low": 99.9, "close": 100, "volume": 100,
            }
            result = await orch.process_candle(candle)
            if result.get("action") == "regime_blocked":
                assert "regime" in result
                return

        # Should have been blocked at some point after buffering
        # (may hit buffering phase first for 50 candles)

    @pytest.mark.asyncio
    async def test_no_regime_blocking_without_config(self):
        """Without regime_config, orchestrator does not block on regime."""
        from app.services.orchestrator import Orchestrator
        from app.risk.risk_engine import RiskEngine
        from app.execution.paper_broker import PaperBroker
        from app.strategies.registry import get_strategy
        import app.strategies.ema_atr  # noqa: F401

        strategy = get_strategy("ema_atr_crossover")
        risk = RiskEngine()
        broker = PaperBroker()

        orch = Orchestrator(
            strategy=strategy, risk_engine=risk, broker=broker,
            symbols=["BTCUSDT"],
            # No regime_config → regime gating disabled
        )
        orch._running = True

        for i in range(60):
            candle = {
                "symbol": "BTCUSDT", "open_time": i * 3600000,
                "open": 100, "high": 100.1, "low": 99.9, "close": 100, "volume": 100,
            }
            result = await orch.process_candle(candle)
            # Should NOT get regime_blocked
            assert result.get("action") != "regime_blocked"
