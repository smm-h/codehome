"""Branch listing, detail, and attention data for the dashboard.

Pure functions -- no FastAPI dependencies. Reads filesystem + JSON
files for listing; runs git commands only for detail/attention.
"""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codehome.config import list_repos
from codehome.paths import (
    PROTECTED_BRANCHES,
    branch_dir,
    prod_ref,
    repo_branches,
    worktree_path,
)
from codehome.utils import load_json


def _parse_qualified(qualified: str) -> tuple[str, str]:
    """Split 'repo:branch' into (repo, branch)."""
    repo, branch = qualified.split(":", 1)
    return repo, branch


def _read_issue(bd: Path) -> dict[str, Any] | None:
    """Read issue.json, returning relevant fields or None."""
    data = load_json(bd / "issue.json")
    if not data:
        return None
    # Return the link fields; full issue data lives in Linear cache.
    return {
        "identifier": data.get("identifier", ""),
        "id": data.get("id", ""),
        "team": data.get("team", ""),
    }


def list_branches() -> list[dict[str, Any]]:
    """Scan all repos and return branch metadata from the filesystem.

    Skips protected branches (production, staging, main) and hidden
    dirs (e.g. .staging). No git commands -- fast and safe.
    """
    results = []
    for repo_cfg in list_repos():
        repo = repo_cfg.name
        branches_root = repo_branches(repo)
        if not branches_root.is_dir():
            continue
        for entry in branches_root.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            # Skip protected/base branches and hidden dirs.
            if name in PROTECTED_BRANCHES or name.startswith("."):
                continue
            bd = branch_dir(repo, name)
            wt = worktree_path(repo, name)
            results.append(
                {
                    "qualified": f"{repo}:{name}",
                    "repo": repo,
                    "branch": name,
                    "worktree_exists": wt.is_dir(),
                    "issue": _read_issue(bd),
                    "has_services": (bd / ".services.json").is_file(),
                    "created": bd.stat().st_mtime,
                },
            )
    # Most recent first.
    results.sort(key=lambda b: float(b["created"]), reverse=True)  # type: ignore[arg-type]
    return results


def _git_count(wt: Path, *args: str) -> int:
    """Run a git command that returns a count. Returns 0 on error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(wt), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return 0
        return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return 0


def _git_lines(wt: Path, *args: str) -> int:
    """Run a git command and count non-empty output lines. 0 on error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(wt), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return 0
        return len([l for l in result.stdout.splitlines() if l.strip()])
    except (subprocess.TimeoutExpired, OSError):
        return 0


def get_branch_detail(qualified: str) -> dict[str, Any] | None:
    """Return detailed info for a single branch, including git stats.

    Returns None if the branch directory doesn't exist.
    """
    repo, branch = _parse_qualified(qualified)
    bd = branch_dir(repo, branch)
    if not bd.is_dir():
        return None

    wt = worktree_path(repo, branch)
    wt_exists = wt.is_dir()

    # Git stats (only if worktree exists).
    behind = 0
    dirty = 0
    if wt_exists:
        behind = _git_count(wt, "rev-list", "--count", "HEAD..origin/production")
        dirty = _git_lines(wt, "status", "--porcelain")

    # Read context.md (first 500 chars).
    context = ""
    ctx_file = bd / "context.md"
    if ctx_file.is_file():
        try:
            text = ctx_file.read_text()
            context = text[:500]
        except OSError:
            pass

    return {
        "qualified": qualified,
        "repo": repo,
        "branch": branch,
        "worktree_exists": wt_exists,
        "issue": _read_issue(bd),
        "has_services": (bd / ".services.json").is_file(),
        "created": bd.stat().st_mtime,
        "commits_behind": behind,
        "dirty_files": dirty,
        "context": context,
    }


