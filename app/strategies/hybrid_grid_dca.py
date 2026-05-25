"""Hybrid Spot Grid/DCA strategy.

Long-only basket strategy for paper/backtest research. It opens a base basket,
adds deterministic scale-ins below an anchor, and exits the full basket on a
weighted-average take profit or hard stop.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.registry import register_strategy


@register_strategy
class HybridGridDcaStrategy(BaseStrategy):
    """Hybrid long-only grid/DCA strategy for Binance Spot research."""

    name = "hybrid_grid_dca"
    description = "Long-only Spot grid/DCA basket with full-basket exits"
    version = "1.0.0"

    def default_params(self) -> dict[str, Any]:
        return {
            "anchor_period": 80,
            "trend_filter_period": 200,
            "grid_spacing_pct": 0.012,
            "max_grid_levels": 5,
            "base_order_pct": 0.05,
            "dca_size_multiplier": 1.25,
            "take_profit_pct": 0.01,
            "stop_loss_pct": 0.12,
            "max_grid_allocation_pct": 0.35,
            "cooldown_bars": 1,
            "min_volatility_pct": 0.002,
            "atr_period": 14,
            "atr_grid_spacing_mult": 0.75,
            "min_trend_slope_pct": -0.02,
            "trend_slope_lookback": 20,
            "max_anchor_deviation_pct": 0.08,
            "take_profit_fee_buffer_pct": 0.002,
            "stop_cooldown_bars": 5,
            "scale_in_requires_below_average": True,
            "scale_in_requires_level_reclaim": True,
            "entry_momentum_lookback": 12,
            "min_entry_momentum_pct": -0.06,
            "support_lookback": 40,
            "support_buffer_pct": 0.005,
            "max_bearish_streak": 4,
            "volatility_zscore_lookback": 80,
            "max_volatility_zscore": 2.5,
            "require_reversal_confirmation": False,
            "min_periods": 200,
        }

    def validate_candles(self, candles: pd.DataFrame) -> bool:
        if not super().validate_candles(candles):
            return False
        min_periods = max(
            int(self.params.get("min_periods", 200)),
            int(self.params.get("anchor_period", 80)),
            int(self.params.get("trend_filter_period", 200)),
            int(self.params.get("atr_period", 14)),
            int(self.params.get("entry_momentum_lookback", 12)),
            int(self.params.get("support_lookback", 40)),
            int(self.params.get("volatility_zscore_lookback", 80)),
        )
        return len(candles) >= min_periods

    def generate_signals(self, candles: pd.DataFrame) -> list[StrategySignal]:
        if not self.validate_candles(candles):
            return []

        df = candles.copy().reset_index(drop=True)
        anchor_period = int(self.params["anchor_period"])
        trend_period = int(self.params["trend_filter_period"])
        spacing = float(self.params["grid_spacing_pct"])
        max_levels = int(self.params["max_grid_levels"])
        base_pct = float(self.params["base_order_pct"])
        multiplier = float(self.params["dca_size_multiplier"])
        tp_pct = float(self.params["take_profit_pct"])
        sl_pct = float(self.params["stop_loss_pct"])
        max_allocation = float(self.params["max_grid_allocation_pct"])
        cooldown = int(self.params["cooldown_bars"])
        min_vol = float(self.params["min_volatility_pct"])
        atr_period = int(self.params.get("atr_period", 14))
        atr_spacing_mult = float(self.params.get("atr_grid_spacing_mult", 0.0))
        min_trend_slope = float(self.params.get("min_trend_slope_pct", -1.0))
        trend_slope_lookback = int(self.params.get("trend_slope_lookback", 20))
        max_anchor_deviation = float(self.params.get("max_anchor_deviation_pct", 1.0))
        tp_fee_buffer = float(self.params.get("take_profit_fee_buffer_pct", 0.0))
        stop_cooldown = int(self.params.get("stop_cooldown_bars", 0))
        scale_requires_below_average = bool(
            self.params.get("scale_in_requires_below_average", False)
        )
        scale_requires_reclaim = bool(
            self.params.get("scale_in_requires_level_reclaim", True)
        )
        entry_momentum_lookback = int(self.params.get("entry_momentum_lookback", 12))
        min_entry_momentum = float(self.params.get("min_entry_momentum_pct", -1.0))
        support_lookback = int(self.params.get("support_lookback", 0))
        support_buffer = float(self.params.get("support_buffer_pct", 0.0))
        max_bearish_streak = int(self.params.get("max_bearish_streak", 999))
        volatility_zscore_lookback = int(self.params.get("volatility_zscore_lookback", 80))
        max_volatility_zscore = float(self.params.get("max_volatility_zscore", 999.0))
        require_reversal = bool(self.params.get("require_reversal_confirmation", False))

        df["anchor"] = df["close"].rolling(anchor_period).mean()
        df["trend"] = df["close"].rolling(trend_period).mean()
        returns = df["close"].pct_change()
        df["volatility"] = returns.rolling(anchor_period).std().fillna(0.0)
        df["atr"] = self._atr(df, atr_period)
        slope_period = max(1, min(trend_slope_lookback, trend_period))
        df["trend_slope"] = df["trend"].pct_change(slope_period).fillna(0.0)
        df["entry_momentum"] = (
            df["close"].pct_change(max(1, entry_momentum_lookback)).fillna(0.0)
        )
        df["support_low"] = self._support_low(df, support_lookback)
        df["bearish_streak"] = self._bearish_streak(df["close"])
        vol_mean = df["volatility"].rolling(max(2, volatility_zscore_lookback)).mean()
        vol_std = df["volatility"].rolling(max(2, volatility_zscore_lookback)).std()
        safe_vol_std = vol_std.where(vol_std != 0)
        df["volatility_zscore"] = (
            (df["volatility"] - vol_mean) / safe_vol_std
        ).fillna(0.0)

        signals: list[StrategySignal] = []
        in_basket = False
        grid_id = ""
        grid_sequence = 0
        anchor_price = 0.0
        average_entry = 0.0
        total_units = 0.0
        allocated_pct = 0.0
        filled_levels: set[int] = set()
        last_signal_bar = -cooldown
        next_open_bar = 0

        start_idx = max(
            anchor_period,
            trend_period,
            atr_period,
            entry_momentum_lookback,
            support_lookback,
            volatility_zscore_lookback,
        )
        for i in range(start_idx, len(df)):
            row = df.iloc[i]
            close = float(row["close"])
            open_price = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            anchor = float(row["anchor"])
            trend = float(row["trend"])
            volatility = float(row["volatility"])
            atr = float(row["atr"])
            atr_pct = atr / close if close > 0 else 0.0
            trend_slope = float(row["trend_slope"])
            entry_momentum = float(row["entry_momentum"])
            support_low = float(row["support_low"])
            bearish_streak = int(row["bearish_streak"])
            volatility_zscore = float(row["volatility_zscore"])
            effective_spacing = max(spacing, atr_pct * atr_spacing_mult)
            symbol = self._row_symbol(row, df)

            if close <= 0 or anchor <= 0 or trend <= 0 or effective_spacing <= 0:
                continue

            can_signal = i - last_signal_bar >= cooldown

            if in_basket:
                stop_price = average_entry * (1 - sl_pct)
                take_profit_price = average_entry * (1 + tp_pct + tp_fee_buffer)

                if low <= stop_price and can_signal:
                    signals.append(self._signal(
                        signal_type=SignalType.SELL,
                        symbol=symbol,
                        price=stop_price,
                        bar_index=i,
                        grid_action="stop_exit",
                        grid_id=grid_id,
                        grid_level=max(filled_levels) if filled_levels else 0,
                        target_notional_pct=0.0,
                        projected_allocation=allocated_pct,
                        max_allocation=max_allocation,
                        anchor_price=anchor_price,
                        average_entry=average_entry,
                        effective_spacing=effective_spacing,
                        volatility=volatility,
                        atr_pct=atr_pct,
                        trend_slope=trend_slope,
                        entry_momentum=entry_momentum,
                        support_distance=self._support_distance(close, support_low),
                        bearish_streak=bearish_streak,
                        volatility_zscore=volatility_zscore,
                        reversal_confirmed=self._reversal_confirmed(df, i, open_price, close),
                        stop_loss=stop_price,
                        take_profit=take_profit_price,
                    ))
                    in_basket = False
                    next_open_bar = i + stop_cooldown
                    last_signal_bar = i
                    continue

                if high >= take_profit_price and can_signal:
                    signals.append(self._signal(
                        signal_type=SignalType.SELL,
                        symbol=symbol,
                        price=take_profit_price,
                        bar_index=i,
                        grid_action="take_profit",
                        grid_id=grid_id,
                        grid_level=max(filled_levels) if filled_levels else 0,
                        target_notional_pct=0.0,
                        projected_allocation=allocated_pct,
                        max_allocation=max_allocation,
                        anchor_price=anchor_price,
                        average_entry=average_entry,
                        effective_spacing=effective_spacing,
                        volatility=volatility,
                        atr_pct=atr_pct,
                        trend_slope=trend_slope,
                        entry_momentum=entry_momentum,
                        support_distance=self._support_distance(close, support_low),
                        bearish_streak=bearish_streak,
                        volatility_zscore=volatility_zscore,
                        reversal_confirmed=self._reversal_confirmed(df, i, open_price, close),
                        stop_loss=stop_price,
                        take_profit=take_profit_price,
                    ))
                    in_basket = False
                    last_signal_bar = i
                    continue

                if can_signal and volatility >= min_vol:
                    if scale_requires_below_average and close >= average_entry:
                        continue
                    next_level = self._next_unfilled_level(
                        low=low,
                        anchor_price=anchor_price,
                        spacing=effective_spacing,
                        max_levels=max_levels,
                        filled_levels=filled_levels,
                    )
                    if next_level is not None:
                        level_price = anchor_price * (1 - effective_spacing * next_level)
                        if scale_requires_reclaim and close < level_price:
                            continue
                        if entry_momentum < min_entry_momentum:
                            continue
                        if bearish_streak > max_bearish_streak:
                            continue
                        if volatility_zscore > max_volatility_zscore:
                            continue
                        order_pct = min(
                            base_pct * (multiplier ** next_level),
                            max(0.0, max_allocation - allocated_pct),
                        )
                        projected = allocated_pct + order_pct
                        if order_pct > 0 and projected <= max_allocation + 1e-12:
                            fill_price = min(close, level_price)
                            signals.append(self._signal(
                                signal_type=SignalType.BUY,
                                symbol=symbol,
                                price=fill_price,
                                bar_index=i,
                                grid_action="scale_in",
                                grid_id=grid_id,
                                grid_level=next_level,
                                target_notional_pct=order_pct,
                                projected_allocation=projected,
                                max_allocation=max_allocation,
                                anchor_price=anchor_price,
                                average_entry=average_entry,
                                effective_spacing=effective_spacing,
                                volatility=volatility,
                                atr_pct=atr_pct,
                                trend_slope=trend_slope,
                                entry_momentum=entry_momentum,
                                support_distance=self._support_distance(close, support_low),
                                bearish_streak=bearish_streak,
                                volatility_zscore=volatility_zscore,
                                reversal_confirmed=self._reversal_confirmed(df, i, open_price, close),
                                stop_loss=stop_price,
                                take_profit=take_profit_price,
                            ))
                            filled_levels.add(next_level)
                            total_units += order_pct / max(fill_price, 1e-12)
                            allocated_pct = projected
                            average_entry = allocated_pct / total_units
                            last_signal_bar = i
                continue

            if i < next_open_bar or not can_signal or volatility < min_vol:
                continue

            if trend_slope < min_trend_slope:
                continue

            if close < anchor * (1 - max_anchor_deviation):
                continue

            support_distance = self._support_distance(close, support_low)
            if entry_momentum < min_entry_momentum:
                continue
            if support_low > 0 and support_distance <= support_buffer:
                continue
            if bearish_streak > max_bearish_streak:
                continue
            if volatility_zscore > max_volatility_zscore:
                continue
            reversal_confirmed = self._reversal_confirmed(df, i, open_price, close)
            if require_reversal and not reversal_confirmed:
                continue

            trend_floor = trend * (1 - sl_pct)
            if close <= anchor and close >= trend_floor:
                grid_sequence += 1
                grid_id = f"{self.name}-{grid_sequence}"
                anchor_price = anchor
                allocated_pct = min(base_pct, max_allocation)
                total_units = allocated_pct / close
                average_entry = close
                filled_levels = {0}
                stop_price = average_entry * (1 - sl_pct)
                take_profit_price = average_entry * (1 + tp_pct + tp_fee_buffer)
                signals.append(self._signal(
                    signal_type=SignalType.BUY,
                    symbol=symbol,
                    price=close,
                    bar_index=i,
                    grid_action="open",
                    grid_id=grid_id,
                    grid_level=0,
                    target_notional_pct=allocated_pct,
                    projected_allocation=allocated_pct,
                    max_allocation=max_allocation,
                    anchor_price=anchor_price,
                    average_entry=average_entry,
                    effective_spacing=effective_spacing,
                    volatility=volatility,
                    atr_pct=atr_pct,
                    trend_slope=trend_slope,
                    entry_momentum=entry_momentum,
                    support_distance=support_distance,
                    bearish_streak=bearish_streak,
                    volatility_zscore=volatility_zscore,
                    reversal_confirmed=reversal_confirmed,
                    stop_loss=stop_price,
                    take_profit=take_profit_price,
                ))
                in_basket = True
                last_signal_bar = i

        return signals

    @staticmethod
    def _row_symbol(row: pd.Series, df: pd.DataFrame) -> str:
        symbol = row.get("symbol", "UNKNOWN")
        if isinstance(symbol, float) and pd.isna(symbol):
            symbol = "UNKNOWN"
        if symbol == "UNKNOWN" and "symbol" in df.columns and not df["symbol"].empty:
            first_symbol = df["symbol"].iloc[0]
            if not pd.isna(first_symbol):
                symbol = first_symbol
        return str(symbol)

    @staticmethod
    def _next_unfilled_level(
        low: float,
        anchor_price: float,
        spacing: float,
        max_levels: int,
        filled_levels: set[int],
    ) -> int | None:
        for level in range(1, max_levels + 1):
            if level in filled_levels:
                continue
            level_price = anchor_price * (1 - spacing * level)
            if low <= level_price:
                return level
        return None

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        previous_close = df["close"].shift(1)
        true_range = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - previous_close).abs(),
                (df["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.rolling(period).mean().fillna(0.0)

    @staticmethod
    def _support_low(df: pd.DataFrame, lookback: int) -> pd.Series:
        if lookback <= 0:
            return pd.Series(0.0, index=df.index)
        return df["low"].shift(1).rolling(lookback).min().fillna(0.0)

    @staticmethod
    def _support_distance(close: float, support_low: float) -> float:
        if support_low <= 0:
            return 1.0
        return (close / support_low) - 1

    @staticmethod
    def _bearish_streak(close: pd.Series) -> pd.Series:
        streak: list[int] = []
        current = 0
        previous = None
        for value in close:
            if previous is not None and float(value) < float(previous):
                current += 1
            else:
                current = 0
            streak.append(current)
            previous = value
        return pd.Series(streak, index=close.index)

    @staticmethod
    def _reversal_confirmed(
        df: pd.DataFrame,
        idx: int,
        open_price: float,
        close: float,
    ) -> bool:
        if idx <= 0:
            return close > open_price
        previous_close = float(df.iloc[idx - 1]["close"])
        return close > open_price and close > previous_close

    @staticmethod
    def _signal(
        *,
        signal_type: SignalType,
        symbol: str,
        price: float,
        bar_index: int,
        grid_action: str,
        grid_id: str,
        grid_level: int,
        target_notional_pct: float,
        projected_allocation: float,
        max_allocation: float,
        anchor_price: float,
        average_entry: float,
        effective_spacing: float,
        volatility: float,
        atr_pct: float,
        trend_slope: float,
        entry_momentum: float,
        support_distance: float,
        bearish_streak: int,
        volatility_zscore: float,
        reversal_confirmed: bool,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> StrategySignal:
        return StrategySignal(
            signal_type=signal_type,
            symbol=symbol,
            price=float(price),
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "_bar_index": int(bar_index),
                "grid_action": grid_action,
                "grid_id": grid_id,
                "grid_level": int(grid_level),
                "target_notional_pct": float(target_notional_pct),
                "projected_grid_notional_pct": float(projected_allocation),
                "max_grid_allocation_pct": float(max_allocation),
                "anchor_price": float(anchor_price),
                "average_entry": float(average_entry),
                "effective_grid_spacing_pct": float(effective_spacing),
                "volatility_pct": float(volatility),
                "atr_pct": float(atr_pct),
                "trend_slope_pct": float(trend_slope),
                "entry_momentum_pct": float(entry_momentum),
                "support_distance_pct": float(support_distance),
                "bearish_streak": int(bearish_streak),
                "volatility_zscore": float(volatility_zscore),
                "reversal_confirmed": bool(reversal_confirmed),
            },
        )
