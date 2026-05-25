"""CLI entrypoints using Typer."""

from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="trading-bot",
    help="Binance Spot Automated Trading Bot CLI",
    add_completion=False,
)
console = Console()

if TYPE_CHECKING:
    from app.strategies.base import BaseStrategy


def _init():
    """Initialize the application for CLI commands."""
    from app.core.logging import setup_logging
    setup_logging()
    from app.config.settings import get_settings
    settings = get_settings()

    # Register strategies
    import app.strategies.ema_atr  # noqa: F401
    import app.strategies.rsi_mean_reversion  # noqa: F401
    import app.strategies.bollinger_mean_reversion  # noqa: F401
    import app.strategies.breakout  # noqa: F401
    import app.strategies.regime_strategy  # noqa: F401
    import app.strategies.momentum_continuation  # noqa: F401
    import app.strategies.pullback_uptrend  # noqa: F401
    import app.strategies.volatility_breakout  # noqa: F401
    import app.strategies.hybrid_grid_dca  # noqa: F401

    return settings


def _get_thresholds(settings):
    """Build QualificationThresholds from settings."""
    from app.backtesting.validation import QualificationThresholds
    return QualificationThresholds(
        min_total_return_pct=settings.qual_min_return_pct,
        min_sharpe_ratio=settings.qual_min_sharpe,
        min_total_trades=settings.qual_min_trades,
        max_drawdown_pct=settings.qual_max_drawdown_pct,
        min_profit_factor=settings.qual_min_profit_factor,
        min_oos_consistency=settings.qual_min_oos_consistency,
        min_benchmark_alpha_pct=settings.qual_min_benchmark_alpha_pct,
    )


_INTERVAL_SECONDS = {
    "1s": 1,
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "8h": 8 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
    "1M": 30 * 24 * 60 * 60,
}


def _candle_limit_for_days(interval: str, days: int) -> int:
    """Return the candle count needed to cover a lookback window."""
    seconds = _INTERVAL_SECONDS.get(str(interval).strip())
    if not seconds:
        raise ValueError(f"Unsupported Binance interval: {interval}")

    lookback_seconds = max(1, int(days)) * 24 * 60 * 60
    return max(1, math.ceil(lookback_seconds / seconds))


def _resolve_strategy(
    strategy_name: str | None,
    settings,
    approved_key_filter: set[str] | None = None,
) -> tuple["BaseStrategy", set[str], bool]:
    """Resolve strategy with approval-driven routing.

    Resolution order:
    1. Manual --strategy flag -> always allowed (bypass approvals)
    2. Approved combinations from DB -> use best approved strategy+params
    3. Qualified winner from backtest -> fallback with warning
    4. Default first registered -> last resort

    Returns:
        (strategy, approved_symbols, is_manual_override)
        approved_symbols: set of "SYMBOL:INTERVAL" strings that are approved.
            Empty set = stay in cash for all datasets.
        is_manual_override: True if manual --strategy was used.
    """
    from app.strategies.registry import get_strategy, list_strategies as ls

    # 1) Manual selection - always allowed, bypasses approvals
    if strategy_name:
        available = ls()
        if strategy_name not in available:
            console.print(f"[red]Strategy '{strategy_name}' not found.[/red]")
            console.print(f"[dim]Available: {', '.join(available)}[/dim]")
            raise typer.Exit(1)
        strategy = get_strategy(strategy_name)
        console.print(f"[cyan]Strategy (manual override): {strategy.name}[/cyan]")
        console.print("[yellow][WARN] Manual override: bypassing approval checks[/yellow]")
        return strategy, set(), True  # Empty approvals = trade everything (override)

    # 2) Approval-driven: load from approved_combinations table
    try:
        from app.persistence.db import get_session
        from app.persistence.repositories import ApprovedCombinationRepository
        session = get_session(settings.database_url)
        acr = ApprovedCombinationRepository(session)

        approved = acr.get_approved()
        if approved_key_filter is not None:
            approved = [
                combo for combo in approved
                if f"{combo.symbol}:{combo.interval}" in approved_key_filter
            ]
        if approved:
            # Use the best approved combination's strategy+params
            best = approved[0]  # Sorted by robustness desc
            params = json.loads(best.parameters) if best.parameters else None
            available = ls()

            if best.strategy_name in available:
                strategy = get_strategy(best.strategy_name, params=params)
                approved_keys = {
                    f"{a.symbol}:{a.interval}" for a in approved
                }

                console.print(
                    f"[bold green]Strategy (approval-driven): {strategy.name} "
                    f"(robustness={best.robustness_score:.3f}, "
                    f"pass-rate={best.pass_rate:.0%})[/bold green]"
                )
                console.print(f"[dim]Params: {params}[/dim]")
                console.print(f"[green]Approved datasets ({len(approved_keys)}):[/green]")
                for a in approved:
                    console.print(f"[green]  [OK] {a.symbol}/{a.interval}[/green]")

                session.close()
                return strategy, approved_keys, False

        session.close()
    except Exception:
        pass

    # 3) Fallback: qualified winner from backtest
    try:
        from app.persistence.db import get_session
        from app.persistence.repositories import SelectedStrategyRepository
        session = get_session(settings.database_url)
        repo = SelectedStrategyRepository(session)

        winner = repo.get_latest_qualified_winner()
        if winner:
            available = ls()
            if winner.strategy_name in available:
                params = json.loads(winner.parameters) if winner.parameters else None
                strategy = get_strategy(winner.strategy_name, params=params)
                console.print(
                    f"[bold yellow]Strategy (qualified winner, no approvals): {strategy.name} "
                    f"(score={winner.composite_score:.4f})[/bold yellow]"
                )
                console.print("[yellow][WARN] No approved combinations found. Using qualified winner.[/yellow]")
                session.close()
                return strategy, set(), False  # No per-dataset approvals

        unqualified = repo.get_latest_winner()
        session.close()

        if unqualified and not unqualified.qualified:
            failures = json.loads(unqualified.qualification_failures) if unqualified.qualification_failures else []
            console.print("[bold yellow][WARN] No approved or qualified strategy found.[/bold yellow]")
            console.print(f"[yellow]Latest winner '{unqualified.strategy_name}' failed qualification:[/yellow]")
            for f in failures:
                console.print(f"[yellow]  - {f}[/yellow]")
            console.print("[yellow]System will stay in cash. Run 'optimize --wf' for better results.[/yellow]\n")
    except Exception:
        pass

    # 4) Last resort: first registered (stay in cash - no approvals)
    available = ls()
    if not available:
        console.print("[red]No strategies registered.[/red]")
        raise typer.Exit(1)
    strategy = get_strategy(available[0])
    console.print(f"[cyan]Strategy (default): {strategy.name}[/cyan]")
    console.print("[bold yellow][WARN] No approved combinations. Trading will stay in cash.[/bold yellow]")
    return strategy, set(), False


def _filter_approved_symbols(
    configured_symbols: list[str],
    approved_keys: set[str],
    interval: str,
) -> tuple[list[str], list[str]]:
    """Filter configured symbols by exact approved symbol+interval key."""
    interval = str(interval)
    active = [s for s in configured_symbols if f"{s}:{interval}" in approved_keys]
    skipped = [s for s in configured_symbols if s not in active]
    return active, skipped


def _runtime_ready_keys_from_report(report: dict[str, Any]) -> set[str]:
    """Return approved symbol:interval keys that pass the recent replay gate."""
    return set(_runtime_ready_key_order_from_report(report))


