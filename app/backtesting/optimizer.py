"""Matrix-wide parameter optimizer with walk-forward validation and regime gating.

Evaluates parameter grids across all configured datasets (symbol × interval)
and ranks parameter sets by cross-dataset robustness rather than single-run profit.

v2 additions:
- Walk-forward train/test validation per dataset
- Degradation penalty in robustness scoring
- Regime gating (skip untradable datasets)
- Pass-rate qualification (% of datasets, not all-or-nothing)
"""

from __future__ import annotations

import itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.backtesting.engine import BacktestEngine
from app.backtesting.grid_diagnostics import aggregate_grid_diagnostics
from app.backtesting.metrics import PerformanceMetrics, calculate_metrics
from app.backtesting.regime_filter import RegimeConfig, RegimeState, assess_regime
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
from app.strategies.registry import get_strategy

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Parameter grids for each strategy
# ──────────────────────────────────────────────

DEFAULT_PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "ema_atr_crossover": {
        "fast_ema": [8, 12],
        "slow_ema": [21, 34],
        "atr_min_threshold": [0.3, 0.5],
        "cooldown_bars": [5, 8],
    },
    "rsi_mean_reversion": {
        "rsi_period": [10, 14],
        "oversold": [25, 30],
        "overbought": [70, 75],
        "cooldown_bars": [5, 8],
    },
    "bollinger_mean_reversion": {
        "bb_period": [20, 30],
        "bb_std_dev": [2.0, 2.5],
        "cooldown_bars": [6, 10],
    },
    "breakout": {
        "lookback_period": [15, 20],
        "volume_mult": [1.5, 2.0],
        "cooldown_bars": [5, 8],
        "min_breakout_atr": [0.3, 0.5],
    },
    "regime_adaptive": {
        "adx_trend_threshold": [20, 25],
        "adx_range_threshold": [15, 20],
        "rsi_oversold": [25, 30],
        "rsi_overbought": [70, 75],
        "cooldown_bars": [5, 8],
    },
    "momentum_continuation": {
        "sma_period": [15, 20],
        "adx_threshold": [20, 25],
        "roc_period": [8, 12],
        "cooldown_bars": [5, 8],
    },
    "pullback_uptrend": {
        "fast_ema": [15, 20],
        "slow_ema": [40, 50],
        "rsi_pullback_low": [30, 35],
        "cooldown_bars": [6, 10],
    },
    "volatility_breakout": {
        "keltner_ema": [15, 20],
        "keltner_atr_mult": [1.5, 2.0],
        "trend_ema": [40, 50],
        "cooldown_bars": [6, 10],
    },
    "hybrid_grid_dca": {
        "anchor_period": [80],
        "trend_filter_period": [200],
        "grid_spacing_pct": [0.012, 0.016, 0.020],
        "max_grid_levels": [2, 3],
        "base_order_pct": [0.03, 0.05],
        "dca_size_multiplier": [1.0, 1.25],
        "take_profit_pct": [0.014, 0.020],
        "stop_loss_pct": [0.06, 0.10],
        "max_grid_allocation_pct": [0.25, 0.35],
        "cooldown_bars": [1],
        "min_volatility_pct": [0.002],
        "atr_period": [14],
        "atr_grid_spacing_mult": [0.75],
        "min_trend_slope_pct": [-0.02],
        "trend_slope_lookback": [20],
        "max_anchor_deviation_pct": [0.08],
        "take_profit_fee_buffer_pct": [0.002],
        "stop_cooldown_bars": [5],
        "scale_in_requires_below_average": [True],
        "scale_in_requires_level_reclaim": [True],
        "entry_momentum_lookback": [12],
        "min_entry_momentum_pct": [-0.06],
        "support_lookback": [40],
        "support_buffer_pct": [0.005],
        "max_bearish_streak": [4],
        "volatility_zscore_lookback": [80],
        "max_volatility_zscore": [2.5],
        "require_reversal_confirmation": [False],
        "min_periods": [200],
    },
}


