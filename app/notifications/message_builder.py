"""Message builder — structured Telegram message templates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_startup_message(mode: str, symbols: list[str]) -> str:
    return (
        f"🟢 <b>Trading Bot Started</b>\n\n"
        f"⏰ {_now()}\n"
        f"📋 Mode: <b>{mode.upper()}</b>\n"
        f"📊 Symbols: {', '.join(symbols)}\n\n"
        f"System initialized and ready."
    )


def build_shutdown_message(reason: str = "Normal shutdown") -> str:
    return (
        f"🔴 <b>Trading Bot Stopped</b>\n\n"
        f"⏰ {_now()}\n"
        f"📋 Reason: {reason}"
    )


def build_trade_opened_message(trade: dict[str, Any]) -> str:
    side_emoji = "🟢" if trade.get("side") == "BUY" else "🔴"
    return (
        f"{side_emoji} <b>Trade Opened</b>\n\n"
        f"📊 {trade.get('symbol', 'N/A')}\n"
        f"📋 Side: {trade.get('side', 'N/A')}\n"
        f"💰 Entry: {trade.get('entry_price', 0):.4f}\n"
        f"📦 Qty: {trade.get('quantity', 0):.6f}\n"
        f"🛡 SL: {trade.get('stop_loss', 'N/A')}\n"
        f"🎯 TP: {trade.get('take_profit', 'N/A')}\n"
        f"🤖 Strategy: {trade.get('strategy', 'N/A')}\n"
        f"⏰ {_now()}"
    )


def build_trade_closed_message(trade: dict[str, Any]) -> str:
    pnl = trade.get("pnl", 0)
    pnl_emoji = "✅" if pnl >= 0 else "❌"
    return (
        f"{pnl_emoji} <b>Trade Closed</b>\n\n"
        f"📊 {trade.get('symbol', 'N/A')}\n"
        f"💰 Entry: {trade.get('entry_price', 0):.4f}\n"
        f"💰 Exit: {trade.get('exit_price', 0):.4f}\n"
        f"📈 PnL: {pnl:+.2f} ({trade.get('pnl_pct', 0):+.2%})\n"
        f"📋 Reason: {trade.get('exit_reason', 'N/A')}\n"
        f"⏰ {_now()}"
    )


def build_stop_loss_message(trade: dict[str, Any]) -> str:
    return (
        f"🛑 <b>Stop Loss Hit</b>\n\n"
        f"📊 {trade.get('symbol', 'N/A')}\n"
        f"💰 Entry: {trade.get('entry_price', 0):.4f}\n"
        f"💰 SL Price: {trade.get('stop_loss', 0):.4f}\n"
        f"📉 Loss: {trade.get('pnl', 0):+.2f}\n"
        f"⏰ {_now()}"
    )


def build_error_message(error: str, component: str = "") -> str:
    return (
        f"⚠️ <b>Error</b>\n\n"
        f"🔧 Component: {component or 'Unknown'}\n"
        f"❌ {error}\n"
        f"⏰ {_now()}"
    )


def build_emergency_halt_message(reason: str) -> str:
    return (
        f"🚨 <b>EMERGENCY HALT</b> 🚨\n\n"
        f"All trading has been stopped.\n"
        f"📋 Reason: {reason}\n\n"
        f"⏰ {_now()}\n"
        f"⚠️ Manual intervention required."
    )


def build_daily_summary_message(summary: dict[str, Any]) -> str:
    return (
        f"📊 <b>Daily Summary</b>\n\n"
        f"⏰ {_now()}\n"
        f"💰 Equity: ${summary.get('equity', 0):,.2f}\n"
        f"📈 Daily PnL: {summary.get('daily_pnl', 0):+.2f}\n"
        f"📊 Trades: {summary.get('trades_today', 0)}\n"
        f"✅ Wins: {summary.get('wins', 0)} | ❌ Losses: {summary.get('losses', 0)}\n"
        f"📉 Open Positions: {summary.get('open_positions', 0)}"
    )


def build_backtest_winner_message(winner: dict[str, Any]) -> str:
    return (
        f"🏆 <b>Backtest Winner</b>\n\n"
        f"🤖 Strategy: {winner.get('strategy_name', 'N/A')}\n"
        f"📊 Symbol: {winner.get('symbol', 'N/A')}\n"
        f"📈 Return: {winner.get('total_return_pct', 0):.2%}\n"
        f"📉 Max DD: {winner.get('max_drawdown_pct', 0):.2%}\n"
        f"📊 Sharpe: {winner.get('sharpe_ratio', 0):.2f}\n"
        f"📊 Win Rate: {winner.get('win_rate', 0):.1%}\n"
        f"📋 Trades: {winner.get('total_trades', 0)}\n"
        f"⏰ {_now()}"
    )
