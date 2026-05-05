"""Pipeline operations for the server API.

Checks GitHub PR status and triggers staging/production workflows
via `gh` CLI subprocess calls. No direct GitHub API usage.
"""

import json
from typing import Any

from supervisor.config import get_repo
from supervisor.serve.git_ops import _resolve_worktree
from supervisor.serve.subprocess_utils import run_gh, run_git, safe_gh_repo


def _parse_checks(status_rollup: list[dict[str, Any]] | None) -> str:
    """Summarize check statuses into pass/fail/pending."""
    if not status_rollup:
        return "unknown"
    states = {c.get("status", "").upper() for c in status_rollup}
    conclusions = {c.get("conclusion", "").upper() for c in status_rollup}
    if "FAILURE" in conclusions or "CANCELLED" in conclusions:
        return "fail"
    if "IN_PROGRESS" in states or "QUEUED" in states or "PENDING" in states:
        return "pending"
    if conclusions <= {"SUCCESS", "NEUTRAL", "SKIPPED", ""}:
        return "pass"
    return "pending"


def _pr_info(pr: dict[str, Any]) -> dict[str, Any]:
    """Extract relevant fields from a gh pr JSON object."""
    return {
        "number": pr.get("number"),
        "url": pr.get("url", ""),
        "title": pr.get("title", ""),
        "state": pr.get("state", "").upper(),
        "checks_status": _parse_checks(pr.get("statusCheckRollup")),
        "mergeable": pr.get("mergeable", "UNKNOWN"),
    }


def get_pipeline_status(repo: str, branch: str, *, gh_token: str | None = None) -> dict[str, Any]:
    """Check GitHub for open PRs targeting staging and production.

    Returns {staging_pr, production_pr, commits} where each PR field
    is either a dict with PR details or None.
    """
    gh_r = safe_gh_repo(repo)
    cfg = get_repo(repo)

    # Count commits on branch vs base ref.
    wt = _resolve_worktree(repo, branch)
    commit_count = 0
    if wt:
        from supervisor.paths import base_ref

        ref = base_ref(repo, branch)
        out, _, rc = run_git(wt, "rev-list", f"{ref}..HEAD", "--count")
        if rc == 0 and out.strip().isdigit():
            commit_count = int(out.strip())

    staging_pr = None
    production_pr = None

    if not gh_r:
        return {"staging_pr": staging_pr, "production_pr": production_pr, "commits": commit_count}

    # Fetch PRs for this branch head.
    fields = "number,state,title,url,statusCheckRollup,mergeable,headRefName,baseRefName"
    stdout, _stderr, rc = run_gh(
        "pr",
        "list",
        "--repo",
        gh_r,
        "--head",
        branch,
        "--state",
        "all",
        "--json",
        fields,
        "--limit",
        "10",
        gh_token=gh_token,
    )

    if rc == 0 and stdout:
        try:
            prs = json.loads(stdout)
        except json.JSONDecodeError:
            prs = []

        staging_branch = cfg.staging_branch
        prod_branch = cfg.base_branch

        for pr in prs:
            base = pr.get("baseRefName", "")
            state = pr.get("state", "").upper()

            if staging_branch and base == staging_branch:
                # Prefer open PRs; fall back to most recent.
                if staging_pr is None or state == "OPEN":
                    staging_pr = _pr_info(pr)
            elif base == prod_branch and (production_pr is None or state == "OPEN"):
                production_pr = _pr_info(pr)

    return {
        "staging_pr": staging_pr,
        "production_pr": production_pr,
        "commits": commit_count,
    }


def get_actions_status(repo: str, branch: str, *, gh_token: str | None = None) -> dict[str, Any]:
    """Fetch the latest GitHub Actions workflow run for a branch.

    Returns run metadata + steps from the first job. If no runs exist,
    returns {"run_id": None}.
    """
    gh_r = safe_gh_repo(repo)
    if not gh_r:
        return {"run_id": None}

    # Get the latest workflow run for this branch
    fields = "databaseId,status,conclusion,name,url,createdAt,updatedAt"
    stdout, stderr, rc = run_gh(
        "run",
        "list",
        "--repo",
        gh_r,
        "--branch",
        branch,
        "--limit",
        "1",
        "--json",
        fields,
        gh_token=gh_token,
    )

    if rc != 0:
        msg = f"GitHub CLI failed: {stderr}"
        raise RuntimeError(msg)

    if not stdout:
        return {"run_id": None}

    try:
        runs = json.loads(stdout)
    except json.JSONDecodeError:
        return {"run_id": None}

    if not runs:
        return {"run_id": None}

    run = runs[0]
    run_id = run.get("databaseId")

    # Fetch steps from the first job of this run
    steps: list[dict[str, object]] = []
    if run_id:
        job_stdout, _job_stderr, job_rc = run_gh(
            "run",
            "view",
            str(run_id),
            "--repo",
            gh_r,
            "--json",
            "jobs",
            gh_token=gh_token,
        )
        if job_rc == 0 and job_stdout:
            try:
                job_data = json.loads(job_stdout)
                jobs = job_data.get("jobs", [])
                if jobs:
                    steps.extend(
                        {
                            "name": step.get("name", ""),
                            "status": step.get("status", ""),
                            "conclusion": step.get("conclusion") or None,
                            "number": step.get("number", 0),
                        }
                        for step in jobs[0].get("steps", [])
                    )
            except json.JSONDecodeError:
                pass

    return {
        "run_id": run_id,
        "status": run.get("status", ""),
        "conclusion": run.get("conclusion") or None,
        "name": run.get("name", ""),
        "html_url": run.get("url", ""),
        "created_at": run.get("createdAt", ""),
        "updated_at": run.get("updatedAt", ""),
        "steps": steps,
    }