PARAM_GRID_PROFILES: dict[str, dict[str, dict[str, list[Any]]]] = {
    "fast": {
        "hybrid_grid_dca": {
            "anchor_period": [60],
            "trend_filter_period": [120],
            "grid_spacing_pct": [0.015, 0.020],
            "max_grid_levels": [2, 3],
            "base_order_pct": [0.03, 0.05],
            "dca_size_multiplier": [1.0, 1.25],
            "take_profit_pct": [0.014, 0.020],
            "stop_loss_pct": [0.06, 0.08],
            "max_grid_allocation_pct": [0.25],
            "cooldown_bars": [1],
            "min_volatility_pct": [0.002],
            "atr_period": [14],
            "atr_grid_spacing_mult": [0.75],
            "min_trend_slope_pct": [-0.02],
            "trend_slope_lookback": [20],
            "max_anchor_deviation_pct": [0.08],
            "take_profit_fee_buffer_pct": [0.002],
            "stop_cooldown_bars": [5],
            "scale_in_requires_below_average": [True],
            "scale_in_requires_level_reclaim": [True],
            "entry_momentum_lookback": [12],
            "min_entry_momentum_pct": [-0.06],
            "support_lookback": [40],
            "support_buffer_pct": [0.005],
            "max_bearish_streak": [4],
            "volatility_zscore_lookback": [80],
            "max_volatility_zscore": [2.5],
            "require_reversal_confirmation": [False],
            "min_periods": [120],
        },
    },
    "standard": {
        "hybrid_grid_dca": DEFAULT_PARAM_GRIDS["hybrid_grid_dca"],
    },
    "deep": {
        "hybrid_grid_dca": {
            "anchor_period": [60, 80],
            "trend_filter_period": [120],
            "grid_spacing_pct": [0.015, 0.020],
            "max_grid_levels": [2, 3],
            "base_order_pct": [0.03, 0.05],
            "dca_size_multiplier": [1.25],
            "take_profit_pct": [0.014, 0.020],
            "stop_loss_pct": [0.06, 0.08],
            "max_grid_allocation_pct": [0.25],
            "cooldown_bars": [1],
            "min_volatility_pct": [0.002],
            "atr_period": [14],
            "atr_grid_spacing_mult": [0.75],
            "min_trend_slope_pct": [-0.02],
            "trend_slope_lookback": [20],
            "max_anchor_deviation_pct": [0.08],
            "take_profit_fee_buffer_pct": [0.002],
            "stop_cooldown_bars": [5],
            "scale_in_requires_below_average": [True],
            "scale_in_requires_level_reclaim": [True],
            "entry_momentum_lookback": [12],
            "min_entry_momentum_pct": [-0.06, -0.04],
            "support_lookback": [40],
            "support_buffer_pct": [0.005],
            "max_bearish_streak": [3, 4],
            "volatility_zscore_lookback": [80],
            "max_volatility_zscore": [2.0],
            "require_reversal_confirmation": [False, True],
            "min_periods": [120],
        },
    },
}


# ──────────────────────────────────────────────
# Result dataclasses
# ──────────────────────────────────────────────

@dataclass
class DatasetApproval:
    """Whether a specific strategy+params+dataset is approved for paper trading."""

    symbol: str
    interval: str
    strategy_name: str
    params: dict[str, Any]
    approved: bool
    reasons: list[str]
    regime_state: RegimeState | None = None
    qualification: QualificationResult | None = None
    wf_result: WalkForwardResult | None = None


