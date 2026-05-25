"""Report service — generates strategy rankings, P&L reports, and summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.backtesting.metrics import PerformanceMetrics
from app.backtesting.ranking import RankedStrategy
from app.core.logging import get_logger

logger = get_logger(__name__)


class ReportService:
    """Generates human-readable and exportable reports."""

    def generate_ranking_report(self, rankings: list[RankedStrategy]) -> dict[str, Any]:
        """Generate a strategy ranking report."""
        report = {
            "title": "Strategy Ranking Report",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_strategies": len(rankings),
            "rankings": [],
        }

        for r in rankings:
            report["rankings"].append({
                "rank": r.rank,
                "strategy_name": r.strategy_name,
                "composite_score": round(r.composite_score, 4),
                "net_profit": round(r.metrics.net_profit, 2),
                "total_return_pct": round(r.metrics.total_return_pct, 4),
                "max_drawdown_pct": round(r.metrics.max_drawdown_pct, 4),
                "sharpe_ratio": round(r.metrics.sharpe_ratio, 4),
                "sortino_ratio": round(r.metrics.sortino_ratio, 4),
                "profit_factor": round(r.metrics.profit_factor, 4),
                "win_rate": round(r.metrics.win_rate, 4),
                "total_trades": r.metrics.total_trades,
                "score_breakdown": r.score_breakdown,
            })

        return report

    def generate_trade_log(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a trade log report."""
        return {
            "title": "Trade Log",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_trades": len(trades),
            "trades": trades,
        }

    def generate_daily_pnl(
        self, equity_curve: list[float], initial_capital: float
    ) -> dict[str, Any]:
        """Generate a daily P&L summary from equity curve."""
        pnl = equity_curve[-1] - initial_capital if equity_curve else 0.0
        return {
            "title": "Daily P&L",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "initial_capital": initial_capital,
            "current_equity": equity_curve[-1] if equity_curve else initial_capital,
            "net_pnl": round(pnl, 2),
            "pnl_pct": round(pnl / initial_capital, 4) if initial_capital > 0 else 0,
            "equity_curve_length": len(equity_curve),
        }

    def generate_summary(self, metrics: PerformanceMetrics) -> str:
        """Generate a markdown summary string."""
        return (
            f"# {metrics.strategy_name} — Performance Summary\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Net Profit | ${metrics.net_profit:,.2f} |\n"
            f"| Total Return | {metrics.total_return_pct:.2%} |\n"
            f"| Max Drawdown | {metrics.max_drawdown_pct:.2%} |\n"
            f"| Sharpe Ratio | {metrics.sharpe_ratio:.2f} |\n"
            f"| Sortino Ratio | {metrics.sortino_ratio:.2f} |\n"
            f"| Profit Factor | {metrics.profit_factor:.2f} |\n"
            f"| Win Rate | {metrics.win_rate:.1%} |\n"
            f"| Total Trades | {metrics.total_trades} |\n"
            f"| Avg Trade Return | {metrics.avg_trade_return_pct:.2%} |\n"
            f"| Fees Paid | ${metrics.fees_paid:,.2f} |\n"
        )
