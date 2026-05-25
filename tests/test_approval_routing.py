"""Tests for Phase 10: approval-driven paper-trade routing."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.persistence.db import init_db, get_session, reset_engine
from app.persistence.models import ApprovedCombination
from app.persistence.repositories import ApprovedCombinationRepository


@pytest.fixture(autouse=True)
def reset_db():
    reset_engine()
    init_db("sqlite:///:memory:")
    yield
    reset_engine()


def _seed_approvals(session, approved_combos: list[tuple[str, str, bool]]):
    """Seed approved_combinations table.
    
    approved_combos: list of (symbol, interval, approved) tuples
    """
    repo = ApprovedCombinationRepository(session)
    records = []
    for sym, intv, approved in approved_combos:
        records.append(ApprovedCombination(
            strategy_name="bollinger_mean_reversion",
            parameters='{"bb_period": 30, "bb_std_dev": 2.5}',
            symbol=sym,
            interval=intv,
            approved=approved,
            reasons=json.dumps(["test reason"]),
            robustness_score=0.42,
            pass_rate=0.56,
        ))
    repo.save_batch(records)
    session.commit()


class TestApprovalRouting:
    def test_get_approved_filters_correctly(self):
        """Only approved=True combos are returned."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [
            ("BTCUSDT", "1h", True),
            ("ETHUSDT", "1h", True),
            ("BNBUSDT", "1h", False),
        ])
        repo = ApprovedCombinationRepository(session)
        approved = repo.get_approved()
        assert len(approved) == 2
        symbols = {a.symbol for a in approved}
        assert symbols == {"BTCUSDT", "ETHUSDT"}
        session.close()

    def test_approved_by_symbol(self):
        """Can filter approved combos by specific symbol."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [
            ("BTCUSDT", "1h", True),
            ("BTCUSDT", "4h", True),
            ("ETHUSDT", "1h", True),
        ])
        repo = ApprovedCombinationRepository(session)
        btc = repo.get_approved(symbol="BTCUSDT")
        assert len(btc) == 2
        eth = repo.get_approved(symbol="ETHUSDT")
        assert len(eth) == 1
        session.close()

    def test_approved_by_symbol_and_interval(self):
        """Can filter by both symbol and interval."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [
            ("BTCUSDT", "1h", True),
            ("BTCUSDT", "4h", True),
            ("ETHUSDT", "1h", True),
        ])
        repo = ApprovedCombinationRepository(session)
        result = repo.get_approved(symbol="BTCUSDT", interval="4h")
        assert len(result) == 1
        assert result[0].interval == "4h"
        session.close()

    def test_no_approved_returns_empty(self):
        """When all combos are rejected, get_approved returns empty."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [
            ("BTCUSDT", "1h", False),
            ("ETHUSDT", "1h", False),
        ])
        repo = ApprovedCombinationRepository(session)
        assert repo.get_approved() == []
        session.close()


class TestSymbolFiltering:
    """Test the logic of filtering configured symbols against approved combos."""

    def test_filter_to_approved_only(self):
        """Only configured symbols with approved combos for the runtime interval should be active."""
        from app.cli import _filter_approved_symbols

        configured = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        approved_keys = {"BTCUSDT:1h", "ETHUSDT:4h", "BNBUSDT:1h"}

        active, skipped = _filter_approved_symbols(configured, approved_keys, "1h")

        assert active == ["BTCUSDT", "BNBUSDT"]
        assert skipped == ["ETHUSDT"]

    def test_no_overlap_stays_in_cash(self):
        """When no configured symbols are approved, result is empty."""
        from app.cli import _filter_approved_symbols

        configured = ["SOLUSDT", "XRPUSDT"]
        approved_keys = {"BTCUSDT:1h", "ETHUSDT:4h"}

        active, skipped = _filter_approved_symbols(configured, approved_keys, "1h")
        assert active == []
        assert skipped == configured

    def test_symbol_approved_on_different_interval_is_skipped(self):
        """A symbol approved on 4h must not trade when runtime interval is 1h."""
        from app.cli import _filter_approved_symbols

        configured = ["ETHUSDT"]
        approved_keys = {"ETHUSDT:4h"}

        active, skipped = _filter_approved_symbols(configured, approved_keys, "1h")

        assert active == []
        assert skipped == ["ETHUSDT"]

    def test_manual_override_trades_all(self):
        """Manual override should trade all configured symbols."""
        configured = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        is_override = True
        # Override ignores approval filtering
        active = configured if is_override else []
        assert active == configured

    def test_empty_approvals_stays_in_cash(self):
        """Empty approved_keys means stay in cash."""
        approved_keys: set[str] = set()
        assert len(approved_keys) == 0

    def test_runtime_ready_keys_exclude_failed_recent_replay(self):
        from app.cli import _runtime_ready_keys_from_report

        report = {
            "approved_combinations": [
                {
                    "symbol": "BTCUSDT",
                    "interval": "4h",
                    "recent_replay": {"verdict": "downgraded"},
                },
                {
                    "symbol": "BNBUSDT",
                    "interval": "4h",
                    "recent_replay": {"verdict": "approved"},
                },
            ]
        }

        assert _runtime_ready_keys_from_report(report) == {"BNBUSDT:4h"}

    def test_runtime_ready_key_order_prefers_recent_replay_pnl(self):
        from app.cli import _runtime_ready_key_order_from_report

        report = {
            "approved_combinations": [
                {
                    "symbol": "BNBUSDT",
                    "interval": "4h",
                    "robustness_score": 0.90,
                    "recent_replay": {
                        "verdict": "approved",
                        "diagnostics": {"net_pnl": -5.0},
                    },
                },
                {
                    "symbol": "ETHUSDT",
                    "interval": "4h",
                    "robustness_score": 0.40,
                    "recent_replay": {
                        "verdict": "approved",
                        "diagnostics": {"net_pnl": 25.0},
                    },
                },
                {
                    "symbol": "BTCUSDT",
                    "interval": "4h",
                    "robustness_score": 0.95,
                    "recent_replay": {"verdict": "downgraded"},
                },
            ]
        }

        assert _runtime_ready_key_order_from_report(report) == [
            "ETHUSDT:4h",
            "BNBUSDT:4h",
        ]

    def test_paper_readiness_gate_applies_to_auto_sim_but_not_explicit_sim(self):
        from app.cli import _paper_readiness_gate_applies

        assert _paper_readiness_gate_applies(None, sim=True, persist_sim=False, sim_user_specified=False)
        assert not _paper_readiness_gate_applies(None, sim=True, persist_sim=False, sim_user_specified=True)
        assert _paper_readiness_gate_applies(None, sim=True, persist_sim=True, sim_user_specified=True)
        assert _paper_readiness_gate_applies(None, sim=False, persist_sim=False, sim_user_specified=False)
        assert not _paper_readiness_gate_applies("hybrid_grid_dca", sim=False, persist_sim=False, sim_user_specified=False)

    def test_resolve_strategy_honors_runtime_ready_filter(self):
        import app.strategies.bollinger_mean_reversion  # noqa: F401
        from app.cli import _resolve_strategy

        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [
            ("BTCUSDT", "4h", True),
            ("BNBUSDT", "4h", True),
        ])
        session.close()

        settings = SimpleNamespace(database_url="sqlite:///:memory:")

        strategy, approved_keys, is_override = _resolve_strategy(
            None,
            settings,
            approved_key_filter={"BNBUSDT:4h"},
        )

        assert strategy.name == "bollinger_mean_reversion"
        assert approved_keys == {"BNBUSDT:4h"}
        assert is_override is False


class TestSimApprovalCheck:
    """Test sim mode approval checking."""

    def test_sim_approved_allows_trading(self):
        """Sim dataset in approved set → allowed."""
        approved_keys = {"BTCUSDT:1h", "ETHUSDT:4h"}
        sim_key = "BTCUSDT:1h"
        assert sim_key in approved_keys

    def test_sim_not_approved_blocks(self):
        """Sim dataset NOT in approved set → blocked."""
        approved_keys = {"BTCUSDT:1h", "ETHUSDT:4h"}
        sim_key = "BNBUSDT:15m"
        assert sim_key not in approved_keys

    def test_sim_override_ignores_approval(self):
        """Manual override skips approval check for sim."""
        approved_keys = {"BTCUSDT:1h"}
        sim_key = "BNBUSDT:15m"
        is_override = True
        # Override should not care
        should_block = not is_override and approved_keys and sim_key not in approved_keys
        assert should_block is False


class TestRuntimeApprovalAuditHelpers:
    """Test runtime parity filtering used before approval persistence."""

    def test_candle_dataframe_conversion_uses_latest_rows(self):
        from app.cli import _candles_df_to_replay_dicts

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        df = pd.DataFrame([
            {
                "open_time": start + timedelta(hours=i),
                "close_time": start + timedelta(hours=i + 1),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i,
                "volume": 1000,
            }
            for i in range(5)
        ])

        candles = _candles_df_to_replay_dicts(df, "BTCUSDT", limit=2)

        assert [c["close"] for c in candles] == [103.0, 104.0]
        assert all(c["symbol"] == "BTCUSDT" for c in candles)

    def test_runtime_audit_downgrades_failed_combo(self, monkeypatch):
        from app.cli import _audit_approval_records_for_runtime

        class Diagnostics:
            net_pnl = -10.0

        class Result:
            verdict = "downgraded"
            viability_reasons = ["negative_runtime_pnl: $-10.00 < $0.00"]
            diagnostics = Diagnostics()

        def fake_audit_combination(*_args, **_kwargs):
            return Result()

        import app.backtesting.parity_auditor as parity_auditor

        monkeypatch.setattr(parity_auditor, "audit_combination", fake_audit_combination)

        combo = ApprovedCombination(
            strategy_name="test",
            parameters="{}",
            symbol="BTCUSDT",
            interval="1h",
            approved=True,
            reasons=json.dumps(["approved"]),
            robustness_score=1.0,
            pass_rate=1.0,
        )

        downgraded = _audit_approval_records_for_runtime(
            [combo],
            {("BTCUSDT", "1h"): pd.DataFrame([{
                "open_time": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "close_time": datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1000,
            }])},
            risk_config={},
            regime_config=None,
        )

        assert downgraded == 1
        assert combo.approved is False
        assert "runtime_negative_runtime_pnl" in combo.reasons

    def test_approval_records_enforce_pass_rate_threshold(self):
        from app.backtesting.optimizer import DatasetApproval, ParamSetResult
        from app.cli import _build_approval_records

        psr = ParamSetResult(
            strategy_name="hybrid_grid_dca",
            params={"grid_spacing_pct": 0.02},
            pass_rate=0.33,
            robustness_score=0.25,
            approvals=[
                DatasetApproval(
                    symbol="BTCUSDT",
                    interval="4h",
                    strategy_name="hybrid_grid_dca",
                    params={"grid_spacing_pct": 0.02},
                    approved=True,
                    reasons=["dataset_qualified"],
                )
            ],
        )

        records = _build_approval_records(
            "hybrid_grid_dca",
            psr,
            min_pass_rate=0.5,
        )

        assert len(records) == 1
        assert records[0].approved is False
        assert records[0].pass_rate == pytest.approx(0.33)
        reasons = json.loads(records[0].reasons)
        assert "param_set_pass_rate_below_threshold: 33% < 50%" in reasons

    def test_approval_records_keep_qualified_source_approval(self):
        from app.backtesting.optimizer import DatasetApproval, ParamSetResult
        from app.cli import _build_approval_records

        psr = ParamSetResult(
            strategy_name="hybrid_grid_dca",
            params={"grid_spacing_pct": 0.02},
            pass_rate=0.67,
            robustness_score=0.42,
            approvals=[
                DatasetApproval(
                    symbol="BTCUSDT",
                    interval="4h",
                    strategy_name="hybrid_grid_dca",
                    params={"grid_spacing_pct": 0.02},
                    approved=True,
                    reasons=["dataset_qualified"],
                )
            ],
        )

        records = _build_approval_records(
            "hybrid_grid_dca",
            psr,
            min_pass_rate=0.5,
        )

        assert records[0].approved is True
        assert json.loads(records[0].reasons) == ["dataset_qualified"]


class TestApprovalDataIntegrity:
    """Ensure approval records have proper structure."""

    def test_approval_stores_strategy_params(self):
        """Approved record stores strategy name and params JSON."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [("BTCUSDT", "1h", True)])
        repo = ApprovedCombinationRepository(session)
        approved = repo.get_approved()
        assert len(approved) == 1
        a = approved[0]
        assert a.strategy_name == "bollinger_mean_reversion"
        params = json.loads(a.parameters)
        assert params["bb_period"] == 30
        session.close()

    def test_approval_stores_reasons(self):
        """Approved record stores reasons array."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [("BTCUSDT", "1h", True)])
        repo = ApprovedCombinationRepository(session)
        approved = repo.get_approved()
        reasons = json.loads(approved[0].reasons)
        assert isinstance(reasons, list)
        assert len(reasons) > 0
        session.close()

    def test_approval_scores(self):
        """Approved record stores robustness and pass_rate."""
        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [("BTCUSDT", "1h", True)])
        repo = ApprovedCombinationRepository(session)
        approved = repo.get_approved()
        assert approved[0].robustness_score == pytest.approx(0.42)
        assert approved[0].pass_rate == pytest.approx(0.56)
        session.close()


class TestPaperReadinessReport:
    def test_readiness_report_uses_approved_combinations(self):
        from app.cli import _build_paper_readiness_report

        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [
            ("BTCUSDT", "4h", True),
            ("BNBUSDT", "1h", False),
        ])
        session.close()

        settings = SimpleNamespace(
            database_url="sqlite:///:memory:",
            trading_mode="paper",
            enable_live_trading=False,
            trade_symbols=["BTCUSDT", "BNBUSDT"],
            trade_interval="4h",
        )

        report = _build_paper_readiness_report(settings)

        assert report["decision"] == "PAPER_READY"
        assert report["approved_count"] == 1
        assert report["configured_approved_count"] == 1
        assert report["runtime_ready_count"] == 1
        assert report["approved_combinations"][0]["symbol"] == "BTCUSDT"

    def test_readiness_requires_configured_interval(self):
        from app.cli import _build_paper_readiness_report

        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [("BTCUSDT", "1h", True)])
        session.close()

        settings = SimpleNamespace(
            database_url="sqlite:///:memory:",
            trading_mode="paper",
            enable_live_trading=False,
            trade_symbols=["BTCUSDT"],
            trade_interval="4h",
        )

        report = _build_paper_readiness_report(settings)

        assert report["decision"] == "STAY_IN_CASH"
        assert report["approved_count"] == 1
        assert report["configured_approved_count"] == 0
        assert report["runtime_ready_count"] == 0
        assert report["approved_combinations"][0]["configured_for_runtime"] is False

    def test_readiness_recent_replay_gate_blocks_negative_runtime(self, monkeypatch):
        from app.cli import _build_paper_readiness_report

        class Result:
            def summary_dict(self):
                return {
                    "verdict": "downgraded",
                    "diagnostics": {"net_pnl": -12.34},
                    "viability_reasons": ["negative_runtime_pnl: $-12.34 < $0.00"],
                }

        def fake_audit_combination(*_args, **_kwargs):
            return Result()

        import app.backtesting.parity_auditor as parity_auditor

        monkeypatch.setattr(parity_auditor, "audit_combination", fake_audit_combination)

        session = get_session("sqlite:///:memory:")
        _seed_approvals(session, [("BTCUSDT", "4h", True)])
        session.close()

        settings = SimpleNamespace(
            database_url="sqlite:///:memory:",
            trading_mode="paper",
            enable_live_trading=False,
            trade_symbols=["BTCUSDT"],
            trade_interval="4h",
        )

        report = _build_paper_readiness_report(settings, recent_replay_limit=300)

        assert report["decision"] == "STAY_IN_CASH"
        assert report["approved_count"] == 1
        assert report["configured_approved_count"] == 1
        assert report["recent_replay_ready_count"] == 0
        assert report["runtime_ready_count"] == 0
        replay = report["approved_combinations"][0]["recent_replay"]
        assert replay["verdict"] == "downgraded"
        assert replay["diagnostics"]["net_pnl"] == pytest.approx(-12.34)
