"""EMA Crossover + ATR Filter strategy (v2).

Generates BUY when fast EMA crosses above slow EMA with:
- ATR volatility filter (skip calm markets)
- Trend confirmation via EMA slope
- Cooldown between signals to reduce overtrading
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class EmaAtrStrategy(BaseStrategy):
    """EMA crossover strategy with ATR filter, trend strength, and cooldown.

    v2 improvements:
    - Trend slope confirmation (EMA slope must confirm direction)
    - Cooldown period between signals
    - Tighter risk/reward defaults
    """

    name = "ema_atr_crossover"
    description = "EMA crossover with ATR filter, trend confirmation, and cooldown"
    version = "2.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "fast_ema": 12,
            "slow_ema": 26,
            "atr_period": 14,
            "atr_min_threshold": 0.5,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 2.5,
            "min_periods": 50,
            # v2 filters
            "cooldown_bars": 5,           # Min bars between signals
            "slope_lookback": 5,          # Bars to measure EMA slope
            "min_slope_pct": 0.001,       # Min slope magnitude as fraction
            "trend_ema": 50,              # Longer EMA for trend bias
        }

    def generate_signals(self, candles: pd.DataFrame) -> list[StrategySignal]:
        if not self.validate_candles(candles):
            return []

        df = candles.copy()
        fast = self.params["fast_ema"]
        slow = self.params["slow_ema"]
        atr_period = self.params["atr_period"]
        cooldown = self.params["cooldown_bars"]
        slope_lb = self.params["slope_lookback"]
        min_slope = self.params["min_slope_pct"]
        trend_ema = self.params["trend_ema"]

        # EMAs
        df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
        df["ema_trend"] = df["close"].ewm(span=trend_ema, adjust=False).mean()

        # ATR
        df["tr"] = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(window=atr_period).mean()
        df["atr_pct"] = (df["atr"] / df["close"]) * 100

        # EMA diff for crossover
        df["ema_diff"] = df["ema_fast"] - df["ema_slow"]

        # Slow EMA slope for trend confirmation
        df["slow_ema_slope"] = (df["ema_slow"] - df["ema_slow"].shift(slope_lb)) / df["ema_slow"].shift(slope_lb)

        signals: list[StrategySignal] = []
        atr_threshold = self.params["atr_min_threshold"]
        last_signal_bar = -cooldown  # Allow first signal

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev_diff = df.iloc[i - 1]["ema_diff"]
            curr_diff = row["ema_diff"]
            atr_pct = row["atr_pct"]
            atr_val = row["atr"]
            slope = row["slow_ema_slope"]

            if pd.isna(prev_diff) or pd.isna(curr_diff) or pd.isna(atr_pct) or pd.isna(slope):
                continue

            # ATR filter
            if atr_pct < atr_threshold:
                continue

            # Cooldown filter
            if i - last_signal_bar < cooldown:
                continue

            symbol = row.get("symbol", candles.get("symbol", ["UNKNOWN"] * len(df)).iloc[0] if "symbol" in candles.columns else "UNKNOWN")
            if isinstance(symbol, float) and pd.isna(symbol):
                symbol = "UNKNOWN"

            price = float(row["close"])
            sl_mult = self.params["stop_loss_atr_mult"]
            tp_mult = self.params["take_profit_atr_mult"]

            # Bullish crossover + trend confirmation
            if prev_diff <= 0 and curr_diff > 0 and slope > min_slope:
                # Extra check: price above long trend EMA for bullish bias
                if not pd.isna(row["ema_trend"]) and price < row["ema_trend"]:
                    continue  # Counter-trend, skip
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY,
                    symbol=str(symbol),
                    price=price,
                    strength=min(1.0, atr_pct / 2.0),
                    stop_loss=price - atr_val * sl_mult,
                    take_profit=price + atr_val * tp_mult,
                    metadata={"_bar_index": int(i),
                              "ema_fast": float(row["ema_fast"]), "ema_slow": float(row["ema_slow"]),
                              "atr": float(atr_val), "slope": float(slope)},
                ))
                last_signal_bar = i

            # Bearish crossover + trend confirmation
            elif prev_diff >= 0 and curr_diff < 0 and slope < -min_slope:
                if not pd.isna(row["ema_trend"]) and price > row["ema_trend"]:
                    continue  # Counter-trend, skip
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL,
                    symbol=str(symbol),
                    price=price,
                    strength=min(1.0, atr_pct / 2.0),
                    stop_loss=price + atr_val * sl_mult,
                    take_profit=price - atr_val * tp_mult,
                    metadata={"_bar_index": int(i),
                              "ema_fast": float(row["ema_fast"]), "ema_slow": float(row["ema_slow"]),
                              "atr": float(atr_val), "slope": float(slope)},
                ))
                last_signal_bar = i

        return signals
