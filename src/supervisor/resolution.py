"""Centralized branch resolution: qualified names and BranchContext.

Every command resolves context through resolve(), which returns a BranchContext
with repo, branch, config, and pre-computed paths. Qualified names use the
repo:branch format (e.g. bag:fix-auth).

Priority: explicit arg > VB env var > CWD inference > error.
"""

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from supervisor.config import RepoConfig, get_repo, list_repos
from supervisor.git import list_worktrees
from supervisor.paths import branch_dir, worktree_path
from supervisor.utils import die


@dataclass
class BranchContext:
    """Everything a command needs about the active branch."""

    repo: str  # "bag"
    branch: str  # "fix-auth"
    qualified: str  # "bag:fix-auth"
    config: RepoConfig  # from repos.toml
    branch_dir: Path  # repos/bag/branches/fix-auth/
    worktree: Path  # repos/bag/branches/fix-auth/worktree/


def _infer_repo(branch: str) -> str:
    """Search all repos for a worktree matching *branch*. Returns repo name.

    Dies if the branch is not found in any repo, or if it exists in
    multiple repos (ambiguous).
    """
    matches: list[str] = []
    for repo_cfg in list_repos():
        if branch in list_worktrees(repo_cfg.name):
            matches.append(repo_cfg.name)

    if len(matches) == 1:
        return matches[0]
    if not matches:
        die(f"branch '{branch}' not found in any repo")
    # Ambiguous -- exists in multiple repos.
    qualified = ", ".join(f"{r}:{branch}" for r in sorted(matches))
    die(f"branch '{branch}' exists in multiple repos: {qualified}\n  use the repo:branch format to disambiguate")
    return ""  # unreachable, satisfies type checker


def _infer_from_cwd() -> str | None:
    """Try to infer repo:branch from the current working directory.

    Walks up from CWD looking for superv/config.toml (the in-repo marker).
    If found, reads the project name and determines the branch from git.
    Returns a qualified "repo:branch" string, or None if inference fails.
    """
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        return None

    # Walk up looking for superv/config.toml
    for parent in [cwd, *cwd.parents]:
        marker = parent / "superv" / "config.toml"
        if marker.is_file():
            try:
                data = tomllib.loads(marker.read_text())
                repo = data.get("project", {}).get("name")
                if not repo:
                    return None
                # Get branch from git
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, cwd=str(parent),
                )
                if result.returncode == 0:
                    branch = result.stdout.strip()
                    if branch and branch != "HEAD":
                        return f"{repo}:{branch}"
            except Exception:
                pass
            # Found the marker but couldn't extract info -- stop walking.
            return None
    return None


def parse_qualified(name: str) -> tuple[str, str]:
    """Split 'repo:branch' into (repo, branch).

    Accepts both 'repo:branch' (explicit) and bare 'branch' (inferred
    by searching all repos for a matching worktree).
    """
    if ":" in name:
        repo, branch = name.split(":", 1)
        if not repo or not branch:
            die(f"invalid qualified name: '{name}'")
        return repo, branch

    # Bare name -- infer repo from worktree search.
    repo = _infer_repo(name)
    return repo, name


def resolve(explicit: str | None = None, allowed_repos: list[str] | None = None) -> BranchContext:
    """Resolve the active branch context.

    Args:
        explicit: qualified name from CLI arg (e.g. 'bag:fix-auth')
        allowed_repos: if set, die if resolved repo is not in this list

    Returns BranchContext with all paths pre-computed.

    """
    qualified = explicit or os.environ.get("VB")
    if not qualified:
        qualified = _infer_from_cwd()
    if not qualified:
        die("no branch selected\n  pass -B repo:branch, set VB=repo:branch, or cd into a worktree with superv/config.toml")

    from supervisor.aliases import resolve_alias

    repo, branch = parse_qualified(qualified)
    branch = resolve_alias(repo, branch)

    # Validate repo exists in config.
    cfg = get_repo(repo)

    # Repo restriction check.
    if allowed_repos and repo not in allowed_repos:
        die(f"this command is only available for: {', '.join(allowed_repos)} (current: {repo}:{branch})")

    # Validate worktree exists.
    wt = worktree_path(repo, branch)
    if not wt.exists():
        die(f"worktree does not exist: {wt}")
    if not (wt / ".git").exists():
        die(f"not a git worktree: {wt}")

    return BranchContext(
        repo=repo,
        branch=branch,
        qualified=f"{repo}:{branch}",
        config=cfg,
        branch_dir=branch_dir(repo, branch),
        worktree=wt,
    )


def active_context() -> BranchContext | None:
    """Return the current BranchContext, or None if no branch is active.

    Non-fatal version of resolve(). Checks the VB env var; returns None
    if unset or if resolution fails.
    """
    vb = os.environ.get("VB")
    if not vb:
        vb = _infer_from_cwd()
    if not vb:
        return None
    try:
        return resolve(vb)
    except SystemExit:
        return None
