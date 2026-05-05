"""Conductor endpoints: sessions, chat, plans, strategy, autonomy.

Route handlers are thin wrappers -- business logic lives in conductor_ops.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from codehome.serve.agent_sessions import AgentSessionManager
from codehome.serve.auth_deps import get_current_user
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

router = APIRouter()


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
async def api_conductor_start(
    req: ConductorBranchRequest,
    request: Request,
    user: dict[str, str] = Depends(get_current_user),
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Start a Conductor for a branch."""
    config = request.app.state.config
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
        raise HTTPException(status_code=status, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.get("/api/conductor/status")
async def api_conductor_status(branch: str) -> object:
    """Get Conductor status for a branch."""
    try:
        return op_get_conductor_status(branch)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/api/conductor/message")
async def api_conductor_message(req: ConductorMessageRequest) -> object:
    """Send a user message to the Conductor."""
    try:
        await op_send_message(req.branch, req.message)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ok": True}


@router.get("/api/conductor/messages")
async def api_conductor_messages(branch: str) -> object:
    """Get conversation history for a branch's Conductor."""
    try:
        return op_get_messages(branch)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/api/conductor/ui")
async def api_conductor_ui(
    req: ConductorUIRequest,
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Receive structured UI messages from the Conductor's MCP tool."""
    try:
        await op_receive_ui_message(
            branch=req.branch,
            msg_type=req.type,
            content=req.content,
            options=req.options,
            events=events,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"ok": True}


@router.delete("/api/conductor/stop")
async def api_conductor_stop(
    branch: str,
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Stop the Conductor for a branch."""
    try:
        await op_stop_conductor(branch, events)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No Conductor for {branch}") from None
    return {"ok": True}


class ConductorAutonomyRequest(BaseModel):
    level: int
    branch: str | None = None


@router.get("/api/conductor/autonomy")
async def api_get_conductor_autonomy(user: dict[str, str] = Depends(get_current_user)) -> object:
    """Return the user's current autonomy level."""
    level = await op_get_autonomy(user["sub"])
    return {"level": level}


@router.put("/api/conductor/autonomy")
async def api_set_conductor_autonomy(
    req: ConductorAutonomyRequest,
    user: dict[str, str] = Depends(get_current_user),
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Set the user's autonomy level (0-4)."""
    try:
        await op_set_autonomy(
            level=req.level,
            branch=req.branch,
            user_sub=user["sub"],
            events=events,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"level": req.level}


# -- Plan (Strategist) endpoints -------------------------------------------


class CreatePlanRequest(BaseModel):
    goal: str


class GateAnswerRequest(BaseModel):
    answer: str


@router.post("/api/branches/{qualified}/plans")
async def api_create_plan(
    qualified: str,
    req: CreatePlanRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Create an execution plan via the strategist agent."""
    config = request.app.state.config
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
        raise HTTPException(status_code=status, detail=str(exc)) from None


@router.get("/api/branches/{qualified}/plans")
async def api_list_plans(qualified: str) -> object:
    """List all plans for this branch."""
    return op_list_plans(qualified)


@router.get("/api/branches/{qualified}/plans/{plan_id}")
async def api_get_plan(qualified: str, plan_id: str) -> object:
    """Get a single plan with all node statuses."""
    try:
        return op_get_plan(qualified, plan_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/api/branches/{qualified}/plans/{plan_id}/execute")
async def api_execute_plan(
    qualified: str,
    plan_id: str,
    request: Request,
) -> object:
    """Start executing a plan as a background task."""
    config = request.app.state.config
    try:
        return op_execute_plan(
            qualified=qualified,
            plan_id=plan_id,
            jwt_secret=config.jwt_secret,
            port=config.port,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/api/branches/{qualified}/plans/{plan_id}/pause")
async def api_pause_plan(qualified: str, plan_id: str) -> object:
    """Pause a running plan."""
    try:
        return op_pause_plan(qualified, plan_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/api/branches/{qualified}/plans/{plan_id}/resume")
async def api_resume_plan(
    qualified: str,
    plan_id: str,
    request: Request,
) -> object:
    """Resume a paused plan."""
    config = request.app.state.config
    try:
        return op_resume_plan(
            qualified=qualified,
            plan_id=plan_id,
            jwt_secret=config.jwt_secret,
            port=config.port,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.delete("/api/branches/{qualified}/plans/{plan_id}")
async def api_delete_plan(
    qualified: str,
    plan_id: str,
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
    question_store: QuestionStore = Depends(get_question_store),
) -> object:
    """Cancel a plan and all its active agents, then delete it."""
    try:
        await op_delete_plan(qualified, plan_id, agent_sessions, question_store)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"ok": True}


@router.post("/api/branches/{qualified}/plans/{plan_id}/gate/{node_id}/answer")
async def api_answer_gate(
    qualified: str,
    plan_id: str,
    node_id: str,
    req: GateAnswerRequest,
    question_store: QuestionStore = Depends(get_question_store),
) -> object:
    """Answer a gate node's question to unblock plan execution."""
    try:
        op_answer_gate(qualified, plan_id, node_id, req.answer, question_store)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        status = 400 if "not a gate" in str(e) else 409
        raise HTTPException(status_code=status, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ok": True}
