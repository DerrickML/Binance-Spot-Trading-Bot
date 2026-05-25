"""CLI safety hardening tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_cli_source_is_ascii_safe_for_windows_console():
    """User-facing CLI strings must not require UTF-8-only console output."""
    cli_text = Path("app/cli.py").read_text(encoding="utf-8")

    assert all(ord(ch) < 128 for ch in cli_text)


@pytest.mark.parametrize(
    ("interval", "days", "expected"),
    [
        ("15m", 2, 192),
        ("1h", 2, 48),
        ("4h", 2, 12),
        ("1d", 2, 2),
        ("3d", 4, 2),
        ("1w", 8, 2),
    ],
)
def test_candle_limit_scales_by_interval(interval, days, expected):
    """Backtest loaders should not treat every interval as hourly."""
    from app.cli import _candle_limit_for_days

    assert _candle_limit_for_days(interval, days) == expected


def test_candle_limit_rejects_unknown_interval():
    """Unknown intervals should fail loudly instead of loading wrong data."""
    from app.cli import _candle_limit_for_days

    with pytest.raises(ValueError, match="Unsupported Binance interval"):
        _candle_limit_for_days("17x", 10)


def test_research_min_candles_never_exceeds_expected_lookback():
    from app.cli import _research_min_candles

    assert _research_min_candles("4h", 7) == 42


def test_audit_min_candles_never_exceeds_limit():
    from app.cli import _audit_min_candles

    assert _audit_min_candles(20) == 20


def test_approval_source_prefers_qualified_coverage_over_robustness():
    """Approval persistence should maximize approved dataset coverage."""
    from app.cli import _select_approval_param_set

    robust_one = SimpleNamespace(
        all_qualified=False,
        datasets_qualified=1,
        pass_rate=0.11,
        robustness_score=0.90,
        avg_sharpe=2.0,
    )
    broader_three = SimpleNamespace(
        all_qualified=False,
        datasets_qualified=3,
        pass_rate=0.33,
        robustness_score=0.20,
        avg_sharpe=-1.0,
    )

    selected_name, selected = _select_approval_param_set([
        ("robust_one", robust_one),
        ("broader_three", broader_three),
    ])

    assert selected_name == "broader_three"
    assert selected is broader_three


def test_approval_source_prefers_all_qualified():
    """All-dataset qualified sets should outrank partial approval sets."""
    from app.cli import _select_approval_param_set

    partial = SimpleNamespace(
        all_qualified=False,
        datasets_qualified=8,
        pass_rate=0.89,
        robustness_score=0.99,
        avg_sharpe=3.0,
    )
    qualified = SimpleNamespace(
        all_qualified=True,
        datasets_qualified=9,
        pass_rate=1.0,
        robustness_score=0.10,
        avg_sharpe=0.1,
    )

    assert _select_approval_param_set([
        ("partial", partial),
        ("qualified", qualified),
    ]) == ("qualified", qualified)


def test_sim_runtime_interval_uses_selected_replay_interval():
    """Sim replay should report the approved replay interval, not live config."""
    from app.cli import _runtime_interval_for_paper

    assert _runtime_interval_for_paper(True, "1h", "4h") == "1h"


def test_live_runtime_interval_uses_configured_trade_interval():
    """Live paper mode should subscribe to the configured trade interval."""
    from app.cli import _runtime_interval_for_paper

    assert _runtime_interval_for_paper(False, "1h", "4h") == "4h"


def test_sim_replay_blocks_without_approvals_unless_override():
    from app.cli import _sim_replay_route

    assert _sim_replay_route(False, set(), "BTCUSDT:4h", False) == (
        "block_no_approvals",
        "BTCUSDT:4h",
    )
    assert _sim_replay_route(True, set(), "BTCUSDT:4h", False) == (
        "allow",
        "BTCUSDT:4h",
    )


def test_sim_replay_auto_selects_only_when_approved_keys_exist():
    from app.cli import _sim_replay_route

    assert _sim_replay_route(
        False,
        {"BNBUSDT:4h"},
        "BTCUSDT:4h",
        False,
    ) == ("auto_select", "BNBUSDT:4h")
    assert _sim_replay_route(
        False,
        {"BNBUSDT:4h"},
        "BTCUSDT:4h",
        True,
    ) == ("block_unapproved", "BTCUSDT:4h")


def test_sim_replay_auto_selects_preferred_ready_key_order():
    from app.cli import _sim_replay_route

    assert _sim_replay_route(
        False,
        {"BNBUSDT:4h", "ETHUSDT:4h"},
        "BTCUSDT:4h",
        False,
        preferred_keys=["ETHUSDT:4h", "BNBUSDT:4h"],
    ) == ("auto_select", "ETHUSDT:4h")
