"""Tests for Telegram message formatting."""

from __future__ import annotations

from app.notifications.message_builder import (
    build_startup_message,
    build_shutdown_message,
    build_trade_opened_message,
    build_trade_closed_message,
    build_stop_loss_message,
    build_error_message,
    build_emergency_halt_message,
    build_daily_summary_message,
    build_backtest_winner_message,
)


class TestMessageBuilder:
    def test_startup_message(self):
        msg = build_startup_message("paper", ["BTCUSDT", "ETHUSDT"])
        assert "Started" in msg
        assert "BTCUSDT" in msg
        assert "PAPER" in msg

    def test_shutdown_message(self):
        msg = build_shutdown_message("Manual stop")
        assert "Stopped" in msg
        assert "Manual stop" in msg

    def test_trade_opened_message(self):
        msg = build_trade_opened_message({
            "symbol": "BTCUSDT", "side": "BUY", "entry_price": 50000,
            "quantity": 0.1, "stop_loss": 49000, "take_profit": 52000,
            "strategy": "ema_atr",
        })
        assert "Trade Opened" in msg
        assert "BTCUSDT" in msg
        assert "BUY" in msg

    def test_trade_closed_profit(self):
        msg = build_trade_closed_message({
            "symbol": "BTCUSDT", "entry_price": 50000, "exit_price": 52000,
            "pnl": 200, "pnl_pct": 0.04, "exit_reason": "take_profit",
        })
        assert "Trade Closed" in msg
        assert "+200" in msg

    def test_trade_closed_loss(self):
        msg = build_trade_closed_message({
            "symbol": "BTCUSDT", "entry_price": 50000, "exit_price": 49000,
            "pnl": -100, "pnl_pct": -0.02, "exit_reason": "stop_loss",
        })
        assert "❌" in msg

    def test_stop_loss_message(self):
        msg = build_stop_loss_message({
            "symbol": "BTCUSDT", "entry_price": 50000,
            "stop_loss": 49000, "pnl": -100,
        })
        assert "Stop Loss" in msg

    def test_error_message(self):
        msg = build_error_message("Connection timeout", "websocket")
        assert "Error" in msg
        assert "websocket" in msg

    def test_emergency_halt_message(self):
        msg = build_emergency_halt_message("Max daily loss breached")
        assert "EMERGENCY HALT" in msg
        assert "Manual intervention" in msg

    def test_daily_summary(self):
        msg = build_daily_summary_message({
            "equity": 10500, "daily_pnl": 500,
            "trades_today": 5, "wins": 3, "losses": 2,
            "open_positions": 1,
        })
        assert "Daily Summary" in msg
        assert "10,500" in msg

    def test_backtest_winner_message(self):
        msg = build_backtest_winner_message({
            "strategy_name": "ema_atr_crossover",
            "symbol": "BTCUSDT",
            "total_return_pct": 0.15,
            "max_drawdown_pct": 0.08,
            "sharpe_ratio": 1.5,
            "win_rate": 0.6,
            "total_trades": 20,
        })
        assert "Winner" in msg
        assert "ema_atr" in msg
