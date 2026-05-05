"""Branch management endpoints: listing, detail, create, close, rename, compare, explain."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.serve.branches import (
    get_branch_attention,
    get_branch_detail,
    get_branches_table,
    list_branches,
)
from codehome.serve.dependencies import get_event_manager
from codehome.serve.events import EventManager
from codehome.serve.explain_ops import (
    DIFF_LINE_LIMIT,
    generate_summary,
    get_cached_summary,
    get_diff_line_count,
    get_head_hash,
    save_cached_summary,
)
from codehome.serve.git_ops import compare_branches as git_compare_branches
from codehome.serve.remote_branches import list_remote_branches

router = APIRouter()


@router.get("/api/branches")
async def api_list_branches() -> object:
    """List all active branches across repos."""
    return await asyncio.to_thread(list_branches)


@router.get("/api/branches/remote")
async def api_remote_branches(repo: str | None = None) -> object:
    """List remote branches with author info, PR enrichment, and ahead/behind counts.

    Optional ?repo= filter (bag, chat, infra). Returns all repos if omitted.
    """
    return await asyncio.to_thread(list_remote_branches, repo)


@router.post("/api/branches/remote/fetch")
async def api_trigger_fetch(repo: str | None = None, force: bool = False) -> object:
    """Trigger an immediate git fetch for a repo (or all repos if omitted).

    Returns the diff (new/deleted branches). Respects a 60-second cooldown
    unless ?force=true is passed.
    """
    from codehome.serve.fetch_scheduler import fetch_scheduler

    if repo:
        result = await fetch_scheduler.fetch_repo(repo, force=force)
        if result is None:
            cooldown = fetch_scheduler.cooldown_remaining(repo)
            return {"repo": repo, "status": "cooldown", "retry_after_secs": round(cooldown)}
        return {"repo": repo, **result}

    results = await fetch_scheduler.fetch_all()
    return {"repos": dict(results.items())}


@router.get("/api/branches/remote/{repo}/{branch:path}/inspect")
async def api_inspect_remote_branch(repo: str, branch: str) -> object:
    """Inspect a remote branch: commits, diff stats, file tree, PR info.

    Uses {branch:path} to handle slashes in branch names.
    """
    from codehome.serve.inspect_ops import inspect_remote_branch

    try:
        result = await asyncio.to_thread(inspect_remote_branch, repo, branch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return result


@router.get("/api/branches/table")
async def api_branches_table() -> object:
    """Batch endpoint: all data for every branch in one call."""
    return await asyncio.to_thread(get_branches_table)


class CreateBranchRequest(BaseModel):
    repo: str
    name: str
    description: str
    create_issue: bool = True
    remote: bool = False


@router.post("/api/branches")
async def api_create_branch(
    req: CreateBranchRequest,
) -> object:
    """Create a new branch with worktree and optional Linear issue."""
    from codehome.commands.branch import BranchError, create_branch

    # Validate description is non-empty.
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="description must be non-empty")

    # Map BranchError categories to HTTP status codes.
    status_map = {"validation": 400, "conflict": 409, "internal": 500}

    try:
        result = await asyncio.to_thread(
            create_branch,
            req.name,
            req.description,
            repo=req.repo,
            no_issue=not req.create_issue,
            remote=req.remote,
        )
    except BranchError as e:
        status = status_map.get(e.category, 500)
        raise HTTPException(status_code=status, detail=str(e)) from None

    # Notify dashboard clients about the new branch.
    await bus_fire(
        Event(
            name="branch.create.DONE",
            payload={
                "qualified": result["qualified"],
                "repo": req.repo,
                "branch": result["name"],
            },
        )
    )

    return {
        "qualified": result["qualified"],
        "worktree_path": result["worktree_path"],
        "issue_id": result["issue_id"],
    }


class CloseBranchRequest(BaseModel):
    cancel: bool = False
    message: str | None = None
    keep_worktree: bool = False
    force: bool = False


@router.post("/api/branches/{qualified}/close")
async def api_close_branch(
    qualified: str,
    req: CloseBranchRequest,
) -> object:
    """Close/archive a branch: validate guards, archive metadata, update Linear."""
    from codehome.commands.branch import BranchError
    from codehome.commands.fin import close_branch

    # Map BranchError categories to HTTP status codes.
    status_map = {
        "validation": 400,
        "not_found": 404,
        "conflict": 409,
        "internal": 500,
    }

    try:
        result = await asyncio.to_thread(
            close_branch,
            qualified,
            cancel=req.cancel,
            message=req.message,
            keep_worktree=req.keep_worktree,
            force=req.force,
        )
    except BranchError as e:
        status = status_map.get(e.category, 500)
        raise HTTPException(status_code=status, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None

    # Notify dashboard clients about the closed branch.
    await bus_fire(
        Event(
            name="branch.close.DONE",
            payload={
                "qualified": result["qualified"],
                "archived_path": result["archived_path"],
            },
        )
    )

    return {
        "qualified": result["qualified"],
        "archived_path": result["archived_path"],
    }


class RenameBranchRequest(BaseModel):
    new_name: str


@router.post("/api/branches/{qualified}/rename")
async def api_rename_branch(
    qualified: str,
    req: RenameBranchRequest,
) -> object:
    """Rename a branch: move worktree, rename git branch, transfer metadata."""
    from codehome.commands.branch import BranchError, rename_branch

    if not req.new_name.strip():
        raise HTTPException(status_code=400, detail="new_name must be non-empty")

    # Map BranchError categories to HTTP status codes.
    status_map = {
        "validation": 400,
        "conflict": 409,
        "internal": 500,
    }

    try:
        result = await asyncio.to_thread(rename_branch, qualified, req.new_name)
    except BranchError as e:
        status = status_map.get(e.category, 500)
        raise HTTPException(status_code=status, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None

    # Notify dashboard clients about the renamed branch.
    await bus_fire(
        Event(
            name="branch.rename.DONE",
            payload={
                "old_qualified": result["old_qualified"],
                "new_qualified": result["new_qualified"],
            },
        )
    )

    return {
        "old_qualified": result["old_qualified"],
        "new_qualified": result["new_qualified"],
    }


class CompareBranchesRequest(BaseModel):
    branch_a: str
    branch_b: str


@router.post("/api/branches/compare")
async def api_compare_branches(req: CompareBranchesRequest) -> object:
    """Compare two branches: diffstat + per-file change stats."""
    from codehome.aliases import resolve_alias
    from codehome.commands.branch import BranchError

    status_map = {
        "validation": 400,
        "not_found": 404,
        "internal": 500,
    }

    # Parse and validate qualified names (repo:branch).
    for label, val in [("branch_a", req.branch_a), ("branch_b", req.branch_b)]:
        if ":" not in val:
            raise HTTPException(status_code=400, detail=f"{label} must be repo:branch format, got '{val}'")

    repo_a, branch_a = req.branch_a.split(":", 1)
    repo_b, branch_b = req.branch_b.split(":", 1)

    # Resolve aliases so renamed branches still work.
    branch_a = resolve_alias(repo_a, branch_a)
    branch_b = resolve_alias(repo_b, branch_b)

    try:
        result = await asyncio.to_thread(git_compare_branches, repo_a, branch_a, repo_b, branch_b)
    except BranchError as e:
        status = status_map.get(e.category, 500)
        raise HTTPException(status_code=status, detail=str(e)) from None

    return result


@router.get("/api/branches/{qualified}")
async def api_get_branch(qualified: str) -> object:
    """Get detailed info for a single branch (includes git stats)."""
    detail = await asyncio.to_thread(get_branch_detail, qualified)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Branch not found: {qualified}")
    return detail


@router.get("/api/branches/{qualified}/attention")
async def api_branch_attention(qualified: str) -> object:
    """Get attention items for a branch (things needing action)."""
    return await asyncio.to_thread(get_branch_attention, qualified)


@router.post("/api/branches/{qualified}/switch")
async def api_branch_switch(
    qualified: str,
) -> object:
    """Set the user's active branch in the dashboard."""
    # Store on app.state for now; will be replaced with proper session mgmt.
    # NOTE: We import app here to avoid circular imports at module level.
    from codehome.serve.server import app

    if not hasattr(app.state, "current_branch"):
        app.state.current_branch = None
    app.state.current_branch = qualified
    await bus_fire(Event(name="branch.switch", payload={"branch": qualified}))
    return {"ok": True, "branch": qualified}


