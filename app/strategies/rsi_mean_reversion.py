"""RSI Mean Reversion strategy (v2).

Generates BUY when RSI is oversold, with:
- Volume confirmation (avoid fading in thin markets)
- Cooldown between signals
- Trend context — only take mean reversion when not in strong trend
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class RsiMeanReversionStrategy(BaseStrategy):
    """RSI-based mean reversion strategy with volume and trend filters.

    v2 improvements:
    - Volume confirmation (above-average volume at signal)
    - Cooldown between signals
    - Trend filter: skip signals when ADX says market is strongly trending
    """

    name = "rsi_mean_reversion"
    description = "RSI mean reversion with volume, trend, and cooldown filters"
    version = "2.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "rsi_period": 14,
            "oversold": 30,
            "overbought": 70,
            "stop_loss_pct": 0.025,
            "take_profit_pct": 0.035,
            "min_periods": 50,
            # v2 filters
            "cooldown_bars": 8,
            "volume_confirm_mult": 0.8,   # Volume must be at least 0.8x average
            "adx_max_for_mr": 30,         # Don't mean-revert in strong trends
            "adx_period": 14,
        }

    def _calc_rsi(self, series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    def _calc_adx(self, df: pd.DataFrame, period: int) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan")) * 100
        return dx.rolling(window=period).mean()

    def generate_signals(self, candles: pd.DataFrame) -> list[StrategySignal]:
        if not self.validate_candles(candles):
            return []

        df = candles.copy()
        period = self.params["rsi_period"]
        oversold = self.params["oversold"]
        overbought = self.params["overbought"]
        cooldown = self.params["cooldown_bars"]
        vol_mult = self.params["volume_confirm_mult"]
        adx_max = self.params["adx_max_for_mr"]

        df["rsi"] = self._calc_rsi(df["close"], period)
        df["rsi_prev"] = df["rsi"].shift(1)
        df["avg_volume"] = df["volume"].rolling(window=20).mean()
        df["adx"] = self._calc_adx(df, self.params["adx_period"])

        signals: list[StrategySignal] = []
        last_signal_bar = -cooldown

        for i in range(1, len(df)):
            row = df.iloc[i]
            rsi = row["rsi"]
            rsi_prev = row["rsi_prev"]

            if pd.isna(rsi) or pd.isna(rsi_prev):
                continue

            # Cooldown
            if i - last_signal_bar < cooldown:
                continue

            # Volume filter
            avg_vol = row["avg_volume"]
            if not pd.isna(avg_vol) and row["volume"] < avg_vol * vol_mult:
                continue

            # ADX filter — skip strong trends for mean reversion
            adx = row["adx"]
            if not pd.isna(adx) and adx > adx_max:
                continue

            price = float(row["close"])
            sl_pct = self.params["stop_loss_pct"]
            tp_pct = self.params["take_profit_pct"]

            symbol = row.get("symbol", "UNKNOWN") if "symbol" in df.columns else "UNKNOWN"
            if isinstance(symbol, float) and pd.isna(symbol):
                symbol = "UNKNOWN"

            # Oversold reversal
            if rsi_prev <= oversold and rsi > oversold:
                strength = min(1.0, (oversold - rsi_prev) / oversold)
                signals.append(StrategySignal(
                    signal_type=SignalType.BUY, symbol=str(symbol), price=price,
                    strength=max(0.1, strength),
                    stop_loss=price * (1 - sl_pct), take_profit=price * (1 + tp_pct),
                    metadata={"_bar_index": int(i),
                              "rsi": float(rsi), "rsi_prev": float(rsi_prev),
                              "adx": float(adx) if not pd.isna(adx) else 0},
                ))
                last_signal_bar = i

            # Overbought reversal
            elif rsi_prev >= overbought and rsi < overbought:
                strength = min(1.0, (rsi_prev - overbought) / (100 - overbought))
                signals.append(StrategySignal(
                    signal_type=SignalType.SELL, symbol=str(symbol), price=price,
                    strength=max(0.1, strength),
                    stop_loss=price * (1 + sl_pct), take_profit=price * (1 - tp_pct),
                    metadata={"_bar_index": int(i),
                              "rsi": float(rsi), "rsi_prev": float(rsi_prev),
                              "adx": float(adx) if not pd.isna(adx) else 0},
                ))
                last_signal_bar = i

        return signals
