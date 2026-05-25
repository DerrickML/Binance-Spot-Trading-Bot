"""Utility functions used across the trading platform."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal


def round_step_size(quantity: float, step_size: float) -> float:
    """Round quantity down to the nearest step size (exchange lot filter).

    Args:
        quantity: Raw quantity to round.
        step_size: Exchange step size for the symbol.

    Returns:
        Rounded quantity compliant with exchange filters.
    """
    if step_size <= 0:
        return quantity
    d_qty = Decimal(str(quantity))
    d_step = Decimal(str(step_size))
    return float((d_qty / d_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_step)


def round_price(price: float, tick_size: float) -> float:
    """Round price to the nearest tick size (exchange price filter).

    Args:
        price: Raw price to round.
        tick_size: Exchange tick size.

    Returns:
        Rounded price compliant with exchange filters.
    """
    if tick_size <= 0:
        return price
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    return float((d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_tick)


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns a default on zero denominator."""
    if denominator == 0:
        return default
    return numerator / denominator


def timestamp_to_datetime(ts_ms: int) -> datetime:
    """Convert millisecond timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def datetime_to_timestamp(dt: datetime) -> int:
    """Convert datetime to millisecond timestamp."""
    return int(dt.timestamp() * 1000)


def pct_change(old: float, new: float) -> float:
    """Calculate percentage change between two values."""
    return safe_div(new - old, abs(old))


def bps_to_pct(bps: int) -> float:
    """Convert basis points to a decimal fraction."""
    return bps / 10_000
