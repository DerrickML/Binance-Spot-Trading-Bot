"""Momentum Continuation strategy — spot long-only.

Buys when price shows strong momentum (close above SMA + rising ADX + positive ROC).
Sells on momentum exhaustion or stop-loss.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class MomentumContinuationStrategy(BaseStrategy):
    """Momentum continuation: ride strong upward trends.

    Spot-only, long-only. Buys when:
    - Close > SMA (price above moving average)
    - ADX > threshold (trend is strong)
    - ROC > 0 (positive rate of change)
    - Volume > average (confirms strength)
    - Cooldown respected

    ATR-scaled stop-loss and take-profit.
    """

    name = "momentum_continuation"
    description = "Momentum continuation with ADX + ROC + volume filters"
    version = "1.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "sma_period": 20,
            "adx_period": 14,
            "adx_threshold": 25,
            "roc_period": 10,
            "atr_period": 14,
            "volume_mult": 1.2,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 3.0,
            "min_periods": 50,
            "cooldown_bars": 5,
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

        # Indicators
        df["sma"] = close.rolling(p["sma_period"]).mean()
        df["roc"] = close.pct_change(p["roc_period"])
        df["atr"] = _atr(high, low, close, p["atr_period"])
        df["vol_sma"] = volume.rolling(p["sma_period"]).mean()

        # ADX
        df["adx"] = _adx(high, low, close, p["adx_period"])

        signals = []
        last_signal_idx = -p["cooldown_bars"] - 1

        for i in range(min_p, len(df)):
            row = df.iloc[i]
            if pd.isna(row["sma"]) or pd.isna(row["adx"]) or pd.isna(row["atr"]):
                continue

            bars_since_signal = i - last_signal_idx

            # BUY: price above SMA + strong ADX + positive ROC + volume confirmation
            if (
                bars_since_signal >= p["cooldown_bars"]
                and row["close"] > row["sma"]
                and row["adx"] > p["adx_threshold"]
                and row["roc"] > 0
                and row["volume"] > row["vol_sma"] * p["volume_mult"]
                and row["atr"] > 0
            ):
                atr = row["atr"]
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY,
                    symbol=df.iloc[i].get("symbol", "UNKNOWN") if hasattr(df.iloc[i], "get") else "UNKNOWN",
                    price=row["close"],
                    strength=min(1.0, row["adx"] / 50),
                    stop_loss=row["close"] - atr * p["stop_loss_atr_mult"],
                    take_profit=row["close"] + atr * p["take_profit_atr_mult"],
                    metadata={"_bar_index": int(i),
                              "adx": round(row["adx"], 2), "roc": round(row["roc"], 4)},
                ))
                last_signal_idx = i

            # SELL: momentum fading (close < SMA and ROC negative)
            elif (
                bars_since_signal >= p["cooldown_bars"]
                and row["close"] < row["sma"]
                and row["roc"] < 0
            ):
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL,
                    symbol="UNKNOWN",
                    price=row["close"],
                    strength=0.5,
                    metadata={"_bar_index": int(i), "reason": "momentum_exhaustion"},
                ))
                last_signal_idx = i

        return signals


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average True Range."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average Directional Index."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = tr.rolling(period).mean()
    plus_di = pd.Series(plus_dm, index=high.index).rolling(period).mean() / atr * 100
    minus_di = pd.Series(minus_dm, index=high.index).rolling(period).mean() / atr * 100

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1) * 100
    return dx.rolling(period).mean()
