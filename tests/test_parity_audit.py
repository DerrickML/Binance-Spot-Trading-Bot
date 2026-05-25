"""Tests for Phase 12: parity auditor, runtime viability, and approval downgrades."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.persistence.db import init_db, reset_engine
from app.persistence.models import ApprovedCombination
from app.backtesting.parity_auditor import (
    AuditResult,
    MIN_EXECUTED_TRADES,
    MIN_EXECUTION_RATIO,
    MIN_RUNTIME_NET_PNL,
    ReplayDiagnostics,
    audit_combination,
)
from app.core.enums import SignalType
from app.strategies.base import BaseStrategy, StrategySignal


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def reset_db():
    import app.strategies.bollinger_mean_reversion  # noqa: F401

    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_combo(symbol="BTCUSDT", interval="15m", strategy="bollinger_mean_reversion",
                params=None, approved=True, robustness=0.415, pass_rate=0.56):
    """Create an ApprovedCombination for testing."""
    return ApprovedCombination(
        strategy_name=strategy,
        parameters=json.dumps(params or {"bb_period": 20, "bb_std_dev": 2.0, "cooldown_bars": 6}),
        symbol=symbol,
        interval=interval,
        approved=approved,
        robustness_score=robustness,
        pass_rate=pass_rate,
        regime_tradable=True,
        regime_state="ranging",
    )


def _make_candles(symbol, count=100, base_price=50000, trend=0):
    """Create candle dicts."""
    candles = []
    for i in range(count):
        price = base_price + i * trend
        candles.append({
            "symbol": symbol,
            "open_time": i * 3600000,
            "close_time": (i + 1) * 3600000,
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000,
            "is_closed": True,
        })
    return candles


class TestReplayDiagnostics:
    """Verify ReplayDiagnostics computed properties."""

    def test_effective_candles(self):
        d = ReplayDiagnostics(total_candles=100, buffering=10, duplicates=5)
        assert d.effective_candles == 85

    def test_execution_ratio(self):
        d = ReplayDiagnostics(total_candles=100, buffering=10, trades_executed=9)
        assert d.execution_ratio == pytest.approx(9 / 90, abs=0.01)

    def test_execution_ratio_zero_effective(self):
        d = ReplayDiagnostics(total_candles=10, buffering=10)
        assert d.execution_ratio == 0.0

    def test_signal_ratio(self):
        d = ReplayDiagnostics(
            total_candles=100, buffering=10,
            trades_executed=5, lifecycle_blocked=20, risk_rejected=15, order_rejected=0,
        )
        assert d.signal_ratio == pytest.approx(40 / 90, abs=0.01)


class TestAuditCombination:
    """Verify audit_combination runs orchestrator replay and classifies correctly."""

    def test_audit_with_no_candles_flagged(self):
        """No candle data → flagged."""
        combo = _make_combo()
        result = audit_combination(combo, [])
        assert result.verdict == "flagged"
        assert "no_candle_data" in result.viability_reasons

    def test_audit_with_short_candles(self):
        """Few candles → likely buffering only, no trades → downgraded."""
        combo = _make_combo()
        candles = _make_candles("BTCUSDT", count=5)
        result = audit_combination(combo, candles)
        # With only 5 candles, orchestrator buffers almost all
        assert result.diagnostics.total_candles == 5
        assert result.verdict in ("downgraded", "flagged")

    def test_audit_preserves_statistical_info(self):
        """Audit should carry over statistical approval info."""
        combo = _make_combo(robustness=0.6, pass_rate=0.8)
        candles = _make_candles("BTCUSDT", count=5)
        result = audit_combination(combo, candles)
        assert result.robustness_score == pytest.approx(0.6)
        assert result.pass_rate == pytest.approx(0.8)
        assert result.statistically_approved is True

    def test_negative_runtime_pnl_downgrades_approval(self, monkeypatch):
        """Runtime-losing combinations should not remain approved for paper routing."""

        class BuyThenSellLowerStrategy(BaseStrategy):
            name = "test_buy_then_sell_lower"

            def default_params(self):
                return {"min_periods": 2}

            def generate_signals(self, candles):
                close = float(candles.iloc[-1]["close"])
                if len(candles) == 50:
                    return [StrategySignal(
                        signal_type=SignalType.BUY,
                        symbol="BTCUSDT",
                        price=close,
                    )]
                if len(candles) == 51:
                    return [StrategySignal(
                        signal_type=SignalType.SELL,
                        symbol="BTCUSDT",
                        price=close,
                    )]
                return []

        def fake_get_strategy(_name, params=None):
            return BuyThenSellLowerStrategy(params=params)

        import app.backtesting.parity_auditor as parity_auditor

        monkeypatch.setattr(parity_auditor, "get_strategy", fake_get_strategy)

        combo = _make_combo(strategy="test_buy_then_sell_lower")
        candles = _make_candles("BTCUSDT", count=60, base_price=100)
        candles[50]["open"] = 90
        candles[50]["high"] = 91
        candles[50]["low"] = 89
        candles[50]["close"] = 90

        result = audit_combination(combo, candles)

        assert result.diagnostics.trades_executed == 2
        assert result.diagnostics.net_pnl < MIN_RUNTIME_NET_PNL
        assert result.verdict == "downgraded"
        assert any("negative_runtime_pnl" in r for r in result.viability_reasons)


class TestViabilityClassification:
    """Verify verdict classification logic."""

    def test_approved_when_trades_exist(self):
        """A combination with sufficient trades should remain approved."""
        d = ReplayDiagnostics(
            total_candles=1000, buffering=50, trades_executed=10,
            lifecycle_blocked=20, risk_rejected=100,
        )
        # Execution ratio: 10 / 950 ≈ 0.0105 > 0.01
        assert d.trades_executed >= MIN_EXECUTED_TRADES
        assert d.execution_ratio >= MIN_EXECUTION_RATIO

    def test_downgraded_zero_trades(self):
        """Zero trades → should be downgraded."""
        d = ReplayDiagnostics(
            total_candles=1000, buffering=50, trades_executed=0,
            lifecycle_blocked=500, risk_rejected=400,
        )
        assert d.trades_executed < MIN_EXECUTED_TRADES

    def test_downgraded_low_execution_ratio(self):
        """Very low execution ratio → should be downgraded."""
        d = ReplayDiagnostics(
            total_candles=100000, buffering=100, trades_executed=1,
            lifecycle_blocked=50000, risk_rejected=49000,
        )
        # 1 / 99900 ≈ 0.00001 < 0.0005
        assert d.execution_ratio < MIN_EXECUTION_RATIO


class TestSummaryDict:
    """Verify serialization."""

    def test_diagnostics_summary(self):
        d = ReplayDiagnostics(
            total_candles=100, buffering=10, trades_executed=5,
            initial_capital=10000, final_equity=10500, net_pnl=500,
        )
        s = d.summary_dict()
        assert s["total_candles"] == 100
        assert s["trades_executed"] == 5
        assert s["net_pnl"] == 500

    def test_audit_result_summary(self):
        r = AuditResult(
            symbol="BNBUSDT", interval="4h",
            strategy_name="bollinger_mean_reversion",
            params={"bb_period": 30},
            runtime_viable=True,
            verdict="approved",
        )
        s = r.summary_dict()
        assert s["symbol"] == "BNBUSDT"
        assert s["verdict"] == "approved"
        assert "diagnostics" in s
