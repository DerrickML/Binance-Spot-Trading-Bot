"""Regime-aware strategy (v2).

Detects market regime (trending vs. ranging) and switches between
trend-following and mean-reversion logic with:
- Stronger ADX discrimination
- Cooldown between signals
- ATR-scaled stops
- Volume confirmation for both modes
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class RegimeStrategy(BaseStrategy):
    """Regime-aware strategy with stronger regime discrimination and cooldown.

    v2 improvements:
    - Regime hysteresis (require ADX to clearly exceed threshold)
    - Cooldown between signals
    - Volume confirmation
    - RSI extremity requirement for mean reversion signals
    """

    name = "regime_adaptive"
    description = "Regime-aware with hysteresis, volume, and cooldown"
    version = "2.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "adx_period": 14,
            "adx_trend_threshold": 25,
            "adx_range_threshold": 20,   # v2: hysteresis — must drop below 20 for ranging
            "fast_ema": 10,
            "slow_ema": 30,
            "rsi_period": 14,
            "rsi_oversold": 28,          # v2: tighter (was 30)
            "rsi_overbought": 72,        # v2: tighter (was 70)
            "atr_period": 14,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 2.5,
            "min_periods": 60,
            # v2 filters
            "cooldown_bars": 6,
            "volume_confirm_mult": 0.8,
        }

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
        adx_period = self.params["adx_period"]
        trend_thresh = self.params["adx_trend_threshold"]
        range_thresh = self.params["adx_range_threshold"]
        cooldown = self.params["cooldown_bars"]
        vol_mult = self.params["volume_confirm_mult"]

        # Indicators
        df["adx"] = self._calc_adx(df, adx_period)
        df["ema_fast"] = df["close"].ewm(span=self.params["fast_ema"], adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.params["slow_ema"], adjust=False).mean()
        df["ema_diff"] = df["ema_fast"] - df["ema_slow"]
        df["rsi"] = self._calc_rsi(df["close"], self.params["rsi_period"])
        df["rsi_prev"] = df["rsi"].shift(1)
        df["avg_volume"] = df["volume"].rolling(window=20).mean()

        # ATR
        df["tr"] = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(window=self.params["atr_period"]).mean()

        signals: list[StrategySignal] = []
        last_signal_bar = -cooldown
        current_regime = "unknown"  # Hysteresis state

        for i in range(1, len(df)):
            row = df.iloc[i]
            adx = row["adx"]
            if pd.isna(adx) or pd.isna(row["atr"]):
                continue

            # Regime hysteresis
            if adx > trend_thresh:
                current_regime = "trending"
            elif adx < range_thresh:
                current_regime = "ranging"
            # else: keep current regime (hysteresis band)

            # Cooldown
            if i - last_signal_bar < cooldown:
                continue

            # Volume filter
            avg_vol = row["avg_volume"]
            if not pd.isna(avg_vol) and row["volume"] < avg_vol * vol_mult:
                continue

            price = float(row["close"])
            atr_val = float(row["atr"])
            sl_mult = self.params["stop_loss_atr_mult"]
            tp_mult = self.params["take_profit_atr_mult"]

            symbol = row.get("symbol", "UNKNOWN") if "symbol" in df.columns else "UNKNOWN"
            if isinstance(symbol, float) and pd.isna(symbol):
                symbol = "UNKNOWN"

            if current_regime == "trending":
                ema_diff = row["ema_diff"]
                ema_diff_prev = df.iloc[i - 1]["ema_diff"] if i > 0 else 0
                if pd.isna(ema_diff) or pd.isna(ema_diff_prev):
                    continue

                if ema_diff_prev <= 0 and ema_diff > 0:
                    signals.append(StrategySignal(
                        signal_type=SignalType.BUY, symbol=str(symbol), price=price,
                        strength=min(1.0, adx / 50.0),
                        stop_loss=price - atr_val * sl_mult,
                        take_profit=price + atr_val * tp_mult,
                        metadata={"_bar_index": int(i), "regime": "trending", "adx": float(adx)},
                    ))
                    last_signal_bar = i
                elif ema_diff_prev >= 0 and ema_diff < 0:
                    signals.append(StrategySignal(
                        signal_type=SignalType.SELL, symbol=str(symbol), price=price,
                        strength=min(1.0, adx / 50.0),
                        stop_loss=price + atr_val * sl_mult,
                        take_profit=price - atr_val * tp_mult,
                        metadata={"_bar_index": int(i), "regime": "trending", "adx": float(adx)},
                    ))
                    last_signal_bar = i

            elif current_regime == "ranging":
                rsi = row["rsi"]
                rsi_prev = row["rsi_prev"]
                if pd.isna(rsi) or pd.isna(rsi_prev):
                    continue

                oversold = self.params["rsi_oversold"]
                overbought = self.params["rsi_overbought"]

                if rsi_prev <= oversold and rsi > oversold:
                    signals.append(StrategySignal(
                        signal_type=SignalType.BUY, symbol=str(symbol), price=price,
                        strength=0.7,
                        stop_loss=price - atr_val * sl_mult,
                        take_profit=price + atr_val * tp_mult,
                        metadata={"_bar_index": int(i), "regime": "ranging",
                                  "adx": float(adx), "rsi": float(rsi)},
                    ))
                    last_signal_bar = i
                elif rsi_prev >= overbought and rsi < overbought:
                    signals.append(StrategySignal(
                        signal_type=SignalType.SELL, symbol=str(symbol), price=price,
                        strength=0.7,
                        stop_loss=price + atr_val * sl_mult,
                        take_profit=price - atr_val * tp_mult,
                        metadata={"_bar_index": int(i), "regime": "ranging",
                                  "adx": float(adx), "rsi": float(rsi)},
                    ))
                    last_signal_bar = i

        return signals
