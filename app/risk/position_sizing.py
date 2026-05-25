"""Position sizing module.

Calculates appropriate position sizes based on risk parameters,
account equity, and ATR-based volatility measures.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


def calculate_position_size(
    equity: float,
    risk_per_trade: float,
    entry_price: float,
    stop_loss_price: float,
    max_position_size_pct: float = 0.25,
) -> float:
    """Calculate position size based on risk parameters.

    Uses fixed-fractional risk: risk_amount = equity * risk_per_trade.
    Position size = risk_amount / (entry_price - stop_loss).

    Args:
        equity: Current account equity.
        risk_per_trade: Maximum risk as fraction of equity (e.g., 0.02 = 2%).
        entry_price: Planned entry price.
        stop_loss_price: Stop-loss price.
        max_position_size_pct: Maximum position as fraction of equity.

    Returns:
        Position size in base asset units.
    """
    if entry_price <= 0 or equity <= 0:
        return 0.0

    risk_amount = equity * risk_per_trade
    price_risk = abs(entry_price - stop_loss_price)

    if price_risk <= 0:
        logger.warning("position_size_zero_risk", entry=entry_price, stop_loss=stop_loss_price)
        return 0.0

    size_by_risk = risk_amount / price_risk
    max_size = (equity * max_position_size_pct) / entry_price

    final_size = min(size_by_risk, max_size)

    logger.debug(
        "position_size_calculated",
        equity=equity,
        risk_per_trade=risk_per_trade,
        entry_price=entry_price,
        stop_loss=stop_loss_price,
        size_by_risk=round(size_by_risk, 8),
        max_size=round(max_size, 8),
        final_size=round(final_size, 8),
    )

    return final_size


def calculate_stop_loss(
    entry_price: float,
    atr: float,
    multiplier: float = 2.0,
    side: str = "BUY",
) -> float:
    """Calculate stop-loss price using ATR.

    Args:
        entry_price: Entry price.
        atr: Current ATR value.
        multiplier: ATR multiplier for stop distance.
        side: BUY or SELL.

    Returns:
        Stop-loss price.
    """
    distance = atr * multiplier
    if side == "BUY":
        return entry_price - distance
    return entry_price + distance
