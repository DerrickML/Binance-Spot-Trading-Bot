"""Order validator — validates orders against exchange filters."""

from __future__ import annotations

from app.core.exceptions import OrderValidationError
from app.core.logging import get_logger
from app.core.utils import round_price, round_step_size
from app.execution.base_broker import OrderRequest

logger = get_logger(__name__)


class OrderValidator:
    """Validates orders against Binance exchange filters.

    Checks lot size, price filter, min notional, and rounds
    quantities/prices to exchange-compliant values.
    """

    def __init__(self, exchange_info: dict | None = None) -> None:
        self._symbol_filters: dict[str, dict] = {}
        if exchange_info:
            self.load_exchange_info(exchange_info)

    def load_exchange_info(self, exchange_info: dict) -> None:
        """Load exchange info and parse symbol filters."""
        for sym in exchange_info.get("symbols", []):
            filters: dict[str, dict] = {}
            for f in sym.get("filters", []):
                filters[f["filterType"]] = f
            self._symbol_filters[sym["symbol"]] = filters

    def validate_and_adjust(self, order: OrderRequest) -> OrderRequest:
        """Validate and adjust order quantities/prices.

        Returns adjusted order or raises OrderValidationError.
        """
        filters = self._symbol_filters.get(order.symbol)
        if not filters:
            logger.warning("no_filters_for_symbol", symbol=order.symbol)
            return order

        # LOT_SIZE filter
        lot_size = filters.get("LOT_SIZE", {})
        if lot_size:
            step = float(lot_size.get("stepSize", 0))
            min_qty = float(lot_size.get("minQty", 0))
            max_qty = float(lot_size.get("maxQty", float("inf")))

            if step > 0:
                order.quantity = round_step_size(order.quantity, step)

            if order.quantity < min_qty:
                raise OrderValidationError(
                    f"Quantity {order.quantity} below minimum {min_qty} for {order.symbol}"
                )
            if order.quantity > max_qty:
                raise OrderValidationError(
                    f"Quantity {order.quantity} above maximum {max_qty} for {order.symbol}"
                )

        # PRICE_FILTER
        if order.price:
            price_filter = filters.get("PRICE_FILTER", {})
            if price_filter:
                tick = float(price_filter.get("tickSize", 0))
                min_price = float(price_filter.get("minPrice", 0))
                max_price = float(price_filter.get("maxPrice", float("inf")))

                if tick > 0:
                    order.price = round_price(order.price, tick)

                if order.price < min_price:
                    raise OrderValidationError(
                        f"Price {order.price} below minimum {min_price}"
                    )
                if max_price > 0 and order.price > max_price:
                    raise OrderValidationError(
                        f"Price {order.price} above maximum {max_price}"
                    )

        # MIN_NOTIONAL
        notional_filter = filters.get("MIN_NOTIONAL", {}) or filters.get("NOTIONAL", {})
        if notional_filter:
            min_notional = float(notional_filter.get("minNotional", 0))
            price = order.price or 0
            if price > 0 and price * order.quantity < min_notional:
                raise OrderValidationError(
                    f"Notional {price * order.quantity:.2f} below minimum {min_notional}"
                )

        logger.debug(
            "order_validated",
            symbol=order.symbol,
            quantity=order.quantity,
            price=order.price,
        )

        return order
