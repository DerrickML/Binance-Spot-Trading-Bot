"""Command runner — executes CLI commands as subprocesses with mutex and server-side buffering.

Key design:
- Only one command can run at a time (mutex lock)
- Output is buffered server-side, survives browser disconnects
- WebSocket clients receive live output; new connections get full replay
- Commands can be force-stopped via SIGTERM/SIGKILL
- History of the last 20 commands is kept in memory
"""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from app.core.logging import get_logger

logger = get_logger(__name__)

# Safety: only these commands can be executed from the UI
ALLOWED_COMMANDS: set[str] = {
    "show-config",
    "health-check",
    "list-strategies",
    "backfill",
    "backfill-matrix",
    "run-backtest",
    "optimize",
    "paper-trade",
    "show-winner",
    "show-approved",
    "show-approval-report",
    "audit-approved",
    "paper-readiness",
    "export-report",
    "send-test-telegram",
    "research-cycle",
}

# Prevent OOM on extremely long-running commands
_MAX_BUFFER_LINES = 50_000


@dataclass
class CommandResult:
    """Record of a completed command."""

    command: str
    args: list[str]
    exit_code: int | None
    started_at: str
    finished_at: str
    duration_seconds: float
    output_lines: int
    output_tail: list[str]  # last 100 lines for history


class CommandRunner:
    """Singleton command executor with mutex, buffering, and WebSocket broadcast."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._current_command: str | None = None
        self._current_args: list[str] = []
        self._started_at: datetime | None = None
        self._output_buffer: list[str] = []
        self._ws_clients: set[WebSocket] = set()
        self._history: deque[CommandResult] = deque(maxlen=20)
        self._exit_code: int | None = None
        self._finished: bool = False

    @property
    def is_running(self) -> bool:
        """Whether a command is currently executing."""
        return self._process is not None and not self._finished

    def get_status(self) -> dict[str, Any]:
        """Return current runner state for the REST API."""
        return {
            "running": self.is_running,
            "command": self._current_command,
            "args": self._current_args,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "exit_code": self._exit_code,
            "finished": self._finished,
            "output_lines": len(self._output_buffer),
            "output": self._output_buffer,
        }

    def get_history(self) -> list[dict[str, Any]]:
        """Return completed command history."""
        return [
            {
                "command": r.command,
                "args": r.args,
                "exit_code": r.exit_code,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "duration_seconds": round(r.duration_seconds, 1),
                "output_lines": r.output_lines,
                "output_tail": r.output_tail,
            }
            for r in reversed(self._history)
        ]

    async def run(self, command: str, args: list[str]) -> dict[str, Any]:
        """Start a CLI command. Returns immediately; output streams via WS.

        Raises:
            ValueError: If the command is not in the whitelist.
            RuntimeError: If another command is already running.
        """
        if command not in ALLOWED_COMMANDS:
            raise ValueError(f"Command '{command}' is not allowed. Allowed: {sorted(ALLOWED_COMMANDS)}")

        if self.is_running:
            raise RuntimeError(
                f"Another command is already running: {self._current_command} {' '.join(self._current_args)}"
            )

        # Acquire the lock (non-blocking check above, but lock ensures safety)
        if self._lock.locked():
            raise RuntimeError("Command runner is locked")

        # Reset state
        self._current_command = command
        self._current_args = list(args)
        self._output_buffer = []
        self._exit_code = None
        self._finished = False
        self._started_at = datetime.now(timezone.utc)

        # Build the subprocess command
        cmd_parts = [sys.executable, "-m", "app.cli", command, *args]
        logger.info("command_start", command=command, args=args)

        # Spawn in background
        asyncio.create_task(self._execute(cmd_parts))

        return {
            "status": "started",
            "command": command,
            "args": args,
            "started_at": self._started_at.isoformat(),
        }

    async def _execute(self, cmd_parts: list[str]) -> None:
        """Execute the subprocess and stream output to buffer + WebSocket clients."""
        async with self._lock:
            start_time = time.monotonic()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *cmd_parts,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
                )

                assert self._process.stdout is not None
                async for raw_line in self._process.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                    if len(self._output_buffer) < _MAX_BUFFER_LINES:
                        self._output_buffer.append(line)
                    await self._broadcast(line)

                await self._process.wait()
                self._exit_code = self._process.returncode

            except Exception as exc:
                error_line = f"[SYSTEM ERROR] {exc}"
                self._output_buffer.append(error_line)
                await self._broadcast(error_line)
                self._exit_code = -1
                logger.error("command_execution_error", error=str(exc))

            finally:
                duration = time.monotonic() - start_time
                self._finished = True
                self._process = None

                # Record in history
                result = CommandResult(
                    command=self._current_command or "",
                    args=self._current_args,
                    exit_code=self._exit_code,
                    started_at=self._started_at.isoformat() if self._started_at else "",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=duration,
                    output_lines=len(self._output_buffer),
                    output_tail=self._output_buffer[-100:],
                )
                self._history.append(result)

                # Notify WS clients that command finished
                await self._broadcast(
                    f"\n[COMMAND FINISHED] exit_code={self._exit_code} "
                    f"duration={duration:.1f}s"
                )

                logger.info(
                    "command_complete",
                    command=self._current_command,
                    exit_code=self._exit_code,
                    duration=round(duration, 1),
                    output_lines=len(self._output_buffer),
                )

    async def stop(self) -> dict[str, Any]:
        """Force-stop the running command."""
        if not self.is_running or self._process is None:
            return {"status": "no_command_running"}

        logger.warning("command_force_stop", command=self._current_command)

        try:
            # Try graceful termination first
            if sys.platform == "win32":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)

            # Wait up to 5 seconds for graceful shutdown
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                # Force kill
                self._process.kill()
                logger.warning("command_force_killed", command=self._current_command)

        except ProcessLookupError:
            pass  # Process already exited

        return {"status": "stopped", "command": self._current_command}

    async def _broadcast(self, line: str) -> None:
        """Send a line to all connected WebSocket clients."""
        dead_clients: set[WebSocket] = set()
        for ws in self._ws_clients:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json({"type": "output", "line": line})
            except Exception:
                dead_clients.add(ws)

        self._ws_clients -= dead_clients

    def register_ws(self, ws: WebSocket) -> None:
        """Register a WebSocket client for live output."""
        self._ws_clients.add(ws)

    def unregister_ws(self, ws: WebSocket) -> None:
        """Unregister a WebSocket client."""
        self._ws_clients.discard(ws)

    def get_buffer_replay(self) -> list[str]:
        """Get the full output buffer for replay on reconnect."""
        return list(self._output_buffer)


# Module-level singleton
command_runner = CommandRunner()
