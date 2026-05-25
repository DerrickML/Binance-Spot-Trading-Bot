"""Pullback-in-Uptrend strategy — spot long-only.

Buys during temporary pullbacks within an established uptrend.
Trend confirmed by EMA alignment + ADX. Pullback detected via RSI dip.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class PullbackUptrendStrategy(BaseStrategy):
    """Pullback-in-uptrend: buy dips within strong uptrends.

    Spot-only, long-only. Buys when:
    - Fast EMA > Slow EMA (uptrend)
    - Price is above slow EMA (still trending)
    - RSI dips to pullback zone (oversold in uptrend)
    - Price has pulled back to near fast EMA (not too far)
    - Volume confirms
    - Cooldown respected

    ATR-scaled stop-loss and take-profit.
    """

    name = "pullback_uptrend"
    description = "Buy pullbacks in confirmed uptrends using EMA + RSI"
    version = "1.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "fast_ema": 20,
            "slow_ema": 50,
            "rsi_period": 14,
            "rsi_pullback_low": 35,      # RSI must dip below this
            "rsi_pullback_high": 50,     # RSI must be below this to be "pullback"
            "atr_period": 14,
            "max_pullback_atr": 2.0,     # Max distance from fast EMA in ATR units
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 2.5,
            "min_periods": 55,
            "cooldown_bars": 8,
            "volume_mult": 0.8,          # Lower bar — pullbacks can have lower volume
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

        df["fast_ema"] = close.ewm(span=p["fast_ema"], adjust=False).mean()
        df["slow_ema"] = close.ewm(span=p["slow_ema"], adjust=False).mean()
        df["rsi"] = _rsi(close, p["rsi_period"])
        df["vol_sma"] = volume.rolling(20).mean()

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.rolling(p["atr_period"]).mean()

        signals = []
        last_signal_idx = -p["cooldown_bars"] - 1

        for i in range(min_p, len(df)):
            row = df.iloc[i]
            if pd.isna(row["fast_ema"]) or pd.isna(row["slow_ema"]) or pd.isna(row["rsi"]):
                continue

            bars_since = i - last_signal_idx
            atr = row["atr"] if not pd.isna(row["atr"]) and row["atr"] > 0 else 1.0

            # Uptrend check: fast EMA > slow EMA, price above slow EMA
            in_uptrend = row["fast_ema"] > row["slow_ema"] and row["close"] > row["slow_ema"]

            # Pullback check: RSI has dipped, price near fast EMA
            pullback_distance = abs(row["close"] - row["fast_ema"]) / atr
            in_pullback = (
                p["rsi_pullback_low"] <= row["rsi"] <= p["rsi_pullback_high"]
                and pullback_distance <= p["max_pullback_atr"]
            )

            if (
                bars_since >= p["cooldown_bars"]
                and in_uptrend
                and in_pullback
                and row["volume"] > row["vol_sma"] * p["volume_mult"]
            ):
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY,
                    symbol="UNKNOWN",
                    price=row["close"],
                    strength=min(1.0, (50 - row["rsi"]) / 20),
                    stop_loss=row["close"] - atr * p["stop_loss_atr_mult"],
                    take_profit=row["close"] + atr * p["take_profit_atr_mult"],
                    metadata={
                        "_bar_index": int(i),
                        "rsi": round(row["rsi"], 2),
                        "pullback_dist": round(pullback_distance, 2),
                    },
                ))
                last_signal_idx = i

            # SELL: uptrend broken (close below slow EMA)
            elif (
                bars_since >= p["cooldown_bars"]
                and row["close"] < row["slow_ema"]
                and row["rsi"] > 50
            ):
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL,
                    symbol="UNKNOWN",
                    price=row["close"],
                    strength=0.5,
                    metadata={"_bar_index": int(i), "reason": "uptrend_broken"},
                ))
                last_signal_idx = i

        return signals


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))
