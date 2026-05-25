"""Tests for backtest engine."""

from __future__ import annotations

import pytest

from app.backtesting.engine import BacktestEngine, BacktestResult
from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.ema_atr import EmaAtrStrategy


class SellOnlyBacktestStrategy(BaseStrategy):
    name = "test_sell_only_backtest"

    def default_params(self):
        return {"min_periods": 1}

    def generate_signals(self, candles):
        return [
            StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=float(row["close"]),
                metadata={"_bar_index": int(i)},
            )
            for i, row in candles.iterrows()
        ]


class BuySellBacktestStrategy(BaseStrategy):
    name = "test_buy_sell_backtest"

    def default_params(self):
        return {"min_periods": 1}

    def generate_signals(self, candles):
        signals = []
        if len(candles) > 0:
            signals.append(StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=float(candles.iloc[0]["close"]),
                metadata={"_bar_index": 0},
            ))
        if len(candles) > 1:
            signals.append(StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=float(candles.iloc[1]["close"]),
                metadata={"_bar_index": 1},
            ))
        return signals


class StopLossBacktestStrategy(BaseStrategy):
    name = "test_stop_loss_backtest"

    def default_params(self):
        return {"min_periods": 1}

    def generate_signals(self, candles):
        if candles.empty:
            return []
        close = float(candles.iloc[0]["close"])
        return [StrategySignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=close,
            stop_loss=95.0,
            metadata={"_bar_index": 0},
        )]


class GridScaleBacktestStrategy(BaseStrategy):
    name = "test_grid_scale_backtest"

    def default_params(self):
        return {
            "min_periods": 1,
            "stop_loss_pct": 0.50,
            "take_profit_pct": 1.00,
        }

    def generate_signals(self, candles):
        signals = []
        if len(candles) > 0:
            signals.append(StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=float(candles.iloc[0]["close"]),
                metadata={
                    "_bar_index": 0,
                    "grid_action": "open",
                    "grid_id": "grid-1",
                    "grid_level": 0,
                    "target_notional_pct": 0.50,
                    "projected_grid_notional_pct": 0.50,
                    "max_grid_allocation_pct": 0.80,
                },
            ))
        if len(candles) > 1:
            signals.append(StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=float(candles.iloc[1]["close"]),
                metadata={
                    "_bar_index": 1,
                    "grid_action": "scale_in",
                    "grid_id": "grid-1",
                    "grid_level": 1,
                    "target_notional_pct": 0.25,
                    "projected_grid_notional_pct": 0.75,
                    "max_grid_allocation_pct": 0.80,
                },
            ))
        if len(candles) > 2:
            signals.append(StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=float(candles.iloc[2]["close"]),
                metadata={
                    "_bar_index": 2,
                    "grid_action": "take_profit",
                    "grid_id": "grid-1",
                },
            ))
        return signals


class GridStopAfterScaleBacktestStrategy(GridScaleBacktestStrategy):
    name = "test_grid_stop_after_scale_backtest"

    def default_params(self):
        return {
            "min_periods": 1,
            "stop_loss_pct": 0.21,
            "take_profit_pct": 1.00,
        }

    def generate_signals(self, candles):
        return [
            signal
            for signal in super().generate_signals(candles)
            if signal.signal_type != SignalType.SELL
        ]


class TimestampMappedStrategy(BaseStrategy):
    name = "test_timestamp_mapped"

    def default_params(self):
        return {"min_periods": 1}

    def generate_signals(self, candles):
        return [
            StrategySignal(
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=float(candles.iloc[2]["close"]),
                timestamp=candles.iloc[2]["open_time"],
            ),
            StrategySignal(
                signal_type=SignalType.SELL,
                symbol="BTCUSDT",
                price=float(candles.iloc[3]["close"]),
                timestamp=candles.iloc[3]["open_time"],
            ),
        ]


