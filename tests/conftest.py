"""Pytest fixtures and sample data shared across all tests."""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd
import pytest

# Set test environment before any imports
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("ENABLE_LIVE_TRADING", "false")
os.environ.setdefault("ENABLE_TELEGRAM", "false")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TRADE_SYMBOLS", '["BTCUSDT"]')


@pytest.fixture
def sample_candles() -> pd.DataFrame:
    """Generate sample OHLCV candle data for testing.

    Creates 200 candles with a trend, reversal, and range-bound section
    to exercise all strategy types.
    """
    np.random.seed(42)
    n = 200
    base_price = 100.0
    dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")

    # Generate price movement with trend + range + reversal
    trend = np.concatenate([
        np.linspace(0, 15, 60),      # Uptrend
        np.linspace(15, 15, 40),      # Range
        np.linspace(15, -5, 50),      # Downtrend
        np.linspace(-5, 5, 50),       # Recovery
    ])

    noise = np.random.normal(0, 1.5, n)
    close = base_price + trend + noise

    # Generate OHLCV from close
    high = close + np.abs(np.random.normal(0.5, 0.3, n))
    low = close - np.abs(np.random.normal(0.5, 0.3, n))
    open_ = np.roll(close, 1)
    open_[0] = base_price
    volume = np.random.uniform(100, 1000, n)

    return pd.DataFrame({
        "open_time": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "close_time": dates + pd.Timedelta(hours=1),
        "symbol": "BTCUSDT",
    })


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    """Safe settings overrides for testing.

    Includes _env_file=None to prevent pydantic-settings from
    loading the project .env and overriding test values.
    """
    return {
        "_env_file": None,
        "app_env": "development",
        "trading_mode": "paper",
        "enable_live_trading": False,
        "enable_telegram": False,
        "database_url": "sqlite:///:memory:",
        "trade_symbols": ["BTCUSDT"],
        "binance_api_key": "",
        "binance_api_secret": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }


@pytest.fixture
def sample_exchange_info() -> dict:
    """Sample Binance exchange info for order validation tests."""
    return {
        "symbols": [{
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "filters": [
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.00001000",
                    "maxQty": "9000.00000000",
                    "stepSize": "0.00001000",
                },
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.01000000",
                    "maxPrice": "1000000.00",
                    "tickSize": "0.01000000",
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "minNotional": "10.00000000",
                },
            ],
        }],
    }
