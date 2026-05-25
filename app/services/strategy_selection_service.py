"""Strategy selection service — runs backtest, rank, and select workflow."""

from __future__ import annotations


import pandas as pd

from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import PerformanceMetrics, calculate_metrics
from app.backtesting.ranking import RankedStrategy, rank_strategies
from app.core.logging import get_logger
from app.strategies.registry import get_all_strategies

logger = get_logger(__name__)


class StrategySelectionService:
    """Runs the backtest → rank → select workflow.

    Backtests all registered strategies and selects the best based
    on multi-metric ranking (not profit alone).
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fee_pct: float = 0.001,
        slippage_pct: float = 0.001,
    ) -> None:
        self.engine = BacktestEngine(
            initial_capital=initial_capital,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )

    def select_best_strategy(
        self,
        candles: pd.DataFrame,
        symbol: str = "UNKNOWN",
        interval: str = "1h",
        weights: dict[str, float] | None = None,
    ) -> tuple[RankedStrategy | None, list[RankedStrategy]]:
        """Backtest all strategies and return the best one.

        Returns:
            Tuple of (best_strategy_ranking, all_rankings).
        """
        strategies = get_all_strategies()
        if not strategies:
            logger.warning("no_strategies_registered")
            return None, []

        metrics_list: list[PerformanceMetrics] = []

        for strategy in strategies:
            try:
                result = self.engine.run(strategy, candles, symbol=symbol, interval=interval)
                metrics = calculate_metrics(result)
                metrics_list.append(metrics)
                logger.info(
                    "strategy_backtested",
                    strategy=strategy.name,
                    return_pct=round(metrics.total_return_pct, 4),
                    sharpe=round(metrics.sharpe_ratio, 2),
                )
            except Exception as e:
                logger.error("strategy_backtest_failed", strategy=strategy.name, error=str(e))

        if not metrics_list:
            return None, []

        rankings = rank_strategies(metrics_list, weights=weights)
        best = rankings[0] if rankings else None

        if best:
            logger.info(
                "best_strategy_selected",
                strategy=best.strategy_name,
                score=round(best.composite_score, 4),
            )

        return best, rankings
