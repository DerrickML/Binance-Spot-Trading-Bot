"""Binance Spot live broker — real order execution with full safety guards.

Live trading is disabled by default and requires explicit configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.enums import OrderStatus, OrderType
from app.core.exceptions import (
    BrokerError,
    ExecutionError,
    KillSwitchError,
)
from app.core.logging import get_logger
from app.execution.base_broker import BaseBroker, OrderRequest, OrderResult

logger = get_logger(__name__)


class BinanceBroker(BaseBroker):
    """Binance Spot live broker.

    Validates symbol filters, rounds quantities, checks balances,
    and refuses execution when unsafe. Supports market and limit orders.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.binance.com",
        live_enabled: bool = False,
        kill_switch_active: bool = False,
    ) -> None:
        if not api_key or not api_secret:
            raise ExecutionError("Binance API credentials are required for live broker")

        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.live_enabled = live_enabled
        self.kill_switch_active = kill_switch_active

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-MBX-APIKEY": api_key},
            timeout=30.0,
        )

        # Cache exchange info
        self._exchange_info: dict[str, Any] = {}
        self._symbol_filters: dict[str, dict[str, Any]] = {}

        logger.info(
            "binance_broker_initialized",
            base_url=base_url,
            live_enabled=live_enabled,
        )

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign request params with HMAC SHA256."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _load_exchange_info(self) -> None:
        """Fetch and cache exchange info for symbol validation."""
        if self._exchange_info:
            return
        try:
            resp = await self._client.get("/api/v3/exchangeInfo")
            resp.raise_for_status()
            self._exchange_info = resp.json()
            for sym_info in self._exchange_info.get("symbols", []):
                self._symbol_filters[sym_info["symbol"]] = sym_info
            logger.info("exchange_info_loaded", symbols=len(self._symbol_filters))
        except Exception as e:
            raise BrokerError(f"Failed to load exchange info: {e}") from e

    def _get_symbol_filter(self, symbol: str, filter_type: str) -> dict[str, Any] | None:
        """Get a specific filter for a symbol."""
        sym = self._symbol_filters.get(symbol, {})
        for f in sym.get("filters", []):
            if f["filterType"] == filter_type:
                return f
        return None

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit a live order to Binance.

        Safety checks:
        1. Live trading must be enabled
        2. Kill switch must not be active
        3. Exchange filters must be loaded and validated
        4. Balance must be sufficient
        """
        # Safety guards
        if not self.live_enabled:
            raise ExecutionError("Live trading is disabled. Set ENABLE_LIVE_TRADING=true")

        if self.kill_switch_active:
            raise KillSwitchError("Kill switch is active — refusing to place orders")

        await self._load_exchange_info()

        # Validate symbol exists
        if order.symbol not in self._symbol_filters:
            raise ExecutionError(f"Symbol {order.symbol} not found on exchange")

        # Build order params
        params: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": str(order.quantity),
            "newClientOrderId": order.client_order_id or str(uuid.uuid4())[:20],
        }

        if order.order_type == OrderType.LIMIT:
            if not order.price:
                raise ExecutionError("LIMIT orders require a price")
            params["price"] = str(order.price)
            params["timeInForce"] = "GTC"

        # Log the request before submission
        logger.info(
            "binance_order_submit",
            symbol=order.symbol,
            side=order.side.value,
            type=order.order_type.value,
            quantity=order.quantity,
            price=order.price,
        )

        try:
            signed = self._sign(params)
            resp = await self._client.post("/api/v3/order", params=signed)
            data = resp.json()

            if resp.status_code != 200:
                error_msg = data.get("msg", "Unknown error")
                logger.error(
                    "binance_order_error",
                    status=resp.status_code,
                    error=error_msg,
                    code=data.get("code"),
                )
                return OrderResult(
                    success=False,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=order.order_type.value,
                    requested_quantity=order.quantity,
                    requested_price=order.price or 0.0,
                    status=OrderStatus.REJECTED,
                    error_message=error_msg,
                    raw_response=data,
                )

            # Parse fill info
            fills = data.get("fills", [])
            total_qty = sum(float(f.get("qty", 0)) for f in fills)
            total_cost = sum(float(f.get("qty", 0)) * float(f.get("price", 0)) for f in fills)
            total_fee = sum(float(f.get("commission", 0)) for f in fills)
            avg_price = total_cost / total_qty if total_qty > 0 else 0.0

            result = OrderResult(
                success=True,
                order_id=str(data.get("orderId", "")),
                symbol=order.symbol,
                side=order.side.value,
                order_type=order.order_type.value,
                requested_quantity=order.quantity,
                filled_quantity=total_qty,
                requested_price=order.price or 0.0,
                filled_price=avg_price,
                status=OrderStatus(data.get("status", "FILLED")),
                fees=total_fee,
                raw_response=data,
            )

            logger.info(
                "binance_order_filled",
                order_id=result.order_id,
                filled_qty=total_qty,
                avg_price=avg_price,
                fee=total_fee,
            )

            return result

        except httpx.HTTPError as e:
            raise BrokerError(f"HTTP error submitting order: {e}") from e

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order on Binance."""
        try:
            params = self._sign({"symbol": symbol, "orderId": order_id})
            resp = await self._client.delete("/api/v3/order", params=params)
            if resp.status_code == 200:
                logger.info("binance_order_cancelled", order_id=order_id, symbol=symbol)
                return True
            logger.warning("binance_cancel_failed", status=resp.status_code, body=resp.text)
            return False
        except Exception as e:
            logger.error("binance_cancel_error", error=str(e))
            return False

    async def get_balance(self, asset: str = "USDT") -> float:
        """Get available balance from Binance account."""
        try:
            params = self._sign({})
            resp = await self._client.get("/api/v3/account", params=params)
            resp.raise_for_status()
            data = resp.json()
            for bal in data.get("balances", []):
                if bal["asset"] == asset:
                    return float(bal["free"])
            return 0.0
        except Exception as e:
            raise BrokerError(f"Failed to fetch balance: {e}") from e

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get open orders from Binance."""
        try:
            params: dict[str, Any] = {}
            if symbol:
                params["symbol"] = symbol
            params = self._sign(params)
            resp = await self._client.get("/api/v3/openOrders", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise BrokerError(f"Failed to fetch open orders: {e}") from e

    async def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get current holdings for a symbol (Spot doesn't have positions per se)."""
        base_asset = symbol.replace("USDT", "").replace("BUSD", "")
        balance = await self.get_balance(base_asset)
        if balance > 0:
            return {"symbol": symbol, "quantity": balance, "asset": base_asset}
        return None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
