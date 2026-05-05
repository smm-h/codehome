"""Git tracking for branch directories (metadata, not code).

Each branch dir (branches/<name>/) gets its own git repo to track
the evolution of docs/, progress.md, todo/, tests.json, issue.json, review.md.
The worktree/ subdirectory (the actual code) is gitignored.

Auto-commits happen at lifecycle points: v new, v switch, v fin.
"""

import subprocess
from pathlib import Path

from supervisor.paths import branch_dir

# Ignore the code worktree and ephemeral logs -- track only metadata.
BRANCH_GITIGNORE = "worktree/\nvite.log\n"


def init_branch_repo(repo: str, name: str) -> None:
    """Initialize a git repo in the branch directory if one doesn't exist."""
    bd = branch_dir(repo, name)
    if (bd / ".git").exists():
        return
    _run(bd, "init", "-q")
    (bd / ".gitignore").write_text(BRANCH_GITIGNORE)


def commit_branch_dir(repo: str, name: str, message: str) -> None:
    """Stage all changes and commit if dirty. Auto-inits if no repo exists."""
    bd = branch_dir(repo, name)
    if not bd.exists():
        return
    if not (bd / ".git").exists():
        init_branch_repo(repo, name)
    _commit(bd, message)


def _commit(bd: Path, message: str) -> None:
    """Stage all and commit. No-op if nothing to commit."""
    _run(bd, "add", "-A")
    # --cached --quiet exits non-zero when there are staged changes.
    result = subprocess.run(
        ["git", "-C", str(bd), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if result.returncode != 0:
        _run(bd, "commit", "-m", message, "-q")


def _run(bd: Path, *args: str) -> None:
    """Run git command in branch dir, suppressing output."""
    subprocess.run(["git", "-C", str(bd), *args], capture_output=True)
