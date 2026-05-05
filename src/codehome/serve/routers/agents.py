"""Agent endpoints: dispatch, sessions, questions, hooks."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.requests import Request

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.serve.agent_ops import (
    answer_agent_question,
    cancel_agent_session,
    dispatch_agent,
    process_agent_hook,
)
from codehome.serve.agent_sessions import AgentSessionManager
from codehome.serve.auth_deps import get_current_user
from codehome.serve.dependencies import (
    get_agent_session_manager,
    get_event_manager,
    get_question_store,
)
from codehome.serve.events import EventManager
from codehome.serve.questions import QuestionStore

# Optional bearer scheme for hook auth (same as in server.py).
_bearer_scheme = HTTPBearer(auto_error=False)

# -- Authenticated agent endpoints -----------------------------------------

router = APIRouter()


class DispatchAgentRequest(BaseModel):
    role: str
    task: str
    model: str = "opus"
    budget: float = 10.0
    timeout: int = 1800
    system_prompt: str = ""


@router.post("/api/branches/{qualified}/agents")
async def api_dispatch_agent(
    qualified: str,
    req: DispatchAgentRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
) -> object:
    """Dispatch a task agent for this branch."""
    config = request.app.state.config
    server_url = f"http://127.0.0.1:{config.port}"

    try:
        return dispatch_agent(
            qualified,
            role=req.role,
            task=req.task,
            model=req.model,
            budget=req.budget,
            timeout=req.timeout,
            system_prompt=req.system_prompt,
            user=user["sub"],
            jwt_secret=config.jwt_secret,
            server_url=server_url,
            agent_sessions=agent_sessions,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.get("/api/branches/{qualified}/agents")
async def api_list_agents(
    qualified: str,
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
) -> object:
    """List agent sessions for this branch (active + recent, last 20)."""
    sessions = agent_sessions.list_sessions(branch=qualified)
    return [s.to_dict() for s in sessions[:20]]


@router.get("/api/branches/{qualified}/agents/{session_id}")
async def api_get_agent(
    qualified: str,
    session_id: str,
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
) -> object:
    """Get detailed info for a single agent session."""
    session = agent_sessions.get_session(session_id)
    if not session or session.branch != qualified:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return session.to_dict()


@router.delete("/api/branches/{qualified}/agents/{session_id}")
async def api_cancel_agent(
    qualified: str,
    session_id: str,
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
    question_store: QuestionStore = Depends(get_question_store),
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Cancel an active agent session."""
    try:
        return await cancel_agent_session(
            qualified,
            session_id,
            agent_sessions=agent_sessions,
            question_store=question_store,
            events=events,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


class AnswerAgentRequest(BaseModel):
    answer: str


@router.post("/api/branches/{qualified}/agents/{session_id}/answer")
async def api_answer_agent(
    qualified: str,
    session_id: str,
    req: AnswerAgentRequest,
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
    question_store: QuestionStore = Depends(get_question_store),
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Answer an agent's question (for future AskUserQuestion hook integration).

    Stores the answer as an event on the session. The agent's hook receiver
    can poll for answers or be notified via the session event list.
    """
    try:
        await answer_agent_question(
            qualified,
            session_id,
            answer=req.answer,
            agent_sessions=agent_sessions,
            question_store=question_store,
            events=events,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ok": True}


# -- Hook receiver (public, uses its own bearer auth) ----------------------

public_router = APIRouter()


class HookEventRequest(BaseModel):
    event_type: str  # PreToolUse, PostToolUse, Notification
    data: dict[str, Any] = {}


@public_router.post("/api/hooks/agent/{session_id}")
async def api_agent_hook(
    session_id: str,
    req: HookEventRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    agent_sessions: AgentSessionManager = Depends(get_agent_session_manager),
    question_store: QuestionStore = Depends(get_question_store),
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Receive Claude Code hook events from an agent subprocess.

    Authenticates via the agent's SA_AUTH_TOKEN (bearer token). Stores the
    event in the session's event list and broadcasts via SSE.
    """
    token = credentials.credentials if credentials else None
    config = request.app.state.config

    try:
        await process_agent_hook(
            session_id,
            event_type=req.event_type,
            data=req.data,
            token=token,
            jwt_secret=config.jwt_secret,
            agent_sessions=agent_sessions,
            question_store=question_store,
            events=events,
        )
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"ok": True}


# -- Question persistence endpoints ----------------------------------------


class CreateQuestionRequest(BaseModel):
    source: str
    branch: str
    role: str
    question: str
    session_id: str | None = None
    plan_id: str | None = None
    node_id: str | None = None
    options: list[str] | None = None


@router.post("/api/questions")
async def api_create_question(
    req: CreateQuestionRequest,
    question_store: QuestionStore = Depends(get_question_store),
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Create a persisted question and broadcast it to the dashboard."""
    q = question_store.create(
        source=req.source,
        branch=req.branch,
        role=req.role,
        question=req.question,
        session_id=req.session_id,
        plan_id=req.plan_id,
        node_id=req.node_id,
        options=req.options,
    )
    await bus_fire(
        Event(
            name="agent.question",
            payload={
                "source": req.source,
                "session_id": req.session_id,
                "branch": req.branch,
                "role": req.role,
                "question": req.question,
                "question_id": q.id,
                "options": req.options,
            },
        )
    )
    return q.to_dict()


@router.get("/api/questions")
async def api_list_questions(
    branch: str | None = None,
    status: str | None = None,
    question_store: QuestionStore = Depends(get_question_store),
) -> object:
    """List persisted questions, optionally filtered by branch and status."""
    qs = question_store.list(branch=branch, status=status)
    return [q.to_dict() for q in qs]


@router.get("/api/questions/{question_id}")
async def api_get_question(
    question_id: str,
    question_store: QuestionStore = Depends(get_question_store),
) -> object:
    """Get a single question by ID."""
    q = question_store.get(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    return q.to_dict()
