"""Orchestrator — end-to-end paper trading pipeline.

Wires: data → candle buffer → strategy → risk engine → broker → persistence → notifications.
Processes CLOSED candles only. Skips duplicates. Respects kill switch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.core.enums import OrderSide, OrderType, SignalType, TradingMode
from app.core.logging import get_logger
from app.execution.base_broker import OrderRequest, OrderResult
from app.execution.paper_broker import PaperBroker
from app.notifications.telegram_notifier import TelegramNotifier
from app.persistence.db import get_session
from app.persistence.models import (
    AccountSnapshot,
    GridEvent,
    GridState,
    Incident,
    Signal as SignalModel,
    Trade,
)
from app.persistence.repositories import (
    AccountSnapshotRepository,
    GridEventRepository,
    GridStateRepository,
    IncidentRepository,
    SignalRepository,
    TradeRepository,
)
from app.risk.risk_engine import RiskEngine
from app.strategies.base import BaseStrategy, StrategySignal

logger = get_logger(__name__)

# Minimum candles required before strategy evaluation
MIN_CANDLE_BUFFER = 50


def _coerce_utc_datetime(value: Any) -> datetime:
    """Normalize candle/signal timestamps for replay and live runtime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # Binance timestamps are milliseconds; tests also use millisecond offsets.
        dt = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = datetime.now(timezone.utc)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Orchestrator:
    """Main orchestrator that coordinates the paper trading pipeline.

    Pipeline per closed candle:
        1. Dedup check
        2. Buffer candle → build DataFrame
        3. Check SL/TP on open positions
        4. Strategy generate_signals (latest only)
        5. Risk engine approve/reject → persist signal
        6. If approved → broker execute → persist trade → telegram notify
        7. Snapshot equity
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_engine: RiskEngine,
        broker: PaperBroker,
        telegram: TelegramNotifier | None = None,
        mode: TradingMode = TradingMode.PAPER,
        symbols: list[str] | None = None,
        interval: str = "1h",
        database_url: str | None = None,
        regime_config: Any | None = None,
        persist_runtime: bool = True,
    ) -> None:
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.broker = broker
        self.telegram = telegram
        self.mode = mode
        self.symbols = symbols or []
        self.interval = str(interval)
        self.database_url = database_url
        self.regime_config = regime_config  # Optional — None = no regime gating
        self._persistence_enabled = persist_runtime
        self._notifications_enabled = True

        self._running = False
        self._ws_client = None

        # Per-symbol candle buffers: symbol → list of candle dicts
        self._candle_buffers: dict[str, list[dict[str, Any]]] = {s: [] for s in self.symbols}
        # Dedup: set of (symbol, open_time) already processed
        self._processed_candles: set[tuple[str, Any]] = set()
        # Open positions tracked by orchestrator: symbol → position info
        self._open_positions: dict[str, dict[str, Any]] = {}
        # Latest mark prices used for paper equity/status snapshots.
        self._last_close_prices: dict[str, float] = {}

    async def start(self) -> None:
        """Start the paper trading loop with WebSocket subscription."""
        self._running = True

        logger.info(
            "orchestrator_started",
            mode=self.mode.value,
            strategy=self.strategy.name,
            symbols=self.symbols,
        )

        if self._can_notify():
            await self.telegram.notify_startup(self.mode.value, self.symbols)

        # Subscribe to WebSocket
        from app.data.websocket_client import BinanceWebSocketClient

        self._ws_client = BinanceWebSocketClient()
        try:
            await self._ws_client.subscribe_klines(
                symbols=self.symbols,
                interval=self.interval,
                callback=self._on_candle,
            )
        except Exception as e:
            logger.error("orchestrator_ws_error", error=str(e))
            await self._record_incident("ERROR", "orchestrator", f"WebSocket error: {e}")
            self.risk_engine.record_error()

    async def stop(self) -> None:
        """Stop the orchestrator cleanly."""
        self._running = False
        if self._ws_client:
            await self._ws_client.stop()

        logger.info(
            "orchestrator_stopped",
            mode=self.mode.value,
            candles_processed=len(self._processed_candles),
        )

        if self._can_notify():
            await self.telegram.notify_shutdown("Normal shutdown")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _on_candle(self, candle: dict[str, Any]) -> None:
        """WebSocket callback — routes closed candles to process_candle."""
        if not candle.get("is_closed", False):
            return  # Skip incomplete candles
        await self.process_candle(candle)

    async def process_candle(self, candle: dict[str, Any]) -> dict[str, Any]:
        """Process a single closed candle through the full pipeline.

        This is the core deterministic method. Testable without WebSocket.

        Args:
            candle: Dict with keys: symbol, open_time, open, high, low, close, volume.

        Returns:
            Dict summarizing what happened: {action, signal, trade, rejected_reason, ...}
        """
        result: dict[str, Any] = {"action": "none", "symbol": candle.get("symbol", "")}

        if not self._running:
            result["action"] = "stopped"
            return result

        # Kill switch check
        if self.risk_engine.kill_switch_active:
            result["action"] = "kill_switch"
            logger.warning("orchestrator_kill_switch_active", symbol=candle.get("symbol"))
            return result

        symbol = candle.get("symbol", "")
        open_time = candle.get("open_time")
        close_price = float(candle.get("close", 0) or 0)
        if close_price > 0:
            self._last_close_prices[symbol] = close_price

        # ----- Dedup check -----
        dedup_key = (symbol, open_time)
        if dedup_key in self._processed_candles:
            result["action"] = "duplicate"
            return result
        self._processed_candles.add(dedup_key)

        # ----- Buffer candle -----
        if symbol not in self._candle_buffers:
            self._candle_buffers[symbol] = []
        self._candle_buffers[symbol].append(candle)

        # ----- Check SL/TP on open positions -----
        # The strategy may also emit the same close-basket SELL on this candle.
        # Keep the broker-managed exit as the authoritative result so replay
        # diagnostics do not count the duplicate SELL as a lifecycle violation.
        sl_tp_result = await self._check_stop_loss_take_profit(symbol, candle)
        if sl_tp_result is not None and sl_tp_result.get("action") != "trade_executed":
            return sl_tp_result

        # ----- Build DataFrame from buffer -----
        df = self._build_dataframe(symbol)
        if df is None or len(df) < MIN_CANDLE_BUFFER:
            result["action"] = "buffering"
            result["buffer_size"] = len(self._candle_buffers.get(symbol, []))
            return result

        # ----- Regime gating (optional) -----
        if self.regime_config is not None:
            from app.backtesting.regime_filter import should_trade
            regime_ok, regime_state = should_trade(df, self.regime_config)
            if not regime_ok:
                result["action"] = "regime_blocked"
                result["regime"] = regime_state.regime
                result["regime_reasons"] = regime_state.reasons
                return result

        # ----- Strategy evaluation -----
        try:
            signals = self.strategy.generate_signals(df)
        except Exception as e:
            logger.error("orchestrator_strategy_error", strategy=self.strategy.name, error=str(e))
            self.risk_engine.record_error()
            await self._record_incident("ERROR", "strategy", f"Strategy error: {e}")
            result["action"] = "strategy_error"
            result["error"] = str(e)
            return result

        # Get a signal mapped to this exact latest candle only. Strategies may
        # return full-history signals; replaying the most recent old signal on
        # every new candle would diverge from backtest behavior.
        latest_signal = self._select_current_signal(signals, df, candle)
        if latest_signal is None:
            if sl_tp_result is not None:
                return sl_tp_result
            result["action"] = "no_signal"
            return result

        latest_signal.symbol = symbol  # Ensure symbol is set

        # Populate timestamp from candle if not set by strategy
        if latest_signal.timestamp is None:
            ts = candle.get("close_time") or datetime.now(timezone.utc)
            latest_signal.timestamp = _coerce_utc_datetime(ts)
        else:
            latest_signal.timestamp = _coerce_utc_datetime(latest_signal.timestamp)

        # ----- Lifecycle pre-check (spot long-only) -----
        if latest_signal.signal_type == SignalType.SELL:
            if symbol not in self._open_positions:
                if sl_tp_result is not None:
                    return sl_tp_result
                if self._is_stale_grid_exit(latest_signal):
                    logger.info(
                        "stale_grid_exit_ignored",
                        symbol=symbol,
                        grid_id=self._signal_grid_id(latest_signal),
                        grid_action=self._signal_grid_action(latest_signal),
                    )
                    result["action"] = "no_signal"
                    result["ignored_reason"] = "stale_grid_exit_without_position"
                    return result
                reject_reason = "no_open_position_for_sell"
                self._persist_signal(latest_signal, False, reject_reason)
                self._persist_grid_event(latest_signal, event_type="rejection", reject_reason=reject_reason)
                logger.info(
                    "lifecycle_blocked_no_position",
                    symbol=symbol,
                    reason="SELL signal but no open position",
                )
                result["action"] = "lifecycle_blocked"
                result["reject_reason"] = reject_reason
                result["metadata"] = dict(latest_signal.metadata)
                return result
        elif latest_signal.signal_type == SignalType.BUY:
            if symbol in self._open_positions:
                if self._is_grid_scale_in(latest_signal):
                    reject_reason = self._validate_grid_scale_in(symbol, latest_signal)
                else:
                    reject_reason = "position_already_open"
                if reject_reason:
                    self._persist_signal(latest_signal, False, reject_reason)
                    self._persist_grid_event(latest_signal, event_type="rejection", reject_reason=reject_reason)
                    logger.info(
                        "lifecycle_blocked_already_positioned",
                        symbol=symbol,
                        reason=reject_reason,
                    )
                    result["action"] = "lifecycle_blocked"
                    result["reject_reason"] = reject_reason
                    result["metadata"] = dict(latest_signal.metadata)
                    return result

        self._sync_risk_account_state()
        self._annotate_order_sizing(latest_signal)

        # ----- Risk engine evaluation -----
        approved, reject_reason = self.risk_engine.is_approved(latest_signal)

        # ----- Persist signal -----
        self._persist_signal(latest_signal, approved, reject_reason)

        if not approved:
            result["action"] = "risk_rejected"
            result["reject_reason"] = reject_reason
            result["metadata"] = dict(latest_signal.metadata)
            self._persist_grid_event(latest_signal, event_type="rejection", reject_reason=reject_reason)
            return result

        # ----- Execute via broker -----
        try:
            order_result = await self._execute_signal(latest_signal)
        except Exception as e:
            logger.error("orchestrator_execution_error", error=str(e))
            self.risk_engine.record_error()
            await self._record_incident("ERROR", "execution", f"Execution error: {e}")
            result["action"] = "execution_error"
            result["error"] = str(e)
            return result

        if not order_result.success:
            result["action"] = "order_rejected"
            result["error"] = order_result.error_message
            result["metadata"] = dict(latest_signal.metadata)
            self._persist_grid_event(
                latest_signal,
                event_type="rejection",
                reject_reason=order_result.error_message,
            )
            return result

        # ----- Persist trade -----
        self._persist_trade(latest_signal, order_result)

        # ----- Update risk engine state -----
        if latest_signal.signal_type == SignalType.BUY:
            if symbol in self._open_positions and self._is_grid_scale_in(latest_signal):
                pos = self._open_positions[symbol]
                old_qty = float(pos.get("quantity", 0.0))
                old_entry = float(pos.get("entry_price", order_result.filled_price))
                add_qty = order_result.filled_quantity
                new_qty = old_qty + add_qty
                pos["entry_price"] = (
                    ((old_entry * old_qty) + (order_result.filled_price * add_qty)) / new_qty
                    if new_qty > 0
                    else old_entry
                )
                pos["quantity"] = new_qty
                pos["entry_fee"] = float(pos.get("entry_fee", 0.0)) + order_result.fees
                pos["stop_loss"] = latest_signal.stop_loss or pos.get("stop_loss")
                pos["take_profit"] = latest_signal.take_profit or pos.get("take_profit")
                pos["strategy"] = self.strategy.name
                self._merge_grid_position_metadata(pos, latest_signal, order_result)
                self._persist_grid_event(latest_signal, order_result=order_result)
                self._persist_grid_state(symbol)
            else:
                self.risk_engine.open_positions += 1
                self._open_positions[symbol] = {
                    "entry_price": order_result.filled_price,
                    "quantity": order_result.filled_quantity,
                    "entry_fee": order_result.fees,
                    "stop_loss": latest_signal.stop_loss,
                    "take_profit": latest_signal.take_profit,
                    "strategy": self.strategy.name,
                    "opened_at": latest_signal.timestamp or datetime.now(timezone.utc),
                }
                self._merge_grid_position_metadata(
                    self._open_positions[symbol],
                    latest_signal,
                    order_result,
                )
                self._persist_grid_event(latest_signal, order_result=order_result)
                self._persist_grid_state(symbol)
        elif latest_signal.signal_type == SignalType.SELL:
            self.risk_engine.open_positions = max(0, self.risk_engine.open_positions - 1)
            if symbol in self._open_positions:
                pos = self._open_positions[symbol]
                entry_price = pos["entry_price"]
                qty = order_result.filled_quantity
                pnl = (
                    (order_result.filled_price - entry_price) * qty
                    - float(pos.get("entry_fee", 0.0))
                    - order_result.fees
                )
                self.risk_engine.record_trade_result(pnl, symbol, latest_signal.timestamp)
                self._persist_grid_event(latest_signal, order_result=order_result, realized_pnl=pnl)
                self._persist_grid_state(symbol, status="CLOSED", realized_pnl=pnl)
                del self._open_positions[symbol]

        # ----- Telegram notify -----
        if self._can_notify():
            try:
                trade_info = {
                    "symbol": symbol,
                    "side": latest_signal.signal_type.value,
                    "entry_price": order_result.filled_price,
                    "quantity": order_result.filled_quantity,
                    "stop_loss": latest_signal.stop_loss,
                    "take_profit": latest_signal.take_profit,
                    "strategy": self.strategy.name,
                }
                await self.telegram.notify_trade_opened(trade_info)
            except Exception as e:
                logger.error("orchestrator_telegram_error", error=str(e))

        # ----- Snapshot equity -----
        self._sync_risk_account_state()
        self._snapshot_equity()

        result["action"] = "trade_executed"
        result["signal"] = latest_signal.signal_type.value
        result["price"] = order_result.filled_price
        result["quantity"] = order_result.filled_quantity
        result["metadata"] = dict(latest_signal.metadata)
        return result

    async def _execute_signal(self, signal: StrategySignal) -> OrderResult:
        """Convert a strategy signal to an order and submit to the broker."""
        order = OrderRequest(
            symbol=signal.symbol,
            side=OrderSide.BUY if signal.signal_type == SignalType.BUY else OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=self._calculate_quantity(signal),
            price=signal.price,
            stop_loss_price=signal.stop_loss,
            take_profit_price=signal.take_profit,
            strategy_name=self.strategy.name,
            metadata=dict(signal.metadata),
        )
        return await self.broker.submit_order(order)

    def _select_current_signal(
        self,
        signals: list[StrategySignal],
        df: pd.DataFrame,
        candle: dict[str, Any],
    ) -> StrategySignal | None:
        """Return the newest signal that belongs to the current candle.

        Full-history strategies tag signals with ``_bar_index``; latest-only
        strategies usually return one untagged signal per call. Timestamped
        signals are matched against the current candle open/close time.
        """
        if not signals:
            return None

        latest_idx = len(df) - 1
        current_time_keys = self._current_candle_time_keys(df, candle)
        unscoped: list[StrategySignal] = []

        for signal in reversed(signals):
            bar_index = self._signal_bar_index(signal)
            if bar_index is not None:
                if bar_index == latest_idx:
                    return signal
                continue

            signal_time_key = self._time_key(signal.timestamp)
            if signal_time_key is not None:
                if signal_time_key in current_time_keys:
                    return signal
                continue

            unscoped.append(signal)

        if len(signals) == 1 and unscoped:
            return signals[-1]

        return None

    @staticmethod
    def _signal_bar_index(signal: StrategySignal) -> int | None:
        """Extract an integer candle index from signal metadata."""
        raw = signal.metadata.get("_bar_index", signal.metadata.get("bar_index"))
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _current_candle_time_keys(
        cls,
        df: pd.DataFrame,
        candle: dict[str, Any],
    ) -> set[int]:
        """Return normalized open/close time keys for the current candle."""
        keys: set[int] = set()
        row = df.iloc[-1]

        for source in (candle, row):
            for key in ("open_time", "close_time"):
                value = source.get(key) if hasattr(source, "get") else None
                time_key = cls._time_key(value)
                if time_key is not None:
                    keys.add(time_key)

        return keys

    @staticmethod
    def _time_key(value: Any) -> int | None:
        """Normalize supported timestamp shapes to UTC nanoseconds."""
        if value is None:
            return None
        try:
            dt = _coerce_utc_datetime(value)
            return int(pd.Timestamp(dt).value)
        except Exception:
            return None

    @staticmethod
    def _signal_grid_action(signal: StrategySignal) -> str:
        return str(signal.metadata.get("grid_action", ""))

    @staticmethod
    def _signal_grid_id(signal: StrategySignal) -> str:
        return str(signal.metadata.get("grid_id", ""))

    @classmethod
    def _is_grid_scale_in(cls, signal: StrategySignal) -> bool:
        return (
            signal.signal_type == SignalType.BUY
            and cls._signal_grid_action(signal) == "scale_in"
            and bool(cls._signal_grid_id(signal))
        )

    @classmethod
    def _is_stale_grid_exit(cls, signal: StrategySignal) -> bool:
        return (
            signal.signal_type == SignalType.SELL
            and cls._signal_grid_action(signal) in {"take_profit", "stop_exit"}
            and bool(cls._signal_grid_id(signal))
        )

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
    def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
        raw = metadata.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _validate_grid_scale_in(self, symbol: str, signal: StrategySignal) -> str:
        """Return rejection reason when a grid scale-in is not lifecycle-safe."""
        pos = self._open_positions.get(symbol)
        if not pos:
            return "no_open_grid_for_scale_in"

        signal_grid_id = self._signal_grid_id(signal)
        position_grid_id = str(pos.get("grid_id", ""))
        if not signal_grid_id or signal_grid_id != position_grid_id:
            return "grid_id_mismatch"

        grid_level = self._metadata_int(signal.metadata, "grid_level")
        filled_levels = set(pos.get("filled_grid_levels", []))
        if grid_level is not None and grid_level in filled_levels:
            return "grid_level_already_filled"

        projected = self._metadata_float(signal.metadata, "projected_grid_notional_pct")
        max_allocation = self._metadata_float(signal.metadata, "max_grid_allocation_pct")
        if projected is not None and max_allocation is not None and projected > max_allocation + 1e-12:
            return "grid_allocation_exceeded"

        return ""

    def _refresh_grid_position_stops(self, position: dict[str, Any]) -> None:
        """Anchor Grid/DCA SL/TP to the weighted average entry."""
        if not position.get("grid_id"):
            return
        try:
            stop_loss_pct = float(self.strategy.params.get("stop_loss_pct"))
            take_profit_pct = float(self.strategy.params.get("take_profit_pct"))
            entry_price = float(position.get("entry_price", 0.0))
        except (TypeError, ValueError):
            return
        if entry_price <= 0:
            return
        position["stop_loss"] = entry_price * (1 - stop_loss_pct)
        position["take_profit"] = entry_price * (1 + take_profit_pct)

    def _merge_grid_position_metadata(
        self,
        position: dict[str, Any],
        signal: StrategySignal,
        order_result: OrderResult,
    ) -> None:
        metadata = signal.metadata or {}
        grid_id = self._signal_grid_id(signal)
        if not grid_id and not position.get("grid_id"):
            return

        if grid_id:
            position["grid_id"] = grid_id
        position["grid_action"] = self._signal_grid_action(signal)
        position["max_grid_allocation_pct"] = self._metadata_float(
            metadata,
            "max_grid_allocation_pct",
        )
        position["allocation_pct"] = self._metadata_float(
            metadata,
            "projected_grid_notional_pct",
        )
        position["anchor_price"] = self._metadata_float(metadata, "anchor_price")

        grid_level = self._metadata_int(metadata, "grid_level")
        if grid_level is not None:
            levels = set(position.get("filled_grid_levels", []))
            levels.add(grid_level)
            position["filled_grid_levels"] = sorted(levels)

        position["last_grid_fill_price"] = order_result.filled_price
        position["last_grid_fill_quantity"] = order_result.filled_quantity
        self._refresh_grid_position_stops(position)

    def _annotate_order_sizing(self, signal: StrategySignal) -> None:
        """Attach deterministic paper sizing metadata before risk evaluation."""
        if signal.signal_type != SignalType.BUY:
            return

        quantity = self._calculate_quantity(signal)
        fill_price = self._estimated_fill_price(signal)
        fee_pct = float(getattr(self.broker, "fee_pct", 0.0))
        notional = fill_price * quantity
        total_cost = notional * (1 + fee_pct)

        signal.metadata.setdefault("estimated_quantity", quantity)
        signal.metadata.setdefault("estimated_fill_price", fill_price)
        signal.metadata.setdefault("notional", total_cost)

    def _estimated_fill_price(self, signal: StrategySignal) -> float:
        """Estimate the paper fill price using the broker slippage model."""
        slippage_pct = float(getattr(self.broker, "slippage_pct", 0.0))
        price = float(signal.price)
        if signal.signal_type == SignalType.BUY:
            return price * (1 + slippage_pct)
        return price * (1 - slippage_pct)

    def _calculate_quantity(self, signal: StrategySignal) -> float:
        """Calculate order quantity from signal and broker/position state.

        For BUY: uses available quote balance × max_position_size_pct.
        For SELL: uses the full held quantity from the open position (close entire position).
        """
        if signal.signal_type == SignalType.SELL:
            # Sell the full position (spot long-only → close entire position)
            pos = self._open_positions.get(signal.symbol)
            if pos:
                return pos["quantity"]
            # Fallback (should not reach here due to lifecycle pre-check)
            return 0.0

        # BUY: use grid target sizing when provided; otherwise use standard
        # max-position sizing. Include expected slippage and fees so the final
        # submitted order stays within the budget.
        balance = self.broker.balances.get(self.broker.quote_asset, 0)
        target_pct = self._metadata_float(signal.metadata, "target_notional_pct")
        if target_pct is not None:
            equity = self.broker.get_total_equity(prices=self._last_close_prices)
            max_invest = max(0.0, equity * target_pct)
            max_allocation = self._metadata_float(signal.metadata, "max_grid_allocation_pct")
            pos = self._open_positions.get(signal.symbol)
            if max_allocation is not None and pos:
                current_notional = float(pos.get("entry_price", 0.0)) * float(pos.get("quantity", 0.0))
                remaining = max(0.0, equity * max_allocation - current_notional)
                max_invest = min(max_invest, remaining)
            max_invest = min(max_invest, balance)
        else:
            max_invest = balance * self.risk_engine.max_position_size_pct
        estimated_fill = self._estimated_fill_price(signal)
        fee_pct = float(getattr(self.broker, "fee_pct", 0.0))
        if estimated_fill > 0:
            return max_invest / (estimated_fill * (1 + fee_pct))
        return 0.0

    async def _check_stop_loss_take_profit(
        self, symbol: str, candle: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Check if any open position's SL/TP is hit by this candle."""
        pos = self._open_positions.get(symbol)
        if not pos:
            return None

        low = float(candle.get("low", 0))
        high = float(candle.get("high", 0))
        close = float(candle.get("close", 0))
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")

        triggered = False
        exit_price = close
        exit_reason = "signal"

        if sl and low <= sl:
            triggered = True
            exit_price = sl
            exit_reason = "stop_loss"
        elif tp and high >= tp:
            triggered = True
            exit_price = tp
            exit_reason = "take_profit"

        if triggered:
            exit_metadata = {
                "grid_id": pos.get("grid_id", ""),
                "grid_action": "stop_exit" if exit_reason == "stop_loss" else "take_profit",
                "grid_level": max(pos.get("filled_grid_levels") or [0]),
                "max_grid_allocation_pct": pos.get("max_grid_allocation_pct"),
                "projected_grid_notional_pct": pos.get("allocation_pct"),
            } if pos.get("grid_id") else {}

            # Create a SELL signal for the exit
            sell_order = OrderRequest(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=pos["quantity"],
                price=exit_price,
                strategy_name=pos.get("strategy", ""),
                metadata=exit_metadata,
            )
            result = await self.broker.submit_order(sell_order)

            if result.success:
                ts = candle.get("close_time") or datetime.now(timezone.utc)
                ts = _coerce_utc_datetime(ts)

                pnl = (
                    (result.filled_price - pos["entry_price"]) * pos["quantity"]
                    - float(pos.get("entry_fee", 0.0))
                    - result.fees
                )

                metadata = {"exit_reason": exit_reason, "source": "sl_tp"}
                metadata.update(exit_metadata)

                exit_signal = StrategySignal(
                    signal_type=SignalType.SELL,
                    symbol=symbol,
                    price=exit_price,
                    timestamp=ts,
                    metadata=metadata,
                )
                self._persist_signal(exit_signal, True, "")
                self._persist_trade(exit_signal, result)
                self._persist_grid_event(exit_signal, order_result=result, realized_pnl=pnl)
                self._persist_grid_state(symbol, status="CLOSED", realized_pnl=pnl)

                self.risk_engine.record_trade_result(pnl, symbol, ts)
                self.risk_engine.open_positions = max(0, self.risk_engine.open_positions - 1)
                del self._open_positions[symbol]
                self._sync_risk_account_state()
                self._snapshot_equity()

                logger.info(
                    "position_closed_by_sl_tp",
                    symbol=symbol,
                    reason=exit_reason,
                    exit_price=exit_price,
                    pnl=round(pnl, 2),
                )

                if self._can_notify():
                    try:
                        if exit_reason == "stop_loss":
                            await self.telegram.notify_stop_loss_hit({
                                "symbol": symbol,
                                "entry_price": pos["entry_price"],
                                "stop_loss": exit_price,
                                "pnl": pnl,
                            })
                        else:
                            await self.telegram.notify_trade_closed({
                                "symbol": symbol,
                                "entry_price": pos["entry_price"],
                                "exit_price": exit_price,
                                "pnl": pnl,
                                "pnl_pct": pnl / (pos["entry_price"] * pos["quantity"]) if pos["entry_price"] > 0 else 0,
                                "exit_reason": exit_reason,
                            })
                    except Exception as e:
                        logger.error("telegram_sl_tp_notify_error", error=str(e))

                return {
                    "action": "trade_executed",
                    "signal": SignalType.SELL.value,
                    "price": result.filled_price,
                    "quantity": result.filled_quantity,
                    "metadata": metadata,
                    "exit_reason": exit_reason,
                    "pnl": pnl,
                }

            return {
                "action": "order_rejected",
                "error": result.error_message,
                "metadata": exit_metadata,
                "exit_reason": exit_reason,
            }

        return None

    def _build_dataframe(self, symbol: str) -> pd.DataFrame | None:
        """Build a DataFrame from the candle buffer for the given symbol."""
        buffer = self._candle_buffers.get(symbol, [])
        if not buffer:
            return None

        df = pd.DataFrame(buffer)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df

    def _persist_signal(
        self, signal: StrategySignal, accepted: bool, reject_reason: str
    ) -> None:
        """Persist a signal record to the database."""
        if not self._persistence_enabled:
            return
        session = get_session(self.database_url)
        try:
            repo = SignalRepository(session)
            repo.save(SignalModel(
                strategy_name=self.strategy.name,
                symbol=signal.symbol,
                signal_type=signal.signal_type.value,
                price=signal.price,
                strength=signal.strength,
                metadata_json=json.dumps(signal.metadata, default=str),
                timestamp=signal.timestamp or datetime.now(timezone.utc),
                accepted=accepted,
                reject_reason=reject_reason,
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("persist_signal_error", error=str(e))
        finally:
            session.close()

    def _persist_trade(self, signal: StrategySignal, result: OrderResult) -> None:
        """Persist a trade record to the database."""
        if not self._persistence_enabled:
            return
        session = get_session(self.database_url)
        try:
            repo = TradeRepository(session)
            repo.save(Trade(
                mode=self.mode.value,
                strategy_name=self.strategy.name,
                symbol=signal.symbol,
                side=signal.signal_type.value,
                order_type="MARKET",
                requested_quantity=result.requested_quantity,
                filled_quantity=result.filled_quantity,
                requested_price=result.requested_price,
                filled_price=result.filled_price,
                status=result.status.value,
                fees=result.fees,
                slippage=abs(result.filled_price - result.requested_price)
                * result.filled_quantity,
                stop_loss_price=signal.stop_loss,
                take_profit_price=signal.take_profit,
                exchange_order_id=result.order_id,
                filled_at=result.timestamp,
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("persist_trade_error", error=str(e))
        finally:
            session.close()

    def _persist_grid_state(
        self,
        symbol: str,
        status: str = "OPEN",
        realized_pnl: float = 0.0,
    ) -> None:
        """Persist current Grid/DCA basket state if the position is grid-backed."""
        if not self._persistence_enabled:
            return
        pos = self._open_positions.get(symbol)
        if not pos or not pos.get("grid_id"):
            return

        session = get_session(self.database_url)
        try:
            now = datetime.now(timezone.utc)
            quantity = float(pos.get("quantity", 0.0))
            entry_price = float(pos.get("entry_price", 0.0))
            allocated_notional = entry_price * quantity
            allocation_pct = pos.get("allocation_pct")
            if allocation_pct is None:
                equity = self.broker.get_total_equity(prices=self._last_close_prices)
                allocation_pct = allocated_notional / equity if equity > 0 else 0.0

            repo = GridStateRepository(session)
            repo.upsert(GridState(
                mode=self.mode.value,
                strategy_name=self.strategy.name,
                symbol=symbol,
                interval=self.interval,
                grid_id=str(pos.get("grid_id", "")),
                status=status,
                anchor_price=float(pos.get("anchor_price") or 0.0),
                average_entry_price=entry_price,
                quantity=quantity,
                allocated_notional=allocated_notional,
                allocation_pct=float(allocation_pct or 0.0),
                filled_levels_json=json.dumps(pos.get("filled_grid_levels", []), default=str),
                params_json=json.dumps(self.strategy.params, default=str),
                opened_at=pos.get("opened_at", now),
                updated_at=now,
                closed_at=now if status != "OPEN" else None,
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("persist_grid_state_error", error=str(e), realized_pnl=realized_pnl)
        finally:
            session.close()

    def _persist_grid_event(
        self,
        signal: StrategySignal,
        order_result: OrderResult | None = None,
        *,
        event_type: str | None = None,
        reject_reason: str = "",
        realized_pnl: float = 0.0,
    ) -> None:
        """Persist append-only Grid/DCA event if the signal belongs to a grid."""
        if not self._persistence_enabled:
            return
        grid_id = self._signal_grid_id(signal)
        grid_action = event_type or self._signal_grid_action(signal)
        if not grid_id or not grid_action:
            return

        session = get_session(self.database_url)
        try:
            price = order_result.filled_price if order_result else signal.price
            quantity = order_result.filled_quantity if order_result else 0.0
            fees = order_result.fees if order_result else 0.0
            notional = price * quantity if order_result else 0.0
            metadata = dict(signal.metadata)
            if reject_reason:
                metadata["reject_reason"] = reject_reason

            repo = GridEventRepository(session)
            repo.save(GridEvent(
                mode=self.mode.value,
                strategy_name=self.strategy.name,
                symbol=signal.symbol,
                interval=self.interval,
                grid_id=grid_id,
                event_type=grid_action,
                side=signal.signal_type.value,
                grid_level=int(metadata.get("grid_level") or 0),
                price=float(price or 0.0),
                quantity=float(quantity or 0.0),
                notional=float(notional or 0.0),
                fees=float(fees or 0.0),
                realized_pnl=float(realized_pnl or 0.0),
                metadata_json=json.dumps(metadata, default=str),
                timestamp=signal.timestamp or datetime.now(timezone.utc),
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("persist_grid_event_error", error=str(e))
        finally:
            session.close()

    def _snapshot_equity(self) -> None:
        """Take a point-in-time equity snapshot."""
        if not self._persistence_enabled:
            return
        session = get_session(self.database_url)
        try:
            equity = self.broker.get_total_equity(prices=self._last_close_prices)
            available = self.broker.balances.get(self.broker.quote_asset, 0)
            unrealized = self._calculate_unrealized_pnl()
            repo = AccountSnapshotRepository(session)
            repo.save(AccountSnapshot(
                mode=self.mode.value,
                total_equity=equity,
                available_balance=available,
                unrealized_pnl=unrealized,
                daily_pnl=self.risk_engine.daily_pnl,
                open_positions=self.risk_engine.open_positions,
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("snapshot_equity_error", error=str(e))
        finally:
            session.close()

    def _sync_risk_account_state(self) -> None:
        """Keep risk account balances aligned with the paper broker."""
        self.risk_engine.equity = self.broker.get_total_equity(
            prices=self._last_close_prices
        )
        self.risk_engine.available_balance = self.broker.balances.get(
            self.broker.quote_asset,
            0.0,
        )

    def _calculate_unrealized_pnl(self) -> float:
        """Calculate mark-to-market unrealized PnL for open paper positions."""
        total = 0.0
        for symbol, pos in self._open_positions.items():
            mark_price = self._last_close_prices.get(symbol, pos["entry_price"])
            entry_value = pos["entry_price"] * pos["quantity"]
            mark_value = mark_price * pos["quantity"]
            total += mark_value - entry_value - float(pos.get("entry_fee", 0.0))
        return total

    async def _record_incident(
        self, severity: str, component: str, message: str
    ) -> None:
        """Record an incident to the database."""
        if not self._persistence_enabled:
            return
        session = get_session(self.database_url)
        try:
            repo = IncidentRepository(session)
            repo.save(Incident(
                severity=severity,
                component=component,
                message=message,
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("record_incident_error", error=str(e))
        finally:
            session.close()

    def _can_notify(self) -> bool:
        return self.telegram is not None and self._notifications_enabled

    def get_status(self) -> dict[str, Any]:
        """Return current orchestrator status."""
        return {
            "running": self._running,
            "mode": self.mode.value,
            "strategy": self.strategy.name,
            "symbols": self.symbols,
            "interval": self.interval,
            "candles_processed": len(self._processed_candles),
            "open_positions": len(self._open_positions),
            "kill_switch": self.risk_engine.kill_switch_active,
            "daily_pnl": self.risk_engine.daily_pnl,
            "consecutive_losses": self.risk_engine.consecutive_losses,
            "equity": self.broker.get_total_equity(prices=self._last_close_prices),
        }

    async def replay_candles(
        self,
        candles: list[dict[str, Any]],
        progress_callback: Any | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        """Replay persisted candles through the orchestrator pipeline.

        Deterministic simulation using the same process_candle() pipeline
        as live paper trading. Useful for debugging and repeated testing.

        Args:
            candles: List of candle dicts (must include symbol, open_time, OHLCV).
            progress_callback: Optional callable(current, total) for progress reporting.
            persist: If true, write replay signals/trades/snapshots to the runtime DB.
                Defaults to false so research replay cannot pollute paper state.

        Returns:
            Summary dict with counts and final status.
        """
        self._running = True
        previous_persistence_enabled = self._persistence_enabled
        previous_notifications_enabled = self._notifications_enabled
        self._persistence_enabled = previous_persistence_enabled and persist
        self._notifications_enabled = False
        if self.telegram is not None:
            logger.info("replay_notifications_suppressed")
        if not self._persistence_enabled:
            logger.info("replay_persistence_suppressed")

        total = len(candles)
        initial_equity = self.broker.get_total_equity()
        summary: dict[str, Any] = {
            "total_candles": total,
            "trades_executed": 0,
            "signals_rejected": 0,
            "lifecycle_blocked": 0,
            "order_rejected": 0,
            "regime_blocked": 0,
            "no_signal": 0,
            "errors": 0,
            "duplicates": 0,
            "buffering": 0,
            "grid_actions": {},
        }

        logger.info(
            "replay_started",
            strategy=self.strategy.name,
            total_candles=total,
            initial_equity=initial_equity,
        )

        last_close_prices: dict[str, float] = {}

        try:
            for i, candle in enumerate(candles):
                if not self._running:
                    break

                last_close_prices[candle.get("symbol", "")] = float(candle.get("close", 0))
                result = await self.process_candle(candle)
                action = result.get("action", "none")

                if action == "trade_executed":
                    summary["trades_executed"] += 1
                elif action == "risk_rejected":
                    summary["signals_rejected"] += 1
                elif action == "lifecycle_blocked":
                    summary["lifecycle_blocked"] += 1
                elif action == "order_rejected":
                    summary["order_rejected"] += 1
                elif action == "regime_blocked":
                    summary["regime_blocked"] += 1
                elif action == "no_signal":
                    summary["no_signal"] += 1
                elif action in ("strategy_error", "execution_error"):
                    summary["errors"] += 1
                elif action == "duplicate":
                    summary["duplicates"] += 1
                elif action == "buffering":
                    summary["buffering"] += 1

                metadata = result.get("metadata") or {}
                grid_action = metadata.get("grid_action")
                if grid_action:
                    grid_actions = summary.setdefault("grid_actions", {})
                    grid_actions[grid_action] = grid_actions.get(grid_action, 0) + 1

                if progress_callback and (i + 1) % 100 == 0:
                    progress_callback(i + 1, total)

        except Exception as exc:
            summary["errors"] += 1
            logger.error("replay_unexpected_error", error=str(exc))
        finally:
            self._running = False
            self._persistence_enabled = previous_persistence_enabled
            self._notifications_enabled = previous_notifications_enabled

            # Always populate final equity keys so callers never get KeyError
            final_equity = self.broker.get_total_equity(prices=last_close_prices)
            summary["final_equity"] = final_equity
            summary["initial_capital"] = initial_equity
            summary["open_positions"] = len(self._open_positions)
            summary["net_pnl"] = final_equity - initial_equity
            summary["daily_pnl"] = self.risk_engine.daily_pnl

        logger.info(
            "replay_complete",
            trades=summary["trades_executed"],
            rejected=summary["signals_rejected"],
            lifecycle_blocked=summary["lifecycle_blocked"],
            equity=summary["final_equity"],
            net_pnl=round(summary["net_pnl"], 2),
        )

        return summary
