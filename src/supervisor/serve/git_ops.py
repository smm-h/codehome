"""Git operations for the server API.

Pure subprocess calls + parsing -- no CLI dependencies, no sys.exit.
Each function takes (repo, branch) and returns structured data.
Errors return empty/error results instead of raising.
"""

from pathlib import Path
from typing import Any

from supervisor.paths import NOISE_PATTERNS, base_ref, worktree_path
from supervisor.serve.subprocess_utils import parse_numstat_line, run_git


def _exclude_args() -> list[str]:
    """Build git pathspec exclusions for noise files."""
    return [f":(exclude){p}" for p in NOISE_PATTERNS]


def _resolve_worktree(repo: str, branch: str) -> Path | None:
    """Return worktree path if it exists, else None."""
    wt = worktree_path(repo, branch)
    return wt if wt.is_dir() else None


def get_diff(repo: str, branch: str) -> dict[str, Any]:
    """Get full diff of branch vs its base ref.

    Returns {files: [{path, status, additions, deletions}], patch: str}.
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return {"files": [], "patch": ""}

    ref = base_ref(repo, branch)
    excl = _exclude_args()

    # Numstat for per-file stats.
    numstat_out, _, _ = run_git(wt, "diff", f"{ref}...HEAD", "--numstat", "--", ".", *excl)
    # Name-status for file statuses (A/M/D/R).
    status_out, _, _ = run_git(wt, "diff", f"{ref}...HEAD", "--name-status", "--", ".", *excl)
    # Full patch text.
    patch_out, _, _ = run_git(wt, "diff", f"{ref}...HEAD", "--", ".", *excl)

    # Parse statuses into a lookup.
    statuses: dict[str, str] = {}
    for line in status_out.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            code = parts[0][0]
            path = parts[2] if len(parts) == 3 else parts[1]
            statuses[path] = code

    # Parse numstat into file entries.
    files: list[dict[str, Any]] = []
    for line in numstat_out.strip().splitlines():
        parsed = parse_numstat_line(line)
        if parsed:
            a, d, path = parsed
            files.append(
                {
                    "path": path,
                    "status": statuses.get(path, "M"),
                    "additions": a,
                    "deletions": d,
                },
            )

    return {"files": files, "patch": patch_out}


def get_changes(repo: str, branch: str) -> list[dict[str, Any]]:
    """Get changed files vs base ref, including uncommitted changes.

    Returns [{path, status, additions, deletions}].
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return []

    ref = base_ref(repo, branch)
    excl = _exclude_args()

    # Committed changes (numstat + name-status).
    numstat_out, _, _ = run_git(wt, "diff", f"{ref}...HEAD", "--numstat", "--", ".", *excl)
    status_out, _, _ = run_git(wt, "diff", f"{ref}...HEAD", "--name-status", "--", ".", *excl)

    statuses: dict[str, str] = {}
    for line in status_out.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            code = parts[0][0]
            path = parts[2] if len(parts) == 3 else parts[1]
            statuses[path] = code

    files: dict[str, dict[str, Any]] = {}
    for line in numstat_out.strip().splitlines():
        parsed = parse_numstat_line(line)
        if parsed:
            a, d, path = parsed
            files[path] = {
                "path": path,
                "status": statuses.get(path, "M"),
                "additions": a,
                "deletions": d,
            }

    # Uncommitted changes (working tree vs HEAD).
    uc_numstat, _, _ = run_git(wt, "diff", "HEAD", "--numstat", "--", ".", *excl)
    uc_status, _, _ = run_git(wt, "diff", "HEAD", "--name-status", "--", ".", *excl)

    uc_statuses: dict[str, str] = {}
    for line in uc_status.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            code = parts[0][0]
            path = parts[2] if len(parts) == 3 else parts[1]
            uc_statuses[path] = code

    for line in uc_numstat.strip().splitlines():
        parsed = parse_numstat_line(line)
        if parsed:
            a, d, path = parsed
            if path not in files:
                files[path] = {
                    "path": path,
                    "status": uc_statuses.get(path, "M"),
                    "additions": a,
                    "deletions": d,
                    "uncommitted": True,
                }

    # Untracked files.
    untracked_out, _, _ = run_git(wt, "ls-files", "--others", "--exclude-standard")
    import fnmatch

    for f in untracked_out.strip().splitlines():
        if not f or any(fnmatch.fnmatch(f, p) for p in NOISE_PATTERNS):
            continue
        if f not in files:
            # Count lines for untracked files.
            try:
                lc = len((wt / f).read_text().splitlines())
            except (OSError, UnicodeDecodeError):
                lc = 0
            files[f] = {
                "path": f,
                "status": "A",
                "additions": lc,
                "deletions": 0,
                "uncommitted": True,
            }

    return list(files.values())


