"""Performance metrics for backtest evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.backtesting.engine import BacktestResult
from app.core.utils import safe_div


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics for a backtest run."""

    strategy_name: str
    symbol: str
    net_profit: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    win_rate: float
    total_trades: int
    avg_trade_return_pct: float
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    max_consecutive_losses: int
    fees_paid: float
    initial_capital: float
    final_equity: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "net_profit": round(self.net_profit, 2),
            "total_return_pct": round(self.total_return_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "win_rate": round(self.win_rate, 4),
            "total_trades": self.total_trades,
            "avg_trade_return_pct": round(self.avg_trade_return_pct, 4),
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_win_pct": round(self.avg_win_pct, 4),
            "avg_loss_pct": round(self.avg_loss_pct, 4),
            "max_consecutive_losses": self.max_consecutive_losses,
            "fees_paid": round(self.fees_paid, 2),
            "initial_capital": round(self.initial_capital, 2),
            "final_equity": round(self.final_equity, 2),
        }


def calculate_metrics(result: BacktestResult) -> PerformanceMetrics:
    """Calculate comprehensive performance metrics from a backtest result.

    Args:
        result: BacktestResult from a completed backtest.

    Returns:
        PerformanceMetrics with all computed values.
    """
    trades = result.trades
    equity_curve = result.equity_curve

    net_profit = result.final_equity - result.initial_capital
    total_return_pct = safe_div(net_profit, result.initial_capital)

    # Trade analysis
    total_trades = len(trades)
    pnl_pcts = [t.pnl_pct for t in trades]
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate = safe_div(winning_trades, total_trades)

    avg_trade_return = float(np.mean(pnl_pcts)) if pnl_pcts else 0.0
    avg_win = float(np.mean([t.pnl_pct for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0

    # Profit factor
    gross_profit = sum(t.pnl for t in wins) if wins else 0.0
    gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0.0
    profit_factor = safe_div(gross_profit, gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    # Max consecutive losses
    max_consec = 0
    current_consec = 0
    for t in trades:
        if t.pnl <= 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0

    # Max drawdown from equity curve
    max_drawdown_pct = _calculate_max_drawdown(equity_curve)

    # Sharpe and Sortino ratios
    sharpe = _calculate_sharpe(equity_curve)
    sortino = _calculate_sortino(equity_curve)

    return PerformanceMetrics(
        strategy_name=result.strategy_name,
        symbol=result.symbol,
        net_profit=net_profit,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        profit_factor=profit_factor,
        win_rate=win_rate,
        total_trades=total_trades,
        avg_trade_return_pct=avg_trade_return,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        max_consecutive_losses=max_consec,
        fees_paid=result.fees_paid,
        initial_capital=result.initial_capital,
        final_equity=result.final_equity,
    )


def _calculate_max_drawdown(equity_curve: list[float]) -> float:
    """Calculate maximum drawdown as a percentage."""
    if len(equity_curve) < 2:
        return 0.0

    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    drawdown = (arr - peak) / np.where(peak > 0, peak, 1.0)
    return float(abs(np.min(drawdown)))


def _calculate_sharpe(
    equity_curve: list[float],
    risk_free_rate: float = 0.0,
    periods_per_year: float = 365 * 24,
) -> float:
    """Calculate annualized Sharpe ratio from equity curve."""
    if len(equity_curve) < 3:
        return 0.0

    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
    returns = returns[np.isfinite(returns)]

    if len(returns) < 2:
        return 0.0

    excess = returns - risk_free_rate / periods_per_year
    std = float(np.std(excess, ddof=1)) if len(excess) >= 2 else 0.0
    if std == 0:
        return 0.0

    return float(np.mean(excess) / std * np.sqrt(periods_per_year))


def _calculate_sortino(
    equity_curve: list[float],
    risk_free_rate: float = 0.0,
    periods_per_year: float = 365 * 24,
) -> float:
    """Calculate annualized Sortino ratio from equity curve."""
    if len(equity_curve) < 3:
        return 0.0

    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
    returns = returns[np.isfinite(returns)]

    if len(returns) < 2:
        return 0.0

    excess = returns - risk_free_rate / periods_per_year
    downside = excess[excess < 0]

    if len(downside) < 2:
        return float("inf") if float(np.mean(excess)) > 0 else 0.0

    downside_std = float(np.std(downside, ddof=1))
    if downside_std == 0:
        return 0.0

    return float(np.mean(excess) / downside_std * np.sqrt(periods_per_year))
