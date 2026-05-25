"""Tests for paper broker behavior."""

from __future__ import annotations

import pytest
import asyncio

from app.core.enums import OrderSide, OrderStatus, OrderType
from app.execution.base_broker import OrderRequest
from app.execution.paper_broker import PaperBroker


@pytest.fixture
def broker():
    return PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.001)


class TestPaperBroker:
    def test_initial_balance(self, broker):
        balance = asyncio.get_event_loop().run_until_complete(broker.get_balance("USDT"))
        assert balance == 10_000.0

    def test_buy_order_fills(self, broker):
        order = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=50_000.0,
        )
        result = asyncio.get_event_loop().run_until_complete(broker.submit_order(order))
        assert result.success is True
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 0.1
        assert result.fees > 0

    def test_sell_order_fills(self, broker):
        # First buy
        buy = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=100.0,
        )
        asyncio.get_event_loop().run_until_complete(broker.submit_order(buy))

        # Then sell
        sell = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity=0.1, price=110.0,
        )
        result = asyncio.get_event_loop().run_until_complete(broker.submit_order(sell))
        assert result.success is True

    def test_insufficient_balance_rejected(self, broker):
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=1.0, price=100_000.0,  # $100K > $10K balance
        )
        result = asyncio.get_event_loop().run_until_complete(broker.submit_order(order))
        assert result.success is False
        assert result.status == OrderStatus.REJECTED

    def test_position_tracked_after_buy(self, broker):
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=100.0,
        )
        asyncio.get_event_loop().run_until_complete(broker.submit_order(order))
        pos = asyncio.get_event_loop().run_until_complete(broker.get_position("BTCUSDT"))
        assert pos is not None
        assert pos["quantity"] == 0.1

    def test_multiple_buys_aggregate_weighted_average_position(self):
        broker = PaperBroker(initial_balance=10_000.0, fee_pct=0.001, slippage_pct=0.0)
        loop = asyncio.get_event_loop()

        loop.run_until_complete(broker.submit_order(OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            price=100.0,
            metadata={"grid_id": "grid-1", "grid_level": 0},
        )))
        loop.run_until_complete(broker.submit_order(OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            price=80.0,
            metadata={"grid_id": "grid-1", "grid_level": 1},
        )))

        pos = loop.run_until_complete(broker.get_position("BTCUSDT"))

        assert pos is not None
        assert pos["quantity"] == pytest.approx(3.0)
        assert pos["entry_price"] == pytest.approx((100.0 + 160.0) / 3.0)
        assert pos["entry_fee"] == pytest.approx(0.26)

    def test_position_cleared_after_sell(self, broker):
        # Buy then sell
        buy = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=100.0,
        )
        asyncio.get_event_loop().run_until_complete(broker.submit_order(buy))
        sell = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity=0.1, price=110.0,
        )
        asyncio.get_event_loop().run_until_complete(broker.submit_order(sell))
        pos = asyncio.get_event_loop().run_until_complete(broker.get_position("BTCUSDT"))
        assert pos is None

    def test_slippage_applied(self, broker):
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.1, price=100.0,
        )
        result = asyncio.get_event_loop().run_until_complete(broker.submit_order(order))
        # Buy slippage pushes price up
        assert result.filled_price > 100.0
