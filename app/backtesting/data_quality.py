"""Candle data-quality checks for research and runtime parity workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


INTERVAL_SECONDS = {
    "1s": 1,
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "8h": 8 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
    "1M": 30 * 24 * 60 * 60,
}


@dataclass
class CandleQualityReport:
    """Validation result for one symbol/interval candle dataset."""

    symbol: str
    interval: str
    total_candles: int
    min_candles: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    first_open_time: str | None = None
    last_close_time: str | None = None

    @property
    def passed(self) -> bool:
        return not self.errors

    def summary_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "passed": self.passed,
            "total_candles": self.total_candles,
            "min_candles": self.min_candles,
            "first_open_time": self.first_open_time,
            "last_close_time": self.last_close_time,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def interval_to_timedelta(interval: str) -> timedelta:
    """Return the expected candle spacing for a Binance interval."""
    seconds = INTERVAL_SECONDS.get(str(interval).strip())
    if not seconds:
        raise ValueError(f"Unsupported Binance interval: {interval}")
    return timedelta(seconds=seconds)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_series(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def validate_candle_frame(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    *,
    min_candles: int,
    require_fresh: bool = True,
    max_gap_multiplier: float = 1.5,
    max_staleness_intervals: int = 12,
    min_staleness_tolerance: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> CandleQualityReport:
    """Validate a candle DataFrame before it is used for research approval."""
    report = CandleQualityReport(
        symbol=symbol,
        interval=interval,
        total_candles=0 if df is None else len(df),
        min_candles=min_candles,
    )

    if df is None or df.empty:
        report.errors.append("no_candle_data")
        return report

    required = ["open_time", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        report.errors.append(f"missing_columns: {', '.join(missing)}")
        return report

    if len(df) < min_candles:
        report.errors.append(f"insufficient_candles: {len(df)} < {min_candles}")

    open_times = _to_utc_series(df["open_time"])
    close_times = _to_utc_series(df["close_time"] if "close_time" in df.columns else df["open_time"])
    if open_times.isna().any() or close_times.isna().any():
        report.errors.append("invalid_timestamps")
        return report

    report.first_open_time = open_times.iloc[0].isoformat()
    report.last_close_time = close_times.iloc[-1].isoformat()

    if open_times.duplicated().any():
        report.errors.append("duplicate_open_time")

    if not open_times.is_monotonic_increasing:
        report.errors.append("non_monotonic_open_time")

    expected_delta = interval_to_timedelta(interval)
    if len(open_times) > 1:
        gaps = open_times.diff().dropna()
        max_allowed_gap = expected_delta * max_gap_multiplier
        large_gaps = gaps[gaps > max_allowed_gap]
        if not large_gaps.empty:
            report.errors.append(
                f"gap_count: {len(large_gaps)} > {max_gap_multiplier:.1f}x interval"
            )

    if require_fresh:
        current_time = now or _utc_now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        future_closes = close_times[close_times > current_time]
        if not future_closes.empty:
            report.errors.append("contains_unclosed_candles")
        stale_after = max(
            expected_delta * max_staleness_intervals,
            min_staleness_tolerance,
        )
        last_close = close_times.iloc[-1].to_pydatetime()
        if current_time - last_close > stale_after:
            report.errors.append(
                f"stale_data: last close {last_close.isoformat()} "
                f"> {stale_after} old"
            )

    numeric = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if not numeric.map(math.isfinite).all().all():
        report.errors.append("non_finite_ohlcv")
        return report

    if (numeric[["open", "high", "low", "close"]] <= 0).any().any():
        report.errors.append("non_positive_price")

    if (numeric["volume"] < 0).any():
        report.errors.append("negative_volume")

    high_too_low = numeric["high"] < numeric[["open", "close"]].max(axis=1)
    low_too_high = numeric["low"] > numeric[["open", "close"]].min(axis=1)
    if high_too_low.any() or low_too_high.any():
        report.errors.append("invalid_ohlc_bounds")

    zero_volume_ratio = float((numeric["volume"] == 0).mean())
    if zero_volume_ratio > 0.05:
        report.warnings.append(f"zero_volume_ratio: {zero_volume_ratio:.2%}")

    return report
