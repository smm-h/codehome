"""Shared branch activation logic used by v switch.

Centralizes the 5-step activation sequence:
1. Resolve aliases
2. Validate worktree exists
3. Advance Linear issue to In Progress
4. Append session header to progress.md
5. Commit branch metadata
"""

from typing import Any

from codehome.aliases import require_worktree, resolve_alias
from codehome.branch_git import commit_branch_dir
from codehome.linear_shared import auto_advance_state, load_issue_link
from codehome.paths import context_file, worktree_path
from codehome.progress import append_session_header


def activate_branch(
    repo: str,
    branch: str,
    commit_prefix: str = "v activate",
) -> dict[str, Any]:
    """Activate a branch: resolve alias, validate, update session, advance Linear, log progress.

    Args:
        repo: repository shorthand (e.g. "bag")
        branch: branch name (e.g. "fix-auth"), may be an alias
        commit_prefix: prefix for the git commit message; the function
                    formats the full message as "{commit_prefix}: session N"

    Returns a dict with activation results for callers to use:
        repo, branch (after alias resolution), worktree path,
        context file path (if non-empty), Linear identifier, session number.

    """
    # Step 1: follow alias chains (prints hint to stderr if aliased).
    branch = resolve_alias(repo, branch)

    # Step 2: validate worktree exists.
    require_worktree(repo, branch)

    # Step 3: advance Linear issue to "In Progress" (graceful degradation).
    auto_advance_state(repo, branch, "In Progress")

    # Step 4: append ## Session N header to progress.md.
    n = append_session_header(repo, branch)

    # Step 5: commit branch metadata.
    msg = f"{commit_prefix}: session {n}"
    commit_branch_dir(repo, branch, msg)

    # Build return dict with useful info for callers.
    wt = worktree_path(repo, branch)
    ctx = context_file(repo, branch)
    ctx_path: str | None = None
    if ctx.exists() and ctx.stat().st_size > 0:
        ctx_path = str(ctx)

    # Look up linked Linear issue identifier (if any).
    link = load_issue_link(repo, branch)
    linear_id: str | None = link["identifier"] if link else None

    return {
        "repo": repo,
        "branch": branch,
        "worktree": str(wt),
        "context": ctx_path,
        "linear": linear_id,
        "session_number": n,
    }
