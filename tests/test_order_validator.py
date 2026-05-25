"""Tests for order validator and rounding utilities."""

from __future__ import annotations

import pytest

from app.core.enums import OrderSide, OrderType
from app.core.exceptions import OrderValidationError
from app.core.utils import round_step_size, round_price
from app.execution.base_broker import OrderRequest
from app.execution.order_validator import OrderValidator


class TestRounding:
    def test_round_step_size(self):
        assert round_step_size(0.12345678, 0.00001) == 0.12345
        assert round_step_size(1.999, 0.001) == 1.999
        assert round_step_size(0.5, 1.0) == 0.0

    def test_round_price(self):
        assert round_price(42123.456, 0.01) == 42123.45
        assert round_price(100.0, 1.0) == 100.0

    def test_round_step_size_exact(self):
        assert round_step_size(1.0, 0.1) == 1.0
        assert round_step_size(0.00001, 0.00001) == 0.00001


class TestOrderValidator:
    def test_validates_lot_size(self, sample_exchange_info):
        validator = OrderValidator(sample_exchange_info)
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.123456789, price=50000.0,
        )
        adjusted = validator.validate_and_adjust(order)
        # Should be rounded to step size 0.00001
        assert adjusted.quantity == 0.12345

    def test_rejects_below_min_qty(self, sample_exchange_info):
        validator = OrderValidator(sample_exchange_info)
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=0.000001, price=50000.0,
        )
        with pytest.raises(OrderValidationError, match="below minimum"):
            validator.validate_and_adjust(order)

    def test_validates_price_filter(self, sample_exchange_info):
        validator = OrderValidator(sample_exchange_info)
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=0.001, price=50000.123,
        )
        adjusted = validator.validate_and_adjust(order)
        assert adjusted.price == 50000.12  # Rounded to tick size 0.01

    def test_rejects_below_min_notional(self, sample_exchange_info):
        validator = OrderValidator(sample_exchange_info)
        order = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=0.00001, price=1.0,  # Notional = $0.00001 < $10
        )
        with pytest.raises(OrderValidationError, match="below minimum"):
            validator.validate_and_adjust(order)

    def test_unknown_symbol_passes(self):
        validator = OrderValidator({"symbols": []})
        order = OrderRequest(
            symbol="XYZUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=1.0, price=100.0,
        )
        result = validator.validate_and_adjust(order)
        assert result.quantity == 1.0