def _runtime_ready_key_order_from_report(report: dict[str, Any]) -> list[str]:
    """Return runtime-ready keys, preferring stronger recent replay diagnostics."""
    ranked: list[tuple[float, float, int, str]] = []
    for idx, combo in enumerate(report.get("approved_combinations", [])):
        recent_replay = combo.get("recent_replay")
        if recent_replay and recent_replay.get("verdict") != "approved":
            continue

        diagnostics = recent_replay.get("diagnostics", {}) if recent_replay else {}
        try:
            recent_pnl = float(diagnostics.get("net_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            recent_pnl = 0.0
        try:
            robustness = float(combo.get("robustness_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            robustness = 0.0

        ranked.append((
            recent_pnl,
            robustness,
            -idx,
            f"{combo['symbol']}:{combo['interval']}",
        ))

    ranked.sort(reverse=True)
    return [key for *_unused, key in ranked]


def _paper_readiness_gate_applies(
    strategy_name: str | None,
    sim: bool,
    persist_sim: bool,
    sim_user_specified: bool,
) -> bool:
    """Return whether paper routing should require recent replay readiness."""
    if strategy_name is not None:
        return False
    return (not sim) or persist_sim or not sim_user_specified


def _select_approval_param_set(
    param_sets: list[tuple[str, Any]],
) -> tuple[str, Any] | None:
    """Select the parameter set whose approvals should drive runtime routing."""
    if not param_sets:
        return None

    all_qualified = [
        item for item in param_sets
        if getattr(item[1], "all_qualified", False)
    ]
    candidates = all_qualified or param_sets

    return max(
        candidates,
        key=lambda item: (
            int(bool(getattr(item[1], "all_qualified", False))),
            int(getattr(item[1], "datasets_qualified", 0)),
            float(getattr(item[1], "pass_rate", 0.0)),
            float(getattr(item[1], "robustness_score", 0.0)),
            float(getattr(item[1], "avg_sharpe", 0.0)),
        ),
    )


def _build_approval_records(
    strategy_name: str,
    param_set_result: Any,
    min_pass_rate: float,
) -> list[Any]:
    """Build persistence records while enforcing the optimizer pass-rate gate."""
    from app.persistence.models import ApprovedCombination

    pass_rate = float(getattr(param_set_result, "pass_rate", 0.0))
    pass_rate_ok = pass_rate >= float(min_pass_rate)
    threshold_reason = (
        f"param_set_pass_rate_below_threshold: "
        f"{pass_rate:.0%} < {float(min_pass_rate):.0%}"
    )

    records = []
    for approval in getattr(param_set_result, "approvals", []):
        reasons = list(getattr(approval, "reasons", []) or [])
        approved = bool(getattr(approval, "approved", False))
        if not pass_rate_ok:
            approved = False
            if threshold_reason not in reasons:
                reasons.append(threshold_reason)

        regime_state = getattr(approval, "regime_state", None)
        qualification = getattr(approval, "qualification", None)
        wf_result = getattr(approval, "wf_result", None)

        records.append(ApprovedCombination(
            strategy_name=strategy_name,
            parameters=json.dumps(getattr(param_set_result, "params", {}), default=str),
            symbol=getattr(approval, "symbol", ""),
            interval=getattr(approval, "interval", ""),
            approved=approved,
            reasons=json.dumps(reasons, default=str),
            robustness_score=getattr(param_set_result, "robustness_score", 0.0),
            pass_rate=pass_rate,
            regime_tradable=(
                bool(getattr(regime_state, "is_tradable", True))
                if regime_state is not None
                else True
            ),
            regime_state=(
                str(getattr(regime_state, "regime", "unknown"))
                if regime_state is not None
                else "unknown"
            ),
            qualification_failures=json.dumps(
                list(getattr(qualification, "failures", []) or []),
                default=str,
            ),
            oos_return=float(getattr(wf_result, "avg_test_return", 0.0) or 0.0),
            degradation_ratio=float(getattr(wf_result, "degradation_ratio", 0.0) or 0.0),
        ))

    return records


def _candles_df_to_replay_dicts(df: Any, symbol: str, limit: int = 1000) -> list[dict[str, Any]]:
    """Convert the latest DataFrame rows to orchestrator replay candle dicts."""
    if df is None or df.empty:
        return []

    replay_df = df.tail(limit).copy()
    candles: list[dict[str, Any]] = []
    for _, row in replay_df.iterrows():
        candles.append({
            "symbol": symbol,
            "open_time": row.get("open_time"),
            "close_time": row.get("close_time", row.get("open_time")),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "is_closed": True,
        })
    return candles


def _candle_records_to_replay_dicts(records: list[Any]) -> list[dict[str, Any]]:
    """Convert persisted Candle rows to orchestrator replay candle dicts."""
    return [
        {
            "symbol": c.symbol,
            "open_time": c.open_time,
            "close_time": c.close_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "is_closed": True,
        }
        for c in records
    ]


def _candle_records_to_frame(records: list[Any]) -> Any:
    """Convert persisted Candle rows to a research DataFrame."""
    import pandas as pd

    return pd.DataFrame([{
        "open_time": c.open_time,
        "close_time": c.close_time,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
    } for c in records])


def _research_min_candles(interval: str, days: int) -> int:
    """Minimum candles required for a requested research lookback."""
    expected = _candle_limit_for_days(interval, days)
    return max(1, min(expected, max(60, math.floor(expected * 0.90))))


def _audit_min_candles(limit: int) -> int:
    """Minimum candles required for parity audit replay."""
    normalized_limit = max(1, limit)
    return max(1, min(normalized_limit, max(60, min(500, math.floor(normalized_limit * 0.80)))))


def _validate_research_candles(
    df: Any,
    symbol: str,
    interval: str,
    *,
    min_candles: int,
    require_fresh: bool = True,
) -> Any:
    """Validate candles and return a data-quality report."""
    from app.backtesting.data_quality import validate_candle_frame

    return validate_candle_frame(
        df,
        symbol,
        interval,
        min_candles=min_candles,
        require_fresh=require_fresh,
    )


def _print_quality_failure(report: Any) -> None:
    """Print one compact data-quality failure."""
    console.print(
        f"  [red][ERR] {report.symbol}/{report.interval}: "
        f"data quality failed[/red]"
    )
    for reason in report.errors:
        console.print(f"    [red]- {reason}[/red]")


def _audit_result_from_quality_failure(combo: Any, report: Any, total_candles: int) -> Any:
    """Build an audit result that downgrades an approved combo on data quality."""
    from app.backtesting.parity_auditor import AuditResult, ReplayDiagnostics

    params = json.loads(combo.parameters) if combo.parameters else {}
    result = AuditResult(
        symbol=combo.symbol,
        interval=combo.interval,
        strategy_name=combo.strategy_name,
        params=params,
        statistically_approved=combo.approved,
        robustness_score=combo.robustness_score or 0.0,
        pass_rate=combo.pass_rate or 0.0,
        diagnostics=ReplayDiagnostics(total_candles=total_candles),
        runtime_viable=False,
        viability_reasons=[f"data_quality_{reason}" for reason in report.errors],
    )
    result.verdict = "downgraded" if combo.approved else "rejected"
    return result


def _audit_approval_records_for_runtime(
    combo_records: list[Any],
    candles_by_dataset: dict[tuple[str, str], Any],
    risk_config: dict[str, Any],
    regime_config: Any | None,
    limit: int = 1000,
) -> int:
    """Downgrade newly generated approvals that fail runtime replay."""
    from app.backtesting.parity_auditor import audit_combination

    downgraded = 0
    for combo in combo_records:
        if not combo.approved:
            continue

        candles = _candles_df_to_replay_dicts(
            candles_by_dataset.get((combo.symbol, combo.interval)),
            combo.symbol,
            limit=limit,
        )
        result = audit_combination(combo, candles, risk_config, regime_config)
        if result.verdict == "approved":
            continue

        existing_reasons = json.loads(combo.reasons or "[]")
        combo.approved = False
        combo.reasons = json.dumps(
            existing_reasons + [f"runtime_{reason}" for reason in result.viability_reasons],
            default=str,
        )
        downgraded += 1

    return downgraded


def _runtime_interval_for_paper(
    sim: bool,
    sim_interval: str | None,
    trade_interval: str,
) -> str:
    """Return the interval the orchestrator should report and subscribe/replay."""
    if sim and sim_interval:
        return str(sim_interval)
    return str(trade_interval)


def _sim_replay_route(
    is_override: bool,
    approved_keys: set[str],
    requested_key: str,
    user_specified: bool,
    preferred_keys: list[str] | None = None,
) -> tuple[str, str]:
    """Decide whether sim replay can run and which symbol:interval to use."""
    if is_override:
        return "allow", requested_key
    if not approved_keys:
        return "block_no_approvals", requested_key
    if requested_key in approved_keys:
        return "allow", requested_key
    if user_specified:
        return "block_unapproved", requested_key
    for key in preferred_keys or []:
        if key in approved_keys:
            return "auto_select", key
    return "auto_select", sorted(approved_keys)[0]


def _risk_config_from_settings(settings: Any) -> dict[str, Any]:
    """Build runtime replay risk settings without assuming a full Settings object."""
    return {
        "taker_fee_pct": getattr(settings, "taker_fee_pct", 0.001),
        "slippage_pct": getattr(settings, "slippage_pct", 0.001),
        "max_risk_per_trade": getattr(settings, "max_risk_per_trade", 0.02),
        "max_open_positions": getattr(settings, "max_open_positions", 3),
        "max_position_size_pct": getattr(settings, "max_position_size_pct", 0.25),
        "max_daily_loss_pct": getattr(settings, "max_daily_loss_pct", 0.05),
        "stop_loss_pct": getattr(settings, "stop_loss_pct", 0.03),
    }


def _regime_config_from_settings(settings: Any) -> Any | None:
    """Build optional runtime replay regime gating config."""
    if not getattr(settings, "enable_regime_gating", False):
        return None

    from app.backtesting.regime_filter import RegimeConfig

    return RegimeConfig(
        min_volatility_pct=getattr(settings, "regime_min_volatility_pct", 0.2),
        max_volatility_pct=getattr(settings, "regime_max_volatility_pct", 8.0),
        enabled=True,
    )


def _build_paper_readiness_report(
    settings,
    recent_replay_limit: int = 0,
) -> dict[str, Any]:
    """Build a concise paper-runtime readiness report from persisted state."""
    from app.persistence.db import get_session
    from app.persistence.repositories import (
        ApprovedCombinationRepository,
        CandleRepository,
    )

    session = get_session(settings.database_url)
    try:
        repo = ApprovedCombinationRepository(session)
        all_combos = repo.get_all(limit=1000)
        approved = [combo for combo in all_combos if combo.approved]
    finally:
        session.close()

    parity_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        import os
        parity_path = os.path.join("outputs", "reports", "parity_audit_report.json")
        if os.path.exists(parity_path):
            with open(parity_path, encoding="utf-8") as fh:
                parity_report = json.load(fh)
            for row in parity_report.get("results", []):
                parity_by_key[
                    (
                        str(row.get("symbol", "")),
                        str(row.get("interval", "")),
                        str(row.get("strategy_name", "")),
                    )
                ] = row.get("diagnostics", {})
    except Exception:
        parity_by_key = {}

    configured_keys = {
        f"{symbol}:{settings.trade_interval}"
        for symbol in getattr(settings, "trade_symbols", [])
    }

    recent_replay_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    if recent_replay_limit > 0 and approved:
        from app.backtesting.parity_auditor import audit_combination

        risk_config = _risk_config_from_settings(settings)
        regime_config = _regime_config_from_settings(settings)
        for combo in approved:
            session = get_session(settings.database_url)
            try:
                candle_repo = CandleRepository(session)
                records = candle_repo.get_candles(
                    combo.symbol,
                    combo.interval,
                    limit=recent_replay_limit,
                    latest=True,
                )
            finally:
                session.close()

            candles = _candle_records_to_replay_dicts(records)
            result = audit_combination(
                combo,
                candles,
                risk_config=risk_config,
                regime_config=regime_config,
            )
            recent_replay_by_key[
                (combo.symbol, combo.interval, combo.strategy_name)
            ] = result.summary_dict()

    def _approved_combo_dict(combo: Any) -> dict[str, Any]:
        params = json.loads(combo.parameters or "{}")
        recent_replay = recent_replay_by_key.get(
            (combo.symbol, combo.interval, combo.strategy_name)
        )
        row = {
            "symbol": combo.symbol,
            "interval": combo.interval,
            "strategy_name": combo.strategy_name,
            "robustness_score": combo.robustness_score,
            "pass_rate": combo.pass_rate,
            "parameters": params,
            "recent_replay": recent_replay,
            "configured_for_runtime": f"{combo.symbol}:{combo.interval}" in configured_keys,
        }
        if combo.strategy_name == "hybrid_grid_dca":
            diagnostics = parity_by_key.get((combo.symbol, combo.interval, combo.strategy_name), {})
            row["grid_diagnostics"] = {
                "approved_grid_params": {
                    key: params.get(key)
                    for key in (
                        "anchor_period",
                        "trend_filter_period",
                        "grid_spacing_pct",
                        "max_grid_levels",
                        "base_order_pct",
                        "dca_size_multiplier",
                        "take_profit_pct",
                        "stop_loss_pct",
                        "max_grid_allocation_pct",
                    )
                    if key in params
                },
                "max_allocation_pct": params.get("max_grid_allocation_pct"),
                "grid_levels": params.get("max_grid_levels"),
                "runtime_replay_pnl": diagnostics.get("net_pnl"),
                "runtime_trades_executed": diagnostics.get("trades_executed"),
                "grid_actions": diagnostics.get("grid_actions", {}),
            }
        return row

    approved_rows = [_approved_combo_dict(combo) for combo in approved]
    recent_ready_rows = [
        row for row in approved_rows
        if not row.get("recent_replay")
        or row["recent_replay"].get("verdict") == "approved"
    ]
    runtime_ready_rows = [
        row for row in approved_rows
        if row.get("configured_for_runtime")
        and (
            not row.get("recent_replay")
            or row["recent_replay"].get("verdict") == "approved"
        )
    ]
    configured_approved_rows = [
        row for row in approved_rows
        if row.get("configured_for_runtime")
    ]
    decision = "PAPER_READY" if runtime_ready_rows else "STAY_IN_CASH"
    return {
        "title": "Paper Readiness Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "trading_mode": str(settings.trading_mode),
        "live_enabled": bool(settings.enable_live_trading),
        "configured_symbols": list(settings.trade_symbols),
        "configured_interval": str(settings.trade_interval),
        "approved_count": len(approved),
        "configured_approved_count": len(configured_approved_rows),
        "recent_replay_ready_count": len(recent_ready_rows),
        "runtime_ready_count": len(runtime_ready_rows),
        "recent_replay_limit": recent_replay_limit,
        "total_combinations": len(all_combos),
        "approved_combinations": approved_rows,
        "rejected_combinations": [
            {
                "symbol": combo.symbol,
                "interval": combo.interval,
                "strategy_name": combo.strategy_name,
                "reasons": json.loads(combo.reasons or "[]"),
            }
            for combo in all_combos
            if not combo.approved
        ],
        "live_trading_note": "Live order execution is intentionally disabled.",
    }


@app.command()
def show_config():
    """Display current configuration."""
    settings = _init()
    console.print("\n[bold cyan]Trading Bot Configuration[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="dim")
    table.add_column("Value")

    table.add_row("APP_ENV", str(settings.app_env))
    table.add_row("TRADING_MODE", str(settings.trading_mode))
    table.add_row("LIVE_ENABLED", str(settings.enable_live_trading))
    table.add_row("KILL_SWITCH", str(settings.enable_kill_switch))
    table.add_row("SYMBOLS", ", ".join(settings.trade_symbols))
    table.add_row("INTERVAL", str(settings.trade_interval))
    table.add_row("MAX_RISK/TRADE", f"{settings.max_risk_per_trade:.1%}")
    table.add_row("MAX_DAILY_LOSS", f"{settings.max_daily_loss_pct:.1%}")
    table.add_row("MAX_POSITIONS", str(settings.max_open_positions))
    table.add_row("STOP_LOSS", f"{settings.stop_loss_pct:.1%}")
    table.add_row("DATABASE", settings.database_url)
    table.add_row("TELEGRAM", str(settings.enable_telegram))
    table.add_row("", "")
    table.add_row("[bold]BACKTEST_SYMBOLS[/bold]", ", ".join(settings.backtest_symbols))
    table.add_row("[bold]BACKTEST_INTERVALS[/bold]", ", ".join(settings.backtest_intervals))
    table.add_row("[bold]BACKTEST_LOOKBACK[/bold]", f"{settings.backtest_lookback_days} days")
    table.add_row("", "")
    table.add_row("QUAL_MIN_RETURN", f"{settings.qual_min_return_pct:.2%}")
    table.add_row("QUAL_MIN_SHARPE", f"{settings.qual_min_sharpe:.2f}")
    table.add_row("QUAL_MIN_TRADES", str(settings.qual_min_trades))
    table.add_row("QUAL_MAX_DRAWDOWN", f"{settings.qual_max_drawdown_pct:.2%}")
    table.add_row("QUAL_MIN_PF", f"{settings.qual_min_profit_factor:.2f}")
    table.add_row("QUAL_MIN_OOS", f"{settings.qual_min_oos_consistency:.0%}")
    table.add_row("QUAL_MIN_ALPHA", f"{settings.qual_min_benchmark_alpha_pct:.2%}")

    console.print(table)


@app.command()
def health_check():
    """Run system health checks."""
    settings = _init()
    from app.persistence.db import init_db
    init_db(settings.database_url)

    from app.services.health_service import HealthService
    health = HealthService()
    report = health.check(settings)
    console.print_json(json.dumps(report, default=str))


@app.command()
def list_strategies():
    """List all registered strategies."""
    _init()
    from app.strategies.registry import list_strategies as ls
    strategies = ls()
    console.print(f"\n[bold]Registered Strategies ({len(strategies)}):[/bold]")
    for s in strategies:
        console.print(f"  - {s}")


@app.command()
def run_backtest(
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbols (overrides config)"),
    intervals: Optional[str] = typer.Option(None, "--intervals", help="Comma-separated intervals (overrides config)"),
    days: Optional[int] = typer.Option(None, "--days", help="Lookback days (overrides config)"),
    capital: float = typer.Option(10000.0, help="Initial capital"),
    export_format: str = typer.Option("json", help="Export format: json, csv, or md"),
    validate: bool = typer.Option(True, "--validate/--no-validate", help="Run walk-forward validation"),
    wf_windows: int = typer.Option(3, "--wf-windows", help="Walk-forward windows"),
):
    """Backtest across all configured symbols/intervals with qualification."""
    import pandas as pd
    settings = _init()

    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    # Resolve evaluation parameters from config or CLI overrides
    eval_symbols = [s.strip().upper() for s in symbols.split(",")] if symbols else settings.backtest_symbols
    eval_intervals = [i.strip() for i in intervals.split(",")] if intervals else settings.backtest_intervals
    eval_days = days or settings.backtest_lookback_days
    thresholds = _get_thresholds(settings)

    # Report exact values used
    console.print("\n[bold cyan]Matrix Evaluation Configuration[/bold cyan]")
    console.print(f"  Symbols:    {', '.join(eval_symbols)}")
    console.print(f"  Intervals:  {', '.join(eval_intervals)}")
    console.print(f"  Lookback:   {eval_days} days")
    console.print(f"  Capital:    ${capital:,.0f}")
    console.print(f"  Validation: {'walk-forward' if validate else 'none'}")
    console.print("\n[dim]Qualification thresholds:[/dim]")
    console.print(f"  [dim]min_return={thresholds.min_total_return_pct:.2%}  min_sharpe={thresholds.min_sharpe_ratio:.2f}  min_trades={thresholds.min_total_trades}[/dim]")
    console.print(f"  [dim]max_dd={thresholds.max_drawdown_pct:.2%}  min_pf={thresholds.min_profit_factor:.2f}  min_oos={thresholds.min_oos_consistency:.0%}  min_alpha={thresholds.min_benchmark_alpha_pct:.2%}[/dim]\n")

    # Load candle data for each symbol/interval combination
    candles_by_dataset: dict[tuple[str, str], pd.DataFrame] = {}
    quality_failures = []
    from app.persistence.repositories import CandleRepository

    for sym in eval_symbols:
        for intv in eval_intervals:
            session = get_session(settings.database_url)
            repo = CandleRepository(session)
            candle_limit = _candle_limit_for_days(intv, eval_days)
            records = repo.get_candles(sym, intv, limit=candle_limit, latest=True)
            session.close()

            if records:
                df = _candle_records_to_frame(records)
                report = _validate_research_candles(
                    df,
                    sym,
                    intv,
                    min_candles=_research_min_candles(intv, eval_days),
                )
                if report.passed:
                    candles_by_dataset[(sym, intv)] = df
                    console.print(f"  [green][OK] {sym}/{intv}: {len(df)} candles from DB[/green]")
                else:
                    quality_failures.append(report)
                    _print_quality_failure(report)
            else:
                # Try Binance API
                try:
                    from app.data.market_data_service import MarketDataService
                    from app.data.historical_loader import HistoricalLoader

                    async def _fetch(s=sym, iv=intv):
                        mds = MarketDataService(base_url=settings.binance_base_url)
                        loader = HistoricalLoader(mds)
                        start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=eval_days)).strftime("%Y-%m-%d")
                        data = await loader.load(s, interval=iv, start_date=start)
                        await mds.close()
                        return data

                    df = asyncio.run(_fetch())
                    if not df.empty:
                        report = _validate_research_candles(
                            df,
                            sym,
                            intv,
                            min_candles=_research_min_candles(intv, eval_days),
                        )
                        if not report.passed:
                            quality_failures.append(report)
                            _print_quality_failure(report)
                            continue
                        candles_by_dataset[(sym, intv)] = df
                        console.print(f"  [green][OK] {sym}/{intv}: {len(df)} candles from Binance[/green]")
                    else:
                        console.print(f"  [yellow][WARN] {sym}/{intv}: no data[/yellow]")
                        report = _validate_research_candles(
                            pd.DataFrame(),
                            sym,
                            intv,
                            min_candles=_research_min_candles(intv, eval_days),
                        )
                        quality_failures.append(report)
                except Exception as e:
                    console.print(f"  [red][ERR] {sym}/{intv}: {e}[/red]")
                    report = _validate_research_candles(
                        pd.DataFrame(),
                        sym,
                        intv,
                        min_candles=_research_min_candles(intv, eval_days),
                    )
                    quality_failures.append(report)

    if quality_failures:
        console.print(
            "\n[bold red]Research data quality failed. "
            "Refusing to run backtest on unsafe datasets.[/bold red]"
        )
        raise typer.Exit(1)

    if not candles_by_dataset:
        console.print("\n[red]No data available. Use 'backfill' first.[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Running matrix evaluation: {len(candles_by_dataset)} datasets x all strategies[/bold]\n")

    # Run matrix evaluation
    from app.backtesting.matrix_eval import evaluate_matrix

    matrix = evaluate_matrix(
        candles_by_dataset,
        thresholds=thresholds,
        initial_capital=capital,
        fee_pct=settings.taker_fee_pct,
        slippage_pct=settings.slippage_pct,
        run_walk_forward=validate,
        wf_windows=wf_windows,
    )

    # Cross-dataset leaderboard
    table = Table(
        show_header=True,
        header_style="bold green",
        title=f"Cross-Dataset Leaderboard ({matrix.total_datasets} datasets)",
    )
    table.add_column("Rank", style="bold")
    table.add_column("Strategy")
    table.add_column("Datasets", justify="center")
    table.add_column("Qualified", justify="center")
    table.add_column("Avg Return", justify="right")
    table.add_column("Avg Sharpe", justify="right")
    table.add_column("Avg Alpha", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Consistency", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Status", justify="center")

    for i, sr in enumerate(matrix.strategies, 1):
        status = "[green]ALL[/green]" if sr.all_qualified else (
            f"[yellow]{sr.datasets_qualified}/{sr.datasets_evaluated}[/yellow]"
            if sr.datasets_qualified > 0 else "[red]NONE[/red]"
        )
        alpha_str = f"[green]{sr.avg_alpha:+.2%}[/green]" if sr.avg_alpha > 0 else f"[red]{sr.avg_alpha:+.2%}[/red]"
        table.add_row(
            str(i), sr.strategy_name,
            str(sr.datasets_evaluated), str(sr.datasets_qualified),
            f"{sr.avg_return:.2%}", f"{sr.avg_sharpe:.2f}", alpha_str,
            f"{sr.max_drawdown:.2%}", f"{sr.consistency_score:.0%}",
            str(sr.total_trades), status,
        )
    console.print(table)

    # Per-dataset detail for top strategy
    if matrix.strategies:
        top = matrix.strategies[0]
        detail_table = Table(
            show_header=True,
            header_style="bold cyan",
            title=f"Per-Dataset Detail: {top.strategy_name}",
        )
        detail_table.add_column("Symbol")
        detail_table.add_column("Interval")
        detail_table.add_column("Return", justify="right")
        detail_table.add_column("Benchmark", justify="right")
        detail_table.add_column("Alpha", justify="right")
        detail_table.add_column("Sharpe", justify="right")
        detail_table.add_column("Max DD", justify="right")
        detail_table.add_column("Trades", justify="right")
        detail_table.add_column("Qualified", justify="center")

        for dr in top.per_dataset:
            q = "YES" if dr.qualification and dr.qualification.qualified else "NO"
            alpha = f"[green]{dr.alpha:+.2%}[/green]" if dr.alpha > 0 else f"[red]{dr.alpha:+.2%}[/red]"
            detail_table.add_row(
                dr.symbol, dr.interval,
                f"{dr.metrics.total_return_pct:.2%}",
                f"{dr.benchmark.total_return_pct:.2%}",
                alpha,
                f"{dr.metrics.sharpe_ratio:.2f}",
                f"{dr.metrics.max_drawdown_pct:.2%}",
                str(dr.metrics.total_trades), q,
            )
        console.print(detail_table)

    # Selection summary
    console.print(f"\n[bold]Best ranked:    {matrix.best_ranked or 'none'}[/bold]")
    if matrix.best_qualified:
        console.print(f"[bold green]Best qualified: {matrix.best_qualified} [OK][/bold green]")
    else:
        console.print("[bold red]Best qualified: none - no strategy qualifies across all datasets[/bold red]")

    # Persist the best selection
    if matrix.strategies:
        top = matrix.strategies[0]
        from app.persistence.repositories import SelectedStrategyRepository
        from app.persistence.models import SelectedStrategy

        session = get_session(settings.database_url)
        sel_repo = SelectedStrategyRepository(session)

        from app.strategies.registry import get_strategy
        strategy_inst = get_strategy(top.strategy_name)

        # Use first symbol/interval as the "primary" for persisted record
        primary_sym = eval_symbols[0]
        primary_intv = eval_intervals[0]

        sel_repo.save(SelectedStrategy(
            strategy_name=top.strategy_name,
            parameters=json.dumps(strategy_inst.params, default=str),
            symbol=primary_sym,
            interval=primary_intv,
            composite_score=top.avg_sharpe,
            total_return_pct=top.avg_return,
            max_drawdown_pct=top.max_drawdown,
            sharpe_ratio=top.avg_sharpe,
            sortino_ratio=0.0,
            profit_factor=top.avg_profit_factor,
            win_rate=top.consistency_score,
            total_trades=top.total_trades,
            qualified=top.all_qualified,
            qualification_failures=json.dumps(
                [f"Qualified on {top.datasets_qualified}/{top.datasets_evaluated} datasets"]
                if not top.all_qualified else []
            ),
            benchmark_return_pct=0.0,
            oos_consistency=top.avg_oos_consistency,
            degradation_ratio=0.0,
            validation_windows=0,
            validation_context=json.dumps(top.summary_dict(), default=str),
        ))
        session.commit()
        session.close()

        status = "QUALIFIED" if top.all_qualified else "UNQUALIFIED"
        console.print(f"[dim]Winner persisted ({status}). Use 'show-winner' to inspect.[/dim]")

    # Export
    from app.reporting.exporters import export_json

    report_data = {
        "title": "Matrix Evaluation Report",
        "symbols": eval_symbols,
        "intervals": eval_intervals,
        "lookback_days": eval_days,
        "total_datasets": matrix.total_datasets,
        "thresholds": matrix.thresholds_used,
        "best_ranked": matrix.best_ranked,
        "best_qualified": matrix.best_qualified,
        "leaderboard": [sr.summary_dict() for sr in matrix.strategies],
    }
    path = export_json(report_data, "matrix_eval_report.json", subdir="reports")
    console.print(f"[dim]Report exported: {path}[/dim]")


@app.command()
def optimize(
    strategy: Optional[str] = typer.Option(None, "--strategy", "-s", help="Strategy to optimize (default: all)"),
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbols override"),
    intervals: Optional[str] = typer.Option(None, "--intervals", help="Comma-separated intervals override"),
    days: Optional[int] = typer.Option(None, "--days", help="Lookback days override"),
    profile: str = typer.Option("standard", "--profile", help="Optimizer profile: fast, standard, deep"),
    capital: float = typer.Option(10000.0, help="Initial capital"),
    top_n: int = typer.Option(10, "--top", help="Show top N parameter sets per strategy"),
    wf: bool = typer.Option(False, "--wf", help="Enable walk-forward validation"),
    wf_windows: int = typer.Option(2, "--wf-windows", help="Walk-forward window count"),
    workers: int = typer.Option(0, "--workers", help="Parallel optimizer workers; 0=auto"),
    max_combinations: int = typer.Option(0, "--max-combinations", help="Cap parameter sets per strategy; 0=profile default"),
):
    """Optimize strategy parameters across all configured datasets.

    Evaluates parameter grids by cross-dataset robustness, not single-run profit.
    Use --wf to enable walk-forward train/test validation (slower but more honest).
    """
    import pandas as pd
    settings = _init()

    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    eval_symbols = [s.strip().upper() for s in symbols.split(",")] if symbols else settings.backtest_symbols
    eval_intervals = [i.strip() for i in intervals.split(",")] if intervals else settings.backtest_intervals
    eval_days = days or settings.backtest_lookback_days
    thresholds = _get_thresholds(settings)
    optimizer_workers = (
        min(4, max(1, os.cpu_count() or 1))
        if workers <= 0
        else max(1, workers)
    )

    # Build regime config from settings
    from app.backtesting.regime_filter import RegimeConfig
    regime_config = RegimeConfig(
        min_volatility_pct=settings.regime_min_volatility_pct,
        max_volatility_pct=settings.regime_max_volatility_pct,
        enabled=settings.enable_regime_gating,
    ) if settings.enable_regime_gating else None
    risk_config = {
        "taker_fee_pct": settings.taker_fee_pct,
        "slippage_pct": settings.slippage_pct,
        "max_risk_per_trade": settings.max_risk_per_trade,
        "max_open_positions": settings.max_open_positions,
        "max_position_size_pct": settings.max_position_size_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "stop_loss_pct": settings.stop_loss_pct,
    }

    console.print("\n[bold cyan]Matrix Parameter Optimization[/bold cyan]")
    console.print(f"  Symbols:    {', '.join(eval_symbols)}")
    console.print(f"  Intervals:  {', '.join(eval_intervals)}")
    console.print(f"  Lookback:   {eval_days} days")
    console.print(f"  Capital:    ${capital:,.0f}")
    console.print(f"  Strategy:   {strategy or 'all'}")
    console.print(f"  Profile:    {profile}")
    console.print(f"  Workers:    {optimizer_workers}")
    console.print(f"  Walk-fwd:   {'yes (' + str(wf_windows) + ' windows)' if wf else 'no'}")
    console.print(f"  Regime:     {'enabled' if regime_config else 'disabled'}")
    console.print(f"  Pass-rate:  >={settings.qual_min_dataset_pass_rate:.0%}\n")

    # Load candle data
    candles_by_dataset: dict[tuple[str, str], pd.DataFrame] = {}
    quality_failures = []
    from app.persistence.repositories import CandleRepository

    for sym in eval_symbols:
        for intv in eval_intervals:
            session = get_session(settings.database_url)
            repo = CandleRepository(session)
            candle_limit = _candle_limit_for_days(intv, eval_days)
            records = repo.get_candles(sym, intv, limit=candle_limit, latest=True)
            session.close()

            if records:
                df = _candle_records_to_frame(records)
                report = _validate_research_candles(
                    df,
                    sym,
                    intv,
                    min_candles=_research_min_candles(intv, eval_days),
                )
                if report.passed:
                    candles_by_dataset[(sym, intv)] = df
                    console.print(f"  [green][OK] {sym}/{intv}: {len(df)} candles[/green]")
                else:
                    quality_failures.append(report)
                    _print_quality_failure(report)
            else:
                console.print(f"  [yellow][WARN] {sym}/{intv}: no data[/yellow]")
                report = _validate_research_candles(
                    pd.DataFrame(),
                    sym,
                    intv,
                    min_candles=_research_min_candles(intv, eval_days),
                )
                quality_failures.append(report)

    if quality_failures:
        console.print(
            "\n[bold red]Research data quality failed. "
            "Refusing to optimize unsafe datasets.[/bold red]"
        )
        raise typer.Exit(1)

    if not candles_by_dataset:
        console.print("\n[red]No data. Use 'backfill-matrix' first.[/red]")
        raise typer.Exit(1)

    from app.backtesting.optimizer import (
        DEFAULT_PARAM_GRIDS,
        generate_param_combinations,
        get_param_grid,
        optimize_strategy_matrix,
    )
    from app.strategies.registry import list_strategies as ls

    strategies_to_optimize = [strategy] if strategy else ls()
    all_results = []

    for strat_name in strategies_to_optimize:
        if strat_name not in DEFAULT_PARAM_GRIDS:
            console.print(f"  [yellow]No param grid for '{strat_name}', skipping[/yellow]")
            continue

        try:
            grid = get_param_grid(strat_name, profile=profile)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        total_combos = len(generate_param_combinations(grid))
        combo_cap = (
            max(1, max_combinations)
            if max_combinations > 0
            else min(total_combos, 512)
        )

        n_datasets = len(candles_by_dataset)
        wf_mult = (1 + wf_windows) if wf else 1
        total_backtests = min(total_combos, combo_cap) * n_datasets * wf_mult
        cap_note = "" if combo_cap >= total_combos else f", capped at {combo_cap}"
        console.print(f"\n[bold]Optimizing {strat_name} ({total_combos} param sets{cap_note} x {n_datasets} datasets x {wf_mult} runs = {total_backtests} backtests)[/bold]")

        from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(f"[cyan]{strat_name}", total=min(total_combos, combo_cap))

            def _progress(current: int, total: int) -> None:
                progress.update(task_id, completed=current)

            result = optimize_strategy_matrix(
                strat_name,
                candles_by_dataset,
                param_grid=grid,
                thresholds=thresholds,
                initial_capital=capital,
                fee_pct=settings.taker_fee_pct,
                slippage_pct=settings.slippage_pct,
                max_combinations=combo_cap,
                run_walk_forward=wf,
                wf_windows=wf_windows,
                regime_config=regime_config,
                min_pass_rate=settings.qual_min_dataset_pass_rate,
                workers=optimizer_workers,
                progress_callback=_progress,
            )
            progress.update(task_id, completed=min(total_combos, combo_cap))

        all_results.append(result)

        # Display top N
        table = Table(
            show_header=True, header_style="bold green",
            title=f"Top {min(top_n, len(result.param_results))} - {strat_name}",
        )
        table.add_column("#", style="bold", width=3)
        table.add_column("Robustness", justify="right")
        table.add_column("Avg Return", justify="right")
        table.add_column("Avg Sharpe", justify="right")
        table.add_column("Avg Alpha", justify="right")
        table.add_column("Max DD", justify="right")
        table.add_column("Pass Rate", justify="right")
        table.add_column("Qual", justify="center")
        if wf:
            table.add_column("OOS Ret", justify="right")
        table.add_column("Key Params")

        for i, psr in enumerate(result.param_results[:top_n], 1):
            status = "YES" if psr.all_qualified else f"{psr.datasets_qualified}/{psr.datasets_evaluated}"
            alpha_str = f"[green]{psr.avg_alpha:+.2%}[/green]" if psr.avg_alpha > 0 else f"[red]{psr.avg_alpha:+.2%}[/red]"
            key_params = ", ".join(f"{k}={v}" for k, v in list(psr.params.items())[:4])
            row = [
                str(i),
                f"{psr.robustness_score:.3f}",
                f"{psr.avg_return:.2%}",
                f"{psr.avg_sharpe:.2f}",
                alpha_str,
                f"{psr.max_drawdown:.2%}",
                f"{psr.pass_rate:.0%}",
                status,
            ]
            if wf:
                oos_str = f"[green]{psr.avg_oos_return:+.2%}[/green]" if psr.avg_oos_return > 0 else f"[red]{psr.avg_oos_return:+.2%}[/red]"
                row.append(oos_str)
            row.append(key_params)
            table.add_row(*row)
        console.print(table)

        if strat_name == "hybrid_grid_dca" and result.param_results:
            grid_diag = result.param_results[0].grid_diagnostics
            if grid_diag:
                signals = grid_diag.get("signals", {})
                exits = grid_diag.get("exit_counts", {})
                console.print(
                    "[dim]Grid diagnostics (best): "
                    f"opens={signals.get('open', 0)}, "
                    f"scale-ins={signals.get('scale_in', 0)}, "
                    f"TP={exits.get('take_profit', 0)}, "
                    f"stop={exits.get('stop_exit', 0)}, "
                    f"avg-levels={grid_diag.get('avg_filled_levels', 0)}, "
                    f"max-alloc={grid_diag.get('max_allocation_pct_observed', 0):.1%}, "
                    f"grid-pnl=${grid_diag.get('total_pnl', 0):,.2f}"
                    "[/dim]"
                )

        # Show best qualified summary
        if result.best_qualified:
            bq = result.best_qualified
            console.print(f"  [bold green]Best qualified: robustness={bq.robustness_score:.3f}, return={bq.avg_return:.2%}, Sharpe={bq.avg_sharpe:.2f}, pass-rate={bq.pass_rate:.0%}[/bold green]")
            console.print(f"  [dim]Params: {bq.params}[/dim]")
        elif result.best_pass_rate:
            bp = result.best_pass_rate
            console.print(f"  [yellow]Best pass-rate ({bp.pass_rate:.0%}): robustness={bp.robustness_score:.3f}, return={bp.avg_return:.2%}[/yellow]")
            console.print(f"  [dim]Params: {bp.params}[/dim]")
        else:
            console.print(f"  [yellow]No param set meets pass-rate >={settings.qual_min_dataset_pass_rate:.0%}[/yellow]")

    # Global best across all strategies
    console.print(f"\n{'='*60}")
    all_param_sets = [(r.strategy_name, psr) for r in all_results for psr in r.param_results if psr.datasets_evaluated > 0]
    all_param_sets.sort(key=lambda x: x[1].robustness_score, reverse=True)

    if all_param_sets:
        best_name, best_psr = all_param_sets[0]
        console.print(f"[bold]Global best: {best_name} (robustness={best_psr.robustness_score:.3f})[/bold]")
        console.print(f"  Return={best_psr.avg_return:.2%}  Sharpe={best_psr.avg_sharpe:.2f}  Alpha={best_psr.avg_alpha:+.2%}  Consistency={best_psr.consistency_score:.0%}")
        console.print(f"  Params: {best_psr.params}")

    # Find global best qualified
    best_qual = None
    for name, psr in all_param_sets:
        if psr.all_qualified:
            best_qual = (name, psr)
            break

    if best_qual:
        bq_name, bq_psr = best_qual
        console.print(f"[bold green]Global best qualified: {bq_name} [OK][/bold green]")
        console.print(f"  Return={bq_psr.avg_return:.2%}  Sharpe={bq_psr.avg_sharpe:.2f}  Alpha={bq_psr.avg_alpha:+.2%}")

        # Persist qualified winner
        from app.persistence.repositories import SelectedStrategyRepository
        from app.persistence.models import SelectedStrategy
        session = get_session(settings.database_url)
        sel_repo = SelectedStrategyRepository(session)
        sel_repo.save(SelectedStrategy(
            strategy_name=bq_name,
            parameters=json.dumps(bq_psr.params, default=str),
            symbol=eval_symbols[0],
            interval=eval_intervals[0],
            composite_score=bq_psr.robustness_score,
            total_return_pct=bq_psr.avg_return,
            max_drawdown_pct=bq_psr.max_drawdown,
            sharpe_ratio=bq_psr.avg_sharpe,
            sortino_ratio=0.0,
            profit_factor=bq_psr.avg_profit_factor,
            win_rate=bq_psr.consistency_score,
            total_trades=bq_psr.total_trades,
            qualified=True,
            qualification_failures="[]",
            benchmark_return_pct=0.0,
            oos_consistency=0.0,
            degradation_ratio=0.0,
            validation_windows=0,
            validation_context=json.dumps(bq_psr.summary_dict(), default=str),
        ))
        session.commit()
        session.close()
        console.print("[dim]Winner persisted (QUALIFIED). Use 'show-winner --qualified' to inspect.[/dim]")
    else:
        console.print("[bold red]No parameter set qualifies across all datasets[/bold red]")

    # Persist dataset-specific approvals from the safest available coverage set
    approval_source = _select_approval_param_set(all_param_sets)
    if approval_source:
        best_name, best_psr = approval_source
        pass_rate_ok = best_psr.pass_rate >= settings.qual_min_dataset_pass_rate
        console.print(
            f"[dim]Approval source: {best_name} "
            f"({best_psr.datasets_qualified}/{best_psr.datasets_evaluated} datasets qualified, "
            f"robustness={best_psr.robustness_score:.3f})[/dim]"
        )
        combo_records = _build_approval_records(
            best_name,
            best_psr,
            settings.qual_min_dataset_pass_rate,
        )
        from app.persistence.repositories import ApprovedCombinationRepository
        session = get_session(settings.database_url)
        acr = ApprovedCombinationRepository(session)

        if combo_records:
            if pass_rate_ok:
                runtime_downgraded = _audit_approval_records_for_runtime(
                    combo_records,
                    candles_by_dataset,
                    risk_config,
                    regime_config,
                )
            else:
                runtime_downgraded = 0
                console.print(
                    f"[yellow][WARN] Approval source pass-rate "
                    f"{best_psr.pass_rate:.0%} is below required "
                    f"{settings.qual_min_dataset_pass_rate:.0%}; "
                    "persisting rejected approvals only[/yellow]"
                )

            if runtime_downgraded:
                console.print(
                    f"[yellow][WARN] Runtime parity downgraded "
                    f"{runtime_downgraded} approval(s) before persistence[/yellow]"
                )

            acr.save_batch(combo_records)
            session.commit()
            session.close()

            approved_count = sum(1 for r in combo_records if r.approved)
            total = len(combo_records)
            console.print(f"[dim]Approvals persisted: {approved_count}/{total} datasets approved[/dim]")
        else:
            acr.save_batch([])
            session.commit()
            session.close()
            console.print("[dim]Approvals cleared: selected parameter set had no dataset decisions[/dim]")
    else:
        from app.persistence.repositories import ApprovedCombinationRepository
        session = get_session(settings.database_url)
        acr = ApprovedCombinationRepository(session)
        acr.save_batch([])
        session.commit()
        session.close()
        console.print("[dim]Approvals cleared: no optimization approval source[/dim]")

    # Export
    from app.reporting.exporters import export_json
    report_data = {
        "title": "Matrix Optimization Report",
        "symbols": eval_symbols,
        "intervals": eval_intervals,
        "lookback_days": eval_days,
        "strategies_optimized": len(all_results),
        "total_param_sets_evaluated": sum(r.evaluated_param_sets for r in all_results),
        "global_best": all_param_sets[0][1].summary_dict() if all_param_sets else None,
        "global_best_qualified": best_qual[1].summary_dict() if best_qual else None,
        "per_strategy": [
            {
                "strategy": r.strategy_name,
                "total_sets": r.total_param_sets,
                "evaluated": r.evaluated_param_sets,
                "top_5": [p.summary_dict() for p in r.param_results[:5]],
            }
            for r in all_results
        ],
    }
    path = export_json(report_data, "optimization_report.json", subdir="reports")
    console.print(f"[dim]Report exported: {path}[/dim]")


@app.command()
def backfill(
    symbol: str = typer.Option("BTCUSDT", help="Trading pair"),
    interval: str = typer.Option("1h", help="Candle interval"),
    days: int = typer.Option(90, help="Days of history to download"),
):
    """Download and persist historical candles from Binance."""
    import pandas as pd
    settings = _init()

    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    console.print(f"\n[bold]Backfilling {symbol} ({interval}) - {days} days[/bold]\n")

    from app.data.market_data_service import MarketDataService
    from app.data.historical_loader import HistoricalLoader
    from app.persistence.repositories import CandleRepository
    from app.persistence.models import Candle

    async def _download():
        mds = MarketDataService(base_url=settings.binance_base_url)
        loader = HistoricalLoader(mds)
        start_date = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        data = await loader.load(symbol, interval=interval, start_date=start_date)
        await mds.close()
        return data

    try:
        df = asyncio.run(_download())
    except Exception as e:
        console.print(f"[red]Failed to fetch data from Binance: {e}[/red]")
        raise typer.Exit(1)

    if df.empty:
        console.print("[yellow]No candles returned from Binance.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[green]Downloaded {len(df)} candles[/green]")

    session = get_session(settings.database_url)
    repo = CandleRepository(session)
    persisted = 0
    for _, row in df.iterrows():
        repo.upsert(Candle(
            symbol=symbol, interval=interval,
            open_time=row["open_time"], close_time=row["close_time"],
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
            quote_volume=float(row.get("quote_volume", 0)),
            trade_count=int(row.get("trade_count", 0)),
        ))
        persisted += 1
    session.commit()
    session.close()
    console.print(f"[green][OK] Persisted {persisted} candles to database[/green]")


@app.command()
def backfill_matrix(
    days: Optional[int] = typer.Option(None, help="Lookback days (overrides config)"),
):
    """Download candles for all configured backtest symbols and intervals."""
    import pandas as pd
    settings = _init()

    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    eval_days = days or settings.backtest_lookback_days

    console.print(f"\n[bold]Backfilling matrix: {', '.join(settings.backtest_symbols)} x {', '.join(settings.backtest_intervals)} - {eval_days} days[/bold]\n")

    from app.data.market_data_service import MarketDataService
    from app.data.historical_loader import HistoricalLoader
    from app.persistence.repositories import CandleRepository
    from app.persistence.models import Candle

    for sym in settings.backtest_symbols:
        for intv in settings.backtest_intervals:
            console.print(f"  Fetching {sym}/{intv}...", end=" ")
            try:
                async def _dl(s=sym, iv=intv):
                    mds = MarketDataService(base_url=settings.binance_base_url)
                    loader = HistoricalLoader(mds)
                    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=eval_days)).strftime("%Y-%m-%d")
                    data = await loader.load(s, interval=iv, start_date=start)
                    await mds.close()
                    return data

                df = asyncio.run(_dl())
                if df.empty:
                    console.print("[yellow]no data[/yellow]")
                    continue

                session = get_session(settings.database_url)
                repo = CandleRepository(session)
                for _, row in df.iterrows():
                    repo.upsert(Candle(
                        symbol=sym, interval=intv,
                        open_time=row["open_time"], close_time=row["close_time"],
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=float(row["volume"]),
                        quote_volume=float(row.get("quote_volume", 0)),
                        trade_count=int(row.get("trade_count", 0)),
                    ))
                session.commit()
                session.close()
                console.print(f"[green]{len(df)} candles[/green]")
            except Exception as e:
                console.print(f"[red]{e}[/red]")

    console.print("\n[green][OK] Matrix backfill complete[/green]")


@app.command()
def paper_trade(
    strategy: Optional[str] = typer.Option(None, "--strategy", "-s", help="Strategy name (overrides auto-selection)"),
    sim: bool = typer.Option(False, "--sim", help="Replay persisted candles instead of live WebSocket"),
    sim_symbol: Optional[str] = typer.Option(None, "--sim-symbol", help="Symbol to replay in sim mode (auto-selects if omitted)"),
    sim_interval: Optional[str] = typer.Option(None, "--sim-interval", help="Interval for sim replay (auto-selects if omitted)"),
    sim_limit: int = typer.Option(5000, "--sim-limit", help="Max candles to replay"),
    persist_sim: bool = typer.Option(False, "--persist-sim", help="Persist sim replay signals/trades to runtime tables"),
    readiness_replay_limit: int = typer.Option(
        300,
        "--readiness-replay-limit",
        help="Latest candles to replay before paper runtime routing; 0 disables this gate",
    ),
):
    """Start paper trading: live WebSocket or --sim replay mode.

    Strategy resolution: approvals -> qualified winner -> default.
    Only trades approved symbol+interval combinations unless --strategy is used.
    """
    settings = _init()

    if settings.trading_mode.value != "paper":
        console.print("[red]TRADING_MODE must be 'paper' for paper trading[/red]")
        raise typer.Exit(1)

    from app.persistence.db import init_db
    init_db(settings.database_url)

    sim_user_specified = sim and (sim_symbol is not None or sim_interval is not None)

    if sim and strategy is None and not persist_sim:
        from app.persistence.db import get_session
        from app.persistence.repositories import ApprovedCombinationRepository

        session = get_session(settings.database_url)
        try:
            approved_preview = ApprovedCombinationRepository(session).get_approved()
        finally:
            session.close()
        if not approved_preview:
            console.print("\n[bold yellow]No approved combinations found. Staying in cash.[/bold yellow]")
            console.print("[dim]Use --strategy for an explicit research override.[/dim]")
            raise typer.Exit(0)

    approved_key_filter = None
    readiness_key_order: list[str] = []
    gate_applies = _paper_readiness_gate_applies(
        strategy,
        sim,
        persist_sim,
        sim_user_specified,
    )
    if gate_applies:
        readiness_report = _build_paper_readiness_report(
            settings,
            recent_replay_limit=max(0, readiness_replay_limit),
        )
        readiness_key_order = _runtime_ready_key_order_from_report(readiness_report)
        approved_key_filter = set(readiness_key_order)
        if not approved_key_filter:
            console.print("\n[bold yellow]No recent replay-ready approvals. Staying in cash.[/bold yellow]")
            console.print(
                "[dim]Approved combinations must pass the recent replay gate before "
                "paper runtime routing can start.[/dim]"
            )
            if readiness_report.get("approved_count", 0):
                console.print(
                    f"[dim]Configured approved: "
                    f"{readiness_report.get('configured_approved_count', 0)}/"
                    f"{readiness_report.get('approved_count', 0)}[/dim]"
                )
                console.print(
                    f"[dim]Runtime ready: "
                    f"{readiness_report.get('runtime_ready_count', 0)}/"
                    f"{readiness_report.get('approved_count', 0)}[/dim]"
                )
            raise typer.Exit(0)

        blocked = readiness_report.get("approved_count", 0) - len(approved_key_filter)
        if blocked > 0:
            console.print(
                f"[yellow]Recent replay gate skipped {blocked} approved "
                "combination(s).[/yellow]"
            )

    selected_strategy, approved_keys, is_override = _resolve_strategy(
        strategy,
        settings,
        approved_key_filter=approved_key_filter,
    )

    from app.execution.paper_broker import PaperBroker
    from app.risk.risk_engine import RiskEngine
    from app.services.orchestrator import Orchestrator

    broker = PaperBroker(
        initial_balance=10_000.0,
        fee_pct=settings.taker_fee_pct,
        slippage_pct=settings.slippage_pct,
    )

    risk = RiskEngine(
        equity=10_000.0,
        max_risk_per_trade=settings.max_risk_per_trade,
        max_open_positions=settings.max_open_positions,
        max_position_size_pct=settings.max_position_size_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        stop_loss_pct=settings.stop_loss_pct,
        is_live=False,
    )

    if settings.enable_kill_switch:
        risk.activate_kill_switch("Config: ENABLE_KILL_SWITCH=true")

    telegram = None
    if settings.enable_telegram and sim:
        console.print("[dim]Telegram notifications: disabled for sim replay[/dim]")
    elif settings.enable_telegram:
        from app.notifications.telegram_notifier import TelegramNotifier
        telegram = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )

    # --- Approval-driven symbol filtering ---
    trade_interval = str(settings.trade_interval)

    if sim:
        # Apply defaults if not specified
        sim_symbol = sim_symbol or settings.trade_symbols[0]
        sim_interval = str(sim_interval or trade_interval)
        sim_key = f"{sim_symbol}:{sim_interval}"

        route, routed_key = _sim_replay_route(
            is_override,
            approved_keys,
            sim_key,
            sim_user_specified,
            preferred_keys=readiness_key_order,
        )
        if route == "block_no_approvals":
            console.print("\n[bold yellow]No approved combinations found. Staying in cash.[/bold yellow]")
            console.print("[dim]Use --strategy for an explicit research override.[/dim]")
            raise typer.Exit(0)
        if route == "block_unapproved":
            console.print(f"[bold red]SIM dataset {sim_symbol}/{sim_interval} is NOT approved.[/bold red]")
            console.print(f"[yellow]Approved: {', '.join(sorted(approved_keys))}[/yellow]")
            console.print("[yellow]Staying in cash. Use --strategy to override.[/yellow]")
            raise typer.Exit(0)
        if route == "auto_select":
            sim_symbol, sim_interval = routed_key.split(":")
            sim_key = routed_key
            console.print(f"[bold cyan]Auto-selected approved dataset: {sim_symbol}/{sim_interval}[/bold cyan]")
            console.print(f"[dim]Available: {', '.join(sorted(approved_keys))}[/dim]")

        all_symbols = [sim_symbol]
    else:
        configured_symbols = settings.trade_symbols
        if is_override:
            # Manual override: trade all configured symbols
            all_symbols = configured_symbols
            console.print("[yellow][WARN] Override mode: trading ALL configured symbols[/yellow]")
        elif approved_keys:
            # Filter to only approved symbol+interval combinations.
            active_symbols, skipped_symbols = _filter_approved_symbols(
                configured_symbols, approved_keys, trade_interval
            )

            if skipped_symbols:
                console.print(f"[yellow]Skipped (not approved for {trade_interval}):[/yellow]")
                for s in skipped_symbols:
                    console.print(f"[yellow]  [NO] {s}[/yellow]")

            if not active_symbols:
                console.print("\n[bold red]No configured symbols are approved. Staying in cash.[/bold red]")
                console.print(f"[dim]Configured: {', '.join(configured_symbols)}[/dim]")
                console.print(f"[dim]Required interval: {trade_interval}[/dim]")
                console.print(f"[dim]Approved: {', '.join(sorted(approved_keys))}[/dim]")
                console.print("[dim]Run 'optimize --wf' and 'show-approved' to find approved combinations.[/dim]")
                raise typer.Exit(0)

            all_symbols = active_symbols
            console.print(f"\n[green]Active symbols ({len(active_symbols)}): {', '.join(active_symbols)}[/green]")
        else:
            # No approvals at all - stay in cash
            console.print("\n[bold yellow]No approved combinations found. Staying in cash.[/bold yellow]")
            console.print("[dim]Run 'optimize --wf' to find approved combinations.[/dim]")
            raise typer.Exit(0)

    symbols = all_symbols
    runtime_interval = _runtime_interval_for_paper(sim, sim_interval, trade_interval)

    # Wire regime gating from settings
    regime_config = None
    if settings.enable_regime_gating:
        from app.backtesting.regime_filter import RegimeConfig
        regime_config = RegimeConfig(
            min_volatility_pct=settings.regime_min_volatility_pct,
            max_volatility_pct=settings.regime_max_volatility_pct,
        )
        console.print(f"[dim]Regime gating: enabled (vol {settings.regime_min_volatility_pct}-{settings.regime_max_volatility_pct}%)[/dim]")
    else:
        console.print("[dim]Regime gating: disabled[/dim]")

    orch = Orchestrator(
        strategy=selected_strategy,
        risk_engine=risk,
        broker=broker,
        telegram=telegram,
        mode=settings.trading_mode,
        symbols=symbols,
        interval=runtime_interval,
        database_url=settings.database_url,
        regime_config=regime_config,
    )

    if sim:
        console.print(f"\n[bold yellow]SIM MODE: replaying {sim_symbol} ({sim_interval})[/bold yellow]")

        from app.persistence.db import get_session
        from app.persistence.repositories import CandleRepository
        session = get_session(settings.database_url)
        repo = CandleRepository(session)
        candle_records = repo.get_candles(sim_symbol, sim_interval, limit=sim_limit, latest=True)
        session.close()

        if not candle_records:
            console.print("[red]No persisted candles found. Run 'backfill' first.[/red]")
            raise typer.Exit(1)

        candle_dicts = _candle_records_to_replay_dicts(candle_records)

        console.print(f"[green]Loaded {len(candle_dicts)} candles for replay[/green]\n")

        from rich.progress import Progress

        async def _replay():
            with Progress() as progress:
                task = progress.add_task("[cyan]Replaying candles...", total=len(candle_dicts))
                def _progress(current, _total):
                    progress.update(task, completed=current)
                summary = await orch.replay_candles(
                    candle_dicts,
                    progress_callback=_progress,
                    persist=persist_sim,
                )
                progress.update(task, completed=len(candle_dicts))
            return summary

        summary = asyncio.run(_replay())

        table = Table(show_header=True, header_style="bold green", title="Replay Summary")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")
        table.add_row("Total Candles", str(summary["total_candles"]))
        table.add_row("Trades Executed", str(summary["trades_executed"]))
        table.add_row("Lifecycle Blocked", str(summary.get("lifecycle_blocked", 0)))
        table.add_row("Signals Rejected (risk)", str(summary["signals_rejected"]))
        table.add_row("Order Rejected (broker)", str(summary.get("order_rejected", 0)))
        table.add_row("Regime Blocked", str(summary.get("regime_blocked", 0)))
        table.add_row("Errors", str(summary["errors"]))
        table.add_row("Initial Capital", f"${summary.get('initial_capital', 10000):,.2f}")
        net_pnl = summary.get("net_pnl", 0)
        pnl_color = "green" if net_pnl >= 0 else "red"
        table.add_row("Final Equity", f"${summary['final_equity']:,.2f}")
        table.add_row("Net PnL", f"[{pnl_color}]${net_pnl:+,.2f}[/{pnl_color}]")
        table.add_row("Open Positions", str(summary["open_positions"]))
        console.print(table)
    else:
        console.print(f"\n[bold green]Starting paper trading - {', '.join(symbols)} ({trade_interval})[/bold green]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        async def _run():
            try:
                await orch.start()
            except KeyboardInterrupt:
                pass
            finally:
                await orch.stop()
                status = orch.get_status()
                console.print("\n[bold]Session Summary[/bold]")
                console.print(f"  Candles processed: {status['candles_processed']}")
                console.print(f"  Open positions: {status['open_positions']}")
                console.print(f"  Daily PnL: {status['daily_pnl']:+.2f}")
                console.print(f"  Equity: ${status['equity']:,.2f}")

        asyncio.run(_run())


@app.command("show-approval-report")
def show_approval_report():
    """Show which dataset x strategy combinations are approved for paper trading.

    Reads the latest optimization report and displays approval status per dataset.
    """
    _init()

    # Try to load latest optimization report
    import os
    report_path = os.path.join("outputs", "reports", "optimization_report.json")
    if not os.path.exists(report_path):
        console.print("[yellow]No optimization report found. Run 'optimize' first.[/yellow]")
        raise typer.Exit(1)

    with open(report_path, encoding="utf-8") as report_file:
        report = json.load(report_file)

    console.print("\n[bold cyan]Dataset Approval Status[/bold cyan]")
    console.print(f"  Report: {report.get('title', 'unknown')}")
    console.print(f"  Strategies optimized: {report.get('strategies_optimized', 0)}")
    console.print(f"  Total param sets: {report.get('total_param_sets_evaluated', 0)}\n")

    # Show global best
    global_best = report.get("global_best")
    if global_best:
        name = global_best.get("strategy_name", "?")
        rob = global_best.get("robustness_score", 0)
        pr = global_best.get("pass_rate", 0)
        console.print(f"[bold]Global best: {name} (robustness={rob:.3f}, pass-rate={pr:.0%})[/bold]")

        approved_datasets = global_best.get("approved_datasets", [])
        if approved_datasets:
            table = Table(
                show_header=True, header_style="bold green",
                title="Approved Datasets - Best Param Set",
            )
            table.add_column("Symbol")
            table.add_column("Interval")
            table.add_column("Status", justify="center")
            table.add_column("Reasons")

            for ds in approved_datasets:
                status = "[green]APPROVED[/green]" if ds["approved"] else "[red]REJECTED[/red]"
                reasons = "; ".join(ds.get("reasons", []))
                table.add_row(
                    ds.get("symbol", "?"),
                    ds.get("interval", "?"),
                    status,
                    reasons[:80],  # Truncate long reasons
                )
            console.print(table)
        else:
            console.print("[dim]No per-dataset approval data in report[/dim]")
    else:
        console.print("[yellow]No global best in report[/yellow]")

    # Show global best qualified
    global_qual = report.get("global_best_qualified")
    if global_qual:
        name = global_qual.get("strategy_name", "?")
        console.print(f"\n[bold green]Best qualified: {name} [OK][/bold green]")
        approved = global_qual.get("approved_datasets", [])
        approved_count = sum(1 for d in approved if d.get("approved"))
        console.print(f"  Approved datasets: {approved_count}/{len(approved)}")
    else:
        console.print("\n[bold red]No fully qualified parameter set[/bold red]")

    # Per-strategy summary
    per_strat = report.get("per_strategy", [])
    if per_strat:
        console.print("\n[bold]Per-Strategy Summary[/bold]")
        summary_table = Table(show_header=True, header_style="bold green")
        summary_table.add_column("Strategy")
        summary_table.add_column("Sets Evaluated", justify="right")
        summary_table.add_column("Top Robustness", justify="right")
        summary_table.add_column("Top Pass Rate", justify="right")
        summary_table.add_column("Top OOS", justify="right")

        for s in per_strat:
            top5 = s.get("top_5", [])
            top_rob = max([t.get("robustness_score", 0) for t in top5]) if top5 else 0
            top_pr = max([t.get("pass_rate", 0) for t in top5]) if top5 else 0
            top_oos = max([t.get("avg_oos_return", 0) for t in top5]) if top5 else 0
            oos_str = f"[green]{top_oos:+.2%}[/green]" if top_oos > 0 else f"[red]{top_oos:+.2%}[/red]"
            summary_table.add_row(
                s.get("strategy", "?"),
                str(s.get("evaluated", 0)),
                f"{top_rob:.3f}",
                f"{top_pr:.0%}",
                oos_str,
            )
        console.print(summary_table)

    # Export approval summary
    from app.reporting.exporters import export_json
    approval_export = {
        "title": "Approval Summary",
        "global_best": global_best,
        "global_best_qualified": global_qual,
        "approved_datasets": global_best.get("approved_datasets", []) if global_best else [],
        "per_strategy": per_strat,
    }
    path = export_json(approval_export, "approval_summary.json", subdir="reports")
    console.print(f"\n[dim]Approval summary exported: {path}[/dim]")

    # Final recommendation
    if global_qual:
        console.print(f"\n[bold green]Paper-trade recommendation: {global_qual.get('strategy_name')} is qualified [OK][/bold green]")
    elif global_best:
        pr = global_best.get("pass_rate", 0)
        if pr > 0:
            console.print(f"\n[yellow]No fully qualified set, but best passes {pr:.0%} of datasets[/yellow]")
        else:
            console.print("\n[bold red]No datasets approved. Stay in cash. Research more strategies.[/bold red]")
    else:
        console.print("\n[bold red]No optimization results. Run 'optimize' first.[/bold red]")


@app.command()
def show_winner(
    qualified_only: bool = typer.Option(False, "--qualified", help="Show only qualified winners"),
):
    """Show the latest persisted backtest winner."""
    settings = _init()
    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    from app.persistence.repositories import SelectedStrategyRepository
    session = get_session(settings.database_url)
    repo = SelectedStrategyRepository(session)

    if qualified_only:
        winner = repo.get_latest_qualified_winner()
    else:
        winner = repo.get_latest_winner()
    session.close()

    if not winner:
        label = "qualified winner" if qualified_only else "winner"
        console.print(f"[yellow]No persisted {label} found. Run 'run-backtest' first.[/yellow]")
        return

    status_str = "[bold green]QUALIFIED[/bold green]" if winner.qualified else "[bold red]UNQUALIFIED[/bold red]"

    table = Table(title="Latest Backtest Winner", show_header=True, header_style="bold green")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")

    table.add_row("Strategy", winner.strategy_name)
    table.add_row("Status", status_str)
    table.add_row("Symbol", winner.symbol)
    table.add_row("Interval", winner.interval)
    table.add_row("Composite Score", f"{winner.composite_score:.4f}")
    table.add_row("Total Return", f"{winner.total_return_pct:.2%}")
    table.add_row("Benchmark Return", f"{winner.benchmark_return_pct:.2%}")
    alpha = winner.total_return_pct - winner.benchmark_return_pct
    table.add_row("Alpha vs B&H", f"{alpha:+.2%}")
    table.add_row("Max Drawdown", f"{winner.max_drawdown_pct:.2%}")
    table.add_row("Sharpe Ratio", f"{winner.sharpe_ratio:.2f}")
    table.add_row("Sortino Ratio", f"{winner.sortino_ratio:.2f}")
    table.add_row("Profit Factor", f"{winner.profit_factor:.2f}")
    table.add_row("Win Rate", f"{winner.win_rate:.1%}")
    table.add_row("Total Trades", str(winner.total_trades))
    table.add_row("OOS Consistency", f"{winner.oos_consistency:.0%}")
    table.add_row("Degradation Ratio", f"{winner.degradation_ratio:.2f}")
    table.add_row("Validation Windows", str(winner.validation_windows))
    table.add_row("Selected At", str(winner.selected_at))

    console.print(table)

    if not winner.qualified:
        failures = json.loads(winner.qualification_failures) if winner.qualification_failures else []
        if failures:
            console.print("\n[red]Qualification Failures:[/red]")
            for f in failures:
                console.print(f"[red]  - {f}[/red]")

    # Show validation context if available
    try:
        ctx = json.loads(winner.validation_context) if winner.validation_context else {}
        if ctx:
            console.print("\n[dim]Validation context:[/dim]")
            for k, v in ctx.items():
                console.print(f"  [dim]{k}: {v}[/dim]")
    except (json.JSONDecodeError, TypeError):
        pass


@app.command()
def export_report(
    format: str = typer.Option("json", help="Export format: json, csv, md"),
    symbol: str = typer.Option("BTCUSDT", help="Symbol to report on"),
):
    """Export the latest backtest or paper trading report."""
    settings = _init()
    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    session = get_session(settings.database_url)
    from app.persistence.repositories import TradeRepository
    trade_repo = TradeRepository(session)
    trades = trade_repo.get_trades(mode="paper", symbol=symbol, limit=500)
    session.close()

    if not trades:
        console.print("[yellow]No paper trades found for export.[/yellow]")
        raise typer.Exit(0)

    from app.reporting.exporters import export_json, export_csv

    rows = [{
        "symbol": t.symbol, "side": t.side, "status": t.status,
        "filled_price": t.filled_price, "filled_quantity": t.filled_quantity,
        "fees": t.fees, "strategy": t.strategy_name, "created_at": str(t.created_at),
    } for t in trades]

    if format == "json":
        path = export_json({"trades": rows, "total": len(rows)}, f"trades_{symbol}.json", subdir="reports")
    elif format == "csv":
        path = export_csv(rows, f"trades_{symbol}.csv", subdir="reports")
    else:
        path = export_json({"trades": rows}, f"trades_{symbol}.json", subdir="reports")

    console.print(f"[green][OK] Exported {len(rows)} trades to {path}[/green]")


@app.command()
def live_trade():
    """Start live trading mode (requires explicit configuration)."""
    settings = _init()
    if not settings.enable_live_trading:
        console.print("[red]Live trading is DISABLED. Set ENABLE_LIVE_TRADING=true in .env[/red]")
        raise typer.Exit(1)
    if not settings.is_live:
        console.print("[red]TRADING_MODE must be 'live' and ENABLE_LIVE_TRADING=true[/red]")
        raise typer.Exit(1)
    console.print("[bold red][WARN] LIVE TRADING MODE REQUESTED[/bold red]")
    console.print("[yellow]Live trading execution is intentionally not wired in this safety pass.[/yellow]")
    console.print("[yellow]Use paper-trade and audit-approved until the live order lifecycle is explicitly hardened.[/yellow]")


@app.command()
def send_test_telegram():
    """Send a test message to Telegram."""
    settings = _init()
    if not settings.enable_telegram:
        console.print("[red]Telegram is disabled. Set ENABLE_TELEGRAM=true[/red]")
        raise typer.Exit(1)

    from app.notifications.telegram_notifier import TelegramNotifier
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    async def _send():
        return await notifier.send_message("<b>Test Message</b>\n\nTrading bot CLI test.")

    success = asyncio.run(_send())
    if success:
        console.print("[green][OK] Test message sent successfully[/green]")
    else:
        console.print("[red][ERR] Failed to send test message[/red]")


@app.command("paper-readiness")
def paper_readiness(
    recent_replay_limit: int = typer.Option(
        300,
        "--recent-replay-limit",
        help="Latest candles to replay per approved combo; 0 disables the recent replay gate",
    ),
):
    """Show whether persisted approvals allow paper trading to proceed."""
    settings = _init()
    from app.persistence.db import init_db
    init_db(settings.database_url)

    report = _build_paper_readiness_report(
        settings,
        recent_replay_limit=max(0, recent_replay_limit),
    )
    decision = report["decision"]
    color = "green" if decision == "PAPER_READY" else "yellow"

    table = Table(show_header=True, header_style="bold cyan", title="Paper Readiness")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")
    table.add_row("Decision", f"[{color}]{decision}[/{color}]")
    table.add_row("Trading Mode", str(report["trading_mode"]))
    table.add_row("Live Enabled", str(report["live_enabled"]))
    table.add_row("Configured Interval", str(report["configured_interval"]))
    table.add_row(
        "Approved",
        f"{report['approved_count']}/{report['total_combinations']}",
    )
    table.add_row(
        "Configured Approved",
        f"{report['configured_approved_count']}/{report['approved_count']}",
    )
    if report.get("recent_replay_limit", 0) > 0:
        table.add_row(
            "Recent Replay Gate",
            f"{report['recent_replay_ready_count']}/{report['approved_count']}",
        )
        table.add_row(
            "Runtime Ready",
            f"{report['runtime_ready_count']}/{report['approved_count']}",
        )
    console.print(table)

    if report["approved_combinations"]:
        approved_table = Table(
            show_header=True,
            header_style="bold green",
            title="Approved Paper Datasets",
        )
        approved_table.add_column("Symbol")
        approved_table.add_column("Interval")
        approved_table.add_column("Strategy")
        approved_table.add_column("Pass Rate", justify="right")
        approved_table.add_column("Grid", justify="right")
        approved_table.add_column("Runtime PnL", justify="right")
        approved_table.add_column("Recent PnL", justify="right")
        approved_table.add_column("Configured", justify="right")
        approved_table.add_column("Gate", justify="right")
        for combo in report["approved_combinations"]:
            grid_diag = combo.get("grid_diagnostics", {})
            runtime_pnl = grid_diag.get("runtime_replay_pnl")
            recent_replay = combo.get("recent_replay") or {}
            recent_diag = recent_replay.get("diagnostics", {})
            recent_pnl = recent_diag.get("net_pnl")
            gate = str(recent_replay.get("verdict", "not_run")).upper()
            approved_table.add_row(
                combo["symbol"],
                combo["interval"],
                combo["strategy_name"],
                f"{combo['pass_rate']:.0%}",
                str(grid_diag.get("grid_levels", "-")),
                "-" if runtime_pnl is None else f"${runtime_pnl:+,.2f}",
                "-" if recent_pnl is None else f"${recent_pnl:+,.2f}",
                "YES" if combo.get("configured_for_runtime") else "NO",
                gate,
            )
        console.print(approved_table)
    else:
        console.print("[yellow]No approved combinations. Paper trading will stay in cash.[/yellow]")

    from app.reporting.exporters import export_json
    path = export_json(report, "paper_readiness_report.json", subdir="reports")
    console.print(f"[dim]Report exported: {path}[/dim]")
    return report


@app.command()
def show_approved():
    """Display all approved dataset combinations."""
    settings = _init()
    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    from app.persistence.repositories import ApprovedCombinationRepository
    session = get_session(settings.database_url)
    repo = ApprovedCombinationRepository(session)
    all_combos = repo.get_all()
    session.close()

    if not all_combos:
        console.print("[yellow]No approved combinations found. Run 'optimize --wf' first.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan", title="Approved Combinations")
    table.add_column("Symbol", style="bold")
    table.add_column("Interval")
    table.add_column("Strategy")
    table.add_column("Approved", justify="center")
    table.add_column("Robustness", justify="right")
    table.add_column("Pass Rate", justify="right")
    table.add_column("Regime", justify="center")

    for c in all_combos:
        status = "[green]YES[/green]" if c.approved else "[red]NO[/red]"
        regime = c.regime_state or "unknown"
        table.add_row(
            c.symbol, c.interval, c.strategy_name, status,
            f"{c.robustness_score:.3f}" if c.robustness_score else "-",
            f"{c.pass_rate:.0%}" if c.pass_rate else "-",
            regime,
        )

    console.print(table)
    approved_count = sum(1 for c in all_combos if c.approved)
    console.print(f"\n[dim]{approved_count}/{len(all_combos)} combinations approved[/dim]")


@app.command()
def audit_approved(
    limit: int = typer.Option(5000, "--limit", help="Max candles per dataset"),
    verbose: bool = typer.Option(False, "--verbose", help="Show full runtime replay logs"),
):
    """Audit approved combinations via runtime replay (parity check).

    Runs each approved combination through the orchestrator pipeline
    (same lifecycle, risk, and regime rules as paper trading) to verify
    that statistically approved combinations are also runtime-viable.
    """
    settings = _init()
    from app.persistence.db import init_db, get_session
    init_db(settings.database_url)

    from app.persistence.repositories import ApprovedCombinationRepository, CandleRepository
    session = get_session(settings.database_url)
    acr = ApprovedCombinationRepository(session)
    approved = acr.get_approved()

    if not approved:
        console.print("[yellow]No approved combinations to audit. Run 'optimize --wf' first.[/yellow]")
        session.close()
        return

    console.print(f"\n[bold cyan]Auditing {len(approved)} approved combinations...[/bold cyan]\n")

    # Build regime config if enabled
    regime_config = None
    if settings.enable_regime_gating:
        from app.backtesting.regime_filter import RegimeConfig
        regime_config = RegimeConfig(
            min_volatility_pct=settings.regime_min_volatility_pct,
            max_volatility_pct=settings.regime_max_volatility_pct,
        )

    risk_config = {
        "taker_fee_pct": settings.taker_fee_pct,
        "slippage_pct": settings.slippage_pct,
        "max_risk_per_trade": settings.max_risk_per_trade,
        "max_open_positions": settings.max_open_positions,
        "max_position_size_pct": settings.max_position_size_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "stop_loss_pct": settings.stop_loss_pct,
    }

    from app.backtesting.parity_auditor import audit_combination

    results = []
    candle_repo = CandleRepository(session)

    for combo in approved:
        console.print(f"  Auditing {combo.symbol}/{combo.interval}...", end=" ")

        candle_records = candle_repo.get_candles(
            combo.symbol,
            combo.interval,
            limit=limit,
            latest=True,
        )
        candle_df = _candle_records_to_frame(candle_records)
        quality_report = _validate_research_candles(
            candle_df,
            combo.symbol,
            combo.interval,
            min_candles=_audit_min_candles(limit),
        )
        candle_dicts = [
            {
                "symbol": c.symbol, "open_time": c.open_time, "close_time": c.close_time,
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                "volume": c.volume, "is_closed": True,
            }
            for c in candle_records
        ]

        if quality_report.passed:
            result = audit_combination(
                combo,
                candle_dicts,
                risk_config,
                regime_config,
                quiet=not verbose,
            )
        else:
            result = _audit_result_from_quality_failure(
                combo,
                quality_report,
                len(candle_records),
            )
        results.append(result)

        color = {"approved": "green", "downgraded": "yellow", "rejected": "red"}.get(result.verdict, "white")
        console.print(f"[{color}]{result.verdict.upper()}[/{color}] "
                       f"(trades={result.diagnostics.trades_executed}, "
                       f"exec={result.diagnostics.execution_ratio:.2%})")

    session.close()

    # Summary table
    console.print()
    table = Table(show_header=True, header_style="bold cyan", title="Parity Audit Results")
    table.add_column("Symbol", style="bold")
    table.add_column("Interval")
    table.add_column("Trades", justify="right")
    table.add_column("Lifecycle Blk", justify="right")
    table.add_column("Risk Rej", justify="right")
    table.add_column("Exec Ratio", justify="right")
    table.add_column("Net PnL", justify="right")
    table.add_column("Verdict", justify="center")

    for r in results:
        d = r.diagnostics
        pnl_color = "green" if d.net_pnl >= 0 else "red"
        verdict_color = {"approved": "green", "downgraded": "yellow", "rejected": "red"}.get(r.verdict, "white")
        table.add_row(
            r.symbol, r.interval,
            str(d.trades_executed), str(d.lifecycle_blocked), str(d.risk_rejected),
            f"{d.execution_ratio:.2%}",
            f"[{pnl_color}]${d.net_pnl:+,.2f}[/{pnl_color}]",
            f"[{verdict_color}]{r.verdict.upper()}[/{verdict_color}]",
        )

    console.print(table)

    approved_count = sum(1 for r in results if r.verdict == "approved")
    downgraded_count = sum(1 for r in results if r.verdict == "downgraded")
    console.print(f"\n[dim]Runtime-viable: {approved_count}/{len(results)} | "
                  f"Downgraded: {downgraded_count}[/dim]")

    # Export audit report
    from app.reporting.exporters import export_json
    report = {
        "title": "Parity Audit Report",
        "total_audited": len(results),
        "runtime_viable": approved_count,
        "downgraded": downgraded_count,
        "results": [r.summary_dict() for r in results],
    }
    path = export_json(report, "parity_audit_report.json", subdir="reports")
    console.print(f"[dim]Report exported: {path}[/dim]")

    # Show warnings for downgraded combos
    for r in results:
        if r.verdict == "downgraded":
            console.print(f"\n[yellow][WARN] {r.symbol}/{r.interval} DOWNGRADED:[/yellow]")
            for reason in r.viability_reasons:
                console.print(f"[yellow]  - {reason}[/yellow]")

    # Wire downgrades back to approval table
    if downgraded_count > 0:
        from app.persistence.db import get_session as get_audit_session
        from app.persistence.models import ApprovedCombination
        audit_session = get_audit_session(settings.database_url)
        updated = 0
        for r in results:
            if r.verdict == "downgraded":
                records = audit_session.query(ApprovedCombination).filter_by(
                    strategy_name=r.strategy_name,
                    symbol=r.symbol,
                    interval=r.interval,
                    approved=True,
                ).all()
                for record in records:
                    existing_reasons = json.loads(record.reasons or "[]")
                    record.approved = False
                    record.reasons = json.dumps(
                        existing_reasons
                        + [f"runtime_{reason}" for reason in r.viability_reasons],
                        default=str,
                    )
                    updated += 1
        audit_session.commit()
        audit_session.close()
        console.print(f"\n[dim]Updated approval table: {updated} combination(s) set to approved=False[/dim]")


@app.command("research-cycle")
def research_cycle(
    strategy: Optional[str] = typer.Option(
        "bollinger_mean_reversion",
        "--strategy",
        "-s",
        help="Strategy to optimize; use empty string for all registered strategies",
    ),
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbols override"),
    intervals: Optional[str] = typer.Option(None, "--intervals", help="Comma-separated intervals override"),
    days: Optional[int] = typer.Option(None, "--days", help="Lookback days override"),
    profile: str = typer.Option("standard", "--profile", help="Optimizer profile: fast, standard, deep"),
    capital: float = typer.Option(10000.0, "--capital", help="Initial research capital"),
    wf: bool = typer.Option(True, "--wf/--no-wf", help="Run walk-forward validation"),
    wf_windows: int = typer.Option(2, "--wf-windows", help="Walk-forward window count"),
    workers: int = typer.Option(0, "--workers", help="Parallel optimizer workers; 0=auto"),
    max_combinations: int = typer.Option(0, "--max-combinations", help="Cap parameter sets per strategy; 0=profile default"),
    top_n: int = typer.Option(5, "--top", help="Top optimization rows to display"),
    audit_limit: int = typer.Option(1000, "--audit-limit", help="Candles per approved dataset audit"),
    sim_limit: int = typer.Option(300, "--sim-limit", help="Candles for final paper sim smoke"),
    skip_backfill: bool = typer.Option(False, "--skip-backfill", help="Use existing persisted candles"),
):
    """Run the safe research-to-paper workflow end to end."""
    console.print("\n[bold cyan]Safe Research Cycle[/bold cyan]")
    console.print("[dim]Live trading remains disabled; this workflow only gates paper routing.[/dim]\n")

    selected_strategy = None if strategy == "" else strategy

    if not skip_backfill:
        console.print("[bold]Step 1/5: Backfill matrix[/bold]")
        backfill_matrix(days=days)
    else:
        console.print("[bold]Step 1/5: Backfill matrix[/bold] [dim]skipped[/dim]")

    console.print("\n[bold]Step 2/5: Optimize with runtime parity gate[/bold]")
    optimize(
        strategy=selected_strategy,
        symbols=symbols,
        intervals=intervals,
        days=days,
        profile=profile,
        capital=capital,
        top_n=top_n,
        wf=wf,
        wf_windows=wf_windows,
        workers=workers,
        max_combinations=max_combinations,
    )

    console.print("\n[bold]Step 3/5: Audit approved combinations[/bold]")
    audit_approved(limit=audit_limit, verbose=False)

    console.print("\n[bold]Step 4/5: Paper readiness[/bold]")
    readiness_report = paper_readiness(recent_replay_limit=sim_limit)

    console.print("\n[bold]Step 5/5: Paper sim smoke[/bold]")
    if readiness_report["decision"] == "PAPER_READY":
        paper_trade(
            strategy=None,
            sim=True,
            sim_symbol=None,
            sim_interval=None,
            sim_limit=sim_limit,
            persist_sim=False,
        )
        console.print("\n[green][OK] Research cycle complete[/green]")
    else:
        console.print("[yellow]Skipping paper sim smoke because readiness is STAY_IN_CASH.[/yellow]")
        console.print("\n[yellow][WARN] Research cycle complete: readiness is STAY_IN_CASH[/yellow]")


if __name__ == "__main__":
    app()
