"""Multi-metric strategy ranking system.

Ranks strategies using weighted scoring across multiple performance dimensions.
Does not select winners by profit alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.backtesting.metrics import PerformanceMetrics
from app.core.logging import get_logger

logger = get_logger(__name__)


# Default metric weights — balanced across return, risk, and consistency
DEFAULT_WEIGHTS: dict[str, float] = {
    "total_return_pct": 0.15,
    "max_drawdown_pct": 0.20,  # Inverted — lower is better
    "sharpe_ratio": 0.20,
    "sortino_ratio": 0.10,
    "profit_factor": 0.10,
    "win_rate": 0.10,
    "avg_trade_return_pct": 0.10,
    "max_consecutive_losses": 0.05,  # Inverted — lower is better
}

# Metrics where lower values are better
INVERTED_METRICS = {"max_drawdown_pct", "max_consecutive_losses"}


@dataclass
class RankedStrategy:
    """A strategy with its composite ranking score."""

    strategy_name: str
    rank: int
    composite_score: float
    metrics: PerformanceMetrics
    score_breakdown: dict[str, float]


def rank_strategies(
    metrics_list: list[PerformanceMetrics],
    weights: dict[str, float] | None = None,
) -> list[RankedStrategy]:
    """Rank strategies using weighted normalized scoring.

    Each metric is normalized to [0, 1] across all strategies, then
    combined using the specified weights. Inverted metrics (lower = better)
    are flipped before normalization.

    Args:
        metrics_list: List of PerformanceMetrics from backtest runs.
        weights: Optional custom metric weights. Must sum roughly to 1.0.

    Returns:
        Sorted list of RankedStrategy, best first.
    """
    if not metrics_list:
        return []

    weights = weights or DEFAULT_WEIGHTS

    # Collect raw metric values for normalization
    metric_names = list(weights.keys())
    raw_values: dict[str, list[float]] = {m: [] for m in metric_names}

    for pm in metrics_list:
        for m in metric_names:
            val = getattr(pm, m, 0.0)
            if val is None or (isinstance(val, float) and (val != val)):  # NaN check
                val = 0.0
            raw_values[m].append(float(val))

    # Normalize each metric to [0, 1]
    normalized: dict[str, list[float]] = {}
    for m in metric_names:
        values = raw_values[m]
        min_v = min(values)
        max_v = max(values)
        rng = max_v - min_v

        if rng == 0:
            normalized[m] = [0.5] * len(values)
        else:
            if m in INVERTED_METRICS:
                # Lower is better — invert
                normalized[m] = [(max_v - v) / rng for v in values]
            else:
                normalized[m] = [(v - min_v) / rng for v in values]

    # Calculate composite scores
    ranked: list[RankedStrategy] = []
    for i, pm in enumerate(metrics_list):
        score_breakdown: dict[str, float] = {}
        composite = 0.0
        for m in metric_names:
            weighted_score = normalized[m][i] * weights.get(m, 0.0)
            score_breakdown[m] = round(weighted_score, 4)
            composite += weighted_score

        ranked.append(RankedStrategy(
            strategy_name=pm.strategy_name,
            rank=0,  # Will be set after sorting
            composite_score=composite,
            metrics=pm,
            score_breakdown=score_breakdown,
        ))

    # Sort by composite score (highest first)
    ranked.sort(key=lambda x: x.composite_score, reverse=True)

    # Assign ranks
    for i, r in enumerate(ranked):
        r.rank = i + 1

    logger.info(
        "strategy_ranking_complete",
        strategies_ranked=len(ranked),
        winner=ranked[0].strategy_name if ranked else "none",
        top_score=round(ranked[0].composite_score, 4) if ranked else 0,
    )

    return ranked
