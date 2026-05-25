"""Telegram OTP authentication and JWT session management.

Security model:
- OTP sent to the configured TELEGRAM_CHAT_ID via the bot
- Only the person controlling that Telegram chat can log in
- JWT signed with HS256 using a secret derived from the bot token
- Sessions expire after a configurable TTL (default 24h)
- Rate-limited OTP requests (max 5 per 15 minutes)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from jose import JWTError, jwt

from app.core.logging import get_logger

logger = get_logger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Rate limiting
_MAX_OTP_REQUESTS = 5
_OTP_RATE_WINDOW_SECONDS = 15 * 60  # 15 minutes
_OTP_TTL_SECONDS = 5 * 60  # 5 minutes


@dataclass
class OTPStore:
    """In-memory OTP storage with TTL and rate limiting."""

    _pending: dict[str, tuple[str, float]] = field(default_factory=dict)  # chat_id -> (code, expiry)
    _request_timestamps: list[float] = field(default_factory=list)

    def can_request(self) -> bool:
        """Check if a new OTP request is allowed (rate limit)."""
        now = time.time()
        self._request_timestamps = [
            ts for ts in self._request_timestamps
            if now - ts < _OTP_RATE_WINDOW_SECONDS
        ]
        return len(self._request_timestamps) < _MAX_OTP_REQUESTS

    def generate(self, chat_id: str) -> str:
        """Generate a 6-digit OTP and store it with TTL."""
        code = f"{secrets.randbelow(900000) + 100000}"
        expiry = time.time() + _OTP_TTL_SECONDS
        self._pending[chat_id] = (code, expiry)
        self._request_timestamps.append(time.time())
        logger.info("otp_generated", chat_id=chat_id)
        return code

    def verify(self, chat_id: str, code: str) -> bool:
        """Verify an OTP. Consumes it on success or expiry."""
        entry = self._pending.get(chat_id)
        if not entry:
            return False

        stored_code, expiry = entry
        # Always remove after verification attempt (single-use)
        del self._pending[chat_id]

        if time.time() > expiry:
            logger.warning("otp_expired", chat_id=chat_id)
            return False

        if not hmac.compare_digest(stored_code, code.strip()):
            logger.warning("otp_invalid", chat_id=chat_id)
            return False

        logger.info("otp_verified", chat_id=chat_id)
        return True


def _derive_jwt_secret(bot_token: str) -> str:
    """Derive a JWT signing secret from the Telegram bot token.

    Uses HMAC-SHA256 with a fixed salt so the secret is deterministic
    but never the raw token itself.
    """
    return hashlib.sha256(
        f"trading-bot-dashboard:{bot_token}".encode()
    ).hexdigest()


def create_session_token(
    chat_id: str,
    bot_token: str,
    ttl_hours: int = 24,
) -> str:
    """Create a signed JWT session token."""
    secret = _derive_jwt_secret(bot_token)
    payload = {
        "sub": chat_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + (ttl_hours * 3600),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    logger.info("session_token_created", chat_id=chat_id, ttl_hours=ttl_hours)
    return token


def verify_session_token(
    token: str,
    bot_token: str,
    expected_chat_id: str,
) -> dict[str, Any] | None:
    """Verify a JWT session token. Returns claims on success, None on failure."""
    secret = _derive_jwt_secret(bot_token)
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
        if claims.get("sub") != expected_chat_id:
            logger.warning("session_token_wrong_chat_id", expected=expected_chat_id)
            return None
        return claims
    except JWTError as e:
        logger.debug("session_token_invalid", error=str(e))
        return None


async def send_otp_via_telegram(
    bot_token: str,
    chat_id: str,
    code: str,
) -> bool:
    """Send the OTP code to Telegram."""
    url = TELEGRAM_API_URL.format(token=bot_token)
    text = (
        "🔐 <b>Trading Bot Dashboard Login</b>\n\n"
        f"Your one-time code: <code>{code}</code>\n\n"
        "This code expires in 5 minutes.\n"
        "If you did not request this, ignore this message."
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("otp_sent_via_telegram", chat_id=chat_id)
                return True
            else:
                logger.error(
                    "otp_telegram_send_failed",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return False
    except Exception as e:
        logger.error("otp_telegram_error", error=str(e))
        return False
