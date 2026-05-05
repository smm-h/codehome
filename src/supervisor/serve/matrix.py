"""Matrix aggregation: per-branch summary data for every dashboard tab.

Used by the home page to render a grid of (branch x tab) cells. Each cell
summarises the tab's state for that branch. Sources are reused from existing
endpoints where possible:

- branches: `list_branches` + user's recent-branches preference for sorting.
- services: in-memory `ServiceManager` singleton.
- todo: filesystem scan of `todo/*.md` (excluding .done/.obsolete/.defer).
- git: `_collect_git_data` from branches.py (ahead/behind/dirty).
- agents: in-memory `AgentSessionManager` singleton (active = pending|running).
- chat: conductor session presence (no running = None).
- inbox: `QuestionStore` filtered by branch + status=pending.
- linear: read from `issue.json` + linear local cache.
- pipeline: read latest staging/production workflow run from prs.json cache.
- review: read review.md existence + prs.json state.
- tests: `tests.json` + most recent tests.run.DONE from events (not persisted -> None).
- worktree: dirty flag reused from git stats.

All data comes from local files / in-memory registries -- no external API calls.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from supervisor.config import list_repos
from supervisor.paths import (
    PROTECTED_BRANCHES,
    branch_dir,
    repo_branches,
    worktree_path,
)
from supervisor.serve.branches import _collect_git_data
from supervisor.utils import load_json

if TYPE_CHECKING:
    from pathlib import Path

# Tabs to include as columns. Order matters -- used by the frontend too.
MATRIX_TABS: tuple[str, ...] = (
    "services",
    "todo",
    "git",
    "agents",
    "chat",
    "inbox",
    "linear",
    "pipeline",
    "review",
    "tests",
    "worktree",
)

# TODO files in these subdirs aren't "active".
_INACTIVE_TODO_DIRS = {".done", ".obsolete", ".defer"}


def _services_cell(qualified: str) -> dict[str, Any] | None:
    """Summarise service state for a branch: running/stopped/stale counts."""
    from supervisor.serve.services import services

    svcs = services.list_for_branch(qualified)
    if not svcs:
        return None

    running = sum(1 for s in svcs if s.state.value == "running")
    stopped = sum(1 for s in svcs if s.state.value == "stopped")
    failed = sum(1 for s in svcs if s.state.value == "failed")
    # Stale deps flag lives in service metadata (populated by deps check).
    stale = sum(1 for s in svcs if s.metadata.get("deps_stale"))
    return {
        "running": running,
        "stopped": stopped,
        "failed": failed,
        "stale": stale,
        "total": len(svcs),
    }


def _todo_cell(bd: Path) -> dict[str, Any] | None:
    """Count active TODOs (branch scope) + recent filenames by mtime."""
    todo_dir = bd / "todo"
    if not todo_dir.is_dir():
        return None

    # Only files directly under todo/ are "active"; subdirs like .done/ are not.
    actives: list[tuple[float, str]] = []
    for entry in todo_dir.iterdir():
        if entry.is_file() and entry.suffix == ".md":
            try:
                actives.append((entry.stat().st_mtime, entry.name))
            except OSError:
                continue

    if not actives:
        return None

    actives.sort(reverse=True)
    recent = [name for _, name in actives[:3]]
    return {"active": len(actives), "recent": recent}


def _git_cell(git_data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract git summary -- just the numbers the cell needs."""
    # When the worktree doesn't exist, all values are None.
    if git_data.get("dirty_files") is None and git_data.get("commits_ahead") is None:
        return None

    modified = git_data.get("dirty_files") or 0
    ahead = git_data.get("commits_ahead") or 0
    behind = git_data.get("commits_behind") or 0
    # If everything is clean and in sync, still emit -- it's meaningful "clean".
    return {
        "modified": modified,
        "ahead": ahead,
        "behind": behind,
        "push_status": git_data.get("push_status"),
    }


def _agents_cell(qualified: str) -> dict[str, Any] | None:
    """Count active agent sessions (pending or running)."""
    from supervisor.serve.agent_sessions import agent_sessions

    sessions = agent_sessions.list_sessions(branch=qualified)
    if not sessions:
        return None

    active = sum(1 for s in sessions if s.status in ("pending", "running"))
    recent = sum(1 for s in sessions[:10])  # recent = capped window
    return {"active": active, "recent": recent}


