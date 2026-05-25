"""Walk-forward validation and benchmark comparison.

Provides out-of-sample testing by splitting data into train/test windows
and computing buy-and-hold benchmark for honest strategy evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import PerformanceMetrics, calculate_metrics
from app.core.logging import get_logger
from app.strategies.base import BaseStrategy

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Benchmark — buy-and-hold
# ──────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """Buy-and-hold benchmark for a symbol over the same data range."""

    symbol: str
    start_price: float
    end_price: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float


def compute_benchmark(candles: pd.DataFrame, symbol: str = "UNKNOWN") -> BenchmarkResult:
    """Compute buy-and-hold return over the given candles.

    Assumes buying at first candle close and holding to last candle close.

    Args:
        candles: DataFrame with at least 'close' column.
        symbol: Symbol name for labeling.

    Returns:
        BenchmarkResult with return, drawdown, and Sharpe.
    """
    if candles.empty or len(candles) < 2:
        return BenchmarkResult(
            symbol=symbol, start_price=0, end_price=0,
            total_return_pct=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
        )

    closes = candles["close"].astype(float).values
    start_price = closes[0]
    end_price = closes[-1]

    total_return_pct = (end_price - start_price) / start_price if start_price > 0 else 0.0

    # Max drawdown
    import numpy as np
    peak = np.maximum.accumulate(closes)
    drawdown = (closes - peak) / np.where(peak > 0, peak, 1.0)
    max_drawdown_pct = float(abs(np.min(drawdown)))

    # Simple Sharpe on daily(ish) returns
    returns = np.diff(closes) / closes[:-1]
    returns = returns[np.isfinite(returns)]
    if len(returns) > 1:
        periods_per_year = 365 * 24  # hourly assumption
        std = float(np.std(returns, ddof=1))
        sharpe = float(np.mean(returns) / std * np.sqrt(periods_per_year)) if std > 0 else 0.0
    else:
        sharpe = 0.0

    return BenchmarkResult(
        symbol=symbol,
        start_price=float(start_price),
        end_price=float(end_price),
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe,
    )


# ──────────────────────────────────────────────
# Walk-forward validation
# ──────────────────────────────────────────────

@dataclass
class WalkForwardWindow:
    """Result of one train/test window in walk-forward validation."""

    window_index: int
    train_size: int
    test_size: int
    train_metrics: PerformanceMetrics
    test_metrics: PerformanceMetrics
    train_benchmark: BenchmarkResult
    test_benchmark: BenchmarkResult


@dataclass
class WalkForwardResult:
    """Complete walk-forward validation result for one strategy."""

    strategy_name: str
    symbol: str
    interval: str
    windows: list[WalkForwardWindow] = field(default_factory=list)
    total_windows: int = 0
    avg_train_return: float = 0.0
    avg_test_return: float = 0.0
    avg_train_sharpe: float = 0.0
    avg_test_sharpe: float = 0.0
    avg_test_benchmark_return: float = 0.0
    test_vs_benchmark_wins: int = 0
    oos_consistency: float = 0.0  # fraction of OOS windows that are profitable
    degradation_ratio: float = 0.0  # test_return / train_return (closer to 1 = more robust)

    def summarize(self) -> dict[str, Any]:
        """Return summary dict for reporting."""
        return {
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "total_windows": self.total_windows,
            "avg_train_return": round(self.avg_train_return, 4),
            "avg_test_return": round(self.avg_test_return, 4),
            "avg_train_sharpe": round(self.avg_train_sharpe, 4),
            "avg_test_sharpe": round(self.avg_test_sharpe, 4),
            "avg_test_benchmark_return": round(self.avg_test_benchmark_return, 4),
            "test_vs_benchmark_wins": self.test_vs_benchmark_wins,
            "oos_consistency": round(self.oos_consistency, 4),
            "degradation_ratio": round(self.degradation_ratio, 4),
        }


def walk_forward_validate(
    strategy: BaseStrategy,
    candles: pd.DataFrame,
    symbol: str = "UNKNOWN",
    interval: str = "1h",
    n_windows: int = 3,
    train_pct: float = 0.7,
    initial_capital: float = 10_000.0,
    fee_pct: float = 0.001,
    slippage_pct: float = 0.001,
) -> WalkForwardResult:
    """Run walk-forward validation with multiple train/test windows.

    Splits data into `n_windows` sequential windows, each with `train_pct`
    used for in-sample training and the remainder for out-of-sample testing.

    Args:
        strategy: Strategy instance to validate.
        candles: Full historical OHLCV DataFrame.
        symbol: Trading symbol.
        interval: Candle interval.
        n_windows: Number of rolling windows.
        train_pct: Fraction of each window used for training.
        initial_capital: Starting capital per window.
        fee_pct: Fee percentage.
        slippage_pct: Slippage percentage.

    Returns:
        WalkForwardResult with per-window and aggregate metrics.
    """
    total = len(candles)
    if total < 100:
        logger.warning("walk_forward_insufficient_data", candles=total)
        return WalkForwardResult(
            strategy_name=strategy.name, symbol=symbol, interval=interval,
        )

    engine = BacktestEngine(
        initial_capital=initial_capital,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )

    # Calculate window boundaries
    # Each window overlaps by sliding forward
    window_size = total // n_windows
    if window_size < 50:
        # Fall back to fewer windows
        n_windows = max(1, total // 100)
        window_size = total // max(n_windows, 1)

    windows: list[WalkForwardWindow] = []

    for i in range(n_windows):
        start = i * (total - window_size) // max(n_windows - 1, 1) if n_windows > 1 else 0
        end = start + window_size
        end = min(end, total)

        window_data = candles.iloc[start:end].reset_index(drop=True)
        split_idx = int(len(window_data) * train_pct)

        if split_idx < 30 or (len(window_data) - split_idx) < 10:
            continue

        train_data = window_data.iloc[:split_idx].reset_index(drop=True)
        test_data = window_data.iloc[split_idx:].reset_index(drop=True)

        # Run backtest on train
        try:
            # Create fresh strategy instance to avoid state leakage
            from app.strategies.registry import get_strategy
            fresh = get_strategy(strategy.name, params=strategy.params)
            train_result = engine.run(fresh, train_data, symbol=symbol, interval=interval)
            train_metrics = calculate_metrics(train_result)
        except Exception as e:
            logger.warning("wf_train_error", window=i, error=str(e))
            continue

        # Run backtest on test
        try:
            fresh2 = get_strategy(strategy.name, params=strategy.params)
            test_result = engine.run(fresh2, test_data, symbol=symbol, interval=interval)
            test_metrics = calculate_metrics(test_result)
        except Exception as e:
            logger.warning("wf_test_error", window=i, error=str(e))
            continue

        # Benchmarks
        train_bm = compute_benchmark(train_data, symbol=symbol)
        test_bm = compute_benchmark(test_data, symbol=symbol)

        windows.append(WalkForwardWindow(
            window_index=i,
            train_size=len(train_data),
            test_size=len(test_data),
            train_metrics=train_metrics,
            test_metrics=test_metrics,
            train_benchmark=train_bm,
            test_benchmark=test_bm,
        ))

    if not windows:
        return WalkForwardResult(
            strategy_name=strategy.name, symbol=symbol, interval=interval,
        )

    # Compute aggregates
    train_returns = [w.train_metrics.total_return_pct for w in windows]
    test_returns = [w.test_metrics.total_return_pct for w in windows]
    train_sharpes = [w.train_metrics.sharpe_ratio for w in windows]
    test_sharpes = [w.test_metrics.sharpe_ratio for w in windows]
    test_bm_returns = [w.test_benchmark.total_return_pct for w in windows]

    avg_train_return = sum(train_returns) / len(train_returns)
    avg_test_return = sum(test_returns) / len(test_returns)
    avg_train_sharpe = sum(train_sharpes) / len(train_sharpes)
    avg_test_sharpe = sum(test_sharpes) / len(test_sharpes)
    avg_test_bm_return = sum(test_bm_returns) / len(test_bm_returns)

    # How many OOS windows beat benchmark?
    wins = sum(
        1 for w in windows
        if w.test_metrics.total_return_pct > w.test_benchmark.total_return_pct
    )

    # OOS consistency: fraction of OOS windows with positive return
    oos_profitable = sum(1 for r in test_returns if r > 0)
    oos_consistency = oos_profitable / len(test_returns)

    # Degradation: how much does performance degrade out-of-sample?
    if avg_train_return != 0:
        degradation = avg_test_return / avg_train_return
    else:
        degradation = 0.0

    result = WalkForwardResult(
        strategy_name=strategy.name,
        symbol=symbol,
        interval=interval,
        windows=windows,
        total_windows=len(windows),
        avg_train_return=avg_train_return,
        avg_test_return=avg_test_return,
        avg_train_sharpe=avg_train_sharpe,
        avg_test_sharpe=avg_test_sharpe,
        avg_test_benchmark_return=avg_test_bm_return,
        test_vs_benchmark_wins=wins,
        oos_consistency=oos_consistency,
        degradation_ratio=degradation,
    )

    logger.info(
        "walk_forward_complete",
        strategy=strategy.name,
        windows=len(windows),
        avg_test_return=round(avg_test_return, 4),
        oos_consistency=round(oos_consistency, 2),
    )

    return result


# ──────────────────────────────────────────────
# Qualification thresholds
# ──────────────────────────────────────────────

@dataclass
class QualificationThresholds:
    """Minimum thresholds a strategy must pass to be considered tradable.

    If a strategy fails any threshold, it should NOT be auto-persisted
    as a winner or auto-selected for paper trading.
    """

    min_total_return_pct: float = 0.0       # Must be non-negative
    min_sharpe_ratio: float = 0.0           # Must be non-negative
    min_total_trades: int = 5               # Must have made trades
    max_drawdown_pct: float = 0.30          # Max 30% drawdown
    min_profit_factor: float = 0.8          # At least close to breakeven
    min_oos_consistency: float = 0.0        # Walk-forward: fraction of OOS windows profitable
    min_benchmark_alpha_pct: float = 0.0    # Must at least match buy-and-hold


DEFAULT_QUALIFICATION = QualificationThresholds()


@dataclass
class QualificationResult:
    """Result of checking a strategy against qualification thresholds."""

    strategy_name: str
    qualified: bool
    failures: list[str] = field(default_factory=list)
    thresholds_used: dict[str, float] = field(default_factory=dict)

    def reason(self) -> str:
        if self.qualified:
            return "QUALIFIED"
        return "UNQUALIFIED: " + "; ".join(self.failures)


def check_qualification(
    metrics: PerformanceMetrics,
    thresholds: QualificationThresholds | None = None,
    wf_result: WalkForwardResult | None = None,
    benchmark_return_pct: float = 0.0,
) -> QualificationResult:
    """Check if a strategy meets minimum qualification thresholds.

    Args:
        metrics: In-sample or full-period performance metrics.
        thresholds: Thresholds to check against.
        wf_result: Optional walk-forward result for OOS consistency check.
        benchmark_return_pct: Buy-and-hold return for alpha comparison.

    Returns:
        QualificationResult indicating pass/fail with reasons.
    """
    thresholds = thresholds or DEFAULT_QUALIFICATION
    failures: list[str] = []

    if metrics.total_return_pct < thresholds.min_total_return_pct:
        failures.append(
            f"Return {metrics.total_return_pct:.2%} < min {thresholds.min_total_return_pct:.2%}"
        )

    if metrics.sharpe_ratio < thresholds.min_sharpe_ratio:
        failures.append(
            f"Sharpe {metrics.sharpe_ratio:.2f} < min {thresholds.min_sharpe_ratio:.2f}"
        )

    if metrics.total_trades < thresholds.min_total_trades:
        failures.append(
            f"Trades {metrics.total_trades} < min {thresholds.min_total_trades}"
        )

    if metrics.max_drawdown_pct > thresholds.max_drawdown_pct:
        failures.append(
            f"Drawdown {metrics.max_drawdown_pct:.2%} > max {thresholds.max_drawdown_pct:.2%}"
        )

    if metrics.profit_factor < thresholds.min_profit_factor:
        failures.append(
            f"Profit factor {metrics.profit_factor:.2f} < min {thresholds.min_profit_factor:.2f}"
        )

    if wf_result and thresholds.min_oos_consistency > 0:
        if wf_result.oos_consistency < thresholds.min_oos_consistency:
            failures.append(
                f"OOS consistency {wf_result.oos_consistency:.0%} < min {thresholds.min_oos_consistency:.0%}"
            )

    # Benchmark alpha check
    if thresholds.min_benchmark_alpha_pct != 0.0:
        alpha = metrics.total_return_pct - benchmark_return_pct
        if alpha < thresholds.min_benchmark_alpha_pct:
            failures.append(
                f"Alpha {alpha:.2%} < min {thresholds.min_benchmark_alpha_pct:.2%}"
            )

    return QualificationResult(
        strategy_name=metrics.strategy_name,
        qualified=len(failures) == 0,
        failures=failures,
        thresholds_used={
            "min_total_return_pct": thresholds.min_total_return_pct,
            "min_sharpe_ratio": thresholds.min_sharpe_ratio,
            "min_total_trades": thresholds.min_total_trades,
            "max_drawdown_pct": thresholds.max_drawdown_pct,
            "min_profit_factor": thresholds.min_profit_factor,
            "min_oos_consistency": thresholds.min_oos_consistency,
            "min_benchmark_alpha_pct": thresholds.min_benchmark_alpha_pct,
        },
    )
