"""Historical data loader — bulk download with pagination and normalization."""

from __future__ import annotations


import pandas as pd

from app.core.logging import get_logger
from app.core.utils import datetime_to_timestamp
from app.data.market_data_service import MarketDataService

logger = get_logger(__name__)


class HistoricalLoader:
    """Loads historical OHLCV data from Binance with pagination support."""

    def __init__(self, market_data: MarketDataService) -> None:
        self.market_data = market_data

    async def load(
        self,
        symbol: str,
        interval: str = "1h",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Load historical candles with automatic pagination.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT").
            interval: Candle interval.
            start_date: Start date string (e.g., "2024-01-01").
            end_date: End date string.
            limit: Max candles per request.

        Returns:
            Consolidated DataFrame of all candles.
        """
        start_ts = None
        end_ts = None
        if start_date:
            dt = pd.Timestamp(start_date, tz="UTC")
            start_ts = datetime_to_timestamp(dt.to_pydatetime())
        if end_date:
            dt = pd.Timestamp(end_date, tz="UTC")
            end_ts = datetime_to_timestamp(dt.to_pydatetime())

        all_frames: list[pd.DataFrame] = []
        current_start = start_ts

        while True:
            df = await self.market_data.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit,
                start_time=current_start,
                end_time=end_ts,
            )

            if df.empty:
                break

            all_frames.append(df)

            if len(df) < limit:
                break  # No more data

            # Move start to after last candle
            last_close_time = df.iloc[-1]["close_time"]
            if hasattr(last_close_time, "timestamp"):
                current_start = int(last_close_time.timestamp() * 1000) + 1
            else:
                break

        if not all_frames:
            logger.warning("no_historical_data", symbol=symbol, interval=interval)
            return pd.DataFrame()

        result = pd.concat(all_frames, ignore_index=True)
        result = result.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        logger.info(
            "historical_data_loaded",
            symbol=symbol,
            interval=interval,
            candles=len(result),
        )

        return result
