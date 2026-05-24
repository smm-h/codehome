"""Conductor endpoints: sessions, chat, plans, strategy, autonomy.

Route handlers are thin wrappers -- business logic lives in conductor_ops.
"""

from typing import Any

from pydantic import BaseModel
from wesktop import Router, HTTPError, Request

from codehome.serve.agent_sessions import AgentSessionManager
from codehome.serve.auth_deps import get_current_user, require_admin
from codehome.serve.conductor_ops import (
    op_answer_gate,
    op_create_plan,
    op_delete_plan,
    op_execute_plan,
    op_get_autonomy,
    op_get_conductor_status,
    op_get_messages,
    op_get_plan,
    op_list_plans,
    op_list_sessions,
    op_pause_plan,
    op_receive_ui_message,
    op_resume_plan,
    op_send_message,
    op_set_autonomy,
    op_start_conductor,
    op_stop_conductor,
)
from codehome.serve.dependencies import (
    get_agent_session_manager,
    get_event_manager,
    get_question_store,
)
from codehome.serve.events import EventManager
from codehome.serve.questions import QuestionStore

router = Router()


@router.get("/api/conductor/sessions")
async def api_conductor_sessions(request: Request, user: dict[str, str] = ...) -> object:
    """List all active conductor sessions across branches."""
    return op_list_sessions()


class ConductorBranchRequest(BaseModel):
    branch: str


class ConductorMessageRequest(BaseModel):
    branch: str
    message: str


class ConductorUIRequest(BaseModel):
    type: str
    content: str
    options: list[str] | None = None
    branch: str


@router.post("/api/conductor/start")
async def api_conductor_start(request: Request, user: dict[str, str] = ...) -> object:
    """Start a Conductor for a branch."""
    req = request.json_as(ConductorBranchRequest)
    events: EventManager = get_event_manager(request)
    config = request.state.config
    try:
        return await op_start_conductor(
            branch=req.branch,
            user_sub=user["sub"],
            jwt_secret=config.jwt_secret,
            port=config.port,
            events=events,
        )
    except ValueError as e:
        status = 400 if "qualified" in str(e) else 404
        raise HTTPError(status, str(e)) from None
    except RuntimeError as e:
        raise HTTPError(409, str(e)) from None


@router.get("/api/conductor/status")
async def api_conductor_status(request: Request, user: dict[str, str] = ...) -> object:
    """Get Conductor status for a branch."""
    branch = request.query("branch")
    if not branch:
        raise HTTPError(422, "Missing required query parameter: branch")
    try:
        return op_get_conductor_status(branch)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None


@router.post("/api/conductor/message")
async def api_conductor_message(request: Request, user: dict[str, str] = ...) -> object:
    """Send a user message to the Conductor."""
    req = request.json_as(ConductorMessageRequest)
    try:
        await op_send_message(req.branch, req.message)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    except RuntimeError as e:
        raise HTTPError(409, str(e)) from None
    return {"ok": True}


@router.get("/api/conductor/messages")
async def api_conductor_messages(request: Request, user: dict[str, str] = ...) -> object:
    """Get conversation history for a branch's Conductor."""
    branch = request.query("branch")
    if not branch:
        raise HTTPError(422, "Missing required query parameter: branch")
    try:
        return op_get_messages(branch)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None


@router.post("/api/conductor/ui")
async def api_conductor_ui(request: Request, user: dict[str, str] = ...) -> object:
    """Receive structured UI messages from the Conductor's MCP tool."""
    req = request.json_as(ConductorUIRequest)
    events: EventManager = get_event_manager(request)
    try:
        await op_receive_ui_message(
            branch=req.branch,
            msg_type=req.type,
            content=req.content,
            options=req.options,
            events=events,
        )
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    return {"ok": True}


@router.delete("/api/conductor/stop")
async def api_conductor_stop(request: Request, user: dict[str, str] = ...) -> object:
    """Stop the Conductor for a branch."""
    branch = request.query("branch")
    if not branch:
        raise HTTPError(422, "Missing required query parameter: branch")
    events: EventManager = get_event_manager(request)
    try:
        await op_stop_conductor(branch, events)
    except KeyError:
        raise HTTPError(404, f"No Conductor for {branch}") from None
    return {"ok": True}


class ConductorAutonomyRequest(BaseModel):
    level: int
    branch: str | None = None


@router.get("/api/conductor/autonomy")
async def api_get_conductor_autonomy(request: Request, user: dict[str, str] = ...) -> object:
    """Return the user's current autonomy level."""
    level = await op_get_autonomy(user["sub"])
    return {"level": level}


