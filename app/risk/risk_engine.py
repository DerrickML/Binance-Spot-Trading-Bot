"""Risk engine — final gate before any trade execution.

The risk engine evaluates all risk rules against a signal and either
approves or rejects it. No module may bypass the risk engine in live mode.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.risk.rules import DEFAULT_RULES, RiskContext, RiskDecision, RiskRule
from app.strategies.base import StrategySignal

logger = get_logger(__name__)


class RiskEngine:
    """Central risk engine that evaluates all rules before execution.

    This is the final gate — it can reject any strategy signal.
    All risk decisions are logged for auditability.
    """

    def __init__(
        self,
        rules: list[RiskRule] | None = None,
        equity: float = 10_000.0,
        max_risk_per_trade: float = 0.02,
        max_open_positions: int = 3,
        max_position_size_pct: float = 0.25,
        max_daily_loss_pct: float = 0.05,
        stop_loss_pct: float = 0.03,
        is_live: bool = False,
    ) -> None:
        self.rules = rules or list(DEFAULT_RULES)
        self.equity = equity
        self.available_balance = equity
        self.max_risk_per_trade = max_risk_per_trade
        self.max_open_positions = max_open_positions
        self.max_position_size_pct = max_position_size_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.stop_loss_pct = stop_loss_pct
        self.is_live = is_live

        # State tracking
        self.open_positions: int = 0
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.last_trade_time: datetime | None = None
        self.symbol_last_trade_time: dict[str, datetime] = {}
        self.disabled_symbols: set[str] = set()
        self.kill_switch_active: bool = False
        self.error_count_today: int = 0

    def evaluate(self, signal: StrategySignal) -> list[RiskDecision]:
        """Evaluate a signal against all risk rules.

        Args:
            signal: The strategy signal to evaluate.

        Returns:
            List of RiskDecision objects (one per rule).
        """
        ctx = RiskContext(
            signal=signal,
            equity=self.equity,
            available_balance=self.available_balance,
            open_positions=self.open_positions,
            daily_pnl=self.daily_pnl,
            daily_loss_pct=abs(min(0, self.daily_pnl) / self.equity) if self.equity > 0 else 0,
            consecutive_losses=self.consecutive_losses,
            last_trade_time=self.last_trade_time,
            symbol_last_trade_time=self.symbol_last_trade_time,
            disabled_symbols=self.disabled_symbols,
            is_live=self.is_live,
            kill_switch_active=self.kill_switch_active,
            error_count_today=self.error_count_today,
            max_risk_per_trade=self.max_risk_per_trade,
            max_open_positions=self.max_open_positions,
            max_position_size_pct=self.max_position_size_pct,
            max_daily_loss_pct=self.max_daily_loss_pct,
            stop_loss_pct=self.stop_loss_pct,
        )

        decisions: list[RiskDecision] = []
        for rule in self.rules:
            decision = rule.evaluate(ctx)
            decisions.append(decision)

            if not decision.approved:
                logger.warning(
                    "risk_signal_rejected",
                    rule=decision.rule_name,
                    reason=decision.reason,
                    symbol=signal.symbol,
                    signal_type=signal.signal_type,
                )

        return decisions

    def is_approved(self, signal: StrategySignal) -> tuple[bool, str]:
        """Check if a signal passes all risk rules.

        Returns:
            Tuple of (approved: bool, reason: str).
        """
        decisions = self.evaluate(signal)
        rejections = [d for d in decisions if not d.approved]

        if rejections:
            reasons = "; ".join(f"[{d.rule_name}] {d.reason}" for d in rejections)
            logger.info(
                "risk_final_decision",
                approved=False,
                symbol=signal.symbol,
                reasons=reasons,
            )
            return False, reasons

        logger.info(
            "risk_final_decision",
            approved=True,
            symbol=signal.symbol,
            signal_type=signal.signal_type,
        )
        return True, ""

    def activate_kill_switch(self, reason: str = "") -> None:
        """Activate the emergency kill switch."""
        self.kill_switch_active = True
        logger.critical("kill_switch_activated", reason=reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the emergency kill switch."""
        self.kill_switch_active = False
        logger.info("kill_switch_deactivated")

    def record_trade_result(self, pnl: float, symbol: str, timestamp: datetime | None = None) -> None:
        """Update internal state after a trade completes."""
        self.daily_pnl += pnl
        self.last_trade_time = timestamp or datetime.now(timezone.utc)
        self.symbol_last_trade_time[symbol] = self.last_trade_time

        if pnl <= 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def record_error(self) -> None:
        """Record an error for the error halt rule."""
        self.error_count_today += 1

    def reset_daily(self) -> None:
        """Reset daily counters (call at start of each trading day)."""
        self.daily_pnl = 0.0
        self.error_count_today = 0
        logger.info("risk_engine_daily_reset")