def _last_commit_timestamp(wt: Path) -> float | None:
    """Unix timestamp of HEAD commit. None on error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(wt), "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def get_branch_attention(qualified: str) -> list[dict[str, Any]]:
    """Return attention items for a branch (things that need action)."""
    repo, branch = _parse_qualified(qualified)
    bd = branch_dir(repo, branch)
    if not bd.is_dir():
        return []

    items: list[dict[str, Any]] = []
    wt = worktree_path(repo, branch)
    wt_exists = wt.is_dir()

    # No issue linked.
    issue = _read_issue(bd)
    if not issue:
        items.append({"type": "no_issue", "message": "No Linear issue linked"})

    if wt_exists:
        # Commits behind production.
        behind = _git_count(wt, "rev-list", "--count", "HEAD..origin/production")
        if behind > 50:
            items.append(
                {
                    "type": "behind",
                    "message": f"{behind} commits behind production",
                    "value": behind,
                },
            )

        # Dirty files.
        dirty = _git_lines(wt, "status", "--porcelain")
        if dirty > 0:
            items.append(
                {
                    "type": "dirty",
                    "message": f"{dirty} uncommitted file(s)",
                    "value": dirty,
                },
            )

        # Stale (no commits in 7 days).
        last_ts = _last_commit_timestamp(wt)
        if last_ts is not None:
            days_ago = (time.time() - last_ts) / 86400
            if days_ago > 7:
                items.append(
                    {
                        "type": "stale",
                        "message": f"No commits in {int(days_ago)} days",
                        "value": int(days_ago),
                    },
                )

    return items


# ---------------------------------------------------------------------------
# Batch table endpoint -- single call returns everything for all branches
# ---------------------------------------------------------------------------


def _git_output(wt: Path, *args: str, timeout: int = 10) -> str:
    """Run a git command and return stdout. Empty string on error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(wt), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _collect_git_data(repo: str, branch: str, wt: Path) -> dict[str, Any]:
    """Gather all git stats for a single worktree. Runs multiple git commands."""
    ref = prod_ref(repo)

    behind = _git_count(wt, "rev-list", "--count", f"HEAD..{ref}")
    ahead = _git_count(wt, "rev-list", "--count", f"{ref}..HEAD")
    dirty = _git_lines(wt, "status", "--porcelain")

    # Last commit info.
    last_log = _git_output(wt, "log", "-1", "--format=%ct\t%s")
    last_commit_time = None
    last_commit_message = ""
    if last_log and "\t" in last_log:
        ts_str, msg = last_log.split("\t", 1)
        try:
            ts = float(ts_str)
            last_commit_time = datetime.fromtimestamp(ts, tz=UTC).isoformat()
        except ValueError:
            pass
        last_commit_message = msg

    # Diff stats vs production (files changed, additions, deletions).
    total_files_changed = 0
    total_additions = 0
    total_deletions = 0
    stat_line = _git_output(wt, "diff", "--shortstat", ref)
    if stat_line:
        # e.g. "22 files changed, 1373 insertions(+), 253 deletions(-)"
        import re

        m_files = re.search(r"(\d+) file", stat_line)
        m_ins = re.search(r"(\d+) insertion", stat_line)
        m_del = re.search(r"(\d+) deletion", stat_line)
        if m_files:
            total_files_changed = int(m_files.group(1))
        if m_ins:
            total_additions = int(m_ins.group(1))
        if m_del:
            total_deletions = int(m_del.group(1))

    # Push status: unpushed commits (ahead of remote tracking branch).
    push_status = None
    unpushed_out = _git_output(wt, "rev-list", "--count", "@{u}..HEAD")
    if unpushed_out:
        try:
            n = int(unpushed_out)
            push_status = f"{n} unpushed" if n > 0 else "up to date"
        except ValueError:
            pass

    # Worktree locked (readonly = merged to production).
    worktree_locked = not os.access(wt, os.W_OK)

    return {
        "commits_behind": behind,
        "commits_ahead": ahead,
        "dirty_files": dirty,
        "last_commit_time": last_commit_time,
        "last_commit_message": last_commit_message,
        "total_files_changed": total_files_changed,
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "push_status": push_status,
        "worktree_locked": worktree_locked,
    }


