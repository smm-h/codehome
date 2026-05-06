"""Agent subprocess dispatch: spawn claude -p with MCP config.

Launches task agents as async subprocesses, streams their output via SSE,
handles timeouts and cleanup. Adapted from the Superagent dispatch pattern
for async operation within the FastAPI server.
"""

import asyncio
import json
import os
import tempfile
from typing import Any

from codehome.bus import Event, fire
from codehome.paths import ROOT
from codehome.serve.agent_sessions import AgentSession, agent_sessions
from codehome.serve.auth import create_token
from codehome.serve.questions import question_store


def _build_mcp_config(
    *,
    role: str,
    task_id: str,
    session_id: str,
    worktree: str,
    server_url: str,
    auth_token: str,
) -> dict[str, Any]:
    """Build the MCP config JSON for claude --mcp-config.

    Points to the supervisor MCP server module, passing role and context
    via environment variables.
    """
    return {
        "mcpServers": {
            "supervisor": {
                "command": "python3",
                "args": ["-m", "codehome.mcp.server"],
                "env": {
                    "SA_ROLE": role,
                    "SA_TASK_ID": task_id,
                    "SA_SESSION_ID": session_id,
                    "SA_WORKTREE": worktree,
                    "SA_PROJECT_ROOT": str(ROOT),
                    "SA_SERVER_URL": server_url,
                    "SA_AUTH_TOKEN": auth_token,
                },
            },
        },
    }


