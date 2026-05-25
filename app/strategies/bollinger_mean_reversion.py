"""Bollinger Band Mean Reversion strategy (v2).

Generates BUY/SELL at Bollinger Band extremes with:
- Minimum bandwidth quality filter (skip squeezes)
- RSI confirmation (avoid catching knives)
- Cooldown between signals
- Improved SL/TP using ATR
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class BollingerMeanReversionStrategy(BaseStrategy):
    """Bollinger Band mean reversion with RSI confirmation and cooldown.

    v2 improvements:
    - RSI confirmation (oversold at lower band, overbought at upper)
    - ATR-based SL/TP instead of fixed percentage
    - Bandwidth quality filter
    - Cooldown between signals
    """

    name = "bollinger_mean_reversion"
    description = "Bollinger Band mean reversion with RSI, ATR stops, and cooldown"
    version = "2.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "bb_period": 20,
            "bb_std_dev": 2.0,
            "min_band_width_pct": 0.02,
            "min_periods": 50,
            # v2 params
            "atr_period": 14,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 2.0,
            "cooldown_bars": 6,
            "rsi_period": 14,
            "rsi_oversold_confirm": 40,     # RSI must be below this for buys
            "rsi_overbought_confirm": 60,   # RSI must be above this for sells
        }

    def _calc_rsi(self, series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    def generate_signals(self, candles: pd.DataFrame) -> list[StrategySignal]:
        if not self.validate_candles(candles):
            return []

        df = candles.copy()
        period = self.params["bb_period"]
        std_dev = self.params["bb_std_dev"]
        atr_period = self.params["atr_period"]
        cooldown = self.params["cooldown_bars"]

        # Bollinger Bands
        df["bb_mid"] = df["close"].rolling(window=period).mean()
        df["bb_std"] = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_mid"] + std_dev * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - std_dev * df["bb_std"]
        df["bb_width_pct"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

        # ATR
        df["tr"] = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(window=atr_period).mean()

        # RSI
        df["rsi"] = self._calc_rsi(df["close"], self.params["rsi_period"])

        # Previous close
        df["prev_close"] = df["close"].shift(1)

        signals: list[StrategySignal] = []
        min_width = self.params["min_band_width_pct"]
        last_signal_bar = -cooldown

        for i in range(1, len(df)):
            row = df.iloc[i]
            if pd.isna(row["bb_mid"]) or pd.isna(row["bb_width_pct"]) or pd.isna(row["atr"]):
                continue

            # Bandwidth quality
            if row["bb_width_pct"] < min_width:
                continue

            # Cooldown
            if i - last_signal_bar < cooldown:
                continue

            price = float(row["close"])
            prev = float(row["prev_close"]) if not pd.isna(row["prev_close"]) else price
            lower = float(row["bb_lower"])
            upper = float(row["bb_upper"])
            mid = float(row["bb_mid"])
            atr_val = float(row["atr"])
            rsi = row["rsi"]

            sl_mult = self.params["stop_loss_atr_mult"]
            tp_mult = self.params["take_profit_atr_mult"]

            symbol = row.get("symbol", "UNKNOWN") if "symbol" in df.columns else "UNKNOWN"
            if isinstance(symbol, float) and pd.isna(symbol):
                symbol = "UNKNOWN"

            # Buy at lower band with RSI confirmation
            if prev <= lower and price > lower:
                if not pd.isna(rsi) and rsi > self.params["rsi_oversold_confirm"]:
                    continue  # RSI not confirming oversold

                dist_from_mid = (mid - price) / mid
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY, symbol=str(symbol), price=price,
                    strength=min(1.0, max(0.1, dist_from_mid * 10)),
                    stop_loss=price - atr_val * sl_mult,
                    take_profit=min(mid, price + atr_val * tp_mult),
                    metadata={"_bar_index": int(i),
                              "bb_lower": lower, "bb_mid": mid, "bb_upper": upper,
                              "rsi": float(rsi) if not pd.isna(rsi) else 0},
                ))
                last_signal_bar = i

            # Sell at upper band with RSI confirmation
            elif prev >= upper and price < upper:
                if not pd.isna(rsi) and rsi < self.params["rsi_overbought_confirm"]:
                    continue

                dist_from_mid = (price - mid) / mid
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL, symbol=str(symbol), price=price,
                    strength=min(1.0, max(0.1, dist_from_mid * 10)),
                    stop_loss=price + atr_val * sl_mult,
                    take_profit=max(mid, price - atr_val * tp_mult),
                    metadata={"_bar_index": int(i),
                              "bb_lower": lower, "bb_mid": mid, "bb_upper": upper,
                              "rsi": float(rsi) if not pd.isna(rsi) else 0},
                ))
                last_signal_bar = i

        return signals
