"""Paper broker — simulated order execution for paper trading mode."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.enums import OrderSide, OrderStatus
from app.core.logging import get_logger
from app.execution.base_broker import BaseBroker, OrderRequest, OrderResult

logger = get_logger(__name__)


class PaperBroker(BaseBroker):
    """Simulated broker for paper trading.

    Maintains virtual balances, simulates fills with configurable
    fees and slippage, and tracks positions.
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        fee_pct: float = 0.001,
        slippage_pct: float = 0.001,
        quote_asset: str = "USDT",
    ) -> None:
        self.quote_asset = quote_asset
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct

        # Virtual state
        self.balances: dict[str, float] = {quote_asset: initial_balance}
        self.positions: dict[str, dict[str, Any]] = {}
        self.orders: list[OrderResult] = []
        self.open_orders: list[dict[str, Any]] = []

        logger.info(
            "paper_broker_initialized",
            initial_balance=initial_balance,
            quote_asset=quote_asset,
        )

    def _base_asset(self, symbol: str) -> str:
        """Derive base asset from symbol by removing the quote asset suffix.

        E.g. BTCUSDT with quote=USDT → BTC, BNBUSDT → BNB.
        Raises ValueError if the symbol doesn't end with the configured quote asset.
        """
        if not symbol.endswith(self.quote_asset):
            raise ValueError(
                f"Symbol '{symbol}' does not end with quote asset '{self.quote_asset}'. "
                "Verify TRADE_SYMBOLS and DEFAULT_QUOTE_ASSET configuration."
            )
        return symbol.removesuffix(self.quote_asset)

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """Simulate order execution with fees and slippage."""
        order_id = str(uuid.uuid4())[:12]

        # Simulate fill price with slippage
        fill_price = order.price or 0.0
        if order.side == OrderSide.BUY:
            fill_price *= (1 + self.slippage_pct)
        else:
            fill_price *= (1 - self.slippage_pct)

        # Calculate cost and fees
        cost = fill_price * order.quantity
        fee = cost * self.fee_pct

        # Check balance
        if order.side == OrderSide.BUY:
            available = self.balances.get(self.quote_asset, 0.0)
            if cost + fee > available:
                logger.warning(
                    "paper_order_rejected",
                    reason="insufficient_balance",
                    required=cost + fee,
                    available=available,
                )
                return OrderResult(
                    success=False,
                    order_id=order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=order.order_type.value,
                    requested_quantity=order.quantity,
                    requested_price=order.price or 0.0,
                    status=OrderStatus.REJECTED,
                    error_message="Insufficient paper balance",
                )

            # Execute buy
            self.balances[self.quote_asset] -= (cost + fee)
            base_asset = self._base_asset(order.symbol)
            self.balances[base_asset] = self.balances.get(base_asset, 0.0) + order.quantity

            # Track/aggregate long-only Spot position.
            existing = self.positions.get(order.symbol)
            if existing:
                old_qty = float(existing.get("quantity", 0.0))
                old_entry = float(existing.get("entry_price", fill_price))
                new_qty = old_qty + order.quantity
                existing["entry_price"] = (
                    ((old_entry * old_qty) + (fill_price * order.quantity)) / new_qty
                    if new_qty > 0
                    else fill_price
                )
                existing["quantity"] = new_qty
                existing["entry_fee"] = float(existing.get("entry_fee", 0.0)) + fee
                existing["stop_loss"] = order.stop_loss_price or existing.get("stop_loss")
                existing["take_profit"] = order.take_profit_price or existing.get("take_profit")
                existing["strategy"] = order.strategy_name or existing.get("strategy", "")
                existing.setdefault("opened_at", datetime.now(timezone.utc))
                existing["updated_at"] = datetime.now(timezone.utc)
                if order.metadata:
                    metadata = dict(existing.get("metadata", {}))
                    metadata.update(order.metadata)
                    existing["metadata"] = metadata
            else:
                self.positions[order.symbol] = {
                    "symbol": order.symbol,
                    "side": "BUY",
                    "entry_price": fill_price,
                    "quantity": order.quantity,
                    "entry_fee": fee,
                    "stop_loss": order.stop_loss_price,
                    "take_profit": order.take_profit_price,
                    "strategy": order.strategy_name,
                    "metadata": dict(order.metadata),
                    "opened_at": datetime.now(timezone.utc),
                }

        else:  # SELL
            base_asset = self._base_asset(order.symbol)
            available = self.balances.get(base_asset, 0.0)
            if order.quantity > available:
                logger.warning(
                    "paper_order_rejected",
                    reason="insufficient_asset",
                    required=order.quantity,
                    available=available,
                )
                return OrderResult(
                    success=False,
                    order_id=order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=order.order_type.value,
                    requested_quantity=order.quantity,
                    requested_price=order.price or 0.0,
                    status=OrderStatus.REJECTED,
                    error_message="Insufficient paper asset balance",
                )

            # Execute sell
            self.balances[base_asset] -= order.quantity
            self.balances[self.quote_asset] = self.balances.get(self.quote_asset, 0.0) + (cost - fee)

            # Close or reduce tracked position.
            if order.symbol in self.positions:
                position = self.positions[order.symbol]
                remaining = float(position.get("quantity", 0.0)) - order.quantity
                if remaining <= 1e-12:
                    del self.positions[order.symbol]
                else:
                    position["quantity"] = remaining
                    position["updated_at"] = datetime.now(timezone.utc)

        result = OrderResult(
            success=True,
            order_id=order_id,
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            requested_quantity=order.quantity,
            filled_quantity=order.quantity,
            requested_price=order.price or 0.0,
            filled_price=fill_price,
            status=OrderStatus.FILLED,
            fees=fee,
        )

        self.orders.append(result)

        logger.info(
            "paper_order_filled",
            order_id=order_id,
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            price=fill_price,
            fee=round(fee, 4),
        )

        return result

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel a paper order (no-op for instant fills)."""
        logger.info("paper_cancel_order", order_id=order_id, symbol=symbol)
        return True

    async def get_balance(self, asset: str = "USDT") -> float:
        """Get virtual balance."""
        return self.balances.get(asset, 0.0)

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Paper broker fills instantly, so no open orders."""
        return []

    async def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get virtual position."""
        return self.positions.get(symbol)

    def get_total_equity(self, prices: dict[str, float] | None = None) -> float:
        """Calculate total equity including position mark-to-market."""
        equity = self.balances.get(self.quote_asset, 0.0)
        prices = prices or {}
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, pos["entry_price"])
            equity += pos["quantity"] * price
        return equity
