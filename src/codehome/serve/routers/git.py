"""Git operation endpoints: diff, commits, changes, push, rebase, file-diff."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from codehome.serve.dependencies import get_event_manager
from codehome.serve.events import EventManager
from codehome.serve.git_ops import (
    get_changes as git_get_changes,
)
from codehome.serve.git_ops import (
    get_commits as git_get_commits,
)
from codehome.serve.git_ops import (
    get_diff as git_get_diff,
)
from codehome.serve.git_ops import (
    get_file_diff as git_get_file_diff,
)
from codehome.serve.git_ops import (
    push_branch as git_push_branch,
)

# Category-to-HTTP-status mapping for BranchError (moved from rebase_ops.py
# because HTTP status codes are a router concern, not a business-logic one).
BRANCH_ERROR_STATUS_MAP: dict[str, int] = {
    "validation": 400,
    "not_found": 404,
    "conflict": 409,
    "internal": 500,
}

router = APIRouter()


@router.get("/api/branches/{qualified}/git/diff")
async def api_git_diff(qualified: str) -> object:
    """Get full diff (files + patch) for a branch vs its base ref."""
    repo, branch = qualified.split(":", 1)
    return await asyncio.to_thread(git_get_diff, repo, branch)


@router.get("/api/branches/{qualified}/git/changes")
async def api_git_changes(qualified: str) -> object:
    """Get changed files with stats, including uncommitted changes."""
    repo, branch = qualified.split(":", 1)
    return await asyncio.to_thread(git_get_changes, repo, branch)


@router.get("/api/branches/{qualified}/git/commits")
async def api_git_commits(qualified: str) -> object:
    """Get commit log for a branch vs its base ref."""
    repo, branch = qualified.split(":", 1)
    return await asyncio.to_thread(git_get_commits, repo, branch)


@router.post("/api/branches/{qualified}/git/push")
async def api_git_push(qualified: str) -> object:
    """Push branch to remote with --force-with-lease."""
    repo, branch = qualified.split(":", 1)
    result = await asyncio.to_thread(git_push_branch, repo, branch)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@router.get("/api/branches/{qualified}/git/file-diff")
async def api_git_file_diff(qualified: str, path: str, compare_ref: str | None = None) -> object:
    """Get the diff for a single file.

    When *compare_ref* is provided (e.g. ``branchA..branchB``), diff that
    range instead of the branch's default base ref.  Used by the cross-branch
    compare page.
    """
    repo, branch = qualified.split(":", 1)
    diff = await asyncio.to_thread(
        git_get_file_diff,
        repo,
        branch,
        path,
        compare_ref,
    )
    return {"path": path, "diff": diff}


@router.post("/api/branches/{qualified}/rebase")
async def api_rebase(
    qualified: str,
    dry_run: bool = False,
    events: EventManager = Depends(get_event_manager),
) -> object:
    """Rebase a branch onto latest production.

    With dry_run=true: returns analysis of what would happen.
    Without dry_run: starts rebase in background, streams progress via SSE.
    """
    from codehome.commands.branch import BranchError
    from codehome.serve.rebase_ops import (
        rebase_dry_run,
        run_rebase_in_background,
    )

    repo, branch = qualified.split(":", 1)

    if dry_run:
        try:
            result = await asyncio.to_thread(rebase_dry_run, repo, branch)
        except BranchError as e:
            status = BRANCH_ERROR_STATUS_MAP.get(e.category, 500)
            raise HTTPException(status_code=status, detail=str(e)) from None
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from None
        return result

    # Pre-validate before spawning the background task so we can return
    # an immediate error for obvious problems (locked, already in progress).
    try:
        await asyncio.to_thread(rebase_dry_run, repo, branch)
    except BranchError as e:
        status = BRANCH_ERROR_STATUS_MAP.get(e.category, 500)
        raise HTTPException(status_code=status, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None

    asyncio.create_task(run_rebase_in_background(repo, branch, qualified, events))
    return {"started": True}
