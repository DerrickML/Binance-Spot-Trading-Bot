"""Portfolio service — tracks portfolio state and position management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PortfolioPosition:
    """A tracked portfolio position."""

    symbol: str
    side: str
    entry_price: float
    quantity: float
    current_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_name: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def unrealized_pnl(self) -> float:
        if self.side == "BUY":
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        cost = self.entry_price * self.quantity
        return self.unrealized_pnl / cost if cost > 0 else 0.0


class PortfolioService:
    """Manages portfolio state with position and balance tracking."""

    def __init__(self, initial_equity: float = 10_000.0) -> None:
        self.initial_equity = initial_equity
        self.cash = initial_equity
        self.positions: dict[str, PortfolioPosition] = {}
        self.closed_trades: list[dict[str, Any]] = []
        self.realized_pnl: float = 0.0

    def open_position(self, position: PortfolioPosition) -> None:
        """Track a new open position."""
        self.positions[position.symbol] = position
        logger.info(
            "position_opened",
            symbol=position.symbol,
            side=position.side,
            price=position.entry_price,
            quantity=position.quantity,
        )

    def close_position(self, symbol: str, exit_price: float) -> float:
        """Close a position and return realized PnL."""
        pos = self.positions.pop(symbol, None)
        if not pos:
            logger.warning("close_position_not_found", symbol=symbol)
            return 0.0

        pos.current_price = exit_price
        pnl = pos.unrealized_pnl
        self.realized_pnl += pnl

        self.closed_trades.append({
            "symbol": symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "pnl": pnl,
            "opened_at": pos.opened_at.isoformat(),
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info("position_closed", symbol=symbol, pnl=round(pnl, 2))
        return pnl

    def get_total_equity(self) -> float:
        """Calculate total equity (cash + unrealized PnL)."""
        unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        return self.cash + unrealized

    def get_open_position_count(self) -> int:
        return len(self.positions)

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update current prices for all positions."""
        for symbol, pos in self.positions.items():
            if symbol in prices:
                pos.current_price = prices[symbol]
