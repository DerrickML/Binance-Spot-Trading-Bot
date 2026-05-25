"""Market data service — Binance REST API client for market data."""

from __future__ import annotations

from typing import Any

import httpx
import pandas as pd

from app.core.logging import get_logger
from app.core.utils import timestamp_to_datetime

logger = get_logger(__name__)


class MarketDataService:
    """Binance REST client for fetching market data.

    Fetches klines, exchange info, and ticker prices. Always validates
    against exchange metadata.
    """

    def __init__(self, base_url: str = "https://api.binance.com") -> None:
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._exchange_info: dict[str, Any] | None = None

    async def get_exchange_info(self) -> dict[str, Any]:
        """Fetch and cache exchange info."""
        if self._exchange_info:
            return self._exchange_info
        resp = await self._client.get("/api/v3/exchangeInfo")
        resp.raise_for_status()
        self._exchange_info = resp.json()
        return self._exchange_info

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV klines from Binance.

        Returns a normalized DataFrame with columns:
        [open_time, open, high, low, close, volume, close_time, quote_volume, trade_count]
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        resp = await self._client.get("/api/v3/klines", params=params)
        resp.raise_for_status()
        raw = resp.json()

        if not raw:
            return pd.DataFrame()

        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trade_count",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])

        # Normalize types
        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = df[col].astype(float)
        df["trade_count"] = df["trade_count"].astype(int)
        df["open_time"] = df["open_time"].apply(timestamp_to_datetime)
        df["close_time"] = df["close_time"].apply(timestamp_to_datetime)
        df["symbol"] = symbol
        now = pd.Timestamp.now(tz="UTC")
        df = df[df["close_time"] <= now].copy()

        # Drop unnecessary columns
        df = df.drop(columns=["taker_buy_base", "taker_buy_quote", "ignore"])

        logger.info("klines_fetched", symbol=symbol, interval=interval, candles=len(df))
        return df

    async def get_ticker_price(self, symbol: str) -> float:
        """Get current ticker price for a symbol."""
        resp = await self._client.get("/api/v3/ticker/price", params={"symbol": symbol})
        resp.raise_for_status()
        return float(resp.json()["price"])

    async def get_all_ticker_prices(self) -> dict[str, float]:
        """Get all ticker prices."""
        resp = await self._client.get("/api/v3/ticker/price")
        resp.raise_for_status()
        return {item["symbol"]: float(item["price"]) for item in resp.json()}

    async def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        """Get symbol info including filters."""
        info = await self.get_exchange_info()
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                return s
        return None

    async def close(self) -> None:
        await self._client.aclose()
