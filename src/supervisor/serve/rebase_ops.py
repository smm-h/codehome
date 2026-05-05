"""Rebase operations for the server API.

Provides dry-run analysis and background execution of git rebase,
reusing logic from the CLI's cmd_rebase but raising BranchError
instead of calling die().  Also provides async SSE-broadcasting
orchestration for the background rebase flow.
"""

import asyncio
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supervisor.serve.events import EventManager

from supervisor.bus import Event
from supervisor.bus import fire as bus_fire
from supervisor.commands.branch import BranchError
from supervisor.git import commits_behind, git, git_passthrough
from supervisor.migrations import detect_stale_migrations
from supervisor.paths import (
    REBASE_STATE,
    base_ref,
    branch_dir,
    repo_anchor,
    worktree_path,
)
from supervisor.serve.subprocess_utils import run_git


def _resolve_worktree(repo: str, branch: str) -> Path:
    """Return worktree path or raise BranchError if it doesn't exist."""
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        msg = f"worktree not found for {repo}:{branch}"
        raise BranchError(
            msg,
            category="not_found",
        )
    return wt


def _current_base_sha(wt: Path, ref: str) -> str:
    """Return the merge-base between HEAD and the target ref."""
    out, _, rc = run_git(wt, "merge-base", "HEAD", ref)
    return out.strip()[:12] if rc == 0 else "(unknown)"


def _target_base_sha(wt: Path, ref: str) -> str:
    """Return the short SHA of the target ref."""
    out, _, rc = run_git(wt, "rev-parse", "--short=12", ref)
    return out.strip() if rc == 0 else "(unknown)"


def rebase_dry_run(repo: str, branch: str) -> dict[str, object]:
    """Analyze what a rebase would do without executing it.

    Raises BranchError on validation failures.
    Returns dict with: commits_to_replay, stale_migrations,
    current_base, target_base.
    """
    from supervisor.git import is_worktree_locked

    wt = _resolve_worktree(repo, branch)

    if is_worktree_locked(repo, branch):
        msg = f"branch '{repo}:{branch}' is merged to production and locked"
        raise BranchError(
            msg,
            category="validation",
        )

    # Guard: refuse if a rebase is already in progress.
    if REBASE_STATE.is_file():
        msg = "a rebase is already in progress"
        raise BranchError(
            msg,
            category="conflict",
        )

    # Sync origin to get accurate staleness counts.
    anchor = repo_anchor(repo)
    subprocess.run(
        ["git", "-C", str(anchor), "fetch", "--prune"],
        capture_output=True,
        text=True,
    )

    stale = detect_stale_migrations(wt, repo)
    behind = commits_behind(branch, repo)
    ref = base_ref(repo, branch)
    current = _current_base_sha(wt, ref)
    target = _target_base_sha(wt, ref)

    return {
        "commits_to_replay": behind,
        "stale_migrations": bool(stale),
        "stale_migration_count": len(stale),
        "current_base": current,
        "target_base": target,
    }


