"""Shared deploy utilities used by both core commands and the deploy plugin.

Functions here were originally in commands/stage.py but are imported by
core commands (branch.py, git_cmd.py), so they live in core to avoid
a core -> plugin dependency.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from supervisor.git import delete_local_branch, remove_worktree
from supervisor.paths import ROOT, repo_anchor, staging_worktree

if TYPE_CHECKING:
    from pathlib import Path

    from supervisor.checks.result import GroupReport
    from supervisor.resolution import BranchContext


def run_extension_group(
    group: str,
    repo: str,
    branch: str,
    worktree: Path,
    branch_ctx: Path | None = None,
) -> GroupReport | None:
    """Discover repo extensions, load them, and run a check group.

    Returns a GroupReport if any checks were found and run, or None if
    the extension system has no checks for this group. Handles the full
    discover -> register -> run pipeline so callers just pass context.
    """
    import asyncio

    from supervisor.checks.registry import CheckContext, CheckRegistry
    from supervisor.checks.runner import run_group
    from supervisor.extensions.discovery import discover_extensions
    from supervisor.extensions.state import load_state, merge_discovered, save_state

    # Discover extensions for this repo, merge state, and register.
    result = discover_extensions(repo, branch)
    if not result.extensions:
        return None

    old_state = load_state(repo, ROOT)
    new_state = merge_discovered(old_state, result.extensions)
    save_state(repo, ROOT, new_state)

    # Build a fresh registry with only repo extensions (avoids conflicts
    # with the global singleton used by core checks).
    registry = CheckRegistry()
    from supervisor.extensions.loader import load_extensions

    load_extensions(repo, branch, ROOT, registry)

    # If no checks ended up in this group, return None.
    if not registry.group(group):
        return None

    ctx = CheckContext(
        root=worktree,
        repo=repo,
        branch=branch,
        branch_dir=branch_ctx,
    )

    return asyncio.run(run_group(group, registry=registry, ctx=ctx))


def native_change_gate(ctx: BranchContext, args: argparse.Namespace) -> int | None:
    """Block push if native iOS/Android files changed without a binary build.

    Returns 1 to abort, or None to proceed. Only applies to the bag repo
    (Capacitor app). Skipped if --skip-native-check is set.

    Runs the "pre-production" extension group via the check runner. The
    native-build-check extension returns a detailed failure message with
    all the context needed for the user to act.
    """
    if getattr(args, "skip_native_check", False):
        return None
    if ctx.repo != "bag":
        return None

    report = run_extension_group(
        "pre-production",
        ctx.repo,
        ctx.branch,
        ctx.worktree,
        ctx.branch_dir,
    )
    if report is None or report.ok:
        return None

    # Print the failure message from the check result(s).
    for r in report.results:
        if r.outcome == "fail" and r.message:
            print(r.message)
    return 1


def remove_staging_worktree(repo: str) -> None:
    """Remove the staging worktree and its local branch."""
    stg = staging_worktree(repo)
    anchor = repo_anchor(repo)
    from supervisor.config import get_repo

    cfg = get_repo(repo)
    staging_branch = cfg.staging_branch or "staging"
    if not stg.exists():
        return
    remove_worktree(anchor, stg)
    delete_local_branch(anchor, staging_branch)
    if stg.parent.exists() and not any(stg.parent.iterdir()):
        stg.parent.rmdir()
