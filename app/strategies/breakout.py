"""Breakout strategy (v2).

Generates BUY/SELL on breakouts with:
- Volume confirmation (stronger threshold)
- Cooldown to avoid repeated breakout signals
- Volatility filter (skip quiet markets)
- Minimum breakout distance (avoid noise breakouts)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class BreakoutStrategy(BaseStrategy):
    """Breakout strategy with volume, distance, and cooldown filters.

    v2 improvements:
    - Minimum breakout distance (ATR-based)
    - Cooldown between signals
    - Volatility quality filter
    """

    name = "breakout"
    description = "Breakout with volume, distance, volatility, and cooldown filters"
    version = "2.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "lookback_period": 20,
            "volume_mult": 1.5,
            "atr_period": 14,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 2.5,
            "min_periods": 50,
            # v2 filters
            "cooldown_bars": 8,
            "min_breakout_atr": 0.3,      # Breakout must exceed S/R by this many ATRs
            "min_atr_pct": 0.3,            # Min ATR as % of price (skip dead markets)
        }

    def generate_signals(self, candles: pd.DataFrame) -> list[StrategySignal]:
        if not self.validate_candles(candles):
            return []

        df = candles.copy()
        lookback = self.params["lookback_period"]
        vol_mult = self.params["volume_mult"]
        atr_period = self.params["atr_period"]
        cooldown = self.params["cooldown_bars"]
        min_breakout_atr = self.params["min_breakout_atr"]
        min_atr_pct = self.params["min_atr_pct"]

        # Resistance/support
        df["resistance"] = df["high"].shift(1).rolling(window=lookback).max()
        df["support"] = df["low"].shift(1).rolling(window=lookback).min()

        # Volume
        df["avg_volume"] = df["volume"].rolling(window=lookback).mean()

        # ATR
        df["tr"] = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(window=atr_period).mean()
        df["atr_pct"] = (df["atr"] / df["close"]) * 100

        signals: list[StrategySignal] = []
        last_signal_bar = -cooldown

        for i in range(lookback + 1, len(df)):
            row = df.iloc[i]
            if pd.isna(row["resistance"]) or pd.isna(row["atr"]):
                continue

            # Cooldown
            if i - last_signal_bar < cooldown:
                continue

            # Volatility filter
            if row["atr_pct"] < min_atr_pct:
                continue

            price = float(row["close"])
            volume = float(row["volume"])
            avg_vol = float(row["avg_volume"])
            resistance = float(row["resistance"])
            support = float(row["support"])
            atr_val = float(row["atr"])

            # Volume confirmation
            if volume <= avg_vol * vol_mult:
                continue

            sl_mult = self.params["stop_loss_atr_mult"]
            tp_mult = self.params["take_profit_atr_mult"]

            symbol = row.get("symbol", "UNKNOWN") if "symbol" in df.columns else "UNKNOWN"
            if isinstance(symbol, float) and pd.isna(symbol):
                symbol = "UNKNOWN"

            # Bullish breakout with minimum distance
            if price > resistance and (price - resistance) > atr_val * min_breakout_atr:
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY, symbol=str(symbol), price=price,
                    strength=min(1.0, volume / (avg_vol * vol_mult * 2)),
                    stop_loss=price - atr_val * sl_mult,
                    take_profit=price + atr_val * tp_mult,
                    metadata={"_bar_index": int(i), "resistance": resistance,
                              "volume_ratio": volume / avg_vol, "atr": atr_val},
                ))
                last_signal_bar = i

            # Bearish breakdown with minimum distance
            elif price < support and (support - price) > atr_val * min_breakout_atr:
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL, symbol=str(symbol), price=price,
                    strength=min(1.0, volume / (avg_vol * vol_mult * 2)),
                    stop_loss=price + atr_val * sl_mult,
                    take_profit=price - atr_val * tp_mult,
                    metadata={"_bar_index": int(i), "support": support,
                              "volume_ratio": volume / avg_vol, "atr": atr_val},
                ))
                last_signal_bar = i

        return signals
