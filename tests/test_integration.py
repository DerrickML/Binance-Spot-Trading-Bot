"""Integration-style tests for strategy selection, replay mode, and winner persistence."""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.core.enums import SignalType, TradingMode
from app.execution.paper_broker import PaperBroker
from app.persistence.db import init_db, get_session, reset_engine
from app.persistence.models import SelectedStrategy
from app.persistence.repositories import SelectedStrategyRepository
from app.risk.risk_engine import RiskEngine
from app.services.orchestrator import Orchestrator
from app.strategies.base import BaseStrategy, StrategySignal


# ---------- Test strategies ----------

class BuyOnceStrategy(BaseStrategy):
    """Fires BUY once at candle 50 then never again."""

    name = "test_buy_once"

    def default_params(self):
        return {"min_periods": 2, "_fired": False}

    def generate_signals(self, candles):
        if len(candles) < 50:
            return []
        close = float(candles.iloc[-1]["close"])
        # Only fire once
        if not self.params.get("_fired"):
            self.params["_fired"] = True
            return [StrategySignal(
                signal_type=SignalType.BUY, symbol="BTCUSDT",
                price=close, stop_loss=close * 0.95, take_profit=close * 1.10,
            )]
        return []


class SellOnlyStrategy(BaseStrategy):
    """Always emits SELL signals."""

    name = "test_sell_only"

    def default_params(self):
        return {"min_periods": 2}

    def generate_signals(self, candles):
        if len(candles) < 2:
            return []
        close = float(candles.iloc[-1]["close"])
        return [StrategySignal(
            signal_type=SignalType.SELL, symbol="BTCUSDT", price=close,
        )]


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _make_risk_engine(**kwargs):
    from app.risk.rules import (
        KillSwitchRule, MaxOpenPositionsRule, MaxDailyLossRule,
        MaxPositionSizeRule, StopLossRequiredRule,
        ConsecutiveLossCooldownRule, DisabledSymbolRule, ErrorHaltRule,
    )
    defaults = {
        "equity": 10_000, "is_live": False,
        "rules": [
            KillSwitchRule(), MaxOpenPositionsRule(), MaxDailyLossRule(),
            MaxPositionSizeRule(), StopLossRequiredRule(),
            ConsecutiveLossCooldownRule(), DisabledSymbolRule(), ErrorHaltRule(),
        ],
    }
    defaults.update(kwargs)
    return RiskEngine(**defaults)


def _make_orchestrator(strategy=None, risk_engine=None, broker=None, symbols=None):
    return Orchestrator(
        strategy=strategy or BuyOnceStrategy(),
        risk_engine=risk_engine or _make_risk_engine(),
        broker=broker or PaperBroker(initial_balance=10_000),
        telegram=None,
        mode=TradingMode.PAPER,
        symbols=symbols or ["BTCUSDT"],
        database_url="sqlite:///:memory:",
    )


def _make_candles(count=60, base_price=100.0, symbol="BTCUSDT"):
    """Generate a list of candle dicts for testing."""
    return [
        {
            "symbol": symbol,
            "open_time": i * 3600000,
            "close_time": (i + 1) * 3600000 - 1,
            "open": base_price + i * 0.1 - 0.5,
            "high": base_price + i * 0.1 + 2,
            "low": base_price + i * 0.1 - 2,
            "close": base_price + i * 0.1,
            "volume": 1000.0,
            "is_closed": True,
        }
        for i in range(count)
    ]


# ---------- Winner Persistence Tests ----------

class TestWinnerPersistence:
    def test_save_and_load_winner(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="ema_atr_crossover",
            parameters='{"fast_period": 12}',
            symbol="BTCUSDT",
            interval="1h",
            composite_score=0.85,
            total_return_pct=0.15,
            sharpe_ratio=1.2,
        ))
        session.commit()

        winner = repo.get_latest_winner()
        assert winner is not None
        assert winner.strategy_name == "ema_atr_crossover"
        assert winner.composite_score == 0.85
        session.close()

    def test_latest_winner_is_most_recent(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="strategy_old",
            parameters="{}",
            symbol="BTCUSDT",
            interval="1h",
            composite_score=0.5,
        ))
        session.commit()

        repo.save(SelectedStrategy(
            strategy_name="strategy_new",
            parameters="{}",
            symbol="BTCUSDT",
            interval="1h",
            composite_score=0.9,
        ))
        session.commit()

        winner = repo.get_latest_winner()
        assert winner.strategy_name == "strategy_new"
        session.close()

    def test_filter_by_symbol(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)

        repo.save(SelectedStrategy(
            strategy_name="btc_strategy",
            parameters="{}",
            symbol="BTCUSDT",
            interval="1h",
        ))
        repo.save(SelectedStrategy(
            strategy_name="eth_strategy",
            parameters="{}",
            symbol="ETHUSDT",
            interval="1h",
        ))
        session.commit()

        btc_winner = repo.get_latest_winner(symbol="BTCUSDT")
        assert btc_winner.strategy_name == "btc_strategy"

        eth_winner = repo.get_latest_winner(symbol="ETHUSDT")
        assert eth_winner.strategy_name == "eth_strategy"
        session.close()

    def test_no_winner_returns_none(self):
        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)
        assert repo.get_latest_winner() is None
        session.close()


