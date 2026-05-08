"""Conductor business logic: session lifecycle, autonomy, plans, strategy.

Extracted from the conductor router so route handlers stay thin.
All functions raise ValueError / KeyError / RuntimeError on failures;
the router maps those to appropriate HTTP status codes.
"""

import asyncio
from pathlib import Path
from typing import Any

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.serve.agent_dispatch import create_agent_token
from codehome.serve.agent_sessions import AgentSessionManager
from codehome.serve.conductor import (
    create_conductor_token,
    get_session,
    list_sessions,
    start_session,
    stop_session,
)
from codehome.serve.events import EventManager
from codehome.serve.plan_executor import (
    answer_gate,
    cancel_plan,
    pause_plan,
    resume_plan,
    start_plan,
)
from codehome.serve.plan_schema import (
    delete_plan_file,
    load_all_plans,
    load_plan,
    save_plan,
)
from codehome.serve.preferences import get_user_preferences, set_user_preferences
from codehome.serve.questions import QuestionStore
from codehome.serve.strategist import create_plan

# -- Helpers ------------------------------------------------------------------


def _get_layout() -> Any:
    """Resolve the ProjectLayout service, raising if unavailable."""
    from codehome.service_protocols import ProjectLayout
    from codehome.state.service_registry import services

    layout = services.get_typed("core.layout", ProjectLayout)
    if layout is None:
        raise ValueError("ProjectLayout not registered (core plugin not loaded)")
    return layout


def resolve_worktree(qualified_branch: str) -> tuple[str, str, Path]:
    """Parse 'repo:branch', validate worktree exists, return (repo, branch, path).

    Raises ValueError if the branch format is invalid or worktree missing.
    """
    if ":" not in qualified_branch:
        raise ValueError("Branch must be qualified (repo:branch)")

    layout = _get_layout()
    repo, branch = qualified_branch.split(":", 1)
    wt = layout.worktree_path(repo, branch)
    if not wt.is_dir():
        raise ValueError(f"Worktree not found: {qualified_branch}")

    return repo, branch, wt


# -- Conductor session ops ----------------------------------------------------


def op_list_sessions() -> list[dict[str, Any]]:
    return list_sessions()


async def op_start_conductor(
    branch: str,
    user_sub: str,
    jwt_secret: str,
    port: int,
    events: EventManager,
) -> dict[str, str]:
    """Start a Conductor for a branch. Returns status dict."""
    _repo, _br, wt = resolve_worktree(branch)

    prefs = await asyncio.to_thread(get_user_preferences, user_sub)
    autonomy_level = prefs.get("autonomy", 2)

    server_url = f"http://127.0.0.1:{port}"
    token = create_conductor_token(jwt_secret, branch)

    await start_session(
        branch=branch,
        worktree=str(wt),
        server_url=server_url,
        auth_token=token,
        autonomy_level=autonomy_level,
    )

    await bus_fire(Event(name="conductor.start.DONE", payload={"branch": branch}))
    return {"status": "started", "branch": branch}


def op_get_conductor_status(branch: str) -> dict[str, Any]:
    """Return status dict for a branch's Conductor. Raises KeyError if missing."""
    session = get_session(branch)
    if not session:
        raise KeyError(f"No Conductor for {branch}")
    return session.to_dict()


async def op_send_message(branch: str, message: str) -> None:
    """Send a user message to the branch's Conductor.

    Raises KeyError if no session, RuntimeError if session not running.
    """
    session = get_session(branch)
    if not session:
        raise KeyError(f"No Conductor for {branch}")
    await session.send_message(message)


def op_get_messages(branch: str) -> list[Any]:
    """Return conversation history. Raises KeyError if no session."""
    session = get_session(branch)
    if not session:
        raise KeyError(f"No Conductor for {branch}")
    return session.messages


async def op_receive_ui_message(
    branch: str,
    msg_type: str,
    content: str,
    options: list[str] | None,
    events: EventManager,
) -> dict[str, Any]:
    """Store a structured UI message from the Conductor's MCP tool and broadcast.

    Raises KeyError if no session.
    """
    session = get_session(branch)
    if not session:
        raise KeyError(f"No Conductor for {branch}")

    message = session.add_ui_message(msg_type, content, options)

    await bus_fire(
        Event(
            name="conductor.message",
            payload={
                "branch": branch,
                "type": msg_type,
                "content": content,
                "options": options,
                "timestamp": message["timestamp"],
            },
        )
    )
    return message


async def op_stop_conductor(branch: str, events: EventManager) -> None:
    """Stop and remove a Conductor session. Raises KeyError if missing."""
    await stop_session(branch)
    await bus_fire(Event(name="conductor.stop", payload={"branch": branch}))


# -- Autonomy ops -------------------------------------------------------------


async def op_get_autonomy(user_sub: str) -> int:
    """Return the user's current autonomy level."""
    prefs = await asyncio.to_thread(get_user_preferences, user_sub)
    return prefs.get("autonomy", 2)  # type: ignore[no-any-return]


async def op_set_autonomy(
    level: int,
    branch: str | None,
    user_sub: str,
    events: EventManager,
) -> None:
    """Persist autonomy level and notify running Conductor if applicable.

    Raises ValueError if level is out of range.
    """
    if level not in range(5):
        raise ValueError("Level must be 0-4")

    await asyncio.to_thread(set_user_preferences, user_sub, {"autonomy": level})

    if branch:
        session = get_session(branch)
        if session and session.process and session.process.returncode is None:
            await session.update_autonomy(level)

    await bus_fire(
        Event(
            name="conductor.autonomy",
            payload={
                "level": level,
                "branch": branch,
            },
        )
    )


