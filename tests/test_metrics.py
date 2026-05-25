"""Tests for performance metrics calculations."""

from __future__ import annotations


from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import (
    PerformanceMetrics,
    calculate_metrics,
    _calculate_max_drawdown,
    _calculate_sharpe,
)
from app.strategies.ema_atr import EmaAtrStrategy


class TestPerformanceMetrics:
    def test_metrics_from_backtest(self, sample_candles):
        engine = BacktestEngine(initial_capital=10_000)
        strategy = EmaAtrStrategy()
        result = engine.run(strategy, sample_candles, symbol="BTCUSDT")
        metrics = calculate_metrics(result)

        assert isinstance(metrics, PerformanceMetrics)
        assert metrics.initial_capital == 10_000
        assert metrics.strategy_name == "ema_atr_crossover"

    def test_max_drawdown_calculation(self):
        # Equity goes up then drops
        curve = [100, 110, 120, 100, 90, 95, 130]
        dd = _calculate_max_drawdown(curve)
        assert dd > 0
        # Max drawdown should be from 120 to 90 = 25%
        assert abs(dd - 0.25) < 0.01

    def test_max_drawdown_no_drawdown(self):
        curve = [100, 110, 120, 130]
        dd = _calculate_max_drawdown(curve)
        assert dd == 0.0

    def test_sharpe_ratio_calculation(self):
        # Steady growth = high Sharpe
        curve = [100 + i * 0.5 for i in range(100)]
        sharpe = _calculate_sharpe(curve)
        assert sharpe > 0

    def test_sharpe_flat_returns(self):
        curve = [100.0] * 50
        sharpe = _calculate_sharpe(curve)
        assert sharpe == 0.0

    def test_metrics_to_dict(self, sample_candles):
        engine = BacktestEngine(initial_capital=10_000)
        strategy = EmaAtrStrategy()
        result = engine.run(strategy, sample_candles)
        metrics = calculate_metrics(result)
        d = metrics.to_dict()

        assert "net_profit" in d
        assert "sharpe_ratio" in d
        assert "max_drawdown_pct" in d
        assert "win_rate" in d
