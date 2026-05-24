"""Agent endpoints: dispatch, sessions, questions, hooks."""

from typing import Any

from pydantic import BaseModel
from wesktop import Router, HTTPError, Request

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

# -- Authenticated agent endpoints -----------------------------------------

router = Router()


class DispatchAgentRequest(BaseModel):
    role: str
    task: str
    model: str = "opus"
    budget: float = 10.0
    timeout: int = 1800
    system_prompt: str = ""


@router.post("/api/branches/{qualified}/agents")
async def api_dispatch_agent(request: Request, user: dict[str, Any] = ...) -> object:
    """Dispatch a task agent for this branch."""
    qualified = request.path_params["qualified"]
    req = request.json_as(DispatchAgentRequest)
    config = request.state.config
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
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
        raise HTTPError(404, str(e)) from None


@router.get("/api/branches/{qualified}/agents")
async def api_list_agents(request: Request, user: dict[str, Any] = ...) -> object:
    """List agent sessions for this branch (active + recent, last 20)."""
    qualified = request.path_params["qualified"]
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
    sessions = agent_sessions.list_sessions(branch=qualified)
    return [s.to_dict() for s in sessions[:20]]


@router.get("/api/branches/{qualified}/agents/{session_id}")
async def api_get_agent(request: Request, user: dict[str, Any] = ...) -> object:
    """Get detailed info for a single agent session."""
    qualified = request.path_params["qualified"]
    session_id = request.path_params["session_id"]
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
    session = agent_sessions.get_session(session_id)
    if not session or session.branch != qualified:
        raise HTTPError(404, "Agent session not found")
    return session.to_dict()


@router.delete("/api/branches/{qualified}/agents/{session_id}")
async def api_cancel_agent(request: Request, user: dict[str, Any] = ...) -> object:
    """Cancel an active agent session."""
    qualified = request.path_params["qualified"]
    session_id = request.path_params["session_id"]
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
    question_store: QuestionStore = get_question_store(request)
    events: EventManager = get_event_manager(request)
    try:
        return await cancel_agent_session(
            qualified,
            session_id,
            agent_sessions=agent_sessions,
            question_store=question_store,
            events=events,
        )
    except LookupError as e:
        raise HTTPError(404, str(e)) from None
    except ValueError as e:
        raise HTTPError(409, str(e)) from None


class AnswerAgentRequest(BaseModel):
    answer: str


@router.post("/api/branches/{qualified}/agents/{session_id}/answer")
async def api_answer_agent(request: Request, user: dict[str, Any] = ...) -> object:
    """Answer an agent's question (for future AskUserQuestion hook integration).

    Stores the answer as an event on the session. The agent's hook receiver
    can poll for answers or be notified via the session event list.
    """
    qualified = request.path_params["qualified"]
    session_id = request.path_params["session_id"]
    req = request.json_as(AnswerAgentRequest)
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
    question_store: QuestionStore = get_question_store(request)
    events: EventManager = get_event_manager(request)
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
        raise HTTPError(404, str(e)) from None
    except ValueError as e:
        raise HTTPError(409, str(e)) from None
    return {"ok": True}


# -- Hook receiver (public, uses its own bearer auth) ----------------------

public_router = Router()


class HookEventRequest(BaseModel):
    event_type: str  # PreToolUse, PostToolUse, Notification
    data: dict[str, Any] = {}


@public_router.post("/api/hooks/agent/{session_id}")
async def api_agent_hook(request: Request) -> object:
    """Receive Claude Code hook events from an agent subprocess.

    Authenticates via the agent's SA_AUTH_TOKEN (bearer token). Stores the
    event in the session's event list and broadcasts via SSE.
    """
    session_id = request.path_params["session_id"]
    req = request.json_as(HookEventRequest)

    # Extract bearer token from Authorization header.
    auth_header = request.header("authorization", "") or ""
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None

    config = request.state.config
    agent_sessions: AgentSessionManager = get_agent_session_manager(request)
    question_store: QuestionStore = get_question_store(request)
    events: EventManager = get_event_manager(request)

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
        raise HTTPError(401, str(e)) from None
    except LookupError as e:
        raise HTTPError(404, str(e)) from None
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
async def api_create_question(request: Request, user: dict[str, Any] = ...) -> object:
    """Create a persisted question and broadcast it to the dashboard."""
    req = request.json_as(CreateQuestionRequest)
    question_store: QuestionStore = get_question_store(request)
    events: EventManager = get_event_manager(request)
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
async def api_list_questions(request: Request, user: dict[str, Any] = ...) -> object:
    """List persisted questions, optionally filtered by branch and status."""
    branch = request.query("branch")
    status = request.query("status")
    question_store: QuestionStore = get_question_store(request)
    qs = question_store.list(branch=branch, status=status)
    return [q.to_dict() for q in qs]


@router.get("/api/questions/{question_id}")
async def api_get_question(request: Request, user: dict[str, Any] = ...) -> object:
    """Get a single question by ID."""
    question_id = request.path_params["question_id"]
    question_store: QuestionStore = get_question_store(request)
    q = question_store.get(question_id)
    if not q:
        raise HTTPError(404, "Question not found")
    return q.to_dict()