def _collect_attention(
    repo: str,
    branch: str,
    wt_exists: bool,
    behind: int,
    dirty: int,
    last_commit_time: str | None,
    has_issue: bool,
) -> list[dict[str, Any]]:
    """Build attention items from already-computed data (no extra git calls)."""
    items: list[dict[str, Any]] = []

    if not has_issue:
        items.append({"type": "no_issue", "message": "No Linear issue linked"})

    if wt_exists:
        if behind > 50:
            items.append(
                {
                    "type": "behind",
                    "message": f"{behind} commits behind production",
                    "value": behind,
                },
            )
        if dirty > 0:
            items.append(
                {
                    "type": "dirty",
                    "message": f"{dirty} uncommitted file(s)",
                    "value": dirty,
                },
            )
        # Stale check from last commit time.
        if last_commit_time:
            try:
                dt = datetime.fromisoformat(last_commit_time)
                days_ago = (time.time() - dt.timestamp()) / 86400
                if days_ago > 7:
                    items.append(
                        {
                            "type": "stale",
                            "message": f"No commits in {int(days_ago)} days",
                            "value": int(days_ago),
                        },
                    )
            except (ValueError, OSError):
                pass

    return items


def _collect_branch_table_row(
    repo: str,
    branch: str,
    linear_cache: dict[str, Any],
    reverse_alias_map: dict[str, Any],
) -> dict[str, Any]:
    """Collect all table data for a single branch. Designed to run in a thread."""
    qualified = f"{repo}:{branch}"
    bd = branch_dir(repo, branch)
    wt = worktree_path(repo, branch)
    wt_exists = wt.is_dir()

    created = bd.stat().st_mtime

    # -- Git data (only if worktree exists) --------------------------------
    git_data = {
        "commits_behind": None,
        "commits_ahead": None,
        "dirty_files": None,
        "last_commit_time": None,
        "last_commit_message": None,
        "total_files_changed": None,
        "total_additions": None,
        "total_deletions": None,
        "push_status": None,
        "worktree_locked": None,
    }
    if wt_exists:
        git_data = _collect_git_data(repo, branch, wt)

    # -- Linear issue (from local cache, no API calls) ---------------------
    issue_link = load_json(bd / "issue.json")
    issue_id = None
    issue_title = None
    issue_state = None
    issue_state_type = None
    issue_priority = None
    issue_priority_label = None
    issue_labels = None
    issue_due_date = None
    issue_estimate = None
    issue_assignee = None

    if issue_link:
        identifier = issue_link.get("identifier", "")
        issue_id = identifier
        cached = linear_cache.get(identifier)
        if cached:
            issue_title = cached.get("title")
            issue_state = cached.get("state")
            issue_state_type = cached.get("stateType")
            issue_priority = cached.get("priority")
            issue_priority_label = cached.get("priorityLabel")
            issue_labels = cached.get("labels", [])
            issue_due_date = cached.get("dueDate")
            issue_estimate = cached.get("estimate")
            issue_assignee = cached.get("assignee")

    # -- Pipeline (from prs.json, no GitHub API) ---------------------------
    prs = load_json(bd / "prs.json", default=[])
    staging_pr_url = None
    staging_pr_number = None
    production_pr_url = None
    production_pr_number = None
    for pr in prs:
        base = pr.get("base", "")
        if base == "staging":
            staging_pr_url = pr.get("url")
            staging_pr_number = pr.get("number")
        elif base in ("production", "main"):
            production_pr_url = pr.get("url")
            production_pr_number = pr.get("number")

    # -- Push metadata (from push.json) ------------------------------------
    push_data = load_json(bd / "push.json", default={})
    last_push = push_data.get("last_push")
    push_count = push_data.get("push_count", 0)

    # -- Quality (filesystem checks) --------------------------------------
    review_file = bd / "review.md"
    review_exists = review_file.is_file()
    review_empty = True
    if review_exists:
        try:
            review_empty = not review_file.read_text().strip()
        except OSError:
            pass

    # Design mode -- delegate to the design plugin via service registry.
    from codehome.state.service_registry import services as service_registry

    design_active = False
    design_spec = None
    if service_registry.has("design.active_state"):
        design_active, design_spec = service_registry.call("design.active_state", branch)

    # Test count from tests.json.
    tests_data = load_json(bd / "tests.json", default=[])
    test_count = len(tests_data) if isinstance(tests_data, list) else 0

    # Todo counts.
    todo_dir = bd / "todo"
    todo_count = 0
    todo_done_count = 0
    if todo_dir.is_dir():
        for f in todo_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                todo_count += 1
        done_dir = todo_dir / ".done"
        if done_dir.is_dir():
            for f in done_dir.iterdir():
                if f.is_file() and f.suffix == ".md":
                    todo_done_count += 1

    # -- Attention (reuse already-computed values) -------------------------
    attention_items = _collect_attention(
        repo,
        branch,
        wt_exists,
        git_data.get("commits_behind") or 0,
        git_data.get("dirty_files") or 0,
        git_data.get("last_commit_time"),
        issue_link is not None,
    )

    # -- Runtime (services + agents from in-memory singletons) -------------
    from codehome.serve.agent_sessions import agent_sessions
    from codehome.serve.services import services

    running_services = len([s for s in services.list_for_branch(qualified) if s.state.value == "running"])
    active_agents = len(
        [s for s in agent_sessions.list_sessions(branch=qualified) if s.status in ("pending", "running")],
    )

    # -- Aliases -----------------------------------------------------------
    aliases = reverse_alias_map.get(branch, [])

    return {
        # Core
        "qualified": qualified,
        "repo": repo,
        "branch": branch,
        "is_current": False,  # Populated by caller or frontend.
        "worktree_exists": wt_exists,
        "created": created,
        # Git
        **git_data,
        "last_push": last_push,
        "push_count": push_count,
        # Linear
        "issue_id": issue_id,
        "issue_title": issue_title,
        "issue_state": issue_state,
        "issue_state_type": issue_state_type,
        "issue_priority": issue_priority,
        "issue_priority_label": issue_priority_label,
        "issue_labels": issue_labels,
        "issue_due_date": issue_due_date,
        "issue_estimate": issue_estimate,
        "issue_assignee": issue_assignee,
        # Pipeline
        "staging_pr_url": staging_pr_url,
        "staging_pr_number": staging_pr_number,
        "production_pr_url": production_pr_url,
        "production_pr_number": production_pr_number,
        # Quality
        "review_exists": review_exists,
        "review_empty": review_empty,
        "design_active": design_active,
        "design_spec": design_spec,
        "test_count": test_count,
        "todo_count": todo_count,
        "todo_done_count": todo_done_count,
        # Attention
        "attention_count": len(attention_items),
        "attention_items": attention_items,
        # Runtime
        "running_services": running_services,
        "active_agents": active_agents,
        "aliases": aliases,
    }