@router.put("/api/conductor/autonomy")
async def api_set_conductor_autonomy(request: Request, user: dict[str, str] = ...) -> object:
    """Set the user's autonomy level (0-4)."""
    req = request.json_as(ConductorAutonomyRequest)
    events: EventManager = get_event_manager(request)
    try:
        await op_set_autonomy(
            level=req.level,
            branch=req.branch,
            user_sub=user["sub"],
            events=events,
        )
    except ValueError as e:
        raise HTTPError(400, str(e)) from None
    return {"level": req.level}


# -- Plan (Strategist) endpoints -------------------------------------------


class CreatePlanRequest(BaseModel):
    goal: str


class GateAnswerRequest(BaseModel):
    answer: str


@router.post("/api/branches/{qualified}/plans")
async def api_create_plan(request: Request, user: dict[str, Any] = ...) -> object:
    """Create an execution plan via the strategist agent."""
    qualified = request.path_params["qualified"]
    req = request.json_as(CreatePlanRequest)
    config = request.state.config
    try:
        return await op_create_plan(
            qualified=qualified,
            goal=req.goal,
            user_sub=user["sub"],
            jwt_secret=config.jwt_secret,
            port=config.port,
        )
    except ValueError as exc:
        status = 404 if "Worktree not found" in str(exc) else 422
        raise HTTPError(status, str(exc)) from None


@router.get("/api/branches/{qualified}/plans")
async def api_list_plans(request: Request, user: dict[str, Any] = ...) -> object:
    """List all plans for this branch."""
    qualified = request.path_params["qualified"]
    return op_list_plans(qualified)


@router.get("/api/branches/{qualified}/plans/{plan_id}")
async def api_get_plan(request: Request, user: dict[str, Any] = ...) -> object:
    """Get a single plan with all node statuses."""
    qualified = request.path_params["qualified"]
    plan_id = request.path_params["plan_id"]
    try:
        return op_get_plan(qualified, plan_id)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None


@router.post("/api/branches/{qualified}/plans/{plan_id}/execute")
async def api_execute_plan(request: Request, user: dict[str, Any] = ...) -> object:
    """Start executing a plan as a background task."""
    qualified = request.path_params["qualified"]
    plan_id = request.path_params["plan_id"]
    config = request.state.config
    try:
        return op_execute_plan(
            qualified=qualified,
            plan_id=plan_id,
            jwt_secret=config.jwt_secret,
            port=config.port,
        )
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    except ValueError as e:
        raise HTTPError(409, str(e)) from None
    except RuntimeError as e:
        raise HTTPError(409, str(e)) from None


@router.post("/api/branches/{qualified}/plans/{plan_id}/pause")
async def api_pause_plan(request: Request, user: dict[str, Any] = ...) -> object:
    """Pause a running plan."""
    qualified = request.path_params["qualified"]
    plan_id = request.path_params["plan_id"]
    try:
        return op_pause_plan(qualified, plan_id)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    except ValueError as e:
        raise HTTPError(409, str(e)) from None
    except RuntimeError as e:
        raise HTTPError(409, str(e)) from None


@router.post("/api/branches/{qualified}/plans/{plan_id}/resume")
async def api_resume_plan(request: Request, user: dict[str, Any] = ...) -> object:
    """Resume a paused plan."""
    qualified = request.path_params["qualified"]
    plan_id = request.path_params["plan_id"]
    config = request.state.config
    try:
        return op_resume_plan(
            qualified=qualified,
            plan_id=plan_id,
            jwt_secret=config.jwt_secret,
            port=config.port,
        )
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    except ValueError as e:
        raise HTTPError(409, str(e)) from None
    except RuntimeError as e:
        raise HTTPError(409, str(e)) from None


@router.delete("/api/branches/{qualified}/plans/{plan_id}", deps={"user": get_current_user})
async def api_delete_plan(request: Request, user: dict[str, Any] = ...) -> object:
    """Cancel a plan and all its active agents, then delete it."""
    qualified = request.path_params["qualified"]
    plan_id = request.path_params["plan_id"]
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
    question_store: QuestionStore = get_question_store(request)
    try:
        await op_delete_plan(qualified, plan_id, agent_sessions, question_store)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    return {"ok": True}


@router.post("/api/branches/{qualified}/plans/{plan_id}/gate/{node_id}/answer")
async def api_answer_gate(request: Request, user: dict[str, Any] = ...) -> object:
    """Answer a gate node's question to unblock plan execution."""
    qualified = request.path_params["qualified"]
    plan_id = request.path_params["plan_id"]
    node_id = request.path_params["node_id"]
    req = request.json_as(GateAnswerRequest)
    question_store: QuestionStore = get_question_store(request)
    try:
        op_answer_gate(qualified, plan_id, node_id, req.answer, question_store)
    except KeyError as e:
        raise HTTPError(404, str(e)) from None
    except ValueError as e:
        status = 400 if "not a gate" in str(e) else 409
        raise HTTPError(status, str(e)) from None
    except RuntimeError as e:
        raise HTTPError(409, str(e)) from None
    return {"ok": True}
