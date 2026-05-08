"""Agent operations for the server API.

Extracted from routers/agents.py so route handlers stay thin. Functions here
have no FastAPI dependencies (no Request, Response, HTTPException).
Errors raise plain Python exceptions that the router maps to HTTP codes.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.serve.agent_dispatch import (
    create_agent_token,
    dispatch_task_agent,
)
from codehome.serve.agent_sessions import AgentSessionManager
from codehome.serve.auth import verify_token
from codehome.serve.events import EventManager
from codehome.serve.questions import QuestionStore


def _resolve_worktree(qualified: str) -> tuple[str, str, Path]:
    """Parse 'repo:branch', validate worktree exists, return (repo, branch, path).

    Raises FileNotFoundError if the worktree directory doesn't exist.
    """
    from codehome.service_protocols import ProjectLayout
    from codehome.state.service_registry import services

    layout = services.get_typed("core.layout", ProjectLayout)
    if layout is None:
        raise FileNotFoundError("ProjectLayout not registered (core plugin not loaded)")

    repo, branch = qualified.split(":", 1)
    wt = layout.worktree_path(repo, branch)
    if not wt.is_dir():
        raise FileNotFoundError(f"Worktree not found: {qualified}")
    return repo, branch, wt


def dispatch_agent(
    qualified: str,
    *,
    role: str,
    task: str,
    model: str,
    budget: float,
    timeout: int,
    system_prompt: str,
    user: str,
    jwt_secret: str,
    server_url: str,
    agent_sessions: AgentSessionManager,
) -> dict[str, Any]:
    """Create a session and fire off an agent subprocess.

    Returns {ok: True, session_id: str}.
    Raises FileNotFoundError if the worktree doesn't exist.
    """
    _repo, _branch, wt = _resolve_worktree(qualified)

    create_agent_token(jwt_secret, "dispatch")

    # Create session first so we can return the ID immediately.
    session = agent_sessions.create_session(
        role=role,
        task=task,
        branch=qualified,
        user=user,
    )

    # Agent-specific token tied to this session.
    agent_token = create_agent_token(jwt_secret, session.id)

    # Fire and forget the agent subprocess -- it updates session state via
    # the agent_sessions manager and broadcasts SSE events.
    asyncio.create_task(
        dispatch_task_agent(
            role=role,
            task=task,
            branch=qualified,
            user=user,
            worktree=str(wt),
            server_url=server_url,
            auth_token=agent_token,
            system_prompt=system_prompt,
            model=model,
            max_budget_usd=budget,
            timeout_seconds=timeout,
        ),
    )

    return {"ok": True, "session_id": session.id}


async def process_agent_hook(
    session_id: str,
    *,
    event_type: str,
    data: dict[str, Any],
    token: str | None,
    jwt_secret: str,
    agent_sessions: AgentSessionManager,
    question_store: QuestionStore,
    events: EventManager,
) -> None:
    """Validate auth, store the hook event, broadcast via SSE, persist questions.

    Raises PermissionError if the token is missing or invalid.
    Raises LookupError if the session doesn't exist.
    """
    if not token:
        raise PermissionError("Not authenticated")
    claims = verify_token(token, jwt_secret)
    if not claims:
        raise PermissionError("Invalid or expired token")

    session = agent_sessions.get_session(session_id)
    if not session:
        raise LookupError("Agent session not found")

    event = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    agent_sessions.add_event(session_id, event)
    await bus_fire(
        Event(
            name="agent.event",
            payload={
                "session_id": session_id,
                "event": event,
            },
        )
    )

    # Persist ask_user / AskUserQuestion events as inbox questions.
    if event_type in ("ask_user", "AskUser", "AskUserQuestion"):
        opts = data.get("options")
        question_store.create(
            source="agent",
            branch=session.branch,
            role=str(data.get("role", session.role)),
            question=str(data.get("question", data.get("text", ""))),
            session_id=session_id,
            options=[str(o) for o in opts] if isinstance(opts, list) else None,
        )


async def cancel_agent_session(
    qualified: str,
    session_id: str,
    *,
    agent_sessions: AgentSessionManager,
    question_store: QuestionStore,
    events: EventManager,
) -> dict[str, Any]:
    """Validate, cancel the session, mark questions answered, broadcast SSE.

    Raises LookupError if the session doesn't exist or doesn't match the branch.
    Raises ValueError if the session is not in a cancellable state.
    """
    session = agent_sessions.get_session(session_id)
    if not session or session.branch != qualified:
        raise LookupError("Agent session not found")
    if session.status not in ("pending", "running"):
        raise ValueError(f"Cannot cancel: status is {session.status}")

    result = await agent_sessions.cancel_session(session_id)
    question_store.mark_session_questions_answered(session_id)
    await bus_fire(
        Event(
            name="agent.state",
            payload={
                "session_id": session_id,
                "status": "cancelled",
                "branch": qualified,
            },
        )
    )
    return {"ok": True, "session": result.to_dict() if result else None}


async def answer_agent_question(
    qualified: str,
    session_id: str,
    *,
    answer: str,
    agent_sessions: AgentSessionManager,
    question_store: QuestionStore,
    events: EventManager,
) -> None:
    """Store an answer event, mark persisted question answered, broadcast SSE.

    Raises LookupError if the session doesn't exist or doesn't match the branch.
    Raises ValueError if the agent is not running.
    """
    session = agent_sessions.get_session(session_id)
    if not session or session.branch != qualified:
        raise LookupError("Agent session not found")
    if session.status != "running":
        raise ValueError("Agent is not running")

    agent_sessions.add_event(
        session_id,
        {
            "type": "user_answer",
            "answer": answer,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    # Mark the persisted question as answered.
    pending = question_store.find_pending_by_session(session_id)
    if pending:
        question_store.mark_answered(pending.id, answer)

    await bus_fire(
        Event(
            name="agent.state",
            payload={
                "session_id": session_id,
                "status": "answer_provided",
                "branch": qualified,
            },
        )
    )
