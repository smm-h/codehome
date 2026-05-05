"""Terminal endpoints: PTY WebSocket, terminal session CRUD, sharing."""

import json as json_mod
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.serve.auth import verify_token
from codehome.serve.auth_deps import get_current_user
from codehome.serve.dependencies import get_pty_manager
from codehome.serve.pty_manager import PTYManager

# -- WebSocket endpoint (public, auth via query param) ---------------------

public_router = APIRouter()


@public_router.websocket("/api/terminal/{session_id}/ws")
async def terminal_ws(ws: WebSocket, session_id: str) -> None:
    """Bidirectional PTY stream over WebSocket.

    Auth is via a ?token= query param since the browser WebSocket API
    cannot set custom headers.  Binary frames carry raw PTY I/O; JSON
    text frames carry control messages (e.g. resize).

    Supports shared sessions: non-owner users can connect if sharing is
    enabled. Only the owner's resize messages are honored to avoid
    conflicting dimensions.

    A single reader task per session (managed by PTYManager) reads from
    the PTY fd and broadcasts to all connections. Each WebSocket handler
    only runs a write loop for forwarding user input to the PTY.
    """
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=1008)
        return

    config = ws.app.state.config
    claims = verify_token(token, config.jwt_secret)
    if not claims:
        await ws.close(code=1008)
        return

    # Access singletons via app.state (WebSocket endpoints cannot use Depends).
    pty_mgr: PTYManager = ws.app.state.pty_manager

    session = pty_mgr.get(session_id)
    if not session:
        await ws.close(code=1008)
        return

    username = claims["sub"]
    is_owner = session.user == username

    # Non-owners can only connect to shared sessions.
    if not is_owner and not session.shared:
        await ws.close(code=1008)
        return

    await ws.accept()

    # Replay scrollback BEFORE registering the connection so the joining
    # client sees all history before the reader task starts broadcasting
    # live output to it (avoids garbled interleaved data).
    for chunk in list(session.scrollback):
        try:
            await ws.send_bytes(chunk)
        except Exception:
            await ws.close()
            return

    # Register this connection AFTER scrollback replay is complete.
    conn = await pty_mgr.add_connection(session, ws, username)

    # Ensure the shared reader task is running (no-op if already active).
    pty_mgr.ensure_reader(session)

    # Broadcast presence event so other clients update their UI.
    await bus_fire(
        Event(
            name="terminal.presence",
            payload={
                "session_id": session_id,
                "action": "joined",
                "user": username,
                "connections": pty_mgr.get_connections(session_id),
            },
        )
    )

    # Write loop: forward WebSocket input to the PTY stdin.
    try:
        while True:
            # Exit if the session was destroyed by the owner.
            if session.destroyed:
                break
            try:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    os.write(session.fd, msg["bytes"])
                elif msg.get("text"):
                    # Text frames carry JSON control messages (resize).
                    try:
                        data = json_mod.loads(msg["text"])
                        if data.get("type") == "resize":
                            # Only the owner can resize to avoid conflicts.
                            if is_owner:
                                pty_mgr.resize(session_id, data["rows"], data["cols"])
                        else:
                            # Plain text typed by the user (fallback).
                            os.write(session.fd, msg["text"].encode())
                    except (json_mod.JSONDecodeError, KeyError):
                        os.write(session.fd, msg["text"].encode())
            except WebSocketDisconnect:
                break
            except OSError:
                # PTY fd was closed (session destroyed or process died).
                break
    finally:
        # Unregister connection and stop reader if no connections remain.
        await pty_mgr.remove_connection(session, conn)
        pty_mgr.stop_reader_if_empty(session)

        await bus_fire(
            Event(
                name="terminal.presence",
                payload={
                    "session_id": session_id,
                    "action": "left",
                    "user": username,
                    "connections": pty_mgr.get_connections(session_id),
                },
            )
        )


# -- Terminal CRUD endpoints (authenticated) -------------------------------

router = APIRouter()


class CreateTerminalRequest(BaseModel):
    branch: str
    cwd: str | None = None


@router.post("/api/terminal")
async def create_terminal(
    req: CreateTerminalRequest,
    user: dict[str, Any] = Depends(get_current_user),
    pty_manager: PTYManager = Depends(get_pty_manager),
) -> object:
    """Create a new PTY session for the given branch.

    If cwd is not provided, defaults to the branch's worktree directory.
    """
    from codehome.paths import worktree_path as _wt_path

    cwd = req.cwd
    if not cwd:
        # Derive worktree from qualified branch name (repo:branch).
        if ":" in req.branch:
            repo, branch = req.branch.split(":", 1)
        else:
            repo, branch = "bag", req.branch
        wt = _wt_path(repo, branch)
        cwd = str(wt) if wt.is_dir() else os.path.expanduser("~")

    session_id = uuid.uuid4().hex[:12]
    pty_manager.create(session_id, cwd, user["sub"], req.branch)
    return {"session_id": session_id}


@router.get("/api/terminal")
async def list_terminals(
    user: dict[str, Any] = Depends(get_current_user),
    pty_manager: PTYManager = Depends(get_pty_manager),
) -> object:
    """List active PTY sessions for the current user."""
    return pty_manager.list_sessions(user=user["sub"])


@router.delete("/api/terminal/{session_id}")
async def destroy_terminal(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    pty_manager: PTYManager = Depends(get_pty_manager),
) -> object:
    """Destroy a PTY session."""
    session = pty_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Terminal session not found")
    if session.user != user["sub"]:
        raise HTTPException(status_code=403, detail="Not your terminal session")
    await pty_manager.destroy(session_id)
    return {"ok": True}


@router.post("/api/terminal/{session_id}/share")
async def share_terminal(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    pty_manager: PTYManager = Depends(get_pty_manager),
) -> object:
    """Enable sharing on a terminal session. Returns the session ID for sharing."""
    session = pty_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Terminal session not found")
    if session.user != user["sub"]:
        raise HTTPException(status_code=403, detail="Not your terminal session")
    pty_manager.set_shared(session_id, shared=True)
    await bus_fire(
        Event(
            name="terminal.presence",
            payload={
                "session_id": session_id,
                "action": "shared",
                "user": user["sub"],
                "connections": pty_manager.get_connections(session_id),
            },
        )
    )
    return {"session_id": session_id, "shared": True}


@router.delete("/api/terminal/{session_id}/share")
async def unshare_terminal(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    pty_manager: PTYManager = Depends(get_pty_manager),
) -> object:
    """Disable sharing on a terminal session and disconnect non-owner connections."""
    session = pty_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Terminal session not found")
    if session.user != user["sub"]:
        raise HTTPException(status_code=403, detail="Not your terminal session")
    disconnected = await pty_manager.disconnect_non_owners(session)
    pty_manager.set_shared(session_id, shared=False)
    await bus_fire(
        Event(
            name="terminal.presence",
            payload={
                "session_id": session_id,
                "action": "unshared",
                "user": user["sub"],
                "disconnected": disconnected,
                "connections": pty_manager.get_connections(session_id),
            },
        )
    )
    return {"ok": True, "disconnected": disconnected}


@router.get("/api/terminal/{session_id}/connections")
async def get_connections(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    pty_manager: PTYManager = Depends(get_pty_manager),
) -> object:
    """List users connected to a terminal session."""
    session = pty_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Terminal session not found")
    return pty_manager.get_connections(session_id)