def _chat_cell(qualified: str) -> dict[str, Any] | None:
    """Summarise conductor chat state: running flag + message count."""
    try:
        from supervisor.serve.conductor import get_session
    except ImportError:
        return None

    try:
        session = get_session(qualified)
    except Exception:
        return None

    if not session:
        return None

    # Treat any active conductor as interesting.
    try:
        msg_count = len(session.messages)
    except AttributeError:
        msg_count = 0
    return {"running": True, "messages": msg_count}


def _inbox_cell(qualified: str) -> dict[str, Any] | None:
    """Count pending agent questions for the branch."""
    try:
        from supervisor.serve.questions import question_store
    except ImportError:
        return None

    try:
        pending = question_store.list(branch=qualified, status="pending")
    except Exception:
        return None

    if not pending:
        return None
    return {"pending": len(pending)}


def _linear_cell(bd: Path, linear_cache: dict[str, Any]) -> dict[str, Any] | None:
    """Return linear issue state from local cache (no API)."""
    issue_link = load_json(bd / "issue.json")
    if not issue_link:
        return None

    identifier = issue_link.get("identifier", "")
    if not identifier:
        return None

    cached = linear_cache.get(identifier)
    if not cached:
        # Linked but cache miss -- still surface the identifier.
        return {"identifier": identifier, "state": None, "state_type": None}

    return {
        "identifier": identifier,
        "state": cached.get("state"),
        "state_type": cached.get("stateType"),
        "title": cached.get("title"),
    }


def _pipeline_cell(bd: Path) -> dict[str, Any] | None:
    """Surface PR status for staging + production from prs.json."""
    prs = load_json(bd / "prs.json", default=[])
    if not prs:
        return None

    staging_state = None
    prod_state = None
    for pr in prs:
        base = pr.get("base", "")
        state = (pr.get("state") or "").upper()
        if base == "staging":
            staging_state = state or None
        elif base in ("production", "main"):
            prod_state = state or None

    if staging_state is None and prod_state is None:
        return None

    return {
        "staging": staging_state,
        "production": prod_state,
    }


def _review_cell(bd: Path) -> dict[str, Any] | None:
    """Summarise review state from review.md + prs.json PR state."""
    prs = load_json(bd / "prs.json", default=[])
    review_file = bd / "review.md"

    has_review = False
    if review_file.is_file():
        try:
            has_review = bool(review_file.read_text().strip())
        except OSError:
            has_review = False

    # Look for an open PR to target (for the cell's "open review" target).
    pr_state: str | None = None
    pr_number: int | None = None
    for pr in prs:
        state = (pr.get("state") or "").upper()
        if state == "OPEN":
            pr_state = state
            pr_number = pr.get("number")
            break
        # Keep last seen as fallback.
        pr_state = state or pr_state
        pr_number = pr.get("number", pr_number)

    if not has_review and not pr_state:
        return None

    return {
        "has_review": has_review,
        "pr_state": pr_state,
        "pr_number": pr_number,
    }


def _tests_cell(bd: Path) -> dict[str, Any] | None:
    """Count tests declared in tests.json. No persisted pass/fail state."""
    tests_data = load_json(bd / "tests.json", default=[])
    if isinstance(tests_data, dict):
        tests_list = tests_data.get("tests", [])
    elif isinstance(tests_data, list):
        tests_list = tests_data
    else:
        tests_list = []

    if not tests_list:
        return None

    return {"count": len(tests_list)}


def _worktree_cell(wt_exists: bool, git_data: dict[str, Any]) -> dict[str, Any] | None:
    """Clean / dirty flag for the worktree."""
    if not wt_exists:
        return {"exists": False}

    dirty = git_data.get("dirty_files") or 0
    locked = bool(git_data.get("worktree_locked"))
    return {
        "exists": True,
        "dirty": int(dirty),
        "locked": locked,
    }


