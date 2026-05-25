"""Telegram notifier — sends trading notifications via Telegram Bot API."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Sends notifications to Telegram using the Bot API.

    Supports all required notification types: startup, shutdown, trades,
    alerts, summaries, errors, and emergency halts.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured Telegram chat.

        Args:
            text: Message text (HTML formatted).
            parse_mode: Parse mode (HTML or Markdown).

        Returns:
            True if sent successfully.
        """
        if not self.enabled:
            logger.debug("telegram_disabled", message_preview=text[:100])
            return False

        url = TELEGRAM_API_URL.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("telegram_sent", preview=text[:80])
                return True
            else:
                logger.error(
                    "telegram_send_failed",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return False
        except Exception as e:
            logger.error("telegram_error", error=str(e))
            return False

    async def notify_startup(self, mode: str, symbols: list[str]) -> bool:
        """Send startup notification."""
        from app.notifications.message_builder import build_startup_message
        return await self.send_message(build_startup_message(mode, symbols))

    async def notify_shutdown(self, reason: str = "Normal shutdown") -> bool:
        """Send shutdown notification."""
        from app.notifications.message_builder import build_shutdown_message
        return await self.send_message(build_shutdown_message(reason))

    async def notify_trade_opened(self, trade_info: dict[str, Any]) -> bool:
        """Send trade opened notification."""
        from app.notifications.message_builder import build_trade_opened_message
        return await self.send_message(build_trade_opened_message(trade_info))

    async def notify_trade_closed(self, trade_info: dict[str, Any]) -> bool:
        """Send trade closed notification."""
        from app.notifications.message_builder import build_trade_closed_message
        return await self.send_message(build_trade_closed_message(trade_info))

    async def notify_stop_loss_hit(self, trade_info: dict[str, Any]) -> bool:
        """Send stop-loss hit notification."""
        from app.notifications.message_builder import build_stop_loss_message
        return await self.send_message(build_stop_loss_message(trade_info))

    async def notify_error(self, error: str, component: str = "") -> bool:
        """Send error notification."""
        from app.notifications.message_builder import build_error_message
        return await self.send_message(build_error_message(error, component))

    async def notify_emergency_halt(self, reason: str) -> bool:
        """Send emergency halt notification."""
        from app.notifications.message_builder import build_emergency_halt_message
        return await self.send_message(build_emergency_halt_message(reason))

    async def notify_daily_summary(self, summary: dict[str, Any]) -> bool:
        """Send daily summary."""
        from app.notifications.message_builder import build_daily_summary_message
        return await self.send_message(build_daily_summary_message(summary))

    async def notify_backtest_winner(self, winner_info: dict[str, Any]) -> bool:
        """Send backtest winner notification."""
        from app.notifications.message_builder import build_backtest_winner_message
        return await self.send_message(build_backtest_winner_message(winner_info))

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