def rebase_execute(
    repo: str,
    branch: str,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Execute a full rebase onto latest production.

    This runs synchronously (designed to be called from a background thread).
    Calls progress_callback(line: str) for each progress line if provided.

    Returns dict with: ok, message, conflicts (list of files if conflict).
    Raises BranchError on pre-validation failures.
    """
    import fnmatch

    from supervisor.commands.branch import _sync
    from supervisor.commands.git_cmd import (
        _conflict_file_list,
        _detect_skip_worktree,
        _is_rebase_in_progress,
        _remigrate,
        _restore_skip_worktree,
        _save_rebase_state,
    )
    from supervisor.git import (
        ignorable_patterns,
        is_worktree_locked,
    )

    def log(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    wt = _resolve_worktree(repo, branch)

    if is_worktree_locked(repo, branch):
        msg = f"branch '{repo}:{branch}' is merged to production and locked"
        raise BranchError(
            msg,
            category="validation",
        )

    # Guard: refuse if a rebase is already in progress.
    if REBASE_STATE.is_file():
        msg = "a rebase is already in progress"
        raise BranchError(
            msg,
            category="conflict",
        )

    log(f"Syncing {repo} with origin...")
    _sync(repo)

    # Load branch.json for rebase state persistence (continue/abort).
    meta_path = branch_dir(repo, branch) / "branch.json"
    meta: dict[str, Any] | None = None
    try:
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        pass

    stale = detect_stale_migrations(wt, repo)

    if stale:
        log(f"Remigrating {len(stale)} stale migration(s)...")
        _remigrate(wt, repo)
        log("Remigration complete.")

    # Handle dirty files: auto-stash ignorable ones, block on real changes.
    # Use check=False: git() with check=True calls sys.exit() on failure,
    # which would crash the server process.
    dirty_output = git(wt, "status", "--porcelain", check=False)
    stashed_ignorable = False
    if dirty_output:
        patterns = ignorable_patterns()
        dirty_files = []
        for line in dirty_output.splitlines():
            filepath = line[3:].split(" -> ")[0]
            dirty_files.append(filepath)
        non_ignorable = [f for f in dirty_files if not any(fnmatch.fnmatch(f, p) for p in patterns)]
        if non_ignorable:
            raise BranchError(
                "uncommitted changes block rebase -- commit first: " + ", ".join(non_ignorable[:5]),
                category="validation",
            )
        log("Stashing ignorable dirty files...")
        git(wt, "stash", "--quiet", check=False)
        stashed_ignorable = True

    # Save skip-worktree state.
    sw_files = _detect_skip_worktree(wt)
    saved: dict[str, bytes | None] = {}
    for f in sw_files:
        p = wt / f
        saved[f] = p.read_bytes() if p.exists() else None
        git(wt, "update-index", "--no-skip-worktree", f, check=False)
        git(wt, "checkout", "--", f, check=False)

    ref = base_ref(repo, branch)

    log(f"Rebasing {branch} onto {ref}...")

    rc = git_passthrough(wt, "rebase", ref)

    if rc != 0:
        # Check if this is a conflict situation.
        if _is_rebase_in_progress(wt):
            # Save rebase state so continue/abort work from CLI.
            _save_rebase_state(wt, branch, repo, saved, stashed_ignorable, str(meta_path), meta)
            conflicts = _conflict_file_list(wt)
            log(f"Rebase paused: conflicts in {len(conflicts)} file(s)")
            for f in conflicts:
                log(f"  {f}")
            return {
                "ok": False,
                "message": f"Rebase paused: conflicts in {len(conflicts)} file(s)",
                "conflicts": conflicts,
            }
        # Not a conflict -- unexpected failure. Still restore state.
        _restore_skip_worktree(wt, saved)
        if stashed_ignorable:
            git(wt, "stash", "pop", "--quiet", check=False)
        return {
            "ok": False,
            "message": "Rebase failed unexpectedly",
            "conflicts": [],
        }

    # Success: restore skip-worktree and pop stash.
    log("Restoring skip-worktree files...")
    _restore_skip_worktree(wt, saved)
    if stashed_ignorable:
        git(wt, "stash", "pop", "--quiet", check=False)

    log("Rebase completed successfully.")
    return {
        "ok": True,
        "message": "Rebase completed successfully",
        "conflicts": [],
    }


async def run_rebase_in_background(
    repo: str,
    branch: str,
    qualified: str,
    events: "EventManager",
) -> None:
    """Background task that runs the rebase and broadcasts SSE events.

    Designed to be wrapped in asyncio.create_task() by the router.
    The EventManager is a plain Python class, not a FastAPI dependency.
    """
    loop = asyncio.get_event_loop()

    def on_progress(line: str) -> None:
        # Schedule bus fire from the sync callback.
        asyncio.run_coroutine_threadsafe(
            bus_fire(
                Event(
                    name="rebase.progress",
                    payload={
                        "qualified": qualified,
                        "line": line,
                    },
                )
            ),
            loop,
        )

    try:
        result = await asyncio.to_thread(
            rebase_execute,
            repo,
            branch,
            progress_callback=on_progress,
        )
        if result["ok"]:
            await bus_fire(
                Event(
                    name="rebase.DONE",
                    payload={
                        "qualified": qualified,
                        "message": result["message"],
                        "outcome": "success",
                    },
                )
            )
        elif result.get("conflicts"):
            await bus_fire(
                Event(
                    name="rebase.conflict",
                    payload={
                        "qualified": qualified,
                        "message": result["message"],
                        "conflicts": result["conflicts"],
                    },
                )
            )
        else:
            await bus_fire(
                Event(
                    name="rebase.DONE",
                    payload={
                        "qualified": qualified,
                        "message": result["message"],
                        "outcome": "error",
                    },
                )
            )
    except BranchError as e:
        await bus_fire(
            Event(
                name="rebase.DONE",
                payload={
                    "qualified": qualified,
                    "message": str(e),
                    "outcome": "error",
                },
            )
        )
    except Exception as e:
        await bus_fire(
            Event(
                name="rebase.DONE",
                payload={
                    "qualified": qualified,
                    "message": str(e),
                    "outcome": "error",
                },
            )
        )