@router.get("/api/user/current-branch")
async def api_current_branch() -> object:
    """Return the current user's active branch (or null)."""
    from codehome.serve.server import app

    branch = getattr(app.state, "current_branch", None)
    return {"branch": branch}


# -- Branch lifecycle notifications (from CLI) --


class BranchSwitchRequest(BaseModel):
    branch: str


@router.post("/api/branch/switch")
async def branch_switch(
    req: BranchSwitchRequest,
) -> object:
    """Notify the dashboard that the active branch has changed (from CLI)."""
    from codehome.serve.server import app

    # Also update the stored current branch.
    app.state.current_branch = req.branch
    await bus_fire(Event(name="branch.switch", payload={"branch": req.branch}))
    return {"ok": True}


@router.post("/api/branches/{qualified}/explain")
async def api_explain(
    qualified: str,
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Generate an AI summary of the branch's changes.

    Returns cached result if the HEAD commit was already explained.
    Otherwise calls claude -p in a background thread while emitting
    operation.progress SSE events for frontend feedback.
    """
    from codehome.serve.operation_progress import operation_progress

    repo, branch = qualified.split(":", 1)

    async with operation_progress(events, "ai-summary", qualified, "AI Summary") as op:
        # Phase 1: resolve HEAD.
        await op.update("Resolving HEAD...")
        commit_hash = await asyncio.to_thread(get_head_hash, repo, branch)
        if commit_hash is None:
            raise HTTPException(status_code=404, detail=f"Worktree not found for {qualified}")

        # Phase 2: check cache.
        await op.update("Checking cache...")
        cached = await asyncio.to_thread(get_cached_summary, qualified, commit_hash)
        if cached is not None:
            return {"summary": cached, "cached": True, "commit_hash": commit_hash}

        # Phase 3: calculate diff size.
        await op.update("Calculating diff...")
        line_count = await asyncio.to_thread(get_diff_line_count, repo, branch)
        if line_count > DIFF_LINE_LIMIT:
            return {
                "warning": (f"Diff is too large ({line_count} lines). Consider using `v git changes` for a summary."),
                "commit_hash": commit_hash,
            }

        # Phase 4: generate via claude -p (the slow part).
        await op.update("Generating AI summary...")
        try:
            summary = await asyncio.to_thread(generate_summary, repo, branch)
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

        await asyncio.to_thread(save_cached_summary, qualified, commit_hash, summary)

    return {"summary": summary, "cached": False, "commit_hash": commit_hash}
