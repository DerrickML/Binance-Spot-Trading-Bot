"""Risk rules — individual enforceable rules for the risk engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.enums import SignalType
from app.strategies.base import StrategySignal


@dataclass
class RiskContext:
    """Context provided to risk rules for evaluation."""

    signal: StrategySignal
    equity: float
    available_balance: float
    open_positions: int
    daily_pnl: float
    daily_loss_pct: float
    consecutive_losses: int
    last_trade_time: datetime | None
    symbol_last_trade_time: dict[str, datetime]
    disabled_symbols: set[str]
    is_live: bool
    kill_switch_active: bool
    error_count_today: int
    max_risk_per_trade: float
    max_open_positions: int
    max_position_size_pct: float
    max_daily_loss_pct: float
    stop_loss_pct: float


@dataclass
class RiskDecision:
    """Result of a risk rule evaluation."""

    approved: bool
    rule_name: str
    reason: str = ""


class RiskRule(ABC):
    """Abstract base class for individual risk rules."""

    name: str = "base_rule"

    @abstractmethod
    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        """Evaluate the rule against the given context."""
        ...


class KillSwitchRule(RiskRule):
    """Reject all signals when kill switch is active."""

    name = "kill_switch"

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.kill_switch_active:
            return RiskDecision(False, self.name, "Kill switch is active — all trading halted")
        return RiskDecision(True, self.name)


class MaxOpenPositionsRule(RiskRule):
    """Reject if maximum open positions reached."""

    name = "max_open_positions"

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.signal.metadata.get("grid_action") == "scale_in":
            return RiskDecision(True, self.name)
        if ctx.signal.signal_type == SignalType.BUY and ctx.open_positions >= ctx.max_open_positions:
            return RiskDecision(False, self.name, f"Max open positions ({ctx.max_open_positions}) reached")
        return RiskDecision(True, self.name)


class MaxDailyLossRule(RiskRule):
    """Reject if daily loss limit breached."""

    name = "max_daily_loss"

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.signal.signal_type != SignalType.BUY:
            return RiskDecision(True, self.name)

        if ctx.daily_loss_pct >= ctx.max_daily_loss_pct:
            return RiskDecision(
                False, self.name,
                f"Daily loss ({ctx.daily_loss_pct:.2%}) exceeds limit ({ctx.max_daily_loss_pct:.2%})"
            )
        return RiskDecision(True, self.name)


class MaxPositionSizeRule(RiskRule):
    """Reject if position cost would exceed max allowed equity fraction.

    Checks whether the dollar cost of the position (price × quantity)
    would exceed max_position_size_pct × equity. Note: the orchestrator's
    _calculate_quantity() already caps BUY size, so this rule acts as a
    safety net rather than the primary sizing control.
    """

    name = "max_position_size"

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.signal.signal_type != SignalType.BUY:
            return RiskDecision(True, self.name)

        if ctx.equity <= 0 or ctx.available_balance <= 0:
            return RiskDecision(
                False, self.name,
                "No equity or available balance for a new position"
            )

        max_cost = ctx.equity * ctx.max_position_size_pct
        if max_cost <= 0:
            return RiskDecision(False, self.name, "Max position size is zero")

        requested_notional = ctx.signal.metadata.get("notional")
        tolerance = max(1e-9, max_cost * 1e-9)
        if requested_notional is not None and float(requested_notional) > max_cost + tolerance:
            return RiskDecision(
                False, self.name,
                f"Requested notional (${float(requested_notional):,.2f}) exceeds max position size (${max_cost:,.2f})"
            )

        return RiskDecision(True, self.name)


class StopLossRequiredRule(RiskRule):
    """In live mode, every trade must have a stop loss."""

    name = "stop_loss_required"

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.is_live and ctx.signal.signal_type == SignalType.BUY and ctx.signal.stop_loss is None:
            return RiskDecision(False, self.name, "Live trading requires stop_loss on every signal")
        return RiskDecision(True, self.name)


class ConsecutiveLossCooldownRule(RiskRule):
    """Enforce cooldown after consecutive losses."""

    name = "consecutive_loss_cooldown"
    max_consecutive: int = 5

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.signal.signal_type != SignalType.BUY:
            return RiskDecision(True, self.name)

        if ctx.consecutive_losses >= self.max_consecutive:
            return RiskDecision(
                False, self.name,
                f"Cooldown active: {ctx.consecutive_losses} consecutive losses"
            )
        return RiskDecision(True, self.name)


class DisabledSymbolRule(RiskRule):
    """Reject signals for disabled symbols."""

    name = "disabled_symbol"

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.signal.symbol in ctx.disabled_symbols:
            return RiskDecision(False, self.name, f"Symbol {ctx.signal.symbol} is disabled")
        return RiskDecision(True, self.name)


class ErrorHaltRule(RiskRule):
    """Halt trading on too many errors in a day."""

    name = "error_halt"
    max_errors: int = 10

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.error_count_today >= self.max_errors:
            return RiskDecision(
                False, self.name,
                f"Too many errors today ({ctx.error_count_today}). Trading halted."
            )
        return RiskDecision(True, self.name)


class SymbolCooldownRule(RiskRule):
    """Enforce minimum time between trades on the same symbol."""

    name = "symbol_cooldown"
    cooldown_seconds: int = 300  # 5 minutes default

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        if ctx.signal.signal_type != SignalType.BUY:
            return RiskDecision(True, self.name)

        last_trade = ctx.symbol_last_trade_time.get(ctx.signal.symbol)
        if last_trade:
            now = ctx.signal.timestamp or datetime.now(timezone.utc)
            elapsed = (now - last_trade).total_seconds()
            if elapsed < self.cooldown_seconds:
                return RiskDecision(
                    False, self.name,
                    f"Symbol cooldown: {int(self.cooldown_seconds - elapsed)}s remaining for {ctx.signal.symbol}"
                )
        return RiskDecision(True, self.name)


# Default rule set
DEFAULT_RULES: list[RiskRule] = [
    KillSwitchRule(),
    MaxOpenPositionsRule(),
    MaxDailyLossRule(),
    MaxPositionSizeRule(),
    StopLossRequiredRule(),
    ConsecutiveLossCooldownRule(),
    DisabledSymbolRule(),
    ErrorHaltRule(),
    SymbolCooldownRule(),
]