def get_commits(repo: str, branch: str) -> list[dict[str, Any]]:
    """Get commit log of branch vs its base ref.

    Returns [{hash, short, author, date, message}], newest first.
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return []

    ref = base_ref(repo, branch)

    # Use a format with NUL separators for reliable parsing.
    fmt = "%H%x00%h%x00%an%x00%aI%x00%s"
    out, _, rc = run_git(wt, "log", f"{ref}..HEAD", f"--format={fmt}")
    if rc != 0 or not out.strip():
        return []

    commits: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = line.split("\0", 4)
        if len(parts) < 5:
            continue
        commits.append(
            {
                "hash": parts[0],
                "short": parts[1],
                "author": parts[2],
                "date": parts[3],
                "message": parts[4],
            },
        )

    return commits


def compare_branches(repo_a: str, branch_a: str, repo_b: str, branch_b: str) -> dict[str, Any]:
    """Compare two branches and return diffstat + per-file stats.

    Both branches must belong to the same repo (cross-repo compare is not
    supported because git diff requires a common object store).  The caller
    is responsible for validating inputs and resolving aliases.

    Raises BranchError on validation/not-found failures so the API layer
    can map them to 400/404 HTTP responses.

    Returns {branch_a, branch_b, diffstat: {files_changed, insertions, deletions},
             files: [{path, status, additions, deletions}]}.
    """
    from supervisor.commands.branch import BranchError

    if repo_a != repo_b:
        msg = f"cross-repo compare not supported ({repo_a} vs {repo_b})"
        raise BranchError(msg)

    wt_a = _resolve_worktree(repo_a, branch_a)
    if not wt_a:
        msg = f"branch not found: {repo_a}:{branch_a}"
        raise BranchError(msg, category="not_found")
    wt_b = _resolve_worktree(repo_b, branch_b)
    if not wt_b:
        msg = f"branch not found: {repo_b}:{branch_b}"
        raise BranchError(msg, category="not_found")

    # Use branch_a's worktree as the working directory (same repo, shared objects).
    wt = wt_a
    excl = _exclude_args()

    # Numstat for per-file stats.
    numstat_out, _, rc = run_git(wt, "diff", f"{branch_a}..{branch_b}", "--numstat", "--", ".", *excl)
    if rc != 0:
        msg = f"git diff failed for {branch_a}..{branch_b}"
        raise BranchError(msg, category="internal")

    # Name-status for file statuses (A/M/D/R).
    status_out, _, _ = run_git(wt, "diff", f"{branch_a}..{branch_b}", "--name-status", "--", ".", *excl)

    # Parse statuses.
    statuses: dict[str, str] = {}
    STATUS_MAP = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    for line in status_out.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            code = parts[0][0]
            path = parts[2] if len(parts) == 3 else parts[1]
            statuses[path] = STATUS_MAP.get(code, "modified")

    # Parse numstat.
    files: list[dict[str, Any]] = []
    total_add = 0
    total_del = 0
    for line in numstat_out.strip().splitlines():
        parsed = parse_numstat_line(line)
        if parsed:
            a, d, path = parsed
            total_add += a
            total_del += d
            files.append(
                {
                    "path": path,
                    "status": statuses.get(path, "modified"),
                    "additions": a,
                    "deletions": d,
                },
            )

    qualified_a = f"{repo_a}:{branch_a}"
    qualified_b = f"{repo_b}:{branch_b}"

    return {
        "branch_a": qualified_a,
        "branch_b": qualified_b,
        "diffstat": {
            "files_changed": len(files),
            "insertions": total_add,
            "deletions": total_del,
        },
        "files": files,
    }


def push_branch(repo: str, branch: str) -> dict[str, Any]:
    """Push branch to remote with --force-with-lease.

    Returns {ok: bool, message: str}.
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return {"ok": False, "message": f"Worktree not found for {repo}:{branch}"}

    stdout, stderr, rc = run_git(
        wt,
        "push",
        "--force-with-lease",
        "-u",
        "origin",
        branch,
        timeout=60,
    )

    if rc != 0:
        # git push writes progress to stderr even on success.
        return {"ok": False, "message": stderr or "Push failed"}

    return {"ok": True, "message": stderr or stdout or "Pushed successfully"}


def get_file_diff(
    repo: str,
    branch: str,
    file_path: str,
    compare_ref: str | None = None,
) -> str:
    """Get the diff for a single file.

    When *compare_ref* is provided (e.g. ``branchA..branchB``), diff that
    range for the file -- used by the cross-branch compare page.
    Otherwise falls back to diffing against the branch's base ref.

    Returns the unified diff text for just that file.
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return ""

    if compare_ref:
        # Cross-branch compare: caller supplies the ref range directly.
        out, _, _ = run_git(wt, "diff", compare_ref, "--", file_path)
    else:
        ref = base_ref(repo, branch)
        out, _, _ = run_git(wt, "diff", f"{ref}...HEAD", "--", file_path)
    return out
