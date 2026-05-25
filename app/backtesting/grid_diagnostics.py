"""Diagnostics for Grid/DCA research runs."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any


GRID_ACTIONS = ("open", "scale_in", "take_profit", "stop_exit", "end_of_data")


def summarize_grid_backtest(
    *,
    trades: list[Any],
    signals: list[Any],
    initial_capital: float,
    final_equity: float,
) -> dict[str, Any]:
    """Summarize Grid/DCA activity from strategy signals and completed trades."""
    action_counts: Counter[str] = Counter()
    for signal in signals:
        action = str(getattr(signal, "metadata", {}).get("grid_action", ""))
        if action:
            action_counts[action] += 1

    grid_trades = [
        trade for trade in trades
        if getattr(trade, "metadata", {}).get("grid_id") or action_counts
    ]
    if not action_counts and not grid_trades:
        return {}

    exit_counts: Counter[str] = Counter()
    pnl_by_exit: defaultdict[str, float] = defaultdict(float)
    pnl_by_max_level: defaultdict[str, float] = defaultdict(float)
    hold_bars: list[int] = []
    filled_level_counts: list[int] = []
    allocations: list[float] = []

    for trade in grid_trades:
        metadata = getattr(trade, "metadata", {}) or {}
        exit_action = _trade_exit_action(trade, metadata)
        exit_counts[exit_action] += 1
        pnl = float(getattr(trade, "pnl", 0.0) or 0.0)
        pnl_by_exit[exit_action] += pnl

        filled_levels = _filled_levels(metadata)
        if filled_levels:
            filled_level_counts.append(len(filled_levels))
            pnl_by_max_level[str(max(filled_levels))] += pnl

        allocation = _float_or_none(metadata.get("projected_grid_notional_pct"))
        if allocation is not None:
            allocations.append(allocation)

        entry_bar = _int_or_none(metadata.get("entry_bar_index"))
        exit_bar = _int_or_none(metadata.get("exit_bar_index"))
        if entry_bar is not None and exit_bar is not None and exit_bar >= entry_bar:
            hold_bars.append(exit_bar - entry_bar)

    total_pnl = sum(float(getattr(trade, "pnl", 0.0) or 0.0) for trade in grid_trades)
    completed = len(grid_trades)
    scale_in_count = action_counts.get("scale_in", 0)
    open_count = action_counts.get("open", 0)

    return {
        "signals": {action: int(action_counts.get(action, 0)) for action in GRID_ACTIONS},
        "completed_baskets": completed,
        "scale_ins_per_open": round(scale_in_count / open_count, 4) if open_count else 0.0,
        "exit_counts": dict(exit_counts),
        "take_profit_count": int(exit_counts.get("take_profit", 0)),
        "stop_exit_count": int(exit_counts.get("stop_exit", 0)),
        "end_of_data_count": int(exit_counts.get("end_of_data", 0)),
        "total_pnl": round(total_pnl, 4),
        "avg_pnl_per_basket": round(total_pnl / completed, 4) if completed else 0.0,
        "return_pct": round((final_equity / initial_capital) - 1, 6) if initial_capital > 0 else 0.0,
        "avg_hold_bars": round(mean(hold_bars), 2) if hold_bars else 0.0,
        "avg_filled_levels": round(mean(filled_level_counts), 2) if filled_level_counts else 0.0,
        "max_filled_levels": max(filled_level_counts) if filled_level_counts else 0,
        "max_allocation_pct_observed": round(max(allocations), 4) if allocations else 0.0,
        "pnl_by_exit": {key: round(value, 4) for key, value in pnl_by_exit.items()},
        "pnl_by_max_level": {key: round(value, 4) for key, value in pnl_by_max_level.items()},
    }


def aggregate_grid_diagnostics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-dataset Grid/DCA diagnostics for optimizer summaries."""
    diagnostics = [item for item in items if item]
    if not diagnostics:
        return {}

    signal_counts: Counter[str] = Counter()
    exit_counts: Counter[str] = Counter()
    pnl_by_exit: defaultdict[str, float] = defaultdict(float)
    pnl_by_max_level: defaultdict[str, float] = defaultdict(float)
    completed = 0
    total_pnl = 0.0
    hold_values: list[float] = []
    filled_values: list[float] = []
    max_alloc = 0.0
    max_levels = 0

    for diag in diagnostics:
        signal_counts.update({key: int(value) for key, value in diag.get("signals", {}).items()})
        exit_counts.update({key: int(value) for key, value in diag.get("exit_counts", {}).items()})
        completed += int(diag.get("completed_baskets", 0) or 0)
        total_pnl += float(diag.get("total_pnl", 0.0) or 0.0)
        if diag.get("avg_hold_bars", 0):
            hold_values.append(float(diag["avg_hold_bars"]))
        if diag.get("avg_filled_levels", 0):
            filled_values.append(float(diag["avg_filled_levels"]))
        max_alloc = max(max_alloc, float(diag.get("max_allocation_pct_observed", 0.0) or 0.0))
        max_levels = max(max_levels, int(diag.get("max_filled_levels", 0) or 0))
        for key, value in diag.get("pnl_by_exit", {}).items():
            pnl_by_exit[key] += float(value)
        for key, value in diag.get("pnl_by_max_level", {}).items():
            pnl_by_max_level[key] += float(value)

    open_count = signal_counts.get("open", 0)
    scale_count = signal_counts.get("scale_in", 0)
    return {
        "datasets_with_grid_activity": len(diagnostics),
        "signals": {action: int(signal_counts.get(action, 0)) for action in GRID_ACTIONS},
        "completed_baskets": completed,
        "scale_ins_per_open": round(scale_count / open_count, 4) if open_count else 0.0,
        "exit_counts": dict(exit_counts),
        "take_profit_count": int(exit_counts.get("take_profit", 0)),
        "stop_exit_count": int(exit_counts.get("stop_exit", 0)),
        "end_of_data_count": int(exit_counts.get("end_of_data", 0)),
        "total_pnl": round(total_pnl, 4),
        "avg_pnl_per_basket": round(total_pnl / completed, 4) if completed else 0.0,
        "avg_hold_bars": round(mean(hold_values), 2) if hold_values else 0.0,
        "avg_filled_levels": round(mean(filled_values), 2) if filled_values else 0.0,
        "max_filled_levels": max_levels,
        "max_allocation_pct_observed": round(max_alloc, 4),
        "pnl_by_exit": {key: round(value, 4) for key, value in pnl_by_exit.items()},
        "pnl_by_max_level": {key: round(value, 4) for key, value in pnl_by_max_level.items()},
    }


def _trade_exit_action(trade: Any, metadata: dict[str, Any]) -> str:
    action = str(metadata.get("grid_action", ""))
    if action in {"take_profit", "stop_exit"}:
        return action
    reason = str(getattr(trade, "exit_reason", "signal"))
    if reason in {"stop_loss", "stop_exit"}:
        return "stop_exit"
    if reason == "take_profit":
        return "take_profit"
    if reason == "end_of_data":
        return "end_of_data"
    return action or reason


def _filled_levels(metadata: dict[str, Any]) -> list[int]:
    raw = metadata.get("filled_grid_levels", [])
    if not isinstance(raw, list):
        return []
    levels: list[int] = []
    for item in raw:
        value = _int_or_none(item)
        if value is not None:
            levels.append(value)
    return levels


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
