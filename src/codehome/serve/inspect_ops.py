"""Remote branch inspection: gather commit log, diff stats, file tree, and PR info.

All git commands run against the production worktree (no checkout required).
Functions here are blocking -- callers should wrap with asyncio.to_thread().
"""

import json
from pathlib import Path
from typing import Any

from codehome.config import get_repo, load_repos
from codehome.paths import repo_anchor, worktree_path
from codehome.serve.roster import resolve
from codehome.serve.subprocess_utils import parse_numstat_line, run_gh, run_git, safe_gh_repo


def _validate_repo(repo: str) -> None:
    """Raise ValueError if repo is unknown."""
    repos = load_repos()
    if repo not in repos:
        available = ", ".join(sorted(repos)) if repos else "(none)"
        msg = f"Unknown repo '{repo}'. Available: {available}"
        raise ValueError(msg)


def _branch_exists_on_remote(anchor: Path, branch: str) -> bool:
    """Check if a branch exists on the remote."""
    _, _, rc = run_git(anchor, "rev-parse", "--verify", f"origin/{branch}")
    return rc == 0


def _has_local_worktree(repo: str, branch: str) -> bool:
    """Check if a local worktree exists for this branch."""
    wt = worktree_path(repo, branch)
    return wt.is_dir()


def get_summary(anchor: Path, repo: str, branch: str) -> dict[str, Any]:
    """Return basic branch info from the tip commit on the remote."""
    fmt = "%cn%x00%ce%x00%cI%x00%s"
    out, _, rc = run_git(anchor, "log", "-1", f"--format={fmt}", f"origin/{branch}")
    if rc != 0 or not out.strip():
        return {}

    parts = out.split("\0", 3)
    if len(parts) < 4:
        return {}

    committer_name = parts[0]
    committer_email = parts[1]

    # Resolve git identity to a team roster entry (try email first, then name).
    member = resolve(committer_email) or resolve(committer_name)

    return {
        "branch": branch,
        "repo": repo,
        "committer_name": committer_name,
        "committer_email": committer_email,
        "display_name": member.name if member else committer_name,
        "handle": member.handle if member else None,
        "commit_date": parts[2],
        "commit_subject": parts[3],
        "has_local_worktree": _has_local_worktree(repo, branch),
    }


def get_commits(anchor: Path, repo: str, branch: str) -> list[dict[str, Any]]:
    """Return commits since divergence from production, newest first."""
    cfg = get_repo(repo)
    base = f"origin/{cfg.base_branch}"
    fmt = "%h%x00%s"
    out, _, rc = run_git(
        anchor,
        "log",
        "--oneline",
        "--max-count",
        "500",
        f"--format={fmt}",
        f"{base}..origin/{branch}",
    )
    if rc != 0 or not out.strip():
        return []

    commits = []
    for line in out.strip().splitlines():
        parts = line.split("\0", 1)
        if len(parts) == 2:
            commits.append({"hash": parts[0], "subject": parts[1]})
    return commits


def get_diff_stats(anchor: Path, repo: str, branch: str) -> list[dict[str, Any]]:
    """Per-file insertions/deletions using three-dot diff against production."""
    cfg = get_repo(repo)
    base = f"origin/{cfg.base_branch}"
    out, _, rc = run_git(anchor, "diff", "--numstat", f"{base}...origin/{branch}")
    if rc != 0 or not out.strip():
        return []

    stats = []
    for line in out.strip().splitlines():
        parsed = parse_numstat_line(line)
        if parsed:
            ins, dels, filepath = parsed
            stats.append({"file": filepath, "insertions": ins, "deletions": dels})
    return stats


def build_file_tree(diff_stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Group changed files by directory path into a nested tree structure.

    Leaf nodes are lists of filenames; intermediate nodes are dicts.
    """
    tree: dict[str, Any] = {}
    for entry in diff_stats:
        filepath = entry["file"]
        parts = filepath.split("/")
        # Navigate/create nested dicts for directory segments.
        node = tree
        for segment in parts[:-1]:
            if segment not in node:
                node[segment] = {}
            target = node[segment]
            # If a previous file created a leaf list here, convert to dict.
            if isinstance(target, list):
                node[segment] = {"__files__": target}
                target = node[segment]
            node = target
        # Add the filename to the leaf list.
        filename = parts[-1]
        if "__files__" not in node:
            node["__files__"] = []
        node["__files__"].append(filename)

    # Flatten single-child directory chains and remove __files__ key
    # for a cleaner output structure.
    return _clean_tree(tree)


def _clean_tree(node: dict[str, Any]) -> dict[str, Any]:
    """Recursively clean the tree: replace __files__ lists at leaf level."""
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key == "__files__":
            continue
        if isinstance(value, dict):
            result[key] = _clean_tree(value)
        else:
            result[key] = value

    # Attach files list directly if present.
    if "__files__" in node:
        files = node["__files__"]
        if not result:
            # Pure leaf directory -- return the file list directly.
            return files  # type: ignore[no-any-return]
        # Mixed: subdirs + files at the same level.
        result["__files__"] = files

    return result


def get_totals(commits: list[dict[str, Any]], diff_stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate stats."""
    total_ins = sum(s["insertions"] for s in diff_stats)
    total_dels = sum(s["deletions"] for s in diff_stats)
    return {
        "files_changed": len(diff_stats),
        "total_insertions": total_ins,
        "total_deletions": total_dels,
        "commits": len(commits),
    }


def get_pr_info(repo: str, branch: str) -> dict[str, Any] | None:
    """Fetch PR info from GitHub using gh CLI. Returns None if no PR found."""
    owner_repo = safe_gh_repo(repo)
    if not owner_repo:
        return None

    fields = "number,title,body,state,url,reviews"
    stdout, _, rc = run_gh(
        "pr",
        "list",
        "--head",
        branch,
        "--repo",
        owner_repo,
        "--state",
        "all",
        "--json",
        fields,
        "--limit",
        "1",
        timeout=15,
    )

    if rc != 0 or not stdout.strip():
        return None

    try:
        prs = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    if not prs:
        return None

    pr = prs[0]
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "body": pr.get("body", ""),
        "state": pr.get("state", ""),
        "url": pr.get("url", ""),
        "reviews": pr.get("reviews", []),
    }


def inspect_remote_branch(repo: str, branch: str) -> dict[str, Any]:
    """Gather all inspection data for a remote branch.

    This is the main entry point -- runs all git commands sequentially
    (caller should wrap in asyncio.to_thread for async context).
    Raises ValueError if the repo is unknown or the branch doesn't exist.
    """
    _validate_repo(repo)
    anchor = repo_anchor(repo)

    # No explicit fetch here -- the fetch_scheduler (Phase 3) handles
    # background fetches with proper cooldown. Running git fetch on every
    # inspection call would be redundant and slow.

    if not _branch_exists_on_remote(anchor, branch):
        msg = f"Branch '{branch}' not found on remote for repo '{repo}'"
        raise LookupError(msg)

    summary = get_summary(anchor, repo, branch)
    commits = get_commits(anchor, repo, branch)
    diff_stats = get_diff_stats(anchor, repo, branch)
    file_tree = build_file_tree(diff_stats)
    totals = get_totals(commits, diff_stats)
    pr = get_pr_info(repo, branch)

    return {
        "summary": summary,
        "commits": commits,
        "diff_stats": diff_stats,
        "file_tree": file_tree,
        "totals": totals,
        "pr": pr,
    }
