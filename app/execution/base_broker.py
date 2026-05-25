"""Abstract broker interface — shared by paper and live brokers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.enums import OrderSide, OrderStatus, OrderType


@dataclass
class OrderRequest:
    """Order request to submit to a broker."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None  # Required for LIMIT orders
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    strategy_name: str = ""
    client_order_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    """Result of an order submission."""

    success: bool
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    requested_quantity: float = 0.0
    filled_quantity: float = 0.0
    requested_price: float = 0.0
    filled_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    fees: float = 0.0
    error_message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_response: dict[str, Any] = field(default_factory=dict)


class BaseBroker(ABC):
    """Abstract broker interface.

    Both paper and live brokers implement this interface,
    ensuring consistent behavior at the interface level.
    """

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order for execution."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def get_balance(self, asset: str = "USDT") -> float:
        """Get available balance for an asset."""
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get all open orders."""
        ...

    @abstractmethod
    async def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get current position for a symbol."""
        ...