def _write_temp_config(config: dict[str, Any]) -> str:
    """Write MCP config to a temp file, return the path."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="sv-agent-mcp-")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)
    return path


def _build_system_prompt(role: str, task: str, branch: str, extra: str = "") -> str:
    """Construct the system prompt for an agent based on its role."""
    parts = [
        f"You are a {role} agent working on branch '{branch}'.",
        f"Your task: {task}",
        "",
        "Use the MCP tools available to you to complete your work.",
        "Report your findings and results clearly.",
    ]
    if extra:
        parts.append("")
        parts.append(extra)
    return "\n".join(parts)


def create_agent_token(jwt_secret: str, session_id: str) -> str:
    """Create a short-lived JWT for an agent to authenticate with the server.

    Uses the 'agent' role with a 2-hour expiry. The subject is the session ID
    so the server can correlate requests back to the agent session.
    """
    return create_token(
        username=f"agent:{session_id}",
        role="agent",
        secret=jwt_secret,
        expires_hours=2,
    )


async def dispatch_task_agent(
    *,
    role: str,
    task: str,
    branch: str,
    user: str,
    worktree: str,
    server_url: str,
    auth_token: str,
    system_prompt: str = "",
    model: str = "opus",
    max_budget_usd: float = 10.0,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """Spawn a claude -p subprocess with MCP config and stream its output.

    Creates an agent session, launches the subprocess, streams stdout via SSE,
    and returns the parsed output on completion. The session is updated
    throughout the lifecycle.

    Returns a dict with {ok, session_id, output?} or {ok: False, error}.
    """
    # Build the full system prompt if not provided.
    if not system_prompt:
        system_prompt = _build_system_prompt(role, task, branch)

    # Look up the existing session (caller creates it before dispatching).
    # Find session by matching role, task, branch, user in pending state.
    session: AgentSession | None = None
    for s in agent_sessions.list_sessions(branch=branch):
        if s.status == "pending" and s.role == role and s.user == user:
            session = s
            break

    if not session:
        session = agent_sessions.create_session(role, task, branch, user)

    session_id = session.id

    # Build MCP config and write to temp file.
    mcp_config = _build_mcp_config(
        role=role,
        task_id=session_id,
        session_id=session_id,
        worktree=worktree,
        server_url=server_url,
        auth_token=auth_token,
    )
    mcp_config_path = _write_temp_config(mcp_config)

    # Full prompt is system_prompt + task combined as the -p argument.
    full_prompt = f"{system_prompt}\n\n---\n\nTask: {task}"

    cmd = [
        "claude",
        "-p",
        full_prompt,
        "--tools",
        "",
        "--mcp-config",
        mcp_config_path,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-budget-usd",
        str(max_budget_usd),
        "--dangerously-skip-permissions",
    ]

    # Prevent nested Claude Code session detection.
    env = {**os.environ, "CLAUDECODE": "0"}

    try:
        # Update session to running.
        agent_sessions.update_status(session_id, "running")
        await fire(
            Event(
                name="agent.state",
                payload={
                    "session_id": session_id,
                    "status": "running",
                    "branch": branch,
                    "role": role,
                },
            )
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=worktree,
            )
        except FileNotFoundError:
            error_msg = "claude binary not found on PATH"
            agent_sessions.update_status(session_id, "failed", error=error_msg)
            question_store.mark_session_questions_answered(session_id)
            await fire(
                Event(
                    name="agent.run.DONE",
                    payload={
                        "session_id": session_id,
                        "output": None,
                        "error": error_msg,
                    },
                )
            )
            return {"ok": False, "session_id": session_id, "error": error_msg}

        # Register process for cancellation support.
        agent_sessions.register_process(session_id, proc)

        # Stream stdout line by line, broadcasting via SSE.
        stdout_lines: list[str] = []
        stderr_data = b""

        async def _stream_stdout() -> None:
            nonlocal stdout_lines
            assert proc.stdout is not None
            async for line_bytes in proc.stdout:
                line = line_bytes.decode(errors="replace").rstrip("\n")
                stdout_lines.append(line)
                await fire(
                    Event(
                        name="agent.output",
                        payload={
                            "session_id": session_id,
                            "line": line,
                        },
                    )
                )

        async def _collect_stderr() -> None:
            nonlocal stderr_data
            assert proc.stderr is not None
            stderr_data = await proc.stderr.read()

        try:
            await asyncio.wait_for(
                asyncio.gather(_stream_stdout(), _collect_stderr(), proc.wait()),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            # Soft terminate, then hard kill after 10s.
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()
                await proc.wait()

            error_msg = f"Agent timed out after {timeout_seconds}s"
            agent_sessions.update_status(session_id, "failed", error=error_msg)
            question_store.mark_session_questions_answered(session_id)
            await fire(
                Event(
                    name="agent.run.DONE",
                    payload={
                        "session_id": session_id,
                        "output": None,
                        "error": error_msg,
                    },
                )
            )
            return {"ok": False, "session_id": session_id, "error": error_msg}

        # Check if cancelled while running.
        current = agent_sessions.get_session(session_id)
        if current and current.status == "cancelled":
            question_store.mark_session_questions_answered(session_id)
            await fire(
                Event(
                    name="agent.run.DONE",
                    payload={
                        "session_id": session_id,
                        "output": None,
                        "error": "Cancelled by user",
                    },
                )
            )
            return {"ok": False, "session_id": session_id, "error": "Cancelled"}

        # Process completed -- parse output.
        raw_output = "\n".join(stdout_lines)

        if proc.returncode != 0:
            stderr_text = stderr_data.decode(errors="replace") if stderr_data else ""
            error_msg = f"Agent exited with code {proc.returncode}" + (f": {stderr_text[:500]}" if stderr_text else "")
            agent_sessions.update_status(session_id, "failed", error=error_msg)
            question_store.mark_session_questions_answered(session_id)
            await fire(
                Event(
                    name="agent.run.DONE",
                    payload={
                        "session_id": session_id,
                        "output": None,
                        "error": error_msg,
                    },
                )
            )
            return {"ok": False, "session_id": session_id, "error": error_msg}

        # Parse JSON output from claude -p.
        output = _parse_output(raw_output)
        agent_sessions.update_status(session_id, "completed", output=output)
        question_store.mark_session_questions_answered(session_id)
        await fire(
            Event(
                name="agent.run.DONE",
                payload={
                    "session_id": session_id,
                    "output": output,
                    "error": None,
                },
            )
        )
        return {"ok": True, "session_id": session_id, "output": output}

    finally:
        agent_sessions.unregister_process(session_id)
        # Clean up temp config file.
        try:
            os.unlink(mcp_config_path)
        except OSError:
            pass


def _parse_output(raw: str) -> dict[str, Any]:
    """Parse JSON output from claude -p --output-format json.

    Claude wraps the response in a JSON object with result and/or
    structured_output fields.
    """
    if not raw.strip():
        return {"raw": ""}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Not valid JSON -- return as raw text.
        return {"raw": raw}

    if isinstance(data, dict):
        if "structured_output" in data:
            return data["structured_output"]  # type: ignore[no-any-return]
        if "result" in data:
            result = data["result"]
            if isinstance(result, dict):
                return result
            try:
                return json.loads(result)  # type: ignore[no-any-return]
            except (json.JSONDecodeError, TypeError):
                return {"result": result}
    return data  # type: ignore[no-any-return]
