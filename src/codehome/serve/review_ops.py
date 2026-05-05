"""Review operations for the server API.

Local diff-based review: parse git diff output, compare with review.md
coverage, and read/write review.md from the branch context directory.

Also provides GitHub PR review posting and thread resolution via `gh` CLI.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from codehome.git import gh_api, gh_env
from codehome.paths import base_ref, branch_dir, worktree_path
from codehome.serve.subprocess_utils import run_git, safe_gh_repo


def _parse_diff_to_files(diff_text: str) -> list[dict[str, Any]]:
    """Parse unified diff into structured file/hunk data with line numbers.

    Returns [{path, hunks: [{old_start, new_start, lines: [{type, text, line_num}]}]}].
    """
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None
    left_line = right_line = 0

    for line in diff_text.splitlines():
        # File header
        m = re.match(r"^diff --git a/.+ b/(.+)", line)
        if m:
            current_file = {"path": m.group(1), "hunks": []}
            files.append(current_file)
            current_hunk = None
            continue

        if not current_file:
            continue

        # Skip index/--- /+++ lines
        if line.startswith(("index ", "---", "+++")):
            continue

        # Hunk header
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
        if m:
            left_line = int(m.group(1))
            right_line = int(m.group(2))
            current_hunk = {
                "old_start": left_line,
                "new_start": right_line,
                "lines": [],
            }
            current_file["hunks"].append(current_hunk)
            continue

        if not current_hunk:
            continue

        # Diff lines
        if line.startswith("+"):
            current_hunk["lines"].append(
                {
                    "type": "add",
                    "text": line[1:],
                    "line_num": right_line,
                },
            )
            right_line += 1
        elif line.startswith("-"):
            current_hunk["lines"].append(
                {
                    "type": "del",
                    "text": line[1:],
                    "line_num": left_line,
                },
            )
            left_line += 1
        else:
            current_hunk["lines"].append(
                {
                    "type": "ctx",
                    "text": line.removeprefix(" "),
                    "line_num": right_line,
                },
            )
            left_line += 1
            right_line += 1

    return files


def get_review_diff(repo: str, branch: str) -> dict[str, Any]:
    """Run git diff against the base ref and return structured file/hunk data.

    Returns {files: [{path, hunks: [{old_start, new_start, lines}]}]}.
    """
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        return {"files": []}

    ref = base_ref(repo, branch)
    out, _, rc = run_git(wt, "diff", f"{ref}...HEAD")
    if rc != 0 or not out.strip():
        return {"files": []}

    return {"files": _parse_diff_to_files(out)}


def get_review_gaps(repo: str, branch: str) -> list[dict[str, Any]]:
    """Find diff hunks that have no corresponding review.md coverage.

    Compares the diff output against review.md: any file mentioned in a
    ## RIGHT/LEFT heading is considered covered. Returns uncovered hunks
    as [{file, line, hunk_preview}].
    """
    wt = worktree_path(repo, branch)
    if not wt.is_dir():
        return []

    ref = base_ref(repo, branch)
    out, _, rc = run_git(wt, "diff", f"{ref}...HEAD")
    if rc != 0 or not out.strip():
        return []

    # Parse review.md for covered files+lines
    bd = branch_dir(repo, branch)
    review_path = bd / "review.md"
    covered_files: set[str] = set()
    covered_lines: set[tuple[str, int]] = set()

    if review_path.is_file():
        for raw_line in review_path.read_text().splitlines():
            m = re.match(
                r"^##\s+(?:RIGHT|LEFT)\s+(\S+?)(?::(\d+))(?:\s+\S+?(?::(\d+)))?$",
                raw_line.strip(),
            )
            if m:
                path = m.group(1)
                covered_files.add(path)
                line_num = int(m.group(2))
                covered_lines.add((path, line_num))
                if m.group(3):
                    end_line = int(m.group(3))
                    for n in range(line_num, end_line + 1):
                        covered_lines.add((path, n))

    # Parse diff into hunks and find uncovered ones
    files = _parse_diff_to_files(out)
    gaps: list[dict[str, Any]] = []

    for file_data in files:
        path = file_data["path"]
        for hunk in file_data["hunks"]:
            # Check if any changed line in this hunk is covered
            hunk_covered = False
            changed_lines = [ln for ln in hunk["lines"] if ln["type"] in ("add", "del")]

            if not changed_lines:
                continue

            # Check coverage: either the file is mentioned with a line
            # in the hunk's range, or the file is generally covered
            for ln in changed_lines:
                if (path, ln["line_num"]) in covered_lines:
                    hunk_covered = True
                    break

            if not hunk_covered:
                # Build a short preview from the first few changed lines
                preview_lines = [f"{'+' if ln['type'] == 'add' else '-'}{ln['text']}" for ln in changed_lines[:3]]
                gaps.append(
                    {
                        "file": path,
                        "line": hunk["new_start"],
                        "hunk_preview": "\n".join(preview_lines),
                    },
                )

    return gaps


def read_review_md(repo: str, branch: str) -> str:
    """Read review.md content from the branch context directory."""
    bd = branch_dir(repo, branch)
    review_path = bd / "review.md"
    if review_path.is_file():
        return review_path.read_text()
    return ""


def save_review_md(repo: str, branch: str, content: str) -> None:
    """Write review.md to the branch context directory."""
    bd = branch_dir(repo, branch)
    if not bd.is_dir():
        bd.mkdir(parents=True, exist_ok=True)
    review_path = bd / "review.md"
    review_path.write_text(content)


# ---------------------------------------------------------------------------
# GitHub PR review operations (require `gh` CLI and network)
# ---------------------------------------------------------------------------


def _find_pr(repo: str, branch: str, *, gh_token: str | None = None) -> int:
    """Find the open production PR for a branch.

    Replicates the logic from commands/review.py but accepts repo as a
    parameter instead of using module-level state.
    """
    gh = safe_gh_repo(repo)
    if not gh:
        msg = f"Cannot resolve GitHub repo for '{repo}'"
        raise ValueError(msg)
    result = subprocess.run(
        ["gh", "pr", "list", "--repo", gh, "--head", branch, "--json", "number,baseRefName"],
        capture_output=True,
        text=True,
        env=gh_env(gh_token),
    )
    try:
        prs = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError:
        prs = []

    if not prs:
        msg = f"No open PR found for branch '{branch}'"
        raise ValueError(msg)

    prod_prs = [p for p in prs if p["baseRefName"] == "production"]
    if not prod_prs:
        bases = ", ".join(p["baseRefName"] for p in prs)
        msg = f"No open PR targeting production for '{branch}' (found: {bases})"
        raise ValueError(msg)
    if len(prod_prs) > 1:
        nums = ", ".join(f"#{p['number']}" for p in prod_prs)
        msg = f"Multiple open production PRs for '{branch}': {nums}"
        raise ValueError(msg)

    return int(prod_prs[0]["number"])


def _head_sha(repo: str, pr: int, *, gh_token: str | None = None) -> str:
    """Get the HEAD commit SHA for a PR."""
    gh = safe_gh_repo(repo)
    if not gh:
        msg = f"Cannot resolve GitHub repo for '{repo}'"
        raise ValueError(msg)
    result = gh_api(f"repos/{gh}/pulls/{pr}", "--jq", ".head.sha", gh_token=gh_token)
    sha = result.stdout.strip()
    if not sha:
        msg = f"Could not get HEAD SHA for PR #{pr}"
        raise ValueError(msg)
    return sha


def _parse_review_file(path: Path) -> dict[str, Any]:
    """Parse review.md into structured data.

    Identical to commands/review._parse_review_file but importable from
    the serve layer without touching CLI module state.
    """
    comments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    delete_existing = False

    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()

        if re.match(r"^<!--\s*DELETE\s*-->$", line):
            delete_existing = True
            continue

        m = re.match(
            r"^##\s+(RIGHT|LEFT)\s+(\S+?)(?::(\d+))(?:\s+(\S+?)(?::(\d+)))?$",
            line,
        )
        if m:
            if current and current["body"].strip():
                comments.append(current)
            side = m.group(1)
            line_num = int(m.group(3))
            end_line = int(m.group(5)) if m.group(5) else None

            current = {
                "path": m.group(2),
                "line": end_line or line_num,
                "side": side,
                "body": "",
            }
            if end_line and end_line != line_num:
                current["start_line"] = line_num
                current["start_side"] = side
            continue

        if re.match(r"^#{1,6}\s", line):
            if current and current["body"].strip():
                comments.append(current)
                current = None
            continue

        if current is not None:
            current["body"] += line + "\n"

    if current and current["body"].strip():
        comments.append(current)

    for c in comments:
        c["body"] = c["body"].strip()

    return {"delete": delete_existing, "comments": comments}


def post_review(repo: str, branch: str, dry_run: bool = False, *, gh_token: str | None = None) -> dict[str, Any]:
    """Post review comments from review.md to the GitHub PR.

    If dry_run is True, returns a preview without posting.
    Returns {"comments_posted": int, "comments": [...]}.
    """
    bd = branch_dir(repo, branch)
    review_file = bd / "review.md"
    if not review_file.is_file():
        msg = "No review.md found for this branch"
        raise FileNotFoundError(msg)

    pr = _find_pr(repo, branch, gh_token=gh_token)
    parsed = _parse_review_file(review_file)
    comments = parsed["comments"]
    delete_existing = parsed["delete"]

    if not comments:
        return {"comments_posted": 0, "comments": []}

    # Build preview data for the response
    preview = []
    for c in comments:
        start = c.get("start_line")
        loc = f"{c['side']} {c['path']}:{f'{start}-' if start else ''}{c['line']}"
        preview.append(
            {
                "file": c["path"],
                "line": c["line"],
                "side": c["side"],
                "start_line": start,
                "location": loc,
                "body": c["body"],
            },
        )

    if dry_run:
        return {"comments_posted": 0, "dry_run": True, "comments": preview}

    gh = safe_gh_repo(repo)
    if not gh:
        msg = f"Cannot resolve GitHub repo for '{repo}'"
        raise ValueError(msg)

    # Delete existing comments if <!-- DELETE --> directive is present
    if delete_existing:
        result = gh_api(
            f"repos/{gh}/pulls/{pr}/comments",
            "--jq",
            ".[].id",
            gh_token=gh_token,
        )
        ids = result.stdout.strip().splitlines()
        for cid in ids:
            if cid.strip():
                gh_api(f"repos/{gh}/pulls/comments/{cid}", method="DELETE", gh_token=gh_token)

    # Post as a single review
    commit_sha = _head_sha(repo, pr, gh_token=gh_token)
    payload = {
        "commit_id": commit_sha,
        "event": "COMMENT",
        "body": "Review comments.",
        "comments": comments,
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
    ) as f:
        json.dump(payload, f)
        tmp_path = f.name

    result = gh_api(
        f"repos/{gh}/pulls/{pr}/reviews",
        method="POST",
        input_file=tmp_path,
        gh_token=gh_token,
    )
    Path(tmp_path).unlink(missing_ok=True)

    try:
        data = json.loads(result.stdout)
        if "id" not in data:
            msg = f"GitHub API error: {result.stdout}"
            raise RuntimeError(msg)
    except json.JSONDecodeError:
        msg = f"GitHub API returned invalid JSON: {result.stdout} {result.stderr}"
        raise RuntimeError(msg) from None

    return {"comments_posted": len(comments), "comments": preview}


def resolve_threads(repo: str, branch: str, *, gh_token: str | None = None) -> dict[str, Any]:
    """Bulk-resolve all unresolved review threads on the production PR.

    Returns {"threads_resolved": int}.
    """
    pr = _find_pr(repo, branch, gh_token=gh_token)
    gh = safe_gh_repo(repo)
    if not gh:
        msg = f"Cannot resolve GitHub repo for '{repo}'"
        raise ValueError(msg)
    owner, name = gh.split("/", 1)
    env = gh_env(gh_token)

    # Fetch unresolved thread IDs via GraphQL
    query = """
query($owner: String!, $name: String!, $pr: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes { id isResolved }
      }
    }
  }
}"""
    result = subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"pr={pr}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        msg = f"Failed to fetch review threads: {result.stdout} {result.stderr}"
        raise RuntimeError(msg) from None

    threads = (
        data.get("data", {}).get("repository", {}).get("pullRequest", {}).get("reviewThreads", {}).get("nodes", [])
    )
    unresolved = [t for t in threads if not t["isResolved"]]

    if not unresolved:
        return {"threads_resolved": 0}

    resolved = 0
    for t in unresolved:
        mutation = 'mutation {{ resolveReviewThread(input: {{threadId: "{}"}}) {{ thread {{ id }} }} }}'.format(t["id"])
        r = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={mutation}"],
            capture_output=True,
            text=True,
            env=env,
        )
        if r.returncode == 0:
            resolved += 1

    return {"threads_resolved": resolved}
