"""AI-powered branch explanation with file-based caching.

Generates a natural-language summary of a branch's changes by feeding
the diff through `claude -p`.  Results are cached per commit hash so
repeated requests for the same HEAD are instant.

Cache layout: ~/.superv/cache/explain/{qualified_safe}/{hash}.md
"""

import subprocess
from pathlib import Path

from supervisor.paths import resolve_global, superv_home, worktree_path

# Prompt sent to claude -p (mirrors the CLI's v explain wording).
_EXPLAIN_PROMPT = (
    "You receive a branch summary (commits, changed files, and full diff) "
    "for a feature branch vs production. Explain what the branch does and why "
    "in a clear, concise narrative. Be specific about intent, not just mechanics. "
    "No preamble, no markdown headings."
)

_CACHE_REL = "cache/explain"


def _safe_qualified(qualified: str) -> str:
    """Convert 'repo:branch' to a filesystem-safe directory name."""
    return qualified.replace(":", "_")


def _cache_path_read(qualified: str, commit_hash: str) -> Path:
    """Resolve the cache path for reading (prefers ~/.superv/, falls back to .supervisor/)."""
    return resolve_global(_CACHE_REL) / _safe_qualified(qualified) / f"{commit_hash}.md"


def _cache_path_write(qualified: str, commit_hash: str) -> Path:
    """Resolve the cache path for writing (always ~/.superv/)."""
    return superv_home() / _CACHE_REL / _safe_qualified(qualified) / f"{commit_hash}.md"


def get_head_hash(repo: str, branch: str) -> str | None:
    """Return the HEAD commit hash for a branch's worktree, or None."""
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace").strip()
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_cached_summary(qualified: str, commit_hash: str) -> str | None:
    """Return cached summary text if it exists for this commit hash."""
    path = _cache_path_read(qualified, commit_hash)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _build_diff_text(repo: str, branch: str) -> str:
    """Build the branch diff text to feed to the AI.

    Reuses the same summary logic as `v git explain` (branch_summary from
    supervisor.summary), which includes commits, file list, and full diff.
    """
    from supervisor.git import staleness_tag
    from supervisor.summary import branch_summary

    wt = worktree_path(repo, branch)
    tag = staleness_tag(branch, repo)
    return branch_summary(wt, branch, repo, tag)


def generate_summary(repo: str, branch: str) -> str:
    """Call claude -p to generate an AI summary of the branch diff.

    Raises RuntimeError if the claude subprocess fails.
    """
    import os

    diff_text = _build_diff_text(repo, branch)
    if not diff_text.strip():
        return "No changes on this branch."

    env = {**os.environ, "CLAUDECODE": "0"}
    result = subprocess.run(
        ["claude", "-p", _EXPLAIN_PROMPT],
        input=diff_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "claude exited with non-zero status")
    return result.stdout.strip()


def save_cached_summary(qualified: str, commit_hash: str, summary: str) -> None:
    """Persist a summary to ~/.superv/cache/explain/."""
    path = _cache_path_write(qualified, commit_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary, encoding="utf-8")


# Maximum diff line count before we refuse to run the AI and return a warning.
DIFF_LINE_LIMIT = 500


def get_diff_line_count(repo: str, branch: str) -> int:
    """Count the number of lines in the branch diff text."""
    diff_text = _build_diff_text(repo, branch)
    return len(diff_text.splitlines())
