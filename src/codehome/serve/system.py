"""System status and health checks for the dashboard System tab.

Provides host-level info (disk, CPU, memory, Docker) and extracts
the fast health checks for API consumption.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from codehome.aliases import load_aliases
from codehome.config import load_repos
from codehome.git import iter_all_branches
from codehome.paths import (
    PROTECTED_BRANCHES,
    STAGING_MERGE_STATE,
    SUPPRESS_CHECKS,
    branch_dir,
    repo_branches,
    staging_worktree,
    worktree_path,
)


def get_system_status() -> dict[str, Any]:
    """Return host-level system status.

    Includes Docker availability, disk usage, CPU count, and memory usage.
    Designed to work even when Docker is not installed.
    """
    # Docker status
    docker_running = False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        docker_running = result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Disk usage
    usage = shutil.disk_usage("/")
    disk_usage = {
        "total_gb": round(usage.total / (1024**3), 1),
        "used_gb": round(usage.used / (1024**3), 1),
        "free_gb": round(usage.free / (1024**3), 1),
    }

    # CPU count
    cpu_count = os.cpu_count() or 0

    # Memory from /proc/meminfo (Linux)
    memory = {"total_mb": 0, "used_mb": 0}
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            lines = meminfo.read_text().splitlines()
            info: dict[str, int] = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    # Values in /proc/meminfo are in kB
                    info[key] = int(parts[1])
            total_kb = info.get("MemTotal", 0)
            available_kb = info.get("MemAvailable", 0)
            memory["total_mb"] = round(total_kb / 1024)
            memory["used_mb"] = round((total_kb - available_kb) / 1024)
        except (ValueError, KeyError, OSError):
            pass

    return {
        "docker_running": docker_running,
        "disk_usage": disk_usage,
        "cpu_count": cpu_count,
        "memory": memory,
    }


def _load_suppressed() -> list[str]:
    """Load suppression patterns from suppress-checks.txt (substring match)."""
    if SUPPRESS_CHECKS.exists():
        return [
            line.strip()
            for line in SUPPRESS_CHECKS.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return []


def run_health_checks() -> list[dict[str, Any]]:
    """Run the fast local health checks (1-8 from check.py).

    Returns a list of check result dicts:
      {name: str, status: "pass"|"warn"|"fail", message: str}

    Network-dependent checks (9-12) are excluded for speed.
    """
    suppressed = _load_suppressed()
    results: list[dict[str, Any]] = []

    # 1. Stale staging worktrees
    try:
        stale_staging = []
        for repo_name in load_repos():
            stg = staging_worktree(repo_name)
            if stg.exists() and not STAGING_MERGE_STATE.exists():
                stale_staging.append(repo_name)
        if stale_staging:
            msg = f"Stale staging worktree(s): {', '.join(stale_staging)}"
            if not any(p in msg for p in suppressed):
                results.append({"name": "Stale staging worktrees", "status": "warn", "message": msg})
        else:
            results.append(
                {"name": "Stale staging worktrees", "status": "pass", "message": "No stale staging worktrees"},
            )
    except Exception as exc:
        results.append({"name": "Stale staging worktrees", "status": "fail", "message": str(exc)})

    # 2. Dangling aliases
    try:
        dangling = []
        for repo_name in load_repos():
            aliases = load_aliases(repo_name)
            for old, new in aliases.items():
                if not worktree_path(repo_name, new).exists():
                    dangling.append(f"{old}={new} in {repo_name}")
        if dangling:
            msg = f"Dangling aliases: {', '.join(dangling)}"
            if not any(p in msg for p in suppressed):
                results.append({"name": "Dangling aliases", "status": "warn", "message": msg})
        else:
            results.append({"name": "Dangling aliases", "status": "pass", "message": "No dangling aliases"})
    except Exception as exc:
        results.append({"name": "Dangling aliases", "status": "fail", "message": str(exc)})

    # 3. Orphaned branch dirs
    try:
        branch_pairs = list(iter_all_branches())
        skip_dirs = {"archive"}
        orphaned: list[str] = []
        for repo_name in load_repos():
            branches = repo_branches(repo_name)
            wt_names = {n for r, n in branch_pairs if r == repo_name}
            wt_names.add("general")
            if branches.is_dir():
                orphaned.extend(
                    f"{repo_name}/{d.name}"
                    for d in sorted(branches.iterdir())
                    if d.is_dir() and not d.name.startswith(".") and d.name not in wt_names and d.name not in skip_dirs
                )
        if orphaned:
            msg = f"Orphaned branch dirs: {', '.join(orphaned)}"
            if not any(p in msg for p in suppressed):
                results.append({"name": "Orphaned branch dirs", "status": "warn", "message": msg})
        else:
            results.append({"name": "Orphaned branch dirs", "status": "pass", "message": "No orphaned branch dirs"})
    except Exception as exc:
        results.append({"name": "Orphaned branch dirs", "status": "fail", "message": str(exc)})

    # 4. Stale design markers
    try:
        from codehome.service_protocols import DesignHasMarkers
        from codehome.state.service_registry import services

        stale_design = []
        has_markers = services.get_typed("design.has_markers", DesignHasMarkers)  # type: ignore[type-abstract]
        if has_markers:
            for repo_name, name in branch_pairs:
                if name in PROTECTED_BRANCHES:
                    continue
                wt = worktree_path(repo_name, name)
                if wt.exists() and has_markers(wt):
                    stale_design.append(f"{repo_name}:{name}")
        if stale_design:
            msg = f"Design markers in: {', '.join(stale_design)}"
            if not any(p in msg for p in suppressed):
                results.append({"name": "Stale design markers", "status": "warn", "message": msg})
        else:
            results.append({"name": "Stale design markers", "status": "pass", "message": "No stale design markers"})
    except Exception as exc:
        results.append({"name": "Stale design markers", "status": "fail", "message": str(exc)})

    # 5. Missing issue links
    try:
        from codehome.linear_shared import load_issue_link

        missing_links = []
        for repo_name, name in branch_pairs:
            if name in PROTECTED_BRANCHES:
                continue
            if worktree_path(repo_name, name).exists() and not load_issue_link(repo_name, name):
                missing_links.append(f"{repo_name}:{name}")
        if missing_links:
            msg = f"No Linear issue: {', '.join(missing_links)}"
            if not any(p in msg for p in suppressed):
                results.append({"name": "Missing issue links", "status": "warn", "message": msg})
        else:
            results.append(
                {"name": "Missing issue links", "status": "pass", "message": "All branches have linked issues"},
            )
    except Exception as exc:
        results.append({"name": "Missing issue links", "status": "fail", "message": str(exc)})

    # 6. Empty review.md (only for branches with PRs)
    try:
        from codehome.utils import load_json

        empty_reviews = []
        for repo_name, name in branch_pairs:
            if name in PROTECTED_BRANCHES:
                continue
            prs_file = branch_dir(repo_name, name) / "prs.json"
            prs = load_json(prs_file, default=[])
            if not prs:
                continue
            review_file = branch_dir(repo_name, name) / "review.md"
            if not review_file.exists():
                empty_reviews.append(f"{repo_name}:{name} (missing)")
            elif review_file.stat().st_size == 0:
                empty_reviews.append(f"{repo_name}:{name} (empty)")
        if empty_reviews:
            msg = f"Review issues: {', '.join(empty_reviews)}"
            if not any(p in msg for p in suppressed):
                results.append({"name": "Review.md status", "status": "warn", "message": msg})
        else:
            results.append(
                {"name": "Review.md status", "status": "pass", "message": "All pushed branches have review.md"},
            )
    except Exception as exc:
        results.append({"name": "Review.md status", "status": "fail", "message": str(exc)})

    return results