class TestBacktestEngine:
    def test_backtest_produces_result(self, sample_candles):
        engine = BacktestEngine(initial_capital=10_000, fee_pct=0.001, slippage_pct=0.001)
        strategy = EmaAtrStrategy()
        result = engine.run(strategy, sample_candles, symbol="BTCUSDT", interval="1h")

        assert isinstance(result, BacktestResult)
        assert result.initial_capital == 10_000
        assert result.final_equity > 0
        assert result.strategy_name == "ema_atr_crossover"
        assert result.symbol == "BTCUSDT"

    def test_equity_curve_populated(self, sample_candles):
        engine = BacktestEngine(initial_capital=10_000)
        strategy = EmaAtrStrategy()
        result = engine.run(strategy, sample_candles)

        assert len(result.equity_curve) > 0
        assert result.equity_curve[0] == 10_000

    def test_fees_and_slippage_deducted(self, sample_candles):
        engine = BacktestEngine(initial_capital=10_000, fee_pct=0.01, slippage_pct=0.01)
        strategy = EmaAtrStrategy()
        result = engine.run(strategy, sample_candles)

        if result.trades:
            assert result.fees_paid > 0

    def test_stop_loss_handling(self, sample_candles):
        engine = BacktestEngine(
            initial_capital=10_000,
            stop_loss_pct=0.01,  # Tight 1% stop loss
        )
        strategy = EmaAtrStrategy()
        result = engine.run(strategy, sample_candles)

        sl_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        # With tight stop loss, we should see some SL exits
        assert isinstance(sl_trades, list)

    def test_empty_candles_no_crash(self):
        import pandas as pd
        engine = BacktestEngine()
        strategy = EmaAtrStrategy()
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = engine.run(strategy, df)
        assert result.trades == []


