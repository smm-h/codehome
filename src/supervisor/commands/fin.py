"""v branch finalize: close branch lifecycle, archive, update Linear."""

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from supervisor.bus import Event, fire_sync
from supervisor.commands.branch import BranchError
from supervisor.linear_shared import auto_advance_state
from supervisor.paths import (
    branch_dir,
    prod_ref,
    repo_anchor,
    repo_archive,
    transfer_metadata,
    worktree_path,
)
from supervisor.resolution import parse_qualified
from supervisor.utils import die


def _notify_server_cleanup(qualified_branch: str) -> None:
    """Best-effort notification to v server to clean up services for a branch."""
    from supervisor.serve import read_server_url

    server_url = read_server_url()
    if not server_url:
        return
    try:
        data = json.dumps({"branch": qualified_branch}).encode()
        req = urllib.request.Request(
            f"{server_url}/api/services/cleanup",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Server notification is best-effort.


def _is_merged_to_production(branch: str, repo: str) -> bool:
    from supervisor.git import is_merged_to_production

    return is_merged_to_production(branch, repo)


def _is_merged_to_staging(branch: str, repo: str) -> bool:
    from supervisor.git import is_merged_to_staging

    return is_merged_to_staging(branch, repo)


def _archive_branch(repo: str, name: str, outcome: str, message: str | None, keep_worktree: bool = False) -> None:
    """Archive branch metadata to repos/<repo>/archive/ and remove the worktree.

    Copies docs/, progress.md, todo/, tests.json, issue.json, review.md.
    Writes end.json with metadata. Removes git worktree + branch + remote
    branch unless keep_worktree is True (keeps worktree and branch dir,
    only archives metadata).
    """
    from supervisor.git import (
        create_bundle,
        delete_local_branch,
        delete_remote_branch,
        git,
        remove_worktree,
        unique_commits,
        unlock_worktree,
        verify_bundle,
    )

    unlock_worktree(repo, name)

    dest = repo_archive(repo) / name
    dest.mkdir(parents=True, exist_ok=True)

    bd = branch_dir(repo, name)

    # Final commit to the branch dir's git repo before archiving.
    from supervisor.branch_git import commit_branch_dir

    commit_branch_dir(repo, name, f"v branch finalize: {outcome}")

    transfer_metadata(bd, dest)

    end_meta: dict[str, Any] = {
        "outcome": outcome,
        "message": message,
        "ended_at": datetime.now(UTC).isoformat(),
        "branch": name,
        "repo": repo,
    }
    try:
        from supervisor.linear_shared import get_linked_issue

        link, issue = get_linked_issue(repo, name)
        if link:
            end_meta["linear_identifier"] = link["identifier"]
            if issue:
                end_meta["linear_state"] = issue.get("state", "")
    except Exception:
        pass
    try:
        wt = worktree_path(repo, name)
        if wt.exists():
            head_sha = git(wt, "rev-parse", "HEAD", check=False)
            if head_sha:
                end_meta["final_commit"] = head_sha.strip()[:12]
    except Exception:
        pass
    (dest / "end.json").write_text(json.dumps(end_meta, indent=2) + "\n")

    # Create git bundle as safeguard before deleting anything.
    # Captures all commits unique to this branch vs production.
    bundle_path = dest / "branch.bundle"
    try:
        anchor = repo_anchor(repo)
        production_ref = prod_ref(repo)
        n_unique = unique_commits(name, repo)
        if n_unique > 0:
            create_bundle(anchor, name, production_ref, bundle_path)
            verify_bundle(anchor, bundle_path)
            end_meta["bundle"] = True
            end_meta["bundle_commits"] = n_unique
        else:
            end_meta["bundle"] = False
            end_meta["bundle_commits"] = 0
        # Re-write end.json with bundle info.
        (dest / "end.json").write_text(json.dumps(end_meta, indent=2) + "\n")
    except (Exception, SystemExit) as exc:
        sys.stderr.write(f"error: failed to create git bundle: {exc}\n")
        sys.stderr.write("aborting -- branch data would be lost without bundle\n")
        sys.exit(1)

    if not keep_worktree:
        anchor = repo_anchor(repo)
        wt = worktree_path(repo, name)
        if wt.exists():
            remove_worktree(anchor, wt)
        delete_local_branch(anchor, name)
        delete_remote_branch(anchor, name)

        if bd.is_dir():
            shutil.rmtree(bd)

        from supervisor.aliases import load_aliases, save_aliases

        aliases = load_aliases(repo)
        # Remove aliases whose chain ultimately resolves to the archived branch.
        stale = []
        for alias_name, alias_target in aliases.items():
            resolved = alias_target
            seen = {alias_name}
            while resolved in aliases and resolved not in seen:
                seen.add(resolved)
                resolved = aliases[resolved]
            if resolved == name:
                stale.append(alias_name)
        if stale:
            for old in stale:
                del aliases[old]
            save_aliases(repo, aliases)

    print(f"  archived: {repo}/archive/{name}/")


def close_branch(
    qualified: str,
    *,
    cancel: bool = False,
    message: str | None = None,
    keep_worktree: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Close a branch: validate, archive, update Linear. Returns result dict.

    Reusable helper used by ``v branch finalize`` (CLI) and ``POST /api/branches/.../close`` (API).
    Raises BranchError on validation/conflict/not-found failures instead of
    calling die() so non-CLI callers can handle errors gracefully.

    Returns dict with keys: qualified, archived_path, outcome.
    """
    repo, branch = parse_qualified(qualified)
    from supervisor.aliases import resolve_alias

    branch = resolve_alias(repo, branch)

    wt = worktree_path(repo, branch)
    if not wt.exists():
        bd = branch_dir(repo, branch)
        if not bd.exists():
            raise BranchError(f"branch '{repo}:{branch}' not found", category="not_found")

    # Cancel guards: block if merged to production, warn if merged to staging.
    if cancel and not force and wt.exists():
        if _is_merged_to_production(branch, repo):
            raise BranchError("branch is merged to production -- use close without cancel", category="conflict")
        if _is_merged_to_staging(branch, repo):
            raise BranchError("branch is merged to staging -- use force to override", category="conflict")

    # Done guards: clean worktree, merged to production.
    if not cancel and not force:
        from supervisor.git import worktree_is_clean

        if wt.exists() and not worktree_is_clean(wt):
            raise BranchError("worktree has uncommitted changes -- commit or use cancel", category="conflict")
        if wt.exists() and not _is_merged_to_production(branch, repo):
            raise BranchError("branch not merged to production -- deploy first or use cancel", category="conflict")

    # Check for leftover Docker volumes before archiving.
    if not force:
        from supervisor.serve.docker import compose_project_name

        project = compose_project_name(f"{repo}:{branch}")
        result = subprocess.run(
            ["docker", "volume", "ls", "--filter", f"name={project}_", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
        )
        leftover = [v for v in result.stdout.strip().splitlines() if v.strip()]
        if leftover:
            raise BranchError(
                f"Docker volumes still exist for this branch."
                f" Run `v services delete-volumes -B {repo}:{branch}` first.",
                category="conflict",
            )

    # Advance Linear issue state.
    if cancel:
        auto_advance_state(repo, branch, "Canceled")
    else:
        auto_advance_state(repo, branch, "Done")

    outcome = "done" if not cancel else "cancel"
    _archive_branch(repo, branch, outcome, message, keep_worktree=keep_worktree)

    # Emit event for audit trail.
    from supervisor.session import get_process_id

    session_id = get_process_id()
    fire_sync(
        Event(
            name="branch.close.DONE",
            payload={
                "session": session_id,
                "repo": repo,
                "branch": branch,
                "data": {"outcome": outcome, "message": message},
            },
            audit=True,
        )
    )

    # Notify the server to clean up services for this branch.
    _notify_server_cleanup(f"{repo}:{branch}")

    archived_path = str(repo_archive(repo) / branch)
    return {
        "qualified": f"{repo}:{branch}",
        "archived_path": archived_path,
        "outcome": outcome,
    }


def cmd_fin(args: argparse.Namespace) -> None:
    """Close a branch: archive metadata, remove worktree, update Linear.

    Resolves the branch from -B/--branch flag.

    Default (done): checks clean worktree, merged to production, non-empty
    progress section. Updates Linear to Done. Archives to branches/archive/.

    --cancel: abandons work. Allows dirty worktree. Updates Linear to
    Canceled. Blocked if branch is merged to production (use v fin
    without --cancel). Blocked if merged to staging (use --force).

    --force: override all guards.
    """
    explicit_branch = getattr(args, "branch", None)

    if explicit_branch:
        # -B flag expects qualified name (repo:branch).
        repo, branch = parse_qualified(explicit_branch)
        from supervisor.aliases import resolve_alias

        branch = resolve_alias(repo, branch)
        wt = worktree_path(repo, branch)
        if not wt.exists():
            bd = branch_dir(repo, branch)
            if not bd.exists():
                die(f"branch '{repo}:{branch}' not found")
    else:
        die("no branch specified -- use: v branch finalize -B <repo:branch>")

    cancel = getattr(args, "cancel", False)
    force = getattr(args, "force", False)
    keep_worktree = getattr(args, "keep_worktree", False)
    message = getattr(args, "message", None)

    # CLI-only guard: warn about empty progress (not an error, just a warning).
    if not cancel and not force:
        from supervisor.progress import latest_section_is_empty

        if latest_section_is_empty(repo, branch):
            sys.stderr.write("warning: latest progress.md session section is empty\n")

    qualified = f"{repo}:{branch}"
    try:
        result = close_branch(
            qualified,
            cancel=cancel,
            message=message,
            keep_worktree=keep_worktree,
            force=force,
        )
    except BranchError as e:
        die(str(e))

    print("Branch closed. Run v branch select <repo:branch> to continue working.")
