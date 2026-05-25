"""Volatility Breakout with Trend Bias — spot long-only.

Buys when price breaks above a volatility envelope (Keltner channel upper band)
with trend confirmation (EMA alignment). Avoids breakouts in downtrends.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class VolatilityBreakoutStrategy(BaseStrategy):
    """Volatility breakout with trend filter.

    Spot-only, long-only. Buys when:
    - Close breaks above Keltner channel upper band
    - Trend EMA is rising (close > trend EMA)
    - Volume surge confirms breakout
    - ATR confirms sufficient volatility
    - Cooldown between signals

    ATR-scaled stop-loss and take-profit.
    """

    name = "volatility_breakout"
    description = "Keltner channel breakout with trend bias and volume confirmation"
    version = "1.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "keltner_ema": 20,
            "keltner_atr_mult": 2.0,     # Band width in ATR units
            "trend_ema": 50,
            "atr_period": 14,
            "volume_mult": 1.5,          # Volume must be above this × average
            "min_atr_pct": 0.3,          # Min ATR% to avoid dead markets
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 3.0,
            "min_periods": 55,
            "cooldown_bars": 8,
        }

    def generate_signals(self, candles: pd.DataFrame) -> list[StrategySignal]:
        p = self.params
        min_p = p["min_periods"]
        if len(candles) < min_p:
            return []

        df = candles.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # Keltner channel
        df["kelt_mid"] = close.ewm(span=p["keltner_ema"], adjust=False).mean()
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.rolling(p["atr_period"]).mean()
        df["kelt_upper"] = df["kelt_mid"] + df["atr"] * p["keltner_atr_mult"]

        # Trend EMA
        df["trend_ema"] = close.ewm(span=p["trend_ema"], adjust=False).mean()

        # Volume average
        df["vol_sma"] = volume.rolling(20).mean()

        # ATR%
        df["atr_pct"] = df["atr"] / close * 100

        signals = []
        last_signal_idx = -p["cooldown_bars"] - 1

        for i in range(min_p, len(df)):
            row = df.iloc[i]
            if pd.isna(row["kelt_upper"]) or pd.isna(row["trend_ema"]) or pd.isna(row["atr"]):
                continue

            bars_since = i - last_signal_idx
            atr = row["atr"] if row["atr"] > 0 else 1.0
            prev_close = df.iloc[i - 1]["close"] if i > 0 else row["close"]
            prev_upper = df.iloc[i - 1].get("kelt_upper", row["kelt_upper"]) if i > 0 else row["kelt_upper"]
            crossed_upper = prev_close <= prev_upper if i > 0 else True

            # BUY: breakout above Keltner upper + trend + volume + volatility
            if (
                bars_since >= p["cooldown_bars"]
                and row["close"] > row["kelt_upper"]
                and crossed_upper
                and row["close"] > row["trend_ema"]
                and row["volume"] > row["vol_sma"] * p["volume_mult"]
                and row["atr_pct"] > p["min_atr_pct"]
            ):
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY,
                    symbol="UNKNOWN",
                    price=row["close"],
                    strength=min(1.0, row["atr_pct"] / 3),
                    stop_loss=row["close"] - atr * p["stop_loss_atr_mult"],
                    take_profit=row["close"] + atr * p["take_profit_atr_mult"],
                    metadata={
                        "_bar_index": int(i),
                        "breakout_above": round(float(row["kelt_upper"]), 2),
                        "atr_pct": round(float(row["atr_pct"]), 2),
                    },
                ))
                last_signal_idx = i

            # SELL: price drops back below Keltner midline
            elif (
                bars_since >= p["cooldown_bars"]
                and row["close"] < row["kelt_mid"]
                and row["close"] < row["trend_ema"]
            ):
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL,
                    symbol="UNKNOWN",
                    price=row["close"],
                    strength=0.5,
                    metadata={"_bar_index": int(i), "reason": "below_keltner_mid"},
                ))
                last_signal_idx = i

        return signals
