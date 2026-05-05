"""Git subprocess helpers."""

import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from codehome.paths import (
    IGNORABLE_FILE,
    NOISE_PATTERNS,
    base_ref,
    branch_name_from_wt,
    prod_ref,
    repo_anchor,
    staging_ref,
)
from codehome.utils import die


def git(worktree: Path, *args: str, check: bool = True, errors: str = "strict") -> str:
    """Run a git command in the given worktree, return stdout.

    errors: passed to bytes.decode(). Use "replace" when output may
    contain binary data (e.g. diff --no-index on non-text files).
    """
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
    )
    stdout = result.stdout.decode("utf-8", errors=errors)
    stderr = result.stderr.decode("utf-8", errors=errors)
    if check and result.returncode != 0:
        sys.stderr.write(stderr)
        sys.exit(result.returncode)
    return stdout.rstrip("\n")


def git_passthrough(worktree: Path, *args: str) -> int:
    """Run a git command with output going directly to the terminal."""
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
    ).returncode


def pipe_through_delta(diff_text: str) -> None:
    """Pipe unified diff through delta for syntax highlighting."""
    if not diff_text.strip():
        return
    subprocess.run(
        [
            "delta",
            "--paging",
            "never",
            "--line-numbers",
            "--minus-style",
            "syntax normal",
            "--plus-style",
            "syntax normal",
            "--minus-emph-style",
            "syntax bold normal",
            "--plus-emph-style",
            "syntax bold normal",
        ],
        input=diff_text,
        text=True,
    )


def list_worktrees(repo: str) -> list[str]:
    """Return branch names of all worktrees for a repo."""
    anchor = repo_anchor(repo)
    output = git(anchor, "worktree", "list", "--porcelain")
    names = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            wt_path = Path(line.split(" ", 1)[1])
            name = branch_name_from_wt(wt_path)
            # Skip internal staging worktrees (.staging, .staging-<branch>, etc.)
            if not name.startswith(".staging"):
                names.append(name)
    return names


def gh_repo(repo: str) -> str:
    """Extract owner/repo from the git remote URL."""
    anchor = repo_anchor(repo)
    remote_url = git(anchor, "remote", "get-url", "origin")
    m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", remote_url)
    if not m:
        die(f"cannot parse owner/repo from remote URL: {remote_url}")
    return m.group(1)


def gh_env(gh_token: str | None) -> dict[str, str] | None:
    """Build a subprocess env dict with GH_TOKEN set, or None for default."""
    if not gh_token:
        return None
    env = os.environ.copy()
    env["GH_TOKEN"] = gh_token
    return env