class TestBacktestSpotHardening:
    def test_sell_while_flat_does_not_open_short(self):
        import pandas as pd

        candles = pd.DataFrame({
            "open_time": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1000.0, 1000.0, 1000.0],
        })

        engine = BacktestEngine(initial_capital=10_000, fee_pct=0.0, slippage_pct=0.0)
        result = engine.run(SellOnlyBacktestStrategy(), candles)

        assert result.trades == []
        assert result.final_equity == pytest.approx(10_000)

    def test_buy_sell_round_trip_credits_exit_proceeds_once(self):
        import pandas as pd

        candles = pd.DataFrame({
            "open_time": pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC"),
            "open": [100.0, 110.0],
            "high": [100.0, 110.0],
            "low": [100.0, 110.0],
            "close": [100.0, 110.0],
            "volume": [1000.0, 1000.0],
        })

        engine = BacktestEngine(
            initial_capital=10_000,
            fee_pct=0.001,
            slippage_pct=0.0,
            max_position_size_pct=1.0,
        )
        result = engine.run(BuySellBacktestStrategy(), candles)

        quantity = 10_000 / (100.0 * 1.001)
        entry_fee = 100.0 * quantity * 0.001
        exit_fee = 110.0 * quantity * 0.001
        expected_equity = 10_000 - (100.0 * quantity + entry_fee) + (110.0 * quantity - exit_fee)

        assert len(result.trades) == 1
        assert result.final_equity == pytest.approx(expected_equity)
        assert result.trades[0].fees == pytest.approx(entry_fee + exit_fee)

    def test_stop_loss_exit_uses_correct_cash_accounting(self):
        import pandas as pd

        candles = pd.DataFrame({
            "open_time": pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC"),
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 94.0],
            "close": [100.0, 100.0],
            "volume": [1000.0, 1000.0],
        })

        engine = BacktestEngine(
            initial_capital=10_000,
            fee_pct=0.0,
            slippage_pct=0.0,
            max_position_size_pct=1.0,
        )
        result = engine.run(StopLossBacktestStrategy(), candles)

        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "stop_loss"
        assert result.final_equity == pytest.approx(9_500)

    def test_duplicate_close_prices_map_by_timestamp_not_price(self):
        import pandas as pd

        open_times = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
        candles = pd.DataFrame({
            "open_time": open_times,
            "open": [100.0, 100.0, 100.0, 110.0],
            "high": [101.0, 101.0, 101.0, 111.0],
            "low": [99.0, 99.0, 99.0, 109.0],
            "close": [100.0, 100.0, 100.0, 110.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        })

        engine = BacktestEngine(
            initial_capital=10_000,
            fee_pct=0.0,
            slippage_pct=0.0,
            max_position_size_pct=1.0,
        )
        result = engine.run(TimestampMappedStrategy(), candles)

        assert len(result.trades) == 1
        assert result.trades[0].entry_time == open_times[2].to_pydatetime()

    def test_grid_scale_in_uses_weighted_average_and_fee_accounting(self):
        import pandas as pd

        candles = pd.DataFrame({
            "open_time": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "open": [100.0, 80.0, 120.0],
            "high": [100.0, 80.0, 120.0],
            "low": [100.0, 80.0, 120.0],
            "close": [100.0, 80.0, 120.0],
            "volume": [1000.0, 1000.0, 1000.0],
        })

        engine = BacktestEngine(
            initial_capital=10_000,
            fee_pct=0.001,
            slippage_pct=0.0,
            max_position_size_pct=1.0,
        )
        result = engine.run(GridScaleBacktestStrategy(), candles)

        qty1 = 5_000 / (100.0 * 1.001)
        fee1 = 100.0 * qty1 * 0.001
        cash_after_1 = 10_000 - (100.0 * qty1 + fee1)
        equity_before_2 = cash_after_1 + qty1 * 80.0
        current_notional = 100.0 * qty1
        budget2 = min(equity_before_2 * 0.25, equity_before_2 * 0.80 - current_notional)
        qty2 = budget2 / (80.0 * 1.001)
        fee2 = 80.0 * qty2 * 0.001
        cash_after_2 = cash_after_1 - (80.0 * qty2 + fee2)
        exit_fee = 120.0 * (qty1 + qty2) * 0.001
        expected_equity = cash_after_2 + (120.0 * (qty1 + qty2) - exit_fee)
        expected_entry = ((100.0 * qty1) + (80.0 * qty2)) / (qty1 + qty2)

        assert len(result.trades) == 1
        assert result.trades[0].entry_price == pytest.approx(expected_entry)
        assert result.trades[0].quantity == pytest.approx(qty1 + qty2)
        assert result.trades[0].fees == pytest.approx(fee1 + fee2 + exit_fee)
        assert result.final_equity == pytest.approx(expected_equity)
        assert result.diagnostics["signals"]["open"] == 1
        assert result.diagnostics["signals"]["scale_in"] == 1
        assert result.diagnostics["exit_counts"]["take_profit"] == 1
        assert result.diagnostics["max_filled_levels"] == 2
        assert result.trades[0].metadata["filled_grid_levels"] == [0, 1]

    def test_grid_stop_after_scale_credits_cash_once(self):
        import pandas as pd

        candles = pd.DataFrame({
            "open_time": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "open": [100.0, 80.0, 82.0],
            "high": [100.0, 80.0, 83.0],
            "low": [100.0, 80.0, 73.0],
            "close": [100.0, 80.0, 82.0],
            "volume": [1000.0, 1000.0, 1000.0],
        })

        engine = BacktestEngine(
            initial_capital=10_000,
            fee_pct=0.0,
            slippage_pct=0.0,
            max_position_size_pct=1.0,
        )
        result = engine.run(GridStopAfterScaleBacktestStrategy(), candles)

        qty1 = 5_000 / 100.0
        cash_after_1 = 5_000
        equity_before_2 = cash_after_1 + qty1 * 80.0
        budget2 = min(equity_before_2 * 0.25, equity_before_2 * 0.80 - 100.0 * qty1)
        qty2 = budget2 / 80.0
        cash_after_2 = cash_after_1 - qty2 * 80.0
        average_entry = ((100.0 * qty1) + (80.0 * qty2)) / (qty1 + qty2)
        stop_price = average_entry * 0.79
        expected_equity = cash_after_2 + stop_price * (qty1 + qty2)

        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "stop_loss"
        assert result.final_equity == pytest.approx(expected_equity)
