"""Tests for kill switch logic and mode safety."""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.core.enums import SignalType
from app.risk.risk_engine import RiskEngine
from app.strategies.base import StrategySignal


def _make_signal() -> StrategySignal:
    return StrategySignal(
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=100.0,
        stop_loss=97.0,
    )


class TestKillSwitch:
    def test_kill_switch_blocks_all_signals(self):
        engine = RiskEngine(equity=10_000)
        engine.activate_kill_switch("test emergency")
        approved, reason = engine.is_approved(_make_signal())
        assert approved is False
        assert "kill switch" in reason.lower() or "kill_switch" in reason.lower()

    def test_kill_switch_deactivation_allows_trading(self):
        engine = RiskEngine(equity=10_000)
        engine.activate_kill_switch("test")
        engine.deactivate_kill_switch()
        approved, _ = engine.is_approved(_make_signal())
        assert approved is True

    def test_kill_switch_persists_across_signals(self):
        engine = RiskEngine(equity=10_000)
        engine.activate_kill_switch("emergency")

        for _ in range(5):
            approved, _ = engine.is_approved(_make_signal())
            assert approved is False


class TestModeSafety:
    def test_default_mode_is_paper(self, settings_overrides):
        s = Settings(**settings_overrides)
        assert s.trading_mode.value == "paper"

    def test_live_disabled_by_default(self, settings_overrides):
        s = Settings(**settings_overrides)
        assert s.enable_live_trading is False
        assert s.is_live is False

    def test_live_requires_credentials(self, settings_overrides):
        with pytest.raises(ValueError):
            Settings(
                **{**settings_overrides, "enable_live_trading": True, "trading_mode": "live"}
            )

    def test_live_requires_stop_loss(self):
        """In live mode, risk engine must reject signals without stop loss."""
        engine = RiskEngine(equity=10_000, is_live=True)
        signal = StrategySignal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=100.0,
            stop_loss=None,
        )
        approved, _ = engine.is_approved(signal)
        assert approved is False

    def test_error_halt_after_repeated_errors(self):
        engine = RiskEngine(equity=10_000)
        for _ in range(10):
            engine.record_error()
        approved, reason = engine.is_approved(_make_signal())
        assert approved is False
        assert "error" in reason.lower()
