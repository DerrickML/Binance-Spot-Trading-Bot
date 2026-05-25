"""Logging safety tests."""

from __future__ import annotations

from app.core.logging import _make_console_safe


def test_console_safe_log_values_fit_cp1252():
    """Emoji-rich notification previews should not crash Windows consoles."""
    event = {
        "preview": "🟢 Trade Opened ✅",
        "nested": ["📊 BTCUSDT", {"status": "❌"}],
    }

    safe_event = _make_console_safe(event, "cp1252")

    str(safe_event).encode("cp1252")
    assert "🟢" not in safe_event["preview"]
    assert "✅" not in safe_event["preview"]
    assert "📊" not in safe_event["nested"][0]
