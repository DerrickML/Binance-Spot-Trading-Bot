"""Authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.web.auth import OTPStore, create_session_token, send_otp_via_telegram

logger = get_logger(__name__)
router = APIRouter()

# Module-level OTP store (lives as long as the FastAPI server process)
_otp_store = OTPStore()


class OTPRequest(BaseModel):
    """Empty body — the chat ID comes from server config, not the client."""
    pass


class OTPVerify(BaseModel):
    """OTP verification request."""
    code: str


@router.post("/request-otp")
async def request_otp(req: OTPRequest) -> dict:
    """Generate and send an OTP to the configured Telegram chat."""
    settings = get_settings()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise HTTPException(
            status_code=503,
            detail="Telegram bot token and chat ID must be configured in .env",
        )

    if not _otp_store.can_request():
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests. Try again in a few minutes.",
        )

    code = _otp_store.generate(settings.telegram_chat_id)
    sent = await send_otp_via_telegram(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        code=code,
    )

    if not sent:
        raise HTTPException(
            status_code=502,
            detail="Failed to send OTP via Telegram. Check bot token and chat ID.",
        )

    return {"status": "otp_sent", "message": "Check your Telegram for the login code."}


@router.post("/verify-otp")
async def verify_otp(req: OTPVerify) -> dict:
    """Verify OTP and return a JWT session token."""
    settings = get_settings()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise HTTPException(status_code=503, detail="Telegram not configured")

    if not _otp_store.verify(settings.telegram_chat_id, req.code):
        raise HTTPException(status_code=401, detail="Invalid or expired code.")

    ttl_hours = int(__import__("os").environ.get("SESSION_TTL_HOURS", "24"))
    token = create_session_token(
        chat_id=settings.telegram_chat_id,
        bot_token=settings.telegram_bot_token,
        ttl_hours=ttl_hours,
    )

    return {"status": "authenticated", "token": token, "expires_in_hours": ttl_hours}


@router.get("/me")
async def get_me(request: Request) -> dict:
    """Return current session info (requires auth via middleware)."""
    return {
        "authenticated": True,
        "chat_id": request.state.chat_id if hasattr(request.state, "chat_id") else None,
    }