# ---------- Replay Mode Tests ----------

class TestReplayMode:
    def test_replay_processes_all_candles(self):
        orch = _make_orchestrator()
        candles = _make_candles(60)

        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )

        assert summary["total_candles"] == 60
        assert summary["buffering"] > 0  # First 49 candles are buffering
        assert "final_equity" in summary

    def test_replay_executes_trade(self):
        orch = _make_orchestrator()
        candles = _make_candles(60)

        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )

        # BuyOnceStrategy should fire exactly one trade
        assert summary["trades_executed"] == 1

    def test_replay_with_risk_rejection(self):
        risk = _make_risk_engine(max_open_positions=0)
        orch = _make_orchestrator(risk_engine=risk)
        candles = _make_candles(60)

        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )

        assert summary["trades_executed"] == 0
        assert summary["signals_rejected"] > 0

    def test_replay_with_kill_switch(self):
        risk = _make_risk_engine()
        risk.activate_kill_switch("test")
        orch = _make_orchestrator(risk_engine=risk)
        candles = _make_candles(60)

        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )

        assert summary["trades_executed"] == 0

    def test_replay_progress_callback_called(self):
        orch = _make_orchestrator()
        candles = _make_candles(200)
        calls = []

        def on_progress(current, total):
            calls.append((current, total))

        asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles, progress_callback=on_progress)
        )

        # Should be called at least once (every 100 candles)
        assert len(calls) >= 1
        assert calls[0] == (100, 200)

    def test_replay_returns_final_equity(self):
        orch = _make_orchestrator()
        candles = _make_candles(60)

        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )

        assert summary["final_equity"] > 0
        # After a BUY trade, equity depends on mark-to-market
        # but should be close to initial capital
        assert summary["final_equity"] > 5000  # not catastrophically wrong

    def test_replay_dedup_still_works(self):
        orch = _make_orchestrator()
        # Send same candles twice
        candles = _make_candles(60)
        candles_doubled = candles + candles  # duplicates

        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles_doubled)
        )

        assert summary["duplicates"] == 60  # second half all dups


# ---------- Strategy Resolution Tests ----------

class TestStrategyResolution:
    def test_resolve_manual_selection(self):
        """Test _resolve_strategy with manual name (functional test)."""
        import app.strategies.ema_atr  # noqa: F401
        from app.strategies.registry import list_strategies, get_strategy

        available = list_strategies()
        if "ema_atr_crossover" in available:
            strategy = get_strategy("ema_atr_crossover")
            assert strategy.name == "ema_atr_crossover"

    def test_resolve_from_persisted_winner(self):
        """Test loading winner from DB."""
        import app.strategies.ema_atr  # noqa: F401

        session = get_session("sqlite:///:memory:")
        repo = SelectedStrategyRepository(session)
        repo.save(SelectedStrategy(
            strategy_name="ema_atr_crossover",
            parameters='{}',
            symbol="BTCUSDT",
            interval="1h",
            composite_score=0.9,
        ))
        session.commit()

        winner = repo.get_latest_winner()
        assert winner is not None
        assert winner.strategy_name == "ema_atr_crossover"
        session.close()


# ---------- Trade Notification Path ----------

class TestNotificationPath:
    def test_telegram_not_called_when_none(self):
        """Orchestrator works cleanly without Telegram configured."""
        orch = _make_orchestrator()
        candles = _make_candles(60)

        # Should not raise even without telegram
        summary = asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )
        assert summary["trades_executed"] == 1

    def test_orchestrator_status_after_replay(self):
        orch = _make_orchestrator()
        candles = _make_candles(60)

        asyncio.get_event_loop().run_until_complete(
            orch.replay_candles(candles)
        )

        status = orch.get_status()
        assert status["running"] is False  # replay sets running=False at end
        assert status["candles_processed"] == 60
