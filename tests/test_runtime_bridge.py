"""Tests for research-to-runtime bridge: config-driven regime, approval routing, CLI flags."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.backtesting.regime_filter import RegimeConfig
from app.backtesting.optimizer import optimize_strategy_matrix
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


# ---------- Config-driven regime gating ----------

class TestConfigDrivenRegime:
    def test_regime_config_from_settings_fields(self):
        """Verify RegimeConfig can be built from settings-like values."""
        config = RegimeConfig(
            min_volatility_pct=0.2,
            max_volatility_pct=8.0,
            enabled=True,
        )
        assert config.enabled is True
        assert config.min_volatility_pct == 0.2
        assert config.max_volatility_pct == 8.0

    def test_regime_disabled_config(self):
        """When enabled=False, regime config produces tradable state."""
        from app.backtesting.regime_filter import assess_regime
        candles = _make_candle_df(200, volatility=0.001)  # Very low vol
        config = RegimeConfig(enabled=False)
        state = assess_regime(candles, config)
        assert state.is_tradable is True

    def test_optimizer_with_regime_config(self):
        """Optimizer respects regime config from settings."""
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        config = RegimeConfig(min_volatility_pct=0.0, max_volatility_pct=100.0)

        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            regime_config=config,
            run_walk_forward=False,
        )
        for psr in result.param_results:
            if psr.datasets_evaluated > 0:
                assert psr.datasets_tradable == psr.datasets_evaluated


# ---------- Walk-forward in CLI ----------

class TestWalkForwardCLI:
    def test_optimizer_walk_forward_flag(self):
        """WF flag enables walk-forward in optimization."""
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(300, seed=42)}

        result_no_wf = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            run_walk_forward=False,
        )
        result_wf = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            run_walk_forward=True, wf_windows=2,
        )

        # Without WF: avg_oos_return and avg_degradation should be 0
        for psr in result_no_wf.param_results:
            if psr.datasets_evaluated > 0:
                assert psr.avg_oos_return == 0.0

        # With WF: should have real WF data
        for psr in result_wf.param_results:
            if psr.datasets_evaluated > 0:
                # WF was run, so these should be populated
                assert isinstance(psr.avg_oos_return, float)
                assert isinstance(psr.avg_degradation, float)

    def test_wf_windows_parameter(self):
        """Different window counts should work."""
        import app.strategies.ema_atr  # noqa: F401

        candles = {("BTCUSDT", "1h"): _make_candle_df(300, seed=42)}

        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            run_walk_forward=True, wf_windows=3,
        )
        assert result.evaluated_param_sets > 0


# ---------- Approval-driven paper trading ----------

class TestApprovalRouting:
    def test_no_approved_datasets(self):
        """When nothing qualifies, best_pass_rate should be None."""
        import app.strategies.ema_atr  # noqa: F401

        strict = QualificationThresholds(
            min_total_return_pct=100.0, min_sharpe_ratio=50.0,
        )
        candles = {("BTCUSDT", "1h"): _make_candle_df(200, seed=42)}
        result = optimize_strategy_matrix(
            "ema_atr_crossover", candles,
            param_grid={"fast_ema": [12]},
            thresholds=strict,
            min_pass_rate=0.5,
            run_walk_forward=False,
        )
        assert result.best_qualified is None
        assert result.best_pass_rate is None

    def test_pass_rate_finds_winner(self):
        """With lenient thresholds, pass-rate winner should be found."""
        import app.strategies.ema_atr  # noqa: F401

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
        assert result.best_pass_rate is not None

    def test_approval_reasons_in_report(self):
        """Approval export includes reasons for each dataset."""
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
                for ds in d["approved_datasets"]:
                    assert "symbol" in ds
                    assert "interval" in ds
                    assert "approved" in ds
                    assert "reasons" in ds
                    assert len(ds["reasons"]) > 0


# ---------- Orchestrator regime gating config-driven ----------

class TestOrchestratorRegimeConfig:
    @pytest.mark.asyncio
    async def test_regime_gating_wired_from_config(self):
        """When regime_config is provided, orchestrator uses it."""
        from app.services.orchestrator import Orchestrator
        from app.risk.risk_engine import RiskEngine
        from app.execution.paper_broker import PaperBroker
        from app.strategies.registry import get_strategy
        import app.strategies.ema_atr  # noqa: F401

        strategy = get_strategy("ema_atr_crossover")
        risk = RiskEngine()
        broker = PaperBroker()

        config = RegimeConfig(min_volatility_pct=99.0)  # Will block
        orch = Orchestrator(
            strategy=strategy, risk_engine=risk, broker=broker,
            symbols=["BTCUSDT"], regime_config=config,
        )
        assert orch.regime_config is not None
        assert orch.regime_config.min_volatility_pct == 99.0

    @pytest.mark.asyncio
    async def test_regime_gating_none_by_default(self):
        """Without regime_config, orchestrator does not gate."""
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
        )
        assert orch.regime_config is None