def get_branches_table() -> list[dict[str, Any]]:
    """Return all data needed for the branch table view in a single call.

    Parallelizes per-branch data collection with a thread pool to avoid
    sequential git command overhead. Reads Linear data from the local
    cache and PR data from prs.json -- no external API calls.
    """
    from codehome.aliases import reverse_aliases
    from codehome.linear_shared import load_cache as load_linear_cache

    # Load shared data once (read by all threads).
    linear_cache = load_linear_cache()

    # Discover branches and build per-repo alias maps.
    branch_list: list[tuple[str, str]] = []  # (repo, branch_name)
    all_reverse_aliases: dict[str, dict[str, list[str]]] = {}

    for repo_cfg in list_repos():
        repo = repo_cfg.name
        branches_root = repo_branches(repo)
        if not branches_root.is_dir():
            continue
        # Build reverse alias map once per repo.
        all_reverse_aliases[repo] = reverse_aliases(repo)
        for entry in branches_root.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name in PROTECTED_BRANCHES or name.startswith("."):
                continue
            branch_list.append((repo, name))

    # Collect data in parallel -- git commands are the bottleneck.
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(branch_list) or 1)) as pool:
        futures = {
            pool.submit(
                _collect_branch_table_row,
                repo,
                branch,
                linear_cache,
                all_reverse_aliases.get(repo, {}),
            ): (repo, branch)
            for repo, branch in branch_list
        }
        for future in as_completed(futures):
            repo, _branch = futures[future]
            try:
                row = future.result()
                results.append(row)
            except Exception:
                # Graceful degradation: skip branches that fail to collect.
                pass

    # Most recent first.
    results.sort(key=lambda b: b["created"], reverse=True)
    return results
