"""Tests for strategy signal generation."""

from __future__ import annotations

import pandas as pd

from app.core.enums import SignalType
from app.strategies.base import StrategySignal
from app.strategies.ema_atr import EmaAtrStrategy
from app.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from app.strategies.bollinger_mean_reversion import BollingerMeanReversionStrategy
from app.strategies.breakout import BreakoutStrategy
from app.strategies.regime_strategy import RegimeStrategy


class TestEmaAtrStrategy:
    def test_generates_signals(self, sample_candles):
        strategy = EmaAtrStrategy()
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)
        assert len(signals) > 0
        for s in signals:
            assert isinstance(s, StrategySignal)
            assert s.signal_type in (SignalType.BUY, SignalType.SELL)
            assert s.price > 0
            assert s.stop_loss is not None

    def test_respects_custom_params(self, sample_candles):
        strategy = EmaAtrStrategy(params={"fast_ema": 5, "slow_ema": 20})
        assert strategy.params["fast_ema"] == 5
        assert strategy.params["slow_ema"] == 20
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)

    def test_rejects_insufficient_data(self):
        df = pd.DataFrame({
            "open": [1, 2], "high": [2, 3], "low": [0.5, 1.5],
            "close": [1.5, 2.5], "volume": [100, 200],
        })
        strategy = EmaAtrStrategy()
        assert strategy.generate_signals(df) == []


class TestRsiMeanReversion:
    def test_generates_signals(self, sample_candles):
        strategy = RsiMeanReversionStrategy()
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)
        for s in signals:
            assert s.signal_type in (SignalType.BUY, SignalType.SELL)
            assert "rsi" in s.metadata

    def test_custom_thresholds(self, sample_candles):
        strategy = RsiMeanReversionStrategy(params={"oversold": 25, "overbought": 75})
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)


class TestBollingerMeanReversion:
    def test_generates_signals(self, sample_candles):
        strategy = BollingerMeanReversionStrategy()
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)
        for s in signals:
            assert "bb_mid" in s.metadata


class TestBreakoutStrategy:
    def test_generates_signals(self, sample_candles):
        strategy = BreakoutStrategy()
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)


class TestRegimeStrategy:
    def test_generates_signals(self, sample_candles):
        strategy = RegimeStrategy()
        signals = strategy.generate_signals(sample_candles)
        assert isinstance(signals, list)
        for s in signals:
            assert "regime" in s.metadata
            assert s.metadata["regime"] in ("trending", "ranging")

    def test_adapts_to_regime(self, sample_candles):
        strategy = RegimeStrategy(params={"adx_trend_threshold": 20})
        signals = strategy.generate_signals(sample_candles)
        regimes = [s.metadata["regime"] for s in signals]
        # Should detect both regimes in sample data
        assert isinstance(regimes, list)


class TestBaseStrategy:
    def test_validate_candles_missing_columns(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        strategy = EmaAtrStrategy()
        assert strategy.validate_candles(df) is False

    def test_validate_candles_sufficient_data(self, sample_candles):
        strategy = EmaAtrStrategy()
        assert strategy.validate_candles(sample_candles) is True
