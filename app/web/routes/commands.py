"""Command execution API routes and WebSocket endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.logging import get_logger
from app.web.command_runner import ALLOWED_COMMANDS, command_runner

logger = get_logger(__name__)
router = APIRouter()


class RunCommandRequest(BaseModel):
    """Request body for starting a CLI command."""
    command: str
    args: list[str] = []


@router.get("/allowed")
async def get_allowed_commands() -> dict:
    """Return the whitelist of allowed CLI commands."""
    return {"commands": sorted(ALLOWED_COMMANDS)}


@router.post("/run")
async def run_command(req: RunCommandRequest) -> dict:
    """Start a CLI command. Returns 409 if one is already running."""
    try:
        result = await command_runner.run(req.command, req.args)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/stop")
async def stop_command() -> dict:
    """Force-stop the currently running command."""
    result = await command_runner.stop()
    return result


@router.get("/status")
async def get_status() -> dict:
    """Get current command runner state + full output buffer."""
    return command_runner.get_status()


@router.get("/history")
async def get_history() -> dict:
    """Get the last 20 completed command results."""
    return {"history": command_runner.get_history()}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """WebSocket endpoint for live command output streaming.

    On connect:
    1. Sends the full output buffer (replay everything so far)
    2. Sends current status
    3. Switches to live streaming until disconnect

    Authentication is checked via query param: ?token=<JWT>
    """
    # Auth check via query param
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4001, reason="Missing auth token")
        return

    from app.config.settings import get_settings
    from app.web.auth import verify_session_token

    settings = get_settings()
    claims = verify_session_token(
        token=token,
        bot_token=settings.telegram_bot_token,
        expected_chat_id=settings.telegram_chat_id,
    )
    if not claims:
        await ws.close(code=4003, reason="Invalid or expired token")
        return

    await ws.accept()
    command_runner.register_ws(ws)

    try:
        # Replay the current output buffer
        buffer = command_runner.get_buffer_replay()
        if buffer:
            await ws.send_json({
                "type": "replay",
                "lines": buffer,
            })

        # Send current status
        await ws.send_json({
            "type": "status",
            "data": command_runner.get_status(),
        })

        # Keep alive — wait for client messages (e.g., ping)
        while True:
            data = await ws.receive_text()
            # Client can send "ping" to keep alive
            if data == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.debug("ws_client_disconnected")
    except Exception as exc:
        logger.debug("ws_client_error", error=str(exc))
    finally:
        command_runner.unregister_ws(ws)
