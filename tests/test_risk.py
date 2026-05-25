"""Tests for risk engine and risk rules."""

from __future__ import annotations


from app.core.enums import SignalType
from app.risk.risk_engine import RiskEngine
from app.strategies.base import StrategySignal


def _make_signal(
    signal_type: SignalType = SignalType.BUY,
    price: float = 100.0,
    stop_loss: float | None = 97.0,
) -> StrategySignal:
    return StrategySignal(
        signal_type=signal_type,
        symbol="BTCUSDT",
        price=price,
        stop_loss=stop_loss,
    )


class TestRiskEngine:
    def test_approves_valid_signal(self):
        engine = RiskEngine(equity=10_000)
        signal = _make_signal()
        approved, reason = engine.is_approved(signal)
        assert approved is True
        assert reason == ""

    def test_rejects_on_kill_switch(self):
        engine = RiskEngine(equity=10_000)
        engine.activate_kill_switch("test")
        signal = _make_signal()
        approved, reason = engine.is_approved(signal)
        assert approved is False
        assert "kill_switch" in reason.lower()

    def test_rejects_max_positions(self):
        engine = RiskEngine(equity=10_000, max_open_positions=2)
        engine.open_positions = 2
        signal = _make_signal()
        approved, reason = engine.is_approved(signal)
        assert approved is False
        assert "max_open_positions" in reason.lower() or "position" in reason.lower()

    def test_rejects_daily_loss(self):
        engine = RiskEngine(equity=10_000, max_daily_loss_pct=0.05)
        engine.daily_pnl = -600  # -6% exceeds 5% limit
        signal = _make_signal()
        approved, reason = engine.is_approved(signal)
        assert approved is False

    def test_sell_signal_bypasses_daily_loss(self):
        """Daily loss should stop new entries, not risk-reducing exits."""
        engine = RiskEngine(equity=10_000, max_daily_loss_pct=0.05)
        engine.daily_pnl = -600
        signal = _make_signal(signal_type=SignalType.SELL, stop_loss=None)
        approved, reason = engine.is_approved(signal)
        assert approved is True
        assert reason == ""

    def test_high_unit_price_fractional_spot_buy_allowed(self):
        """Spot supports fractional BTC, so unit price > equity is not a rejection."""
        engine = RiskEngine(equity=10_000, max_position_size_pct=0.25)
        signal = _make_signal(price=110_000.0)
        approved, reason = engine.is_approved(signal)
        assert approved is True
        assert reason == ""

    def test_requested_notional_over_max_position_size_rejected(self):
        engine = RiskEngine(equity=10_000, max_position_size_pct=0.25)
        signal = _make_signal(price=100.0)
        signal.metadata["notional"] = 3_000.0
        approved, reason = engine.is_approved(signal)
        assert approved is False
        assert "max_position_size" in reason

    def test_rejects_no_stop_loss_in_live(self):
        engine = RiskEngine(equity=10_000, is_live=True)
        signal = _make_signal(stop_loss=None)
        approved, reason = engine.is_approved(signal)
        assert approved is False
        assert "stop_loss" in reason.lower()

    def test_sell_signal_does_not_require_stop_loss_in_live(self):
        engine = RiskEngine(equity=10_000, is_live=True)
        signal = _make_signal(signal_type=SignalType.SELL, stop_loss=None)
        approved, reason = engine.is_approved(signal)
        assert approved is True
        assert reason == ""

    def test_approves_no_stop_loss_in_paper(self):
        engine = RiskEngine(equity=10_000, is_live=False)
        signal = _make_signal(stop_loss=None)
        approved, reason = engine.is_approved(signal)
        assert approved is True

    def test_kill_switch_toggle(self):
        engine = RiskEngine()
        assert engine.kill_switch_active is False
        engine.activate_kill_switch("emergency")
        assert engine.kill_switch_active is True
        engine.deactivate_kill_switch()
        assert engine.kill_switch_active is False

    def test_consecutive_loss_cooldown(self):
        engine = RiskEngine(equity=10_000)
        # Simulate 5 consecutive losses
        for _ in range(5):
            engine.record_trade_result(-50, "BTCUSDT")
        assert engine.consecutive_losses == 5
        signal = _make_signal()
        approved, _ = engine.is_approved(signal)
        assert approved is False

    def test_sell_signal_bypasses_consecutive_loss_cooldown(self):
        engine = RiskEngine(equity=10_000)
        for _ in range(5):
            engine.record_trade_result(-50, "BTCUSDT")
        signal = _make_signal(signal_type=SignalType.SELL, stop_loss=None)
        approved, reason = engine.is_approved(signal)
        assert approved is True
        assert reason == ""

    def test_consecutive_losses_reset_on_win(self):
        engine = RiskEngine(equity=10_000)
        engine.record_trade_result(-50, "BTCUSDT")
        engine.record_trade_result(-50, "BTCUSDT")
        assert engine.consecutive_losses == 2
        engine.record_trade_result(100, "BTCUSDT")
        assert engine.consecutive_losses == 0

    def test_daily_reset(self):
        engine = RiskEngine(equity=10_000)
        engine.daily_pnl = -500
        engine.error_count_today = 5
        engine.reset_daily()
        assert engine.daily_pnl == 0.0
        assert engine.error_count_today == 0

    def test_sell_signal_bypasses_max_positions(self):
        """SELL signals should be allowed even at max positions."""
        engine = RiskEngine(equity=10_000, max_open_positions=2)
        engine.open_positions = 2
        signal = _make_signal(signal_type=SignalType.SELL)
        approved, reason = engine.is_approved(signal)
        assert approved is True
