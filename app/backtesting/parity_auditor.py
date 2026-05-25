"""Parity auditor — validates approved combinations against runtime replay.

Bridges the gap between optimization (permissive backtest engine) and runtime
(orchestrator with lifecycle pre-checks, risk engine, regime gating).

Runs each approved combination through the same orchestrator pipeline used
in paper trading, then compares results and flags non-viable approvals.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import TradingMode
from app.core.logging import get_logger
from app.execution.paper_broker import PaperBroker
from app.persistence.models import ApprovedCombination
from app.risk.risk_engine import RiskEngine
from app.services.orchestrator import Orchestrator
from app.strategies.registry import get_strategy

logger = get_logger(__name__)

# Minimum thresholds for runtime viability
MIN_EXECUTED_TRADES = 1
MIN_EXECUTION_RATIO = 0.0005  # At least 0.05% of effective candles produce trades
MIN_RUNTIME_NET_PNL = 0.0
MAX_LIFECYCLE_BLOCK_RATIO = 0.80


@contextmanager
def _quiet_runtime_replay(enabled: bool):
    """Temporarily reduce noisy replay loggers during audit simulations."""
    if not enabled:
        yield
        return

    logger_names = [
        "app.execution.paper_broker",
        "app.risk.risk_engine",
        "app.services.orchestrator",
    ]
    previous_levels = {
        name: logging.getLogger(name).level
        for name in logger_names
    }
    try:
        for name in logger_names:
            logging.getLogger(name).setLevel(logging.WARNING)
        yield
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)


@dataclass
class ReplayDiagnostics:
    """Detailed breakdown of replay rejection categories."""

    total_candles: int = 0
    buffering: int = 0
    no_signal: int = 0
    lifecycle_blocked: int = 0
    risk_rejected: int = 0
    regime_blocked: int = 0
    order_rejected: int = 0
    trades_executed: int = 0
    errors: int = 0
    duplicates: int = 0
    grid_actions: dict[str, int] = field(default_factory=dict)

    # Derived
    initial_capital: float = 0.0
    final_equity: float = 0.0
    net_pnl: float = 0.0

    @property
    def effective_candles(self) -> int:
        """Candles past buffering that could produce signals."""
        return self.total_candles - self.buffering - self.duplicates

    @property
    def execution_ratio(self) -> float:
        """Fraction of effective candles that produced executed trades."""
        if self.effective_candles <= 0:
            return 0.0
        return self.trades_executed / self.effective_candles

    @property
    def signal_ratio(self) -> float:
        """Fraction of effective candles that produced any signal activity."""
        active = (
            self.trades_executed + self.lifecycle_blocked
            + self.risk_rejected + self.order_rejected
        )
        if self.effective_candles <= 0:
            return 0.0
        return active / self.effective_candles

    def summary_dict(self) -> dict[str, Any]:
        return {
            "total_candles": self.total_candles,
            "effective_candles": self.effective_candles,
            "trades_executed": self.trades_executed,
            "lifecycle_blocked": self.lifecycle_blocked,
            "risk_rejected": self.risk_rejected,
            "regime_blocked": self.regime_blocked,
            "order_rejected": self.order_rejected,
            "no_signal": self.no_signal,
            "errors": self.errors,
            "execution_ratio": round(self.execution_ratio, 4),
            "signal_ratio": round(self.signal_ratio, 4),
            "initial_capital": round(self.initial_capital, 2),
            "final_equity": round(self.final_equity, 2),
            "net_pnl": round(self.net_pnl, 2),
            "grid_actions": dict(self.grid_actions),
        }


@dataclass
class AuditResult:
    """Result of auditing one approved combination via runtime replay."""

    symbol: str
    interval: str
    strategy_name: str
    params: dict[str, Any]

    # Statistical approval (from optimizer)
    statistically_approved: bool = True
    robustness_score: float = 0.0
    pass_rate: float = 0.0

    # Runtime viability (from replay)
    diagnostics: ReplayDiagnostics = field(default_factory=ReplayDiagnostics)
    runtime_viable: bool = False
    viability_reasons: list[str] = field(default_factory=list)

    # Final verdict
    verdict: str = "unknown"  # "approved", "downgraded", "flagged"

    def summary_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "strategy_name": self.strategy_name,
            "params": self.params,
            "statistically_approved": self.statistically_approved,
            "robustness_score": round(self.robustness_score, 4),
            "runtime_viable": self.runtime_viable,
            "verdict": self.verdict,
            "viability_reasons": self.viability_reasons,
            "diagnostics": self.diagnostics.summary_dict(),
        }


def audit_combination(
    combo: ApprovedCombination,
    candle_dicts: list[dict[str, Any]],
    risk_config: dict[str, Any] | None = None,
    regime_config: Any | None = None,
    quiet: bool = True,
) -> AuditResult:
    """Audit one approved combination by running it through the orchestrator.

    Args:
        combo: The approved combination to audit.
        candle_dicts: List of candle dicts for the symbol/interval.
        risk_config: Risk engine configuration overrides.
        regime_config: Optional RegimeConfig for regime gating.
        quiet: Reduce runtime replay logs to warnings/errors.

    Returns:
        AuditResult with statistical and runtime viability assessment.
    """
    import asyncio

    params = json.loads(combo.parameters) if combo.parameters else {}

    # Load app settings to align audit risk config with actual paper trading config.
    # Caller-supplied risk_config overrides individual keys.
    try:
        from app.config.settings import get_settings
        _settings = get_settings()
        _settings_risk: dict[str, Any] = {
            "taker_fee_pct": _settings.taker_fee_pct,
            "slippage_pct": _settings.slippage_pct,
            "max_risk_per_trade": _settings.max_risk_per_trade,
            "max_open_positions": _settings.max_open_positions,
            "max_position_size_pct": _settings.max_position_size_pct,
            "max_daily_loss_pct": _settings.max_daily_loss_pct,
            "stop_loss_pct": _settings.stop_loss_pct,
        }
    except Exception:
        _settings_risk = {
            "taker_fee_pct": 0.001,
            "slippage_pct": 0.001,
            "max_risk_per_trade": 0.02,
            "max_open_positions": 3,
            "max_position_size_pct": 0.25,
            "max_daily_loss_pct": 0.05,
            "stop_loss_pct": 0.03,
        }

    # Merge: caller overrides take precedence
    risk_cfg = {**_settings_risk, **(risk_config or {})}

    result = AuditResult(
        symbol=combo.symbol,
        interval=combo.interval,
        strategy_name=combo.strategy_name,
        params=params,
        statistically_approved=combo.approved,
        robustness_score=combo.robustness_score or 0.0,
        pass_rate=combo.pass_rate or 0.0,
    )

    if not candle_dicts:
        result.viability_reasons.append("no_candle_data")
        result.verdict = "flagged"
        return result

    # Build orchestrator with same config as paper trading
    try:
        strategy = get_strategy(combo.strategy_name, params=params)
    except Exception as e:
        result.viability_reasons.append(f"strategy_load_error: {e}")
        result.verdict = "flagged"
        return result

    broker = PaperBroker(
        initial_balance=10_000.0,
        fee_pct=risk_cfg["taker_fee_pct"],
        slippage_pct=risk_cfg["slippage_pct"],
    )

    risk = RiskEngine(
        equity=10_000.0,
        max_risk_per_trade=risk_cfg["max_risk_per_trade"],
        max_open_positions=risk_cfg["max_open_positions"],
        max_position_size_pct=risk_cfg["max_position_size_pct"],
        max_daily_loss_pct=risk_cfg["max_daily_loss_pct"],
        stop_loss_pct=risk_cfg["stop_loss_pct"],
        is_live=False,
    )

    orch = Orchestrator(
        strategy=strategy,
        risk_engine=risk,
        broker=broker,
        mode=TradingMode.PAPER,
        symbols=[combo.symbol],
        interval=combo.interval,
        database_url="sqlite:///:memory:",  # Ephemeral for audit
        regime_config=regime_config,
        persist_runtime=False,
    )

    # Run replay
    loop = asyncio.new_event_loop()
    try:
        with _quiet_runtime_replay(quiet):
            summary = loop.run_until_complete(orch.replay_candles(candle_dicts))
    finally:
        loop.close()

    # Build diagnostics
    diag = ReplayDiagnostics(
        total_candles=summary.get("total_candles", 0),
        buffering=summary.get("buffering", 0),
        no_signal=summary.get("no_signal", 0),
        lifecycle_blocked=summary.get("lifecycle_blocked", 0),
        risk_rejected=summary.get("signals_rejected", 0),
        regime_blocked=summary.get("regime_blocked", 0),
        order_rejected=summary.get("order_rejected", 0),
        trades_executed=summary.get("trades_executed", 0),
        errors=summary.get("errors", 0),
        duplicates=summary.get("duplicates", 0),
        grid_actions=dict(summary.get("grid_actions", {})),
        initial_capital=summary.get("initial_capital", 10_000.0),
        final_equity=summary.get("final_equity", 10_000.0),
        net_pnl=summary.get("net_pnl", 0.0),
    )
    result.diagnostics = diag

    # Evaluate runtime viability
    reasons: list[str] = []

    if diag.trades_executed < MIN_EXECUTED_TRADES:
        reasons.append(f"insufficient_trades: {diag.trades_executed} < {MIN_EXECUTED_TRADES}")

    if diag.effective_candles > 0 and diag.execution_ratio < MIN_EXECUTION_RATIO:
        reasons.append(f"low_execution_ratio: {diag.execution_ratio:.4f} < {MIN_EXECUTION_RATIO}")

    if diag.net_pnl < MIN_RUNTIME_NET_PNL:
        reasons.append(f"negative_runtime_pnl: ${diag.net_pnl:,.2f} < $0.00")

    if diag.signal_ratio > 0:
        lifecycle_ratio = diag.lifecycle_blocked / max(
            1,
            diag.trades_executed
            + diag.lifecycle_blocked
            + diag.risk_rejected
            + diag.order_rejected,
        )
        if lifecycle_ratio > MAX_LIFECYCLE_BLOCK_RATIO:
            reasons.append(
                f"high_lifecycle_block_ratio: "
                f"{lifecycle_ratio:.2%} > {MAX_LIFECYCLE_BLOCK_RATIO:.2%}"
            )

    if diag.errors > 0:
        reasons.append(f"replay_errors: {diag.errors}")

    result.viability_reasons = reasons
    result.runtime_viable = len(reasons) == 0

    # Verdict
    if result.statistically_approved and result.runtime_viable:
        result.verdict = "approved"
    elif result.statistically_approved and not result.runtime_viable:
        result.verdict = "downgraded"
    else:
        result.verdict = "rejected"

    logger.info(
        "parity_audit_complete",
        symbol=combo.symbol,
        interval=combo.interval,
        strategy=combo.strategy_name,
        verdict=result.verdict,
        trades=diag.trades_executed,
        execution_ratio=round(diag.execution_ratio, 4),
    )

    return result


def audit_all_approved(
    approved_combos: list[ApprovedCombination],
    candle_loader: Any,  # Callable[[str, str], list[dict]]
    risk_config: dict[str, Any] | None = None,
    regime_config: Any | None = None,
) -> list[AuditResult]:
    """Audit all approved combinations.

    Args:
        approved_combos: List of approved combinations to audit.
        candle_loader: Callable(symbol, interval) -> list[candle_dict].
        risk_config: Risk engine configuration overrides.
        regime_config: Optional regime gating config.

    Returns:
        List of AuditResult, one per combination.
    """
    results: list[AuditResult] = []

    for combo in approved_combos:
        candles = candle_loader(combo.symbol, combo.interval)
        result = audit_combination(combo, candles, risk_config, regime_config)
        results.append(result)

    return results