# -- Plan (Strategist) ops ----------------------------------------------------


async def op_create_plan(
    qualified: str,
    goal: str,
    user_sub: str,
    jwt_secret: str,
    port: int,
) -> dict[str, Any]:
    """Create an execution plan via the strategist agent.

    Raises ValueError if worktree not found or plan creation fails.
    """
    _repo, _branch, wt = resolve_worktree(qualified)

    server_url = f"http://127.0.0.1:{port}"
    agent_token = create_agent_token(jwt_secret, "strategist")

    plan = await create_plan(
        goal=goal,
        branch=qualified,
        user=user_sub,
        worktree=str(wt),
        server_url=server_url,
        auth_token=agent_token,
    )

    return {"ok": True, "plan_id": plan.id, "plan": plan.to_dict()}


def op_list_plans(qualified: str) -> list[dict[str, Any]]:
    """List all plans for a branch."""
    all_plans = load_all_plans()
    return [p.to_dict() for p in all_plans if p.branch == qualified]


def op_get_plan(qualified: str, plan_id: str) -> dict[str, Any]:
    """Get a single plan. Raises KeyError if not found."""
    plan = load_plan(plan_id)
    if not plan or plan.branch != qualified:
        raise KeyError("Plan not found")
    return plan.to_dict()


def op_execute_plan(
    qualified: str,
    plan_id: str,
    jwt_secret: str,
    port: int,
) -> dict[str, Any]:
    """Start or resume a plan. Raises KeyError/ValueError/RuntimeError."""
    plan = load_plan(plan_id)
    if not plan or plan.branch != qualified:
        raise KeyError("Plan not found")
    if plan.status not in ("pending", "paused"):
        raise ValueError(f"Cannot execute plan in '{plan.status}' state")

    layout = _get_layout()
    repo, branch = qualified.split(":", 1)
    wt = str(layout.worktree_path(repo, branch))
    server_url = f"http://127.0.0.1:{port}"
    agent_token = create_agent_token(jwt_secret, "plan-executor")

    if plan.status == "paused":
        ok = resume_plan(plan, server_url, agent_token, wt)
    else:
        ok = start_plan(plan, server_url, agent_token, wt)

    if not ok:
        raise RuntimeError("Another plan is already running on this branch")

    return {"ok": True, "plan_id": plan.id, "status": plan.status}


def op_pause_plan(qualified: str, plan_id: str) -> dict[str, Any]:
    """Pause a running plan. Raises KeyError/ValueError/RuntimeError."""
    plan = load_plan(plan_id)
    if not plan or plan.branch != qualified:
        raise KeyError("Plan not found")
    if plan.status != "running":
        raise ValueError(f"Cannot pause plan in '{plan.status}' state")

    ok = pause_plan(plan_id)
    if not ok:
        raise RuntimeError("Failed to pause plan")

    return {"ok": True, "plan_id": plan.id, "status": "paused"}


def op_resume_plan(
    qualified: str,
    plan_id: str,
    jwt_secret: str,
    port: int,
) -> dict[str, Any]:
    """Resume a paused plan. Raises KeyError/ValueError/RuntimeError."""
    plan = load_plan(plan_id)
    if not plan or plan.branch != qualified:
        raise KeyError("Plan not found")
    if plan.status != "paused":
        raise ValueError(f"Cannot resume plan in '{plan.status}' state")

    layout = _get_layout()
    repo, branch = qualified.split(":", 1)
    wt = str(layout.worktree_path(repo, branch))
    server_url = f"http://127.0.0.1:{port}"
    agent_token = create_agent_token(jwt_secret, "plan-executor")

    ok = resume_plan(plan, server_url, agent_token, wt)
    if not ok:
        raise RuntimeError("Another plan is already running on this branch")

    return {"ok": True, "plan_id": plan.id, "status": "running"}


async def op_delete_plan(
    qualified: str,
    plan_id: str,
    agent_sessions: AgentSessionManager,
    question_store: QuestionStore,
) -> None:
    """Cancel a plan and all its active agents, then delete it.

    Raises KeyError if the plan is not found.
    """
    plan = load_plan(plan_id)
    if not plan or plan.branch != qualified:
        raise KeyError("Plan not found")

    cancel_plan(plan_id)

    for node in plan.nodes:
        if node.agent_session_id and node.status in ("running", "waiting"):
            await agent_sessions.cancel_session(node.agent_session_id)
            question_store.mark_session_questions_answered(node.agent_session_id)

    delete_plan_file(plan_id)


def op_answer_gate(
    qualified: str,
    plan_id: str,
    node_id: str,
    answer: str,
    question_store: QuestionStore,
) -> None:
    """Answer a gate node's question to unblock plan execution.

    Raises KeyError if plan/node not found, ValueError for invalid node state,
    RuntimeError if the gate event is missing.
    """
    plan = load_plan(plan_id)
    if not plan or plan.branch != qualified:
        raise KeyError("Plan not found")

    node = plan.get_node(node_id)
    if not node:
        raise KeyError("Node not found")
    if node.type != "gate":
        raise ValueError("Node is not a gate")
    if node.status != "waiting":
        raise ValueError(f"Gate is not waiting (status: {node.status})")

    # Store the answer on the node.
    node.output = {"answer": answer}
    save_plan(plan)

    # Mark the persisted question as answered.
    pending = question_store.find_pending_by_gate(plan_id, node_id)
    if pending:
        question_store.mark_answered(pending.id, answer)

    # Unblock the executor.
    ok = answer_gate(plan_id, node_id, answer)
    if not ok:
        raise RuntimeError("Gate event not found (executor may have stopped)")
