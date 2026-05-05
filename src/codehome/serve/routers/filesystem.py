"""Filesystem endpoints: file browser, search, file content."""

import asyncio

from fastapi import APIRouter, HTTPException

from codehome.serve.filesystem import (
    list_files as fs_list_files,
)
from codehome.serve.filesystem import (
    read_file as fs_read_file,
)
from codehome.serve.filesystem import (
    search_files as fs_search_files,
)

router = APIRouter()


@router.get("/api/branches/{qualified}/files")
async def api_list_files(qualified: str, path: str = "") -> object:
    """List directory contents in a branch's worktree."""
    from codehome.paths import worktree_path

    repo, branch = qualified.split(":", 1)
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        raise HTTPException(status_code=404, detail="Worktree not found")
    try:
        return await asyncio.to_thread(fs_list_files, wt, path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/api/branches/{qualified}/files/content")
async def api_read_file(qualified: str, path: str) -> object:
    """Read a file's content from a branch's worktree."""
    from codehome.paths import worktree_path

    repo, branch = qualified.split(":", 1)
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        raise HTTPException(status_code=404, detail="Worktree not found")
    try:
        return await asyncio.to_thread(fs_read_file, wt, path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except ValueError as e:
        detail = str(e)
        # Binary files and too-large files get 422 (unprocessable).
        raise HTTPException(status_code=422, detail=detail) from None


@router.get("/api/branches/{qualified}/files/search")
async def api_search_files(qualified: str, q: str = "", max: int = 50) -> object:  # noqa: A002
    """Search file contents in a branch's worktree using ripgrep."""
    from codehome.paths import worktree_path

    repo, branch = qualified.split(":", 1)
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        raise HTTPException(status_code=404, detail="Worktree not found")
    return await asyncio.to_thread(fs_search_files, wt, q, min(max, 200))
