"""WebSocket client for live Binance kline/trade streams."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import websockets

from app.core.logging import get_logger

logger = get_logger(__name__)


class BinanceWebSocketClient:
    """WebSocket client for live market data with reconnect handling.

    Subscribes to kline streams and passes candle updates to a callback.
    Handles disconnections with automatic reconnect.
    """

    def __init__(
        self,
        ws_url: str = "wss://stream.binance.com:9443/ws",
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10,
    ) -> None:
        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        self._running = False
        self._ws = None

    async def subscribe_klines(
        self,
        symbols: list[str],
        interval: str,
        callback: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Subscribe to kline streams for multiple symbols.

        Args:
            symbols: List of trading pairs.
            interval: Candle interval (e.g., "1h").
            callback: Async or sync function called with each kline update.
        """
        streams = [f"{s.lower()}@kline_{interval}" for s in symbols]
        url = f"{self.ws_url}/{'/'.join(streams)}" if len(streams) == 1 else self.ws_url

        self._running = True
        attempts = 0

        while self._running and attempts < self.max_reconnect_attempts:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    attempts = 0
                    logger.info("ws_connected", symbols=symbols, interval=interval)

                    # Subscribe if using combined stream
                    if len(streams) > 1:
                        sub_msg = {
                            "method": "SUBSCRIBE",
                            "params": streams,
                            "id": 1,
                        }
                        await ws.send(json.dumps(sub_msg))

                    async for message in ws:
                        try:
                            data = json.loads(message)

                            # Handle combined stream format
                            if "data" in data:
                                data = data["data"]

                            if "k" in data:
                                kline = data["k"]
                                candle = {
                                    "symbol": kline["s"],
                                    "interval": kline["i"],
                                    "open_time": kline["t"],
                                    "close_time": kline["T"],
                                    "open": float(kline["o"]),
                                    "high": float(kline["h"]),
                                    "low": float(kline["l"]),
                                    "close": float(kline["c"]),
                                    "volume": float(kline["v"]),
                                    "is_closed": kline["x"],
                                }
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(candle)
                                else:
                                    callback(candle)
                        except json.JSONDecodeError:
                            logger.warning("ws_invalid_message", message=str(message)[:200])

            except websockets.exceptions.ConnectionClosed as e:
                attempts += 1
                logger.warning(
                    "ws_disconnected",
                    code=e.code,
                    reason=str(e.reason)[:100],
                    attempt=attempts,
                )
                if self._running:
                    await asyncio.sleep(self.reconnect_delay)

            except Exception as e:
                attempts += 1
                logger.error("ws_error", error=str(e), attempt=attempts)
                if self._running:
                    await asyncio.sleep(self.reconnect_delay)

        if attempts >= self.max_reconnect_attempts:
            logger.critical("ws_max_reconnects", max=self.max_reconnect_attempts)

    async def stop(self) -> None:
        """Stop the WebSocket client."""
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("ws_stopped")