@dataclass
class ParamSetDatasetResult:
    """One param set evaluated on one dataset."""
    symbol: str
    interval: str
    metrics: PerformanceMetrics
    benchmark: BenchmarkResult
    alpha: float
    qualification: QualificationResult
    wf_result: WalkForwardResult | None = None
    regime_state: RegimeState | None = None
    regime_tradable: bool = True
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParamSetResult:
    """Cross-dataset result for one parameter set."""
    strategy_name: str
    params: dict[str, Any]
    datasets_evaluated: int = 0
    datasets_qualified: int = 0
    datasets_profitable: int = 0
    datasets_tradable: int = 0  # Regime-approved datasets
    avg_return: float = 0.0
    avg_sharpe: float = 0.0
    avg_alpha: float = 0.0
    max_drawdown: float = 0.0
    avg_drawdown: float = 0.0
    avg_profit_factor: float = 0.0
    total_trades: int = 0
    consistency_score: float = 0.0
    all_qualified: bool = False
    pass_rate: float = 0.0  # fraction of datasets that qualify
    # Walk-forward aggregates
    avg_oos_return: float = 0.0
    avg_degradation: float = 0.0  # avg test_return / train_return
    # Composite robustness score
    robustness_score: float = 0.0
    per_dataset: list[ParamSetDatasetResult] = field(default_factory=list)
    # Per-dataset approval decisions
    approvals: list[DatasetApproval] = field(default_factory=list)
    grid_diagnostics: dict[str, Any] = field(default_factory=dict)

    def summary_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "params": self.params,
            "datasets_evaluated": self.datasets_evaluated,
            "datasets_qualified": self.datasets_qualified,
            "datasets_profitable": self.datasets_profitable,
            "datasets_tradable": self.datasets_tradable,
            "avg_return": round(self.avg_return, 4),
            "avg_sharpe": round(self.avg_sharpe, 4),
            "avg_alpha": round(self.avg_alpha, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_profit_factor": round(self.avg_profit_factor, 4),
            "total_trades": self.total_trades,
            "consistency_score": round(self.consistency_score, 4),
            "pass_rate": round(self.pass_rate, 4),
            "avg_oos_return": round(self.avg_oos_return, 4),
            "avg_degradation": round(self.avg_degradation, 4),
            "robustness_score": round(self.robustness_score, 4),
            "all_qualified": self.all_qualified,
            "approved_datasets": [
                {"symbol": a.symbol, "interval": a.interval, "approved": a.approved, "reasons": a.reasons}
                for a in self.approvals
            ],
            "grid_diagnostics": self.grid_diagnostics,
        }


@dataclass
class OptimizationResult:
    """Full optimization result for one strategy across all param sets."""
    strategy_name: str
    total_param_sets: int = 0
    evaluated_param_sets: int = 0
    datasets_used: int = 0
    param_results: list[ParamSetResult] = field(default_factory=list)
    best_robust: ParamSetResult | None = None
    best_qualified: ParamSetResult | None = None
    best_pass_rate: ParamSetResult | None = None  # Best by pass-rate


def compute_robustness_score(psr: ParamSetResult) -> float:
    """Compute a composite robustness score.

    Weights:
    - consistency (30%): fraction of datasets profitable
    - risk-quality (25%): avg Sharpe, penalized for high drawdown
    - alpha (15%): avg alpha vs benchmark
    - walk-forward quality (20%): OOS return and degradation
    - volume (10%): trade count

    Score range roughly 0..1 but can go beyond.
    """
    consistency_part = psr.consistency_score * 0.30

    # Risk-quality: Sharpe contribution, clipped and scaled
    sharpe_contrib = max(-1.0, min(3.0, psr.avg_sharpe)) / 3.0
    dd_penalty = max(0, psr.max_drawdown - 0.10) * 2
    risk_part = max(0, sharpe_contrib - dd_penalty) * 0.25

    # Alpha contribution
    alpha_part = max(-0.1, min(0.5, psr.avg_alpha)) * 0.15

    # Walk-forward quality — reward positive OOS, penalize degradation
    oos_return_contrib = max(-0.1, min(0.2, psr.avg_oos_return)) * 0.5
    # Degradation closer to 1.0 = good (train ≈ test); < 0 or > 2 = bad
    degrad_factor = 0.0
    if psr.avg_degradation > 0:
        degrad_factor = min(1.0, psr.avg_degradation) * 0.5
    wf_part = (oos_return_contrib + degrad_factor) * 0.20

    # Trade volume
    trade_norm = min(1.0, psr.total_trades / 100) * 0.10

    return consistency_part + risk_part + alpha_part + wf_part + trade_norm


