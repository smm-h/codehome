"""TODO endpoints: CRUD and move operations."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from supervisor.serve.todo_ops import (
    create_todo as todo_create,
)
from supervisor.serve.todo_ops import (
    list_todos as todo_list,
)
from supervisor.serve.todo_ops import (
    move_todo as todo_move,
)
from supervisor.serve.todo_ops import (
    read_todo as todo_read,
)
from supervisor.serve.todo_ops import (
    update_todo as todo_update,
)

router = APIRouter()


@router.get("/api/branches/{qualified}/todos")
async def api_list_todos(qualified: str) -> object:
    """List all TODO files at branch, repo, and super levels."""
    repo, branch = qualified.split(":", 1)
    return await asyncio.to_thread(todo_list, repo, branch)


@router.get("/api/branches/{qualified}/todos/content")
async def api_read_todo(qualified: str, path: str) -> object:
    """Read a single TODO file's content."""
    repo, branch = qualified.split(":", 1)
    try:
        return await asyncio.to_thread(todo_read, repo, branch, path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


class TodoCreateRequest(BaseModel):
    level: str
    filename: str
    content: str


@router.post("/api/branches/{qualified}/todos")
async def api_create_todo(qualified: str, req: TodoCreateRequest) -> object:
    """Create a new TODO markdown file."""
    repo, branch = qualified.split(":", 1)
    try:
        return await asyncio.to_thread(
            todo_create,
            repo,
            branch,
            req.level,
            req.filename,
            req.content,
        )
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


class TodoUpdateRequest(BaseModel):
    content: str


@router.put("/api/branches/{qualified}/todos/content")
async def api_update_todo(qualified: str, path: str, req: TodoUpdateRequest) -> object:
    """Update a TODO file's content."""
    repo, branch = qualified.split(":", 1)
    try:
        return await asyncio.to_thread(
            todo_update,
            repo,
            branch,
            path,
            req.content,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


class TodoMoveRequest(BaseModel):
    target: str


@router.post("/api/branches/{qualified}/todos/move")
async def api_move_todo(qualified: str, path: str, req: TodoMoveRequest) -> object:
    """Move a TODO file between status subdirectories."""
    repo, branch = qualified.split(":", 1)
    try:
        return await asyncio.to_thread(
            todo_move,
            repo,
            branch,
            path,
            req.target,
        )
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