def gh_api(
    endpoint: str, *extra_args: str, method: str = "GET", input_file: str | None = None, gh_token: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Call gh api with the correct repo context. Returns CompletedProcess.

    When *gh_token* is provided it is injected as ``GH_TOKEN`` in the
    subprocess environment so the ``gh`` CLI authenticates as that user.
    """
    cmd = ["gh", "api", endpoint]
    if method != "GET":
        cmd += ["-X", method]
    cmd += list(extra_args)
    if input_file:
        cmd += ["--input", input_file]
    return subprocess.run(cmd, capture_output=True, text=True, env=gh_env(gh_token))


def exclude_args() -> list[str]:
    """Build git pathspec exclusions for noise files."""
    return [f":(exclude){p}" for p in NOISE_PATTERNS]


def commits_behind(name: str, repo: str) -> int:
    """Count how many commits the base branch is ahead of a branch."""
    from codehome.paths import worktree_path

    wt = worktree_path(repo, name)
    ref = base_ref(repo, name)
    return int(git(wt, "rev-list", f"{name}..{ref}", "--count"))


def staleness_tag(name: str, repo: str) -> str:
    """Return '(N behind)' if stale, else ''."""
    behind = commits_behind(name, repo)
    return f"({behind} behind)" if behind else ""


def require_fresh(name: str, repo: str) -> None:
    """Fetch and die if branch is behind the base branch."""
    anchor = repo_anchor(repo)
    git_passthrough(anchor, "fetch", "--prune")
    behind = commits_behind(name, repo)
    if behind:
        die(f"{name} is {behind} commits behind -- run: v git rebase")


def ignorable_patterns() -> list[str]:
    """Read patterns from scripts/ignorable-worktree-changes."""
    if not IGNORABLE_FILE.exists():
        return []
    patterns = []
    for raw_line in IGNORABLE_FILE.read_text().splitlines():
        stripped = raw_line.split("#")[0].strip()
        if stripped:
            patterns.append(stripped)
    return patterns


def unique_commits(name: str, repo: str) -> int:
    """Count commits on branch that are not on the base branch."""
    anchor = repo_anchor(repo)
    ref = base_ref(repo, name)
    out = git(anchor, "rev-list", "--count", f"{ref}..{name}", check=False)
    return int(out.strip()) if out.strip() else 0


def create_bundle(anchor: Path, branch: str, base_ref: str, dest: Path) -> None:
    """Create a git bundle containing commits unique to *branch* vs *base_ref*."""
    git(anchor, "bundle", "create", str(dest), branch, "--not", base_ref)


def verify_bundle(anchor: Path, bundle_path: Path) -> None:
    """Verify a git bundle is valid; raises on failure."""
    git(anchor, "bundle", "verify", str(bundle_path))


def is_merged_to(name: str, target_ref: str, repo: str) -> bool:
    """Check if the branch ref is an ancestor of target_ref."""
    anchor = repo_anchor(repo)
    result = subprocess.run(
        ["git", "-C", str(anchor), "merge-base", "--is-ancestor", name, target_ref],
        capture_output=True,
    )
    return result.returncode == 0


def is_merged_to_production(name: str, repo: str) -> bool:
    return is_merged_to(name, prod_ref(repo), repo)


def is_merged_to_staging(name: str, repo: str) -> bool:
    ref = staging_ref(repo)
    if not ref:
        return False
    return is_merged_to(name, ref, repo)


def lock_worktree(repo: str, name: str) -> None:
    """Make worktree readonly via unix permissions."""
    from codehome.paths import worktree_path

    wt = worktree_path(repo, name)
    if not wt.exists():
        return
    subprocess.run(["chmod", "-R", "a-w", str(wt)], check=False)


def unlock_worktree(repo: str, name: str) -> None:
    """Restore worktree write permissions."""
    from codehome.paths import worktree_path

    wt = worktree_path(repo, name)
    if not wt.exists():
        return
    subprocess.run(["chmod", "-R", "u+w", str(wt)], check=False)


def is_worktree_locked(repo: str, name: str) -> bool:
    """Check if worktree is readonly (locked after prod merge)."""
    from codehome.paths import worktree_path

    wt = worktree_path(repo, name)
    return wt.exists() and not os.access(wt, os.W_OK)


def require_unlocked(repo: str, name: str) -> None:
    """Die if worktree is locked (merged to production)."""
    if is_worktree_locked(repo, name):
        die(f"branch '{repo}:{name}' is merged to production and locked -- run: v branch finalize -B {repo}:{name}")


def worktree_is_clean(wt: Path) -> bool:
    """Check if all dirty files in the worktree match ignorable patterns."""
    dirty_output = git(wt, "status", "--porcelain")
    if not dirty_output:
        return True
    patterns = ignorable_patterns()
    for line in dirty_output.splitlines():
        filepath = line[3:].split(" -> ")[0]
        if not any(fnmatch.fnmatch(filepath, p) for p in patterns):
            return False
    return True


def delete_local_branch(anchor: Path, branch: str) -> None:
    """Delete a local branch (force). Ignores errors if branch doesn't exist."""
    git(anchor, "branch", "-D", branch, check=False)


def remove_worktree(anchor: Path, worktree: Path) -> None:
    """Force-remove a git worktree. Ignores errors if already gone."""
    git(anchor, "worktree", "remove", "--force", str(worktree), check=False)


def delete_remote_branch(anchor: Path, branch: str) -> None:
    """Delete a remote branch on origin. Ignores errors if already gone."""
    git(anchor, "push", "origin", "--delete", branch, check=False)


def iter_all_branches() -> Iterator[tuple[str, str]]:
    """Yield (repo, branch) for every active worktree across all repos."""
    from codehome.config import load_repos

    for repo in load_repos():
        if not repo_anchor(repo).exists():
            continue
        for branch in list_worktrees(repo):
            yield repo, branch
