"""Conductor session management: long-running interactive claude processes.

Each branch can have one active Conductor. The Conductor communicates with the
user via structured UI messages (MCP tool -> POST /api/conductor/ui -> SSE)
and receives user input via stdin.
"""

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

from codehome.bus import Event, fire
from codehome.conductor.prompt import build_conductor_prompt
from codehome.mcp.conductor_config import build_conductor_mcp_config
from codehome.serve.auth import create_token

# Module-level registry of active Conductor sessions, keyed by branch.
_conductors: dict[str, "ConductorSession"] = {}


def _write_temp_config(config: dict[str, Any]) -> str:
    """Write MCP config to a temp file, return the path."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="sv-conductor-mcp-")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)
    return path


class ConductorSession:
    """Manages a single Conductor process for a branch."""

    def __init__(self, branch: str):
        self.branch = branch
        self.process: asyncio.subprocess.Process | None = None
        self.status: str = "idle"
        self.messages: list[dict[str, Any]] = []
        self.created_at: str = datetime.now(UTC).isoformat()
        self._config_path: str | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    async def start(
        self,
        worktree: str,
        server_url: str,
        auth_token: str,
        autonomy_level: int = 2,
    ) -> None:
        """Start the claude process with conductor MCP config."""
        if self.process and self.process.returncode is None:
            msg = f"Conductor already running for {self.branch}"
            raise RuntimeError(msg)

        mcp_config = build_conductor_mcp_config(
            server_url=server_url,
            auth_token=auth_token,
            branch=self.branch,
            worktree=worktree,
        )
        self._config_path = _write_temp_config(mcp_config)

        system_prompt = build_conductor_prompt(autonomy_level=autonomy_level)

        cmd = [
            "claude",
            "--tools",
            "",
            "--mcp-config",
            self._config_path,
            "--system-prompt",
            system_prompt,
            "--dangerously-skip-permissions",
        ]

        env = {**os.environ, "CLAUDECODE": "0"}

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=worktree,
        )

        self.status = "running"
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._monitor_task = asyncio.create_task(self._monitor_process())

    async def send_message(self, text: str) -> None:
        """Write user message to stdin."""
        if not self.process or self.process.returncode is not None:
            msg = "Conductor is not running"
            raise RuntimeError(msg)
        if self.process.stdin is None:
            msg = "Conductor stdin not available"
            raise RuntimeError(msg)

        self.messages.append(
            {
                "role": "user",
                "content": text,
                "type": "text",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

        self.process.stdin.write((text + "\n").encode())
        await self.process.stdin.drain()

    async def update_autonomy(self, level: int) -> None:
        """Notify the running Conductor of an autonomy level change."""
        await self.send_message(f"[SYSTEM] Autonomy level changed to {level}")

    async def stop(self) -> None:
        """Gracefully stop the process."""
        if not self.process or self.process.returncode is not None:
            self.status = "stopped"
            self._cleanup_config()
            return

        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()

        self.status = "stopped"

        for task in (self._stdout_task, self._stderr_task, self._monitor_task):
            if task and not task.done():
                task.cancel()

        self._cleanup_config()

    def add_ui_message(self, msg_type: str, content: str, options: list[str] | None = None) -> dict[str, Any]:
        """Record a structured UI message from the Conductor."""
        message = {
            "role": "conductor",
            "content": content,
            "type": msg_type,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if options:
            message["options"] = options  # type: ignore[assignment]
        self.messages.append(message)
        return message

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "status": self.status,
            "message_count": len(self.messages),
            "created_at": self.created_at,
        }

    async def _read_stdout(self) -> None:
        """Read stdout lines -- mostly for logging.

        Structured communication happens via MCP tool calls to /api/conductor/ui.
        """
        assert self.process
        assert self.process.stdout
        try:
            async for _line_bytes in self.process.stdout:
                # Stdout is informational; no parsing needed since the Conductor
                # communicates structured data via its MCP ui_message tool.
                pass
        except asyncio.CancelledError:
            pass

    async def _read_stderr(self) -> None:
        """Drain stderr to prevent pipe buffer deadlocks."""
        assert self.process
        assert self.process.stderr
        try:
            await self.process.stderr.read()
        except asyncio.CancelledError:
            pass

    async def _monitor_process(self) -> None:
        """Watch for process exit and update status."""
        assert self.process
        try:
            await self.process.wait()
            if self.status != "stopped":
                self.status = "stopped"
                await fire(
                    Event(
                        name="conductor.crash",
                        payload={
                            "branch": self.branch,
                        },
                    )
                )
            self._cleanup_config()
        except asyncio.CancelledError:
            pass

    def _cleanup_config(self) -> None:
        if self._config_path:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None


# -- Public API ----------------------------------------------------------------


def list_sessions() -> list[dict[str, Any]]:
    return [s.to_dict() for s in _conductors.values()]


def get_session(branch: str) -> ConductorSession | None:
    return _conductors.get(branch)


async def start_session(
    branch: str,
    worktree: str,
    server_url: str,
    auth_token: str,
    autonomy_level: int = 2,
) -> ConductorSession:
    """Create and start a new Conductor session for a branch."""
    if branch in _conductors:
        existing = _conductors[branch]
        if existing.process and existing.process.returncode is None:
            msg = f"Conductor already running for {branch}"
            raise RuntimeError(msg)
        # Previous session exited -- clean up and replace.
        del _conductors[branch]

    session = ConductorSession(branch)
    _conductors[branch] = session
    await session.start(worktree, server_url, auth_token, autonomy_level=autonomy_level)
    return session


async def stop_session(branch: str) -> None:
    """Stop and remove a Conductor session."""
    session = _conductors.get(branch)
    if not session:
        msg = f"No Conductor session for {branch}"
        raise KeyError(msg)
    await session.stop()
    del _conductors[branch]


def create_conductor_token(jwt_secret: str, branch: str) -> str:
    """Create a JWT for the Conductor to authenticate with the server."""
    return create_token(
        username=f"conductor:{branch}",
        role="agent",
        secret=jwt_secret,
        expires_hours=24,
    )
