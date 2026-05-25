"""Regime filter — market condition gating for strategy signals.

Provides configurable filters that let the orchestrator and backtester
decide whether market conditions are suitable for trading or whether
the system should stay in cash.

Filters:
- Trend regime (SMA/EMA slope)
- Volatility regime (ATR as % of price)
- Benchmark trend (overall direction of the asset)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RegimeState:
    """Current market regime assessment."""

    is_tradable: bool
    regime: str  # "bullish", "bearish", "ranging", "volatile", "quiet", "unknown"
    trend_slope: float  # Normalized slope of trend indicator
    volatility_pct: float  # ATR as % of price
    benchmark_direction: str  # "up", "down", "flat"
    reasons: list[str]  # Why tradable or not


@dataclass
class RegimeConfig:
    """Configurable regime filter settings."""

    # Trend filter
    trend_sma_period: int = 50
    min_trend_slope_pct: float = 0.0  # 0 = disabled
    # Volatility filter
    atr_period: int = 14
    min_volatility_pct: float = 0.2  # Skip very quiet markets
    max_volatility_pct: float = 8.0  # Skip extremely volatile markets
    # Benchmark direction
    benchmark_sma_period: int = 100
    require_bullish_benchmark: bool = False  # If true, only trade when benchmark trending up
    # Overall
    enabled: bool = True


def assess_regime(
    candles: pd.DataFrame,
    config: RegimeConfig | None = None,
) -> RegimeState:
    """Assess market regime from candle data.

    Args:
        candles: OHLCV DataFrame.
        config: Regime filter configuration.

    Returns:
        RegimeState describing whether conditions are tradable.
    """
    config = config or RegimeConfig()

    if not config.enabled or candles.empty or len(candles) < config.trend_sma_period:
        return RegimeState(
            is_tradable=True, regime="unknown", trend_slope=0.0,
            volatility_pct=0.0, benchmark_direction="flat", reasons=["insufficient_data"],
        )

    closes = candles["close"].astype(float)
    reasons: list[str] = []
    is_tradable = True

    # --- Trend slope ---
    sma = closes.rolling(window=config.trend_sma_period).mean()
    sma_current = sma.iloc[-1]
    sma_prev = sma.iloc[-config.trend_sma_period] if len(sma) > config.trend_sma_period else sma.iloc[0]
    trend_slope = (sma_current - sma_prev) / sma_prev if sma_prev > 0 else 0.0

    if config.min_trend_slope_pct > 0 and abs(trend_slope) < config.min_trend_slope_pct:
        reasons.append(f"trend_too_flat: slope={trend_slope:.4f}")

    # --- Volatility ---
    tr = pd.concat([
        candles["high"].astype(float) - candles["low"].astype(float),
        (candles["high"].astype(float) - closes.shift(1)).abs(),
        (candles["low"].astype(float) - closes.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=config.atr_period).mean()
    volatility_pct = (atr.iloc[-1] / closes.iloc[-1]) * 100 if closes.iloc[-1] > 0 else 0.0

    if volatility_pct < config.min_volatility_pct:
        reasons.append(f"volatility_too_low: {volatility_pct:.2f}%")
        is_tradable = False
    elif volatility_pct > config.max_volatility_pct:
        reasons.append(f"volatility_too_high: {volatility_pct:.2f}%")
        is_tradable = False

    # --- Benchmark direction ---
    if len(closes) >= config.benchmark_sma_period:
        bench_sma = closes.rolling(window=config.benchmark_sma_period).mean()
        current_price = closes.iloc[-1]
        bench_sma_val = bench_sma.iloc[-1]

        if current_price > bench_sma_val * 1.01:
            benchmark_direction = "up"
        elif current_price < bench_sma_val * 0.99:
            benchmark_direction = "down"
        else:
            benchmark_direction = "flat"
    else:
        benchmark_direction = "flat"

    if config.require_bullish_benchmark and benchmark_direction != "up":
        reasons.append(f"benchmark_not_bullish: {benchmark_direction}")
        is_tradable = False

    # --- Determine regime label ---
    if trend_slope > 0.02:
        regime = "bullish"
    elif trend_slope < -0.02:
        regime = "bearish"
    elif volatility_pct > 3.0:
        regime = "volatile"
    elif volatility_pct < 0.5:
        regime = "quiet"
    else:
        regime = "ranging"

    if not reasons:
        reasons.append("conditions_acceptable")

    return RegimeState(
        is_tradable=is_tradable,
        regime=regime,
        trend_slope=round(trend_slope, 6),
        volatility_pct=round(volatility_pct, 4),
        benchmark_direction=benchmark_direction,
        reasons=reasons,
    )


def should_trade(
    candles: pd.DataFrame,
    config: RegimeConfig | None = None,
) -> tuple[bool, RegimeState]:
    """Convenience function: should we trade on this data?

    Returns:
        (is_tradable, regime_state)
    """
    state = assess_regime(candles, config)
    if not state.is_tradable:
        logger.info(
            "regime_gate_blocked",
            regime=state.regime,
            volatility_pct=state.volatility_pct,
            reasons=state.reasons,
        )
    return state.is_tradable, state
