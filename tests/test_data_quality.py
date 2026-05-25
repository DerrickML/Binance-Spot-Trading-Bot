"""Tests for research candle data-quality checks."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.backtesting.data_quality import validate_candle_frame


def _frame(count=10, interval="1h", start="2026-04-01T00:00:00Z"):
    open_times = pd.date_range(start=start, periods=count, freq=interval, tz="UTC")
    return pd.DataFrame({
        "open_time": open_times,
        "close_time": open_times + pd.Timedelta(interval),
        "open": [100.0 + i for i in range(count)],
        "high": [101.0 + i for i in range(count)],
        "low": [99.0 + i for i in range(count)],
        "close": [100.5 + i for i in range(count)],
        "volume": [1000.0 for _ in range(count)],
    })


def test_valid_candle_frame_passes():
    df = _frame()

    report = validate_candle_frame(
        df,
        "BTCUSDT",
        "1h",
        min_candles=10,
        now=datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
    )

    assert report.passed
    assert report.errors == []


def test_duplicate_timestamp_fails():
    df = _frame()
    df.loc[5, "open_time"] = df.loc[4, "open_time"]

    report = validate_candle_frame(
        df,
        "BTCUSDT",
        "1h",
        min_candles=10,
        require_fresh=False,
    )

    assert not report.passed
    assert "duplicate_open_time" in report.errors


def test_large_gap_fails():
    df = _frame()
    df.loc[5:, "open_time"] = df.loc[5:, "open_time"] + pd.Timedelta(hours=4)

    report = validate_candle_frame(
        df,
        "BTCUSDT",
        "1h",
        min_candles=10,
        require_fresh=False,
    )

    assert not report.passed
    assert any(reason.startswith("gap_count") for reason in report.errors)


def test_stale_data_fails_when_freshness_required():
    df = _frame()

    report = validate_candle_frame(
        df,
        "BTCUSDT",
        "1h",
        min_candles=10,
        now=datetime(2026, 4, 3, tzinfo=timezone.utc),
    )

    assert not report.passed
    assert any(reason.startswith("stale_data") for reason in report.errors)


def test_future_close_time_fails_as_unclosed_candle():
    df = _frame()
    df.loc[9, "close_time"] = datetime(2026, 4, 1, 13, tzinfo=timezone.utc)

    report = validate_candle_frame(
        df,
        "BTCUSDT",
        "1h",
        min_candles=10,
        now=datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
    )

    assert not report.passed
    assert "contains_unclosed_candles" in report.errors


def test_invalid_ohlc_bounds_fail():
    df = _frame()
    df.loc[3, "high"] = 50.0

    report = validate_candle_frame(
        df,
        "BTCUSDT",
        "1h",
        min_candles=10,
        require_fresh=False,
    )

    assert not report.passed
    assert "invalid_ohlc_bounds" in report.errors
