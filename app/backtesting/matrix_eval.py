"""Multi-dataset evaluation — matrix of symbols × intervals.

Evaluates strategies across all configured symbol/interval combinations
and aggregates cross-dataset consistency metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import PerformanceMetrics, calculate_metrics
from app.backtesting.validation import (
    BenchmarkResult,
    QualificationResult,
    QualificationThresholds,
    WalkForwardResult,
    check_qualification,
    compute_benchmark,
    walk_forward_validate,
)
from app.core.logging import get_logger
from app.strategies.registry import get_all_strategies, get_strategy

import pandas as pd

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Per-dataset result
# ──────────────────────────────────────────────

@dataclass
class DatasetResult:
    """Results of evaluating one strategy on one symbol/interval dataset."""

    strategy_name: str
    symbol: str
    interval: str
    metrics: PerformanceMetrics
    benchmark: BenchmarkResult
    alpha: float  # strategy return minus benchmark return
    wf_result: WalkForwardResult | None = None
    qualification: QualificationResult | None = None


# ──────────────────────────────────────────────
# Cross-dataset aggregated result per strategy
# ──────────────────────────────────────────────

@dataclass
class StrategyMatrixResult:
    """Aggregated cross-dataset performance for one strategy."""

    strategy_name: str
    datasets_evaluated: int = 0
    datasets_qualified: int = 0
    avg_return: float = 0.0
    avg_sharpe: float = 0.0
    avg_drawdown: float = 0.0
    max_drawdown: float = 0.0
    avg_alpha: float = 0.0
    avg_oos_consistency: float = 0.0
    avg_profit_factor: float = 0.0
    total_trades: int = 0
    consistency_score: float = 0.0  # fraction of datasets profitable
    all_qualified: bool = False
    per_dataset: list[DatasetResult] = field(default_factory=list)

    def summary_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "datasets_evaluated": self.datasets_evaluated,
            "datasets_qualified": self.datasets_qualified,
            "avg_return": round(self.avg_return, 4),
            "avg_sharpe": round(self.avg_sharpe, 4),
            "avg_drawdown": round(self.avg_drawdown, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_alpha": round(self.avg_alpha, 4),
            "avg_oos_consistency": round(self.avg_oos_consistency, 4),
            "avg_profit_factor": round(self.avg_profit_factor, 4),
            "total_trades": self.total_trades,
            "consistency_score": round(self.consistency_score, 4),
            "all_qualified": self.all_qualified,
        }


# ──────────────────────────────────────────────
# Matrix evaluation
# ──────────────────────────────────────────────

@dataclass
class MatrixEvaluationResult:
    """Full matrix evaluation result — all strategies × all datasets."""

    strategies: list[StrategyMatrixResult] = field(default_factory=list)
    symbols_evaluated: list[str] = field(default_factory=list)
    intervals_evaluated: list[str] = field(default_factory=list)
    total_datasets: int = 0
    best_ranked: str | None = None
    best_qualified: str | None = None
    thresholds_used: dict[str, float] = field(default_factory=dict)


def evaluate_matrix(
    candles_by_dataset: dict[tuple[str, str], pd.DataFrame],
    thresholds: QualificationThresholds | None = None,
    initial_capital: float = 10_000.0,
    fee_pct: float = 0.001,
    slippage_pct: float = 0.001,
    run_walk_forward: bool = True,
    wf_windows: int = 3,
) -> MatrixEvaluationResult:
    """Evaluate all registered strategies across a matrix of datasets.

    Args:
        candles_by_dataset: Dict mapping (symbol, interval) to candle DataFrame.
        thresholds: Qualification thresholds.
        initial_capital: Starting capital per backtest.
        fee_pct: Fee percentage.
        slippage_pct: Slippage percentage.
        run_walk_forward: Whether to run walk-forward validation per dataset.
        wf_windows: Number of walk-forward windows.

    Returns:
        MatrixEvaluationResult with per-strategy aggregated results.
    """
    thresholds = thresholds or QualificationThresholds()
    strategies = get_all_strategies()

    if not strategies:
        logger.warning("matrix_eval_no_strategies")
        return MatrixEvaluationResult()

    engine = BacktestEngine(
        initial_capital=initial_capital,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )

    symbols = sorted(set(s for s, _ in candles_by_dataset.keys()))
    intervals = sorted(set(i for _, i in candles_by_dataset.keys()))
    total_datasets = len(candles_by_dataset)

    logger.info(
        "matrix_evaluation_start",
        strategies=len(strategies),
        symbols=symbols,
        intervals=intervals,
        datasets=total_datasets,
    )

    # Evaluate each strategy across all datasets
    strategy_results: dict[str, StrategyMatrixResult] = {}

    for strategy in strategies:
        matrix_result = StrategyMatrixResult(strategy_name=strategy.name)
        dataset_results: list[DatasetResult] = []

        for (symbol, interval), candles in candles_by_dataset.items():
            if candles.empty or len(candles) < 50:
                logger.warning(
                    "matrix_dataset_too_small",
                    strategy=strategy.name,
                    symbol=symbol,
                    interval=interval,
                    candles=len(candles),
                )
                continue

            try:
                # Fresh strategy instance per dataset
                fresh = get_strategy(strategy.name, params=strategy.params)
                result = engine.run(fresh, candles, symbol=symbol, interval=interval)
                metrics = calculate_metrics(result)
            except Exception as e:
                logger.warning(
                    "matrix_backtest_error",
                    strategy=strategy.name,
                    symbol=symbol,
                    interval=interval,
                    error=str(e),
                )
                continue

            # Benchmark
            benchmark = compute_benchmark(candles, symbol=symbol)
            alpha = metrics.total_return_pct - benchmark.total_return_pct

            # Walk-forward (optional)
            wf = None
            if run_walk_forward and len(candles) >= 100:
                try:
                    wf_strategy = get_strategy(strategy.name, params=strategy.params)
                    wf = walk_forward_validate(
                        wf_strategy, candles,
                        symbol=symbol, interval=interval,
                        n_windows=wf_windows,
                        initial_capital=initial_capital,
                        fee_pct=fee_pct,
                        slippage_pct=slippage_pct,
                    )
                except Exception as e:
                    logger.warning("matrix_wf_error", strategy=strategy.name, error=str(e))

            # Qualification per dataset
            qual = check_qualification(
                metrics,
                thresholds=thresholds,
                wf_result=wf,
                benchmark_return_pct=benchmark.total_return_pct,
            )

            dataset_results.append(DatasetResult(
                strategy_name=strategy.name,
                symbol=symbol,
                interval=interval,
                metrics=metrics,
                benchmark=benchmark,
                alpha=alpha,
                wf_result=wf,
                qualification=qual,
            ))

        if not dataset_results:
            strategy_results[strategy.name] = matrix_result
            continue

        # Aggregate across datasets
        returns = [dr.metrics.total_return_pct for dr in dataset_results]
        sharpes = [dr.metrics.sharpe_ratio for dr in dataset_results]
        drawdowns = [dr.metrics.max_drawdown_pct for dr in dataset_results]
        alphas = [dr.alpha for dr in dataset_results]
        pfs = [dr.metrics.profit_factor for dr in dataset_results]
        trades = sum(dr.metrics.total_trades for dr in dataset_results)
        oos_scores = [
            dr.wf_result.oos_consistency
            for dr in dataset_results
            if dr.wf_result and dr.wf_result.total_windows > 0
        ]
        qualified_count = sum(1 for dr in dataset_results if dr.qualification and dr.qualification.qualified)
        profitable_count = sum(1 for r in returns if r > 0)

        n = len(dataset_results)
        matrix_result.datasets_evaluated = n
        matrix_result.datasets_qualified = qualified_count
        matrix_result.avg_return = sum(returns) / n
        matrix_result.avg_sharpe = sum(sharpes) / n
        matrix_result.avg_drawdown = sum(drawdowns) / n
        matrix_result.max_drawdown = max(drawdowns)
        matrix_result.avg_alpha = sum(alphas) / n
        matrix_result.avg_profit_factor = sum(pfs) / n
        matrix_result.total_trades = trades
        matrix_result.avg_oos_consistency = sum(oos_scores) / len(oos_scores) if oos_scores else 0.0
        matrix_result.consistency_score = profitable_count / n
        matrix_result.all_qualified = qualified_count == n
        matrix_result.per_dataset = dataset_results

        strategy_results[strategy.name] = matrix_result

        logger.info(
            "matrix_strategy_complete",
            strategy=strategy.name,
            datasets=n,
            qualified=qualified_count,
            avg_return=round(matrix_result.avg_return, 4),
            consistency=round(matrix_result.consistency_score, 2),
        )

    # Sort by consistency_score, then avg_sharpe, then avg_return
    sorted_results = sorted(
        strategy_results.values(),
        key=lambda s: (s.consistency_score, s.avg_sharpe, s.avg_return),
        reverse=True,
    )

    # Determine best ranked and best qualified
    best_ranked = sorted_results[0].strategy_name if sorted_results else None
    best_qualified = None
    for sr in sorted_results:
        if sr.all_qualified and sr.datasets_evaluated > 0:
            best_qualified = sr.strategy_name
            break

    result = MatrixEvaluationResult(
        strategies=sorted_results,
        symbols_evaluated=symbols,
        intervals_evaluated=intervals,
        total_datasets=total_datasets,
        best_ranked=best_ranked,
        best_qualified=best_qualified,
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

    logger.info(
        "matrix_evaluation_complete",
        strategies=len(sorted_results),
        best_ranked=best_ranked,
        best_qualified=best_qualified,
    )

    return result
