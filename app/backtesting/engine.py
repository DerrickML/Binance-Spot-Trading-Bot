"""Backtesting engine — candle-by-candle simulation with fees and slippage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.core.enums import SignalType
from app.core.logging import get_logger
from app.strategies.base import BaseStrategy, StrategySignal
from app.backtesting.grid_diagnostics import summarize_grid_backtest

logger = get_logger(__name__)


@dataclass
class BacktestTrade:
    """Record of a completed backtest trade."""

    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    pnl: float = 0.0
    pnl_pct: float = 0.0
    fees: float = 0.0
    exit_reason: str = "signal"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestPosition:
    """An open position during backtesting."""

    symbol: str
    side: str
    entry_price: float
    quantity: float
    entry_time: datetime
    entry_fee: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Complete backtest result."""

    strategy_name: str
    symbol: str
    interval: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_equity: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    fees_paid: float = 0.0
    slippage_cost: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    """Candle-by-candle backtesting engine.

    Supports fees, slippage, stop-loss, take-profit, and position sizing.
    Produces trade logs, equity curve, and exportable results.
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fee_pct: float = 0.001,
        slippage_pct: float = 0.001,
        max_position_size_pct: float = 0.25,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.max_position_size_pct = max_position_size_pct
        self.default_stop_loss_pct = stop_loss_pct
        self.default_take_profit_pct = take_profit_pct

    @staticmethod
    def _metadata_float(metadata: dict[str, Any], key: str) -> float | None:
        raw = metadata.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _grid_id(signal: StrategySignal) -> str:
        return str(signal.metadata.get("grid_id", ""))

    @classmethod
    def _is_grid_scale_in(cls, signal: StrategySignal) -> bool:
        return (
            signal.signal_type == SignalType.BUY
            and signal.metadata.get("grid_action") == "scale_in"
            and bool(cls._grid_id(signal))
        )

    @staticmethod
    def _execution_reference_price(signal: StrategySignal, close: float) -> float:
        price = float(signal.price or 0.0)
        return price if price > 0 else close

    def _buy_fill(
        self,
        *,
        signal: StrategySignal,
        cash: float,
        account_equity: float,
        close: float,
        position: BacktestPosition | None = None,
    ) -> tuple[float, float, float, float]:
        """Return entry_price, quantity, fee, slippage_cost for a BUY."""
        raw_price = self._execution_reference_price(signal, close)
        entry_price = raw_price * (1 + self.slippage_pct)
        if entry_price <= 0:
            return entry_price, 0.0, 0.0, 0.0

        target_pct = self._metadata_float(signal.metadata, "target_notional_pct")
        if target_pct is not None:
            max_invest = max(0.0, account_equity * target_pct)
            max_allocation = self._metadata_float(signal.metadata, "max_grid_allocation_pct")
            if max_allocation is not None and position is not None:
                current_notional = position.entry_price * position.quantity
                remaining = max(0.0, account_equity * max_allocation - current_notional)
                max_invest = min(max_invest, remaining)
            max_invest = min(max_invest, cash)
        else:
            max_invest = cash * self.max_position_size_pct

        quantity = max_invest / (entry_price * (1 + self.fee_pct)) if max_invest > 0 else 0.0
        fee = entry_price * quantity * self.fee_pct
        slippage_cost = abs(entry_price - raw_price) * quantity
        return entry_price, quantity, fee, slippage_cost

    @staticmethod
    def _merge_grid_metadata(
        position: BacktestPosition,
        signal: StrategySignal,
        fill_price: float,
        quantity: float,
    ) -> None:
        metadata = dict(position.metadata)
        signal_metadata = signal.metadata or {}
        for key in (
            "grid_id",
            "grid_action",
            "projected_grid_notional_pct",
            "max_grid_allocation_pct",
            "anchor_price",
        ):
            if key in signal_metadata:
                metadata[key] = signal_metadata[key]

        raw_level = signal_metadata.get("grid_level")
        if raw_level is not None:
            try:
                level = int(raw_level)
                levels = set(metadata.get("filled_grid_levels", []))
                levels.add(level)
                metadata["filled_grid_levels"] = sorted(levels)
            except (TypeError, ValueError):
                pass

        bar_idx = signal_metadata.get("_bar_index", signal_metadata.get("bar_index"))
        try:
            normalized_bar_idx = int(bar_idx) if bar_idx is not None else None
        except (TypeError, ValueError):
            normalized_bar_idx = None
        if normalized_bar_idx is not None:
            if signal_metadata.get("grid_action") == "open":
                metadata.setdefault("entry_bar_index", normalized_bar_idx)
            elif signal_metadata.get("grid_action") == "scale_in":
                metadata["last_scale_bar_index"] = normalized_bar_idx

        metadata["average_entry"] = position.entry_price
        metadata["quantity"] = position.quantity
        metadata["last_fill_price"] = fill_price
        metadata["last_fill_quantity"] = quantity
        position.metadata = metadata

    @staticmethod
    def _refresh_grid_stops(position: BacktestPosition, strategy_params: dict[str, Any]) -> None:
        """For grid baskets, anchor SL/TP to the weighted average entry."""
        if not position.metadata.get("grid_id"):
            return
        try:
            stop_loss_pct = float(strategy_params.get("stop_loss_pct"))
            take_profit_pct = float(strategy_params.get("take_profit_pct"))
        except (TypeError, ValueError):
            return
        position.stop_loss = position.entry_price * (1 - stop_loss_pct)
        position.take_profit = position.entry_price * (1 + take_profit_pct)

    @staticmethod
    def _trade_metadata(
        position: BacktestPosition,
        *,
        exit_reason: str,
        signal: StrategySignal | None = None,
        candle_idx: int | None = None,
    ) -> dict[str, Any]:
        metadata = dict(position.metadata)
        if signal is not None:
            metadata.update(signal.metadata)
            raw_exit_idx = signal.metadata.get("_bar_index", signal.metadata.get("bar_index"))
            try:
                metadata["exit_bar_index"] = int(raw_exit_idx)
            except (TypeError, ValueError):
                pass
        if candle_idx is not None:
            metadata["exit_bar_index"] = int(candle_idx)

        if "entry_bar_index" not in metadata:
            raw_entry_idx = position.metadata.get("_bar_index", position.metadata.get("bar_index"))
            try:
                metadata["entry_bar_index"] = int(raw_entry_idx)
            except (TypeError, ValueError):
                pass

        if metadata.get("grid_id"):
            if exit_reason == "stop_loss":
                metadata["grid_action"] = "stop_exit"
            elif exit_reason == "take_profit":
                metadata["grid_action"] = "take_profit"
            elif exit_reason == "end_of_data":
                metadata["grid_action"] = "end_of_data"
            elif signal is not None and signal.metadata.get("grid_action"):
                metadata["grid_action"] = signal.metadata["grid_action"]

        metadata["exit_reason"] = exit_reason
        return metadata

    def run(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        symbol: str = "UNKNOWN",
        interval: str = "1h",
    ) -> BacktestResult:
        """Run a full backtest over the given candle data.

        Args:
            strategy: Strategy instance to test.
            candles: DataFrame with OHLCV columns.
            symbol: Trading symbol.
            interval: Candle interval.

        Returns:
            BacktestResult with trades, equity curve, and metrics.
        """
        equity = self.initial_capital
        cash = equity
        position: BacktestPosition | None = None
        trades: list[BacktestTrade] = []
        equity_curve: list[float] = [equity]
        total_fees = 0.0
        total_slippage = 0.0

        # Generate all signals at once for efficiency
        if candles.empty or len(candles) == 0:
            now = datetime.now(timezone.utc)
            return BacktestResult(
                strategy_name=strategy.name,
                symbol=symbol,
                interval=interval,
                start_date=now,
                end_date=now,
                initial_capital=self.initial_capital,
                final_equity=self.initial_capital,
                equity_curve=[self.initial_capital],
                parameters=strategy.params,
            )

        strategy_params = dict(strategy.params)
        all_signals = strategy.generate_signals(candles)
        signal_map = self._build_signal_map(strategy, candles, all_signals, strategy_params)

        start_date = candles.iloc[0].get("open_time", datetime.now(timezone.utc))
        end_date = candles.iloc[-1].get("open_time", datetime.now(timezone.utc))
        if isinstance(start_date, (int, float)):
            start_date = datetime.fromtimestamp(start_date / 1000 if start_date > 1e10 else start_date, tz=timezone.utc)
        if isinstance(end_date, (int, float)):
            end_date = datetime.fromtimestamp(end_date / 1000 if end_date > 1e10 else end_date, tz=timezone.utc)

        for i in range(len(candles)):
            row = candles.iloc[i]
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            candle_time = row.get("open_time", datetime.now(timezone.utc))
            if isinstance(candle_time, (int, float)):
                candle_time = datetime.fromtimestamp(
                    candle_time / 1000 if candle_time > 1e10 else candle_time,
                    tz=timezone.utc,
                )

            # Check stop-loss / take-profit on open position
            if position is not None:
                closed = False
                exit_price = close
                exit_reason = "signal"

                if position.stop_loss and low <= position.stop_loss:
                    exit_price = position.stop_loss
                    exit_reason = "stop_loss"
                    closed = True
                elif position.take_profit and high >= position.take_profit:
                    exit_price = position.take_profit
                    exit_reason = "take_profit"
                    closed = True

                if closed:
                    raw_exit_price = exit_price
                    # Apply slippage on exit
                    exit_price *= (1 - self.slippage_pct)

                    entry_value = position.entry_price * position.quantity
                    exit_value = exit_price * position.quantity
                    exit_fee = exit_value * self.fee_pct
                    pnl = exit_value - exit_fee - entry_value - position.entry_fee
                    total_fees += exit_fee
                    total_slippage += abs(exit_price - raw_exit_price) * position.quantity

                    cash += exit_value - exit_fee
                    pnl_pct = pnl / (entry_value + position.entry_fee) if entry_value > 0 else 0

                    trades.append(BacktestTrade(
                        symbol=symbol,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        entry_time=position.entry_time,
                        exit_time=candle_time,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        fees=position.entry_fee + exit_fee,
                        exit_reason=exit_reason,
                        metadata=self._trade_metadata(
                            position,
                            exit_reason=exit_reason,
                            candle_idx=i,
                        ),
                    ))
                    position = None

            # Process signal for this candle
            signal = signal_map.get(i)
            if signal and position is None and signal.signal_type == SignalType.BUY:
                # Calculate position size and apply slippage/fees.
                entry_price, quantity, fee, slippage_cost = self._buy_fill(
                    signal=signal,
                    cash=cash,
                    account_equity=cash,
                    close=close,
                )
                if quantity <= 0:
                    equity_curve.append(cash)
                    continue
                total_slippage += slippage_cost

                total_fees += fee
                cash -= (entry_price * quantity + fee)

                # Set stops
                sl = signal.stop_loss
                tp = signal.take_profit
                if sl is None and self.default_stop_loss_pct:
                    if signal.signal_type == SignalType.BUY:
                        sl = entry_price * (1 - self.default_stop_loss_pct)
                    else:
                        sl = entry_price * (1 + self.default_stop_loss_pct)
                if tp is None and self.default_take_profit_pct:
                    if signal.signal_type == SignalType.BUY:
                        tp = entry_price * (1 + self.default_take_profit_pct)
                    else:
                        tp = entry_price * (1 - self.default_take_profit_pct)

                position = BacktestPosition(
                    symbol=symbol,
                    side=signal.signal_type.value,
                    entry_price=entry_price,
                    quantity=quantity,
                    entry_time=candle_time,
                    entry_fee=fee,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata=dict(signal.metadata),
                )
                self._merge_grid_metadata(position, signal, entry_price, quantity)
                self._refresh_grid_stops(position, strategy_params)

            elif signal and position is not None:
                # Spot long-only: SELL closes an existing long; BUY while long is ignored.
                if signal.signal_type == SignalType.BUY:
                    if (
                        self._is_grid_scale_in(signal)
                        and self._grid_id(signal) == str(position.metadata.get("grid_id", ""))
                    ):
                        account_equity = cash + position.quantity * close
                        entry_price, quantity, fee, slippage_cost = self._buy_fill(
                            signal=signal,
                            cash=cash,
                            account_equity=account_equity,
                            close=close,
                            position=position,
                        )
                        if quantity > 0:
                            old_value = position.entry_price * position.quantity
                            add_value = entry_price * quantity
                            new_quantity = position.quantity + quantity
                            position.entry_price = (
                                (old_value + add_value) / new_quantity
                                if new_quantity > 0
                                else position.entry_price
                            )
                            position.quantity = new_quantity
                            position.entry_fee += fee
                            position.stop_loss = signal.stop_loss or position.stop_loss
                            position.take_profit = signal.take_profit or position.take_profit
                            cash -= add_value + fee
                            total_fees += fee
                            total_slippage += slippage_cost
                            self._merge_grid_metadata(position, signal, entry_price, quantity)
                            self._refresh_grid_stops(position, strategy_params)

                elif signal.signal_type == SignalType.SELL:
                    raw_exit_price = self._execution_reference_price(signal, close)
                    exit_price = raw_exit_price * (1 - self.slippage_pct)
                    total_slippage += abs(exit_price - raw_exit_price) * position.quantity

                    entry_value = position.entry_price * position.quantity
                    exit_value = exit_price * position.quantity
                    exit_fee = exit_value * self.fee_pct
                    pnl = exit_value - exit_fee - entry_value - position.entry_fee
                    total_fees += exit_fee

                    cash += exit_value - exit_fee
                    pnl_pct = pnl / (entry_value + position.entry_fee) if entry_value > 0 else 0

                    trades.append(BacktestTrade(
                        symbol=symbol,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        entry_time=position.entry_time,
                        exit_time=candle_time,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        fees=position.entry_fee + exit_fee,
                        exit_reason=str(signal.metadata.get("grid_action", "signal")),
                        metadata=self._trade_metadata(
                            position,
                            exit_reason=str(signal.metadata.get("grid_action", "signal")),
                            signal=signal,
                        ),
                    ))
                    position = None

            # Update equity
            if position:
                mark_value = position.quantity * close
                equity = cash + mark_value
            else:
                equity = cash
            equity_curve.append(equity)

        # Close any open position at end
        if position is not None:
            final_close = float(candles.iloc[-1]["close"])
            entry_value = position.entry_price * position.quantity
            exit_value = final_close * position.quantity
            exit_fee = exit_value * self.fee_pct
            pnl = exit_value - exit_fee - entry_value - position.entry_fee
            total_fees += exit_fee
            cash += exit_value - exit_fee
            equity = cash

            trades.append(BacktestTrade(
                symbol=symbol,
                side=position.side,
                entry_price=position.entry_price,
                exit_price=final_close,
                quantity=position.quantity,
                entry_time=position.entry_time,
                exit_time=end_date,
                pnl=pnl,
                pnl_pct=pnl / (entry_value + position.entry_fee) if entry_value > 0 else 0,
                fees=position.entry_fee + exit_fee,
                exit_reason="end_of_data",
                metadata=self._trade_metadata(
                    position,
                    exit_reason="end_of_data",
                    candle_idx=len(candles) - 1,
                ),
            ))

        logger.info(
            "backtest_complete",
            strategy=strategy.name,
            symbol=symbol,
            trades=len(trades),
            final_equity=round(equity, 2),
        )

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_equity=equity,
            trades=trades,
            equity_curve=equity_curve,
            parameters=strategy.params,
            fees_paid=total_fees,
            slippage_cost=total_slippage,
            diagnostics=summarize_grid_backtest(
                trades=trades,
                signals=all_signals,
                initial_capital=self.initial_capital,
                final_equity=equity,
            ),
        )

    def _build_signal_map(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        all_signals: list[StrategySignal],
        strategy_params: dict[str, Any],
    ) -> dict[int, StrategySignal]:
        """Map strategy signals to candle indexes without close-price matching.

        Preferred mapping order:
        1. Internal strategy metadata: ``_bar_index`` or ``bar_index``.
        2. Signal timestamp matching candle open_time or close_time.
        3. A deterministic prefix replay fallback for legacy/test strategies.
        """
        signal_map: dict[int, StrategySignal] = {}
        unresolved: list[StrategySignal] = []
        time_lookup = self._build_time_lookup(candles)

        for sig in all_signals:
            mapped_idx = self._signal_index_from_metadata(sig, len(candles))
            if mapped_idx is None:
                mapped_idx = self._signal_index_from_timestamp(sig, time_lookup)

            if mapped_idx is None:
                unresolved.append(sig)
                continue

            signal_map.setdefault(mapped_idx, sig)

        if unresolved:
            self._map_unresolved_signals_by_prefix(
                strategy, candles, unresolved, signal_map, strategy_params
            )

        return signal_map

    @staticmethod
    def _signal_index_from_metadata(signal: StrategySignal, candles_len: int) -> int | None:
        """Return a valid candle index from signal metadata when available."""
        raw_idx = signal.metadata.get("_bar_index", signal.metadata.get("bar_index"))
        if raw_idx is None:
            return None
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            return None
        if 0 <= idx < candles_len:
            return idx
        return None

    @classmethod
    def _signal_index_from_timestamp(
        cls,
        signal: StrategySignal,
        time_lookup: dict[int, int],
    ) -> int | None:
        """Return a candle index by matching signal timestamp to candle times."""
        key = cls._time_key(signal.timestamp)
        if key is None:
            return None
        return time_lookup.get(key)

    @classmethod
    def _build_time_lookup(cls, candles: pd.DataFrame) -> dict[int, int]:
        """Build lookup of candle open/close timestamps to row index."""
        lookup: dict[int, int] = {}
        for row_idx, (_, row) in enumerate(candles.iterrows()):
            for col in ("open_time", "close_time"):
                if col not in candles.columns:
                    continue
                key = cls._time_key(row.get(col))
                if key is not None:
                    lookup.setdefault(key, row_idx)
        return lookup

    @staticmethod
    def _time_key(value: Any) -> int | None:
        """Normalize supported timestamp shapes to UTC nanoseconds."""
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        try:
            if isinstance(value, (int, float)):
                value = datetime.fromtimestamp(
                    value / 1000 if value > 1e10 else value,
                    tz=timezone.utc,
                )
            timestamp = pd.Timestamp(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            return int(timestamp.value)
        except Exception:
            return None

    def _map_unresolved_signals_by_prefix(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        unresolved: list[StrategySignal],
        signal_map: dict[int, StrategySignal],
        strategy_params: dict[str, Any],
    ) -> None:
        """Fallback-map legacy signals by detecting when signal count increases."""
        unresolved_idx = 0
        previous_count = 0

        for candle_idx in range(len(candles)):
            if unresolved_idx >= len(unresolved):
                return

            try:
                fresh = strategy.__class__(params=dict(strategy_params))
                prefix_signals = fresh.generate_signals(
                    candles.iloc[: candle_idx + 1].reset_index(drop=True)
                )
            except Exception:
                break

            current_count = len(prefix_signals)
            while current_count > previous_count and unresolved_idx < len(unresolved):
                signal_map.setdefault(candle_idx, unresolved[unresolved_idx])
                unresolved_idx += 1
                previous_count += 1

        # Last-resort deterministic behavior for latest-only strategies that
        # return one unindexed signal for the full DataFrame.
        while unresolved_idx < len(unresolved):
            signal_map.setdefault(len(candles) - 1, unresolved[unresolved_idx])
            unresolved_idx += 1