def generate_param_combinations(param_grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Generate all parameter combinations from a grid."""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def get_param_grid(strategy_name: str, profile: str = "standard") -> dict[str, list[Any]]:
    """Return the optimizer grid for a strategy/profile pair."""
    normalized_profile = str(profile or "standard").lower()
    if normalized_profile not in PARAM_GRID_PROFILES:
        raise ValueError(
            f"Unknown optimizer profile '{profile}'. "
            f"Expected one of: {', '.join(sorted(PARAM_GRID_PROFILES))}"
        )

    profile_grid = PARAM_GRID_PROFILES.get(normalized_profile, {}).get(strategy_name)
    source = profile_grid or DEFAULT_PARAM_GRIDS.get(strategy_name, {})
    return {key: list(values) for key, values in source.items()}


def _evaluate_dataset_for_param_set(
    strategy_name: str,
    params: dict[str, Any],
    symbol: str,
    interval: str,
    candles: pd.DataFrame,
    engine: BacktestEngine,
    thresholds: QualificationThresholds,
    regime_config: RegimeConfig | None = None,
    run_walk_forward: bool = True,
    wf_windows: int = 2,
) -> ParamSetDatasetResult | None:
    """Evaluate one param set on one dataset with regime + WF."""

    # --- Regime gating ---
    regime_state = assess_regime(candles, regime_config)

    try:
        strategy_inst = get_strategy(strategy_name, params=params)
        bt_result = engine.run(strategy_inst, candles, symbol=symbol, interval=interval)
        metrics = calculate_metrics(bt_result)
    except Exception:
        return None

    benchmark = compute_benchmark(candles, symbol=symbol)
    alpha = metrics.total_return_pct - benchmark.total_return_pct

    # --- Walk-forward validation ---
    wf = None
    if run_walk_forward and len(candles) >= 100:
        try:
            wf_strategy = get_strategy(strategy_name, params=params)
            wf = walk_forward_validate(
                wf_strategy, candles,
                symbol=symbol, interval=interval,
                n_windows=wf_windows,
                initial_capital=engine.initial_capital,
                fee_pct=engine.fee_pct,
                slippage_pct=engine.slippage_pct,
            )
        except Exception:
            pass

    qual = check_qualification(
        metrics, thresholds=thresholds,
        wf_result=wf,
        benchmark_return_pct=benchmark.total_return_pct,
    )

    return ParamSetDatasetResult(
        symbol=symbol, interval=interval,
        metrics=metrics, benchmark=benchmark,
        alpha=alpha, qualification=qual,
        wf_result=wf, regime_state=regime_state,
        regime_tradable=regime_state.is_tradable,
        diagnostics=dict(bt_result.diagnostics),
    )


def _build_approval(
    pdr: ParamSetDatasetResult,
    strategy_name: str,
    params: dict[str, Any],
) -> DatasetApproval:
    """Build a DatasetApproval from evaluation results."""
    reasons: list[str] = []
    approved = True

    if not pdr.regime_tradable:
        approved = False
        reasons.append(f"regime_blocked: {', '.join(pdr.regime_state.reasons) if pdr.regime_state else 'unknown'}")

    if not pdr.qualification.qualified:
        approved = False
        reasons.extend(pdr.qualification.failures)

    if not reasons:
        reasons.append("approved")

    return DatasetApproval(
        symbol=pdr.symbol, interval=pdr.interval,
        strategy_name=strategy_name, params=params,
        approved=approved, reasons=reasons,
        regime_state=pdr.regime_state,
        qualification=pdr.qualification,
        wf_result=pdr.wf_result,
    )


def _evaluate_param_set_across_datasets(
    *,
    strategy_name: str,
    params: dict[str, Any],
    valid_datasets: dict[tuple[str, str], pd.DataFrame],
    engine_kwargs: dict[str, Any],
    thresholds: QualificationThresholds,
    regime_config: RegimeConfig | None,
    run_walk_forward: bool,
    wf_windows: int,
) -> ParamSetResult:
    """Evaluate one parameter set across all valid datasets."""
    engine = BacktestEngine(**engine_kwargs)
    psr = ParamSetResult(strategy_name=strategy_name, params=params)
    dataset_results: list[ParamSetDatasetResult] = []
    approvals: list[DatasetApproval] = []

    for (symbol, interval), candles in valid_datasets.items():
        pdr = _evaluate_dataset_for_param_set(
            strategy_name, params, symbol, interval, candles, engine,
            thresholds, regime_config, run_walk_forward, wf_windows,
        )
        if pdr is None:
            continue

        dataset_results.append(pdr)
        approvals.append(_build_approval(pdr, strategy_name, params))

    if not dataset_results:
        return psr

    n = len(dataset_results)
    returns = [dr.metrics.total_return_pct for dr in dataset_results]
    sharpes = [dr.metrics.sharpe_ratio for dr in dataset_results]
    drawdowns = [dr.metrics.max_drawdown_pct for dr in dataset_results]
    alphas = [dr.alpha for dr in dataset_results]
    pfs = [dr.metrics.profit_factor for dr in dataset_results]
    trades = sum(dr.metrics.total_trades for dr in dataset_results)
    qualified_count = sum(1 for dr in dataset_results if dr.qualification.qualified)
    profitable_count = sum(1 for r in returns if r > 0)
    tradable_count = sum(1 for dr in dataset_results if dr.regime_tradable)

    oos_returns = [
        dr.wf_result.avg_test_return
        for dr in dataset_results
        if dr.wf_result and dr.wf_result.total_windows > 0
    ]
    degradations = [
        dr.wf_result.degradation_ratio
        for dr in dataset_results
        if dr.wf_result and dr.wf_result.total_windows > 0
    ]

    psr.datasets_evaluated = n
    psr.datasets_qualified = qualified_count
    psr.datasets_profitable = profitable_count
    psr.datasets_tradable = tradable_count
    psr.avg_return = sum(returns) / n
    psr.avg_sharpe = sum(sharpes) / n
    psr.avg_alpha = sum(alphas) / n
    psr.max_drawdown = max(drawdowns)
    psr.avg_drawdown = sum(drawdowns) / n
    psr.avg_profit_factor = sum(pfs) / n
    psr.total_trades = trades
    psr.consistency_score = profitable_count / n
    psr.pass_rate = qualified_count / n
    psr.all_qualified = qualified_count == n
    psr.avg_oos_return = sum(oos_returns) / len(oos_returns) if oos_returns else 0.0
    psr.avg_degradation = sum(degradations) / len(degradations) if degradations else 0.0
    psr.per_dataset = dataset_results
    psr.approvals = approvals
    psr.grid_diagnostics = aggregate_grid_diagnostics(
        [dr.diagnostics for dr in dataset_results]
    )
    psr.robustness_score = compute_robustness_score(psr)
    return psr


def optimize_strategy_matrix(
    strategy_name: str,
    candles_by_dataset: dict[tuple[str, str], pd.DataFrame],
    param_grid: dict[str, list[Any]] | None = None,
    thresholds: QualificationThresholds | None = None,
    initial_capital: float = 10_000.0,
    fee_pct: float = 0.001,
    slippage_pct: float = 0.001,
    max_combinations: int = 100,
    run_walk_forward: bool = True,
    wf_windows: int = 2,
    regime_config: RegimeConfig | None = None,
    min_pass_rate: float = 0.5,
    workers: int = 1,
    progress_callback: Any | None = None,
) -> OptimizationResult:
    """Optimize a strategy's parameters across a matrix of datasets.

    Each parameter combination is evaluated on every dataset with:
    - Optional walk-forward train/test validation
    - Regime gating
    - Per-dataset qualification and approval

    Results ranked by cross-dataset robustness, not single-run profit.

    Args:
        strategy_name: Registered strategy name.
        candles_by_dataset: Dict mapping (symbol, interval) → DataFrame.
        param_grid: Parameter grid. Uses DEFAULT_PARAM_GRIDS if None.
        thresholds: Qualification thresholds.
        initial_capital: Starting capital per backtest.
        fee_pct: Fee percentage.
        slippage_pct: Slippage percentage.
        max_combinations: Safety cap on parameter combinations.
        run_walk_forward: Whether to run walk-forward validation.
        wf_windows: Number of walk-forward windows per dataset.
        regime_config: Regime filter configuration.
        min_pass_rate: Minimum fraction of datasets that must qualify.
        workers: Parameter sets to evaluate concurrently. ``1`` keeps deterministic serial behavior.

    Returns:
        OptimizationResult with all param sets ranked by robustness.
    """
    thresholds = thresholds or QualificationThresholds()
    if param_grid is None:
        param_grid = DEFAULT_PARAM_GRIDS.get(strategy_name, {})

    if not param_grid:
        logger.warning("optimizer_no_grid", strategy=strategy_name)
        return OptimizationResult(strategy_name=strategy_name)

    combinations = generate_param_combinations(param_grid)
    if len(combinations) > max_combinations:
        logger.warning(
            "optimizer_grid_too_large",
            strategy=strategy_name,
            total=len(combinations),
            max=max_combinations,
        )
        combinations = combinations[:max_combinations]

    engine_kwargs = {
        "initial_capital": initial_capital,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
    }

    valid_datasets = {
        k: v for k, v in candles_by_dataset.items()
        if not v.empty and len(v) >= 50
    }

    logger.info(
        "matrix_optimization_start",
        strategy=strategy_name,
        param_sets=len(combinations),
        datasets=len(valid_datasets),
        walk_forward=run_walk_forward,
        workers=max(1, int(workers or 1)),
    )

    param_results: list[ParamSetResult] = []
    worker_count = max(1, int(workers or 1))

    if worker_count == 1 or len(combinations) <= 1:
        for completed, params in enumerate(combinations, 1):
            param_results.append(_evaluate_param_set_across_datasets(
                strategy_name=strategy_name,
                params=params,
                valid_datasets=valid_datasets,
                engine_kwargs=engine_kwargs,
                thresholds=thresholds,
                regime_config=regime_config,
                run_walk_forward=run_walk_forward,
                wf_windows=wf_windows,
            ))
            if progress_callback:
                progress_callback(completed, len(combinations))
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    _evaluate_param_set_across_datasets,
                    strategy_name=strategy_name,
                    params=params,
                    valid_datasets=valid_datasets,
                    engine_kwargs=engine_kwargs,
                    thresholds=thresholds,
                    regime_config=regime_config,
                    run_walk_forward=run_walk_forward,
                    wf_windows=wf_windows,
                )
                for params in combinations
            ]
            for completed, future in enumerate(as_completed(futures), 1):
                param_results.append(future.result())
                if progress_callback:
                    progress_callback(completed, len(combinations))

    # Sort by robustness score (primary), then avg_sharpe (tiebreak)
    param_results.sort(
        key=lambda p: (p.robustness_score, p.avg_sharpe),
        reverse=True,
    )

    # Identify bests
    best_robust = param_results[0] if param_results else None

    best_qualified = None
    for psr in param_results:
        if psr.all_qualified and psr.datasets_evaluated > 0:
            best_qualified = psr
            break

    best_pass_rate = None
    for psr in param_results:
        if psr.pass_rate >= min_pass_rate and psr.datasets_evaluated > 0:
            best_pass_rate = psr
            break

    result = OptimizationResult(
        strategy_name=strategy_name,
        total_param_sets=len(combinations),
        evaluated_param_sets=len([p for p in param_results if p.datasets_evaluated > 0]),
        datasets_used=len(valid_datasets),
        param_results=param_results,
        best_robust=best_robust,
        best_qualified=best_qualified,
        best_pass_rate=best_pass_rate,
    )

    logger.info(
        "matrix_optimization_complete",
        strategy=strategy_name,
        evaluated=result.evaluated_param_sets,
        best_robust_score=round(best_robust.robustness_score, 4) if best_robust else 0,
        has_qualified=best_qualified is not None,
        has_pass_rate=best_pass_rate is not None,
    )

    return result