def trigger_stage(repo: str, branch: str, message: str, *, gh_token: str | None = None) -> dict[str, Any]:
    """Push the branch and create a staging PR.

    Returns {ok, pr_url, message}.
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return {"ok": False, "pr_url": "", "message": f"Worktree not found for {repo}:{branch}"}

    cfg = get_repo(repo)
    if not cfg.staging_branch:
        return {"ok": False, "pr_url": "", "message": f"No staging branch configured for {repo}"}

    gh_r = safe_gh_repo(repo)
    if not gh_r:
        return {"ok": False, "pr_url": "", "message": f"Cannot resolve GitHub repo for {repo}"}

    # Push branch first.
    _, stderr, rc = run_git(wt, "push", "--force-with-lease", "-u", "origin", branch, timeout=60)
    if rc != 0:
        return {"ok": False, "pr_url": "", "message": f"Push failed: {stderr}"}

    # Create PR (or find existing one).
    stdout, stderr, rc = run_gh(
        "pr",
        "create",
        "--repo",
        gh_r,
        "--head",
        branch,
        "--base",
        cfg.staging_branch,
        "--title",
        branch,
        "--body",
        message or "",
        timeout=30,
        gh_token=gh_token,
    )

    if rc != 0:
        if "already exists" in stderr:
            # Fetch existing PR URL.
            url_out, _, _ = run_gh(
                "pr",
                "list",
                "--repo",
                gh_r,
                "--head",
                branch,
                "--base",
                cfg.staging_branch,
                "--json",
                "url",
                "--jq",
                ".[0].url",
                gh_token=gh_token,
            )
            if url_out:
                return {"ok": True, "pr_url": url_out, "message": "PR already exists"}
        return {"ok": False, "pr_url": "", "message": f"PR creation failed: {stderr}"}

    return {"ok": True, "pr_url": stdout, "message": "PR created"}


def trigger_prod(repo: str, branch: str, message: str, *, gh_token: str | None = None) -> dict[str, Any]:
    """Push the branch and create a production PR.

    Returns {ok, pr_url, message}.
    """
    wt = _resolve_worktree(repo, branch)
    if not wt:
        return {"ok": False, "pr_url": "", "message": f"Worktree not found for {repo}:{branch}"}

    cfg = get_repo(repo)
    gh_r = safe_gh_repo(repo)
    if not gh_r:
        return {"ok": False, "pr_url": "", "message": f"Cannot resolve GitHub repo for {repo}"}

    # Push branch first.
    _, stderr, rc = run_git(wt, "push", "--force-with-lease", "-u", "origin", branch, timeout=60)
    if rc != 0:
        return {"ok": False, "pr_url": "", "message": f"Push failed: {stderr}"}

    # Create PR (or find existing one).
    stdout, stderr, rc = run_gh(
        "pr",
        "create",
        "--repo",
        gh_r,
        "--head",
        branch,
        "--base",
        cfg.base_branch,
        "--title",
        branch,
        "--body",
        message or "",
        timeout=30,
        gh_token=gh_token,
    )

    if rc != 0:
        if "already exists" in stderr:
            url_out, _, _ = run_gh(
                "pr",
                "list",
                "--repo",
                gh_r,
                "--head",
                branch,
                "--base",
                cfg.base_branch,
                "--json",
                "url",
                "--jq",
                ".[0].url",
                gh_token=gh_token,
            )
            if url_out:
                return {"ok": True, "pr_url": url_out, "message": "PR already exists"}
        return {"ok": False, "pr_url": "", "message": f"PR creation failed: {stderr}"}

    return {"ok": True, "pr_url": stdout, "message": "PR created"}