def _collect_cells(
    repo: str,
    branch: str,
    linear_cache: dict[str, Any],
) -> tuple[str, float, str | None, dict[str, Any]]:
    """Collect all tab cells for a single branch. Returns (qualified, created, last_accessed_iso, cells)."""
    qualified = f"{repo}:{branch}"
    bd = branch_dir(repo, branch)
    wt = worktree_path(repo, branch)
    wt_exists = wt.is_dir()

    # Git data is shared between git + worktree cells -- collect once.
    git_data: dict[str, Any] = {
        "commits_behind": None,
        "commits_ahead": None,
        "dirty_files": None,
        "last_commit_time": None,
        "push_status": None,
        "worktree_locked": None,
    }
    if wt_exists:
        git_data = _collect_git_data(repo, branch, wt)

    cells: dict[str, Any] = {
        "services": _services_cell(qualified),
        "todo": _todo_cell(bd),
        "git": _git_cell(git_data),
        "agents": _agents_cell(qualified),
        "chat": _chat_cell(qualified),
        "inbox": _inbox_cell(qualified),
        "linear": _linear_cell(bd, linear_cache),
        "pipeline": _pipeline_cell(bd),
        "review": _review_cell(bd),
        "tests": _tests_cell(bd),
        "worktree": _worktree_cell(wt_exists, git_data),
    }

    created = bd.stat().st_mtime
    last_commit_iso = git_data.get("last_commit_time")
    return qualified, created, last_commit_iso, cells


def get_matrix(recent_branches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate branch/tab data for the home-page matrix view.

    Parameters
    ----------
    recent_branches:
        Optional list of {qualified, timestamp} entries from the user's
        recent-branches preference. When provided, branches are sorted by
        recent-access first, then by last-commit time.

    """
    from supervisor.linear_shared import load_cache as load_linear_cache

    linear_cache = load_linear_cache()

    # Discover all active branches across repos.
    branch_list: list[tuple[str, str]] = []
    for repo_cfg in list_repos():
        repo = repo_cfg.name
        branches_root = repo_branches(repo)
        if not branches_root.is_dir():
            continue
        for entry in branches_root.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name in PROTECTED_BRANCHES or name.startswith("."):
                continue
            branch_list.append((repo, name))

    # Parallelize: git commands dominate the wall time.
    branch_rows: list[dict[str, Any]] = []
    cells_by_branch: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(branch_list) or 1)) as pool:
        futures = {
            pool.submit(_collect_cells, repo, branch, linear_cache): (repo, branch) for repo, branch in branch_list
        }
        for future in as_completed(futures):
            repo, branch = futures[future]
            try:
                qualified, created, last_commit_iso, cells = future.result()
            except Exception as exc:
                # Graceful: skip branches that fail to collect, but log.
                from supervisor.serve.logging_config import get_logger

                get_logger(component="matrix").warning(
                    "matrix cell collection failed",
                    repo=repo,
                    branch=branch,
                    error=str(exc),
                )
                continue
            cells_by_branch[qualified] = cells
            branch_rows.append(
                {
                    "qualified": qualified,
                    "repo": repo,
                    "name": branch,
                    "created": created,
                    "last_commit": last_commit_iso,
                },
            )

    # Build recency index: newer = smaller rank. Missing = infinity.
    recency_rank: dict[str, float] = {}
    if recent_branches:
        for idx, rb_entry in enumerate(recent_branches):
            q = rb_entry.get("qualified") if isinstance(rb_entry, dict) else None
            if not q:
                continue
            # Higher timestamp -> earlier rank. Preference is already sorted,
            # so just use the index directly.
            recency_rank.setdefault(q, float(idx))

    def _sort_key(row: dict[str, Any]) -> tuple[float, float]:
        rank = recency_rank.get(row["qualified"], float("inf"))
        # Fallback: -last_commit (newer first). Branches without commits sink.
        last_commit_ts = 0.0
        if row.get("last_commit"):
            try:
                dt = datetime.fromisoformat(row["last_commit"])
                last_commit_ts = dt.timestamp()
            except (TypeError, ValueError):
                last_commit_ts = 0.0
        return (rank, -last_commit_ts)

    branch_rows.sort(key=_sort_key)

    # Attach ISO last_accessed from recent_branches, fall back to last_commit.
    accessed_ts: dict[str, str | None] = {}
    if recent_branches:
        for rb_entry in recent_branches:
            if not isinstance(rb_entry, dict):
                continue
            q = rb_entry.get("qualified")
            ts = rb_entry.get("timestamp")
            if not q or not isinstance(ts, (int, float)):
                continue
            try:
                accessed_ts[q] = datetime.fromtimestamp(ts / 1000.0, tz=UTC).isoformat()
            except (OverflowError, OSError, ValueError):
                accessed_ts[q] = None

    branches_out = [
        {
            "qualified": r["qualified"],
            "repo": r["repo"],
            "name": r["name"],
            "last_accessed": accessed_ts.get(r["qualified"]) or r["last_commit"],
        }
        for r in branch_rows
    ]

    return {
        "branches": branches_out,
        "tabs": list(MATRIX_TABS),
        "cells": cells_by_branch,
    }
