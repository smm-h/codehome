"""Branch management commands: v branch {list,rename,alias,create,finalize,select,status}.

Also contains the create_branch helper used by cmd_new.
"""

import argparse
import datetime as dt_mod
import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from codehome.aliases import (
    load_aliases,
    require_worktree,
    reverse_aliases,
    save_aliases,
)
from codehome.git import git, git_passthrough, list_worktrees, unlock_worktree
from codehome.paths import (
    PROTECTED_BRANCHES,
    branch_dir,
    branch_name_from_wt,
    docs_path,
    repo_anchor,
    repo_archive,
    repo_dir,
    tests_file,
    transfer_metadata,
    worktree_path,
)
from codehome.resolution import active_context
from codehome.summary import worktree_info
from codehome.utils import bold, colorize, die, dim, group_by_date, render_date_tree, warn


class BranchError(Exception):
    """Raised when branch creation fails.

    Carries a category so callers (CLI, API) can map to appropriate
    exit codes or HTTP status codes.
    """

    def __init__(self, message: str, category: str = "validation"):
        super().__init__(message)
        # "validation" (bad input), "conflict" (already exists), "internal" (git/io failure)
        self.category = category


def _sync(repo: str = "bag") -> None:
    """Fetch and fast-forward production and staging (internal helper)."""
    from codehome.paths import staging_worktree

    anchor = repo_anchor(repo)
    git_passthrough(anchor, "fetch", "--prune")
    git_passthrough(anchor, "pull", "--ff-only")
    st = staging_worktree(repo)
    if st.exists():
        git_passthrough(st, "pull", "--ff-only")


def _sync_quiet(repo: str = "bag") -> bool:
    """Fetch origin silently. Returns True on success."""
    anchor = repo_anchor(repo)
    result = subprocess.run(
        ["git", "-C", str(anchor), "fetch", "--prune"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _print_branch_help(_args: argparse.Namespace) -> None:
    """Print help for the branch command group."""
    from codehome.cli import build_parser

    parser = build_parser()
    # Reach into the branch subparser and print its help.
    if parser._subparsers is None:
        print("Usage: v branch <subcommand>")
        return
    for action in parser._subparsers._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "branch" in choices:
            choices["branch"].print_help()
            return
    # Fallback: should not happen.
    print("Usage: v branch <subcommand>")


def cmd_alias(args: argparse.Namespace) -> None:
    """List, add, or remove branch aliases.

    Uses the active session's repo, or lists all repos if no session.
    """
    from codehome.config import load_repos

    active_context()  # side-effect: validates session state

    if args.rm:
        if not args.name:
            die("usage: v branch alias --rm <name>")
        # Search all repos for the alias.
        for repo_name in load_repos():
            aliases = load_aliases(repo_name)
            if args.name in aliases:
                del aliases[args.name]
                save_aliases(repo_name, aliases)
                print(f"removed alias: {args.name} (repo: {repo_name})")
                return
        die(f"no alias for '{args.name}'")

    if args.name and args.target:
        # Target must be a qualified name (repo:branch).
        from codehome.resolution import parse_qualified

        repo, branch = parse_qualified(args.target)
        require_worktree(repo, branch)
        aliases = load_aliases(repo)
        aliases[args.name] = branch
        save_aliases(repo, aliases)
        print(f"alias: {args.name}={branch} (repo: {repo})")
        return

    if args.name and not args.target:
        die("usage: v branch alias <name> <target>  or  v branch alias --rm <name>")

    # Neither name nor target given -> show help.
    die("subcommand required: v branch alias <name> <target> | v branch alias --rm <name>")


def cmd_ls(args: argparse.Namespace) -> None:
    """List branches with full status, auto-syncing for accurate staleness.

    Bare names by default. -M for full markdown (commits behind,
    push status, diffstat, dirty files, tests, context). -S selected
    only. -F fetches first for accurate staleness.
    """
    from codehome.config import load_repos

    if args.fetch:
        sys.stderr.write("syncing...\n")
        for repo_name in load_repos():
            _sync_quiet(repo_name)

    # Gather worktrees from all repos. Track repo for each branch.
    worktrees: dict[str, tuple[Path, str, str]] = {}  # name -> (path, hash, repo)
    for repo_name in load_repos():
        anchor = repo_anchor(repo_name)
        if not anchor.exists():
            continue
        output = git(anchor, "worktree", "list", "--porcelain")
        cur_path = cur_hash = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                cur_path = Path(line.split(" ", 1)[1])
            elif line.startswith("HEAD "):
                cur_hash = line.split(" ", 1)[1][:8]
            elif line == "":
                if cur_path and cur_hash:
                    name = branch_name_from_wt(cur_path)
                    worktrees[name] = (cur_path, cur_hash, repo_name)
                cur_path = cur_hash = None
        # Flush last entry (porcelain output doesn't end with a blank line).
        if cur_path and cur_hash:
            name = branch_name_from_wt(cur_path)
            worktrees[name] = (cur_path, cur_hash, repo_name)

    names = [n for n in worktrees if n not in PROTECTED_BRANCHES]

    # Include archived branches when --all is set.
    archived: set[str] = set()
    # Map archived branch name -> archive dir path.
    archived_dirs: dict[str, Path] = {}
    # Map archived branch name -> repo name (for repo filtering).
    archived_repos: dict[str, str] = {}
    if args.all:
        for repo_name in load_repos():
            arch = repo_archive(repo_name)
            if arch.is_dir():
                for d in arch.iterdir():
                    if d.is_dir() and d.name not in worktrees:
                        names.append(d.name)
                        archived.add(d.name)
                        archived_dirs[d.name] = d
                        archived_repos[d.name] = repo_name

    if args.patterns:
        repo_names = set(load_repos().keys())
        # Patterns that match a repo name filter by repo; others match branch names.
        repo_pats = [p for p in args.patterns if p in repo_names]
        name_pats = [p for p in args.patterns if p not in repo_names]
        filtered = []
        for n in names:
            # Determine which repo this branch belongs to.
            repo = worktrees[n][2] if n in worktrees else archived_repos.get(n)
            # Match if branch belongs to a requested repo...
            if (repo_pats and repo in repo_pats) or (name_pats and any(fnmatch.fnmatch(n, p) for p in name_pats)):
                filtered.append(n)
        names = filtered

    # Sort by directory creation time (newest first) when -t/--recent is set.
    if args.recent:

        def _ctime(n: str) -> float:
            if n in archived_dirs:
                return archived_dirs[n].stat().st_ctime
            repo = worktrees[n][2]
            return branch_dir(repo, n).stat().st_ctime

        names.sort(key=_ctime, reverse=True)

    ctx = active_context()
    sel = ctx.branch if ctx else None
    if args.selected:
        if not sel:
            die("no branch selected\n  run: v branch select <repo:branch>")
        names = [n for n in names if n == sel]

    # Build combined reverse aliases from all repos.
    rev_aliases: dict[str, list[str]] = {}
    for repo_name in load_repos():
        for branch_name, alias_list in reverse_aliases(repo_name).items():
            rev_aliases.setdefault(branch_name, []).extend(alias_list)

    if not args.markdown:

        def _branch_label(n: str) -> str:
            """Build display label for a branch name."""
            aka = rev_aliases.get(n)
            parts = [n]
            if aka:
                parts.append(f"(aka: {', '.join(sorted(aka))})")
            if n in archived:
                parts.append("(archived)")
            label = " ".join(parts)
            return dim(label) if n in archived else label

        def _head_date(n: str) -> dt_mod.date:
            """Get HEAD commit date for a branch, or dir ctime for archived."""
            from datetime import UTC, datetime

            if n in archived_dirs:
                return datetime.fromtimestamp(archived_dirs[n].stat().st_ctime, tz=UTC).date()
            wt, _, _ = worktrees[n]
            raw = git(wt, "log", "-1", "--format=%ct", "HEAD")
            return datetime.fromtimestamp(int(raw.strip()), tz=UTC).date()

        # Group by repo, then by date within each repo.
        repos_with_branches: dict[str, list[str]] = {}
        for n in names:
            if n in archived_dirs:
                # Archived branches: figure out which repo they're in.
                for repo_name in load_repos():
                    if (repo_archive(repo_name) / n).is_dir():
                        repos_with_branches.setdefault(repo_name, []).append(n)
                        break
            elif n in worktrees:
                repos_with_branches.setdefault(worktrees[n][2], []).append(n)

        repo_names_sorted = sorted(repos_with_branches.keys())
        # If only one repo has branches, skip the repo header.
        show_repo_header = len(repo_names_sorted) > 1

        for repo_name in repo_names_sorted:
            repo_branches_list = repos_with_branches[repo_name]
            repo_branches_list.sort(key=_head_date, reverse=True)
            groups = group_by_date(repo_branches_list, _head_date)

            if show_repo_header:
                print(bold(repo_name))
                indent = "  "
            else:
                indent = ""

            lines = render_date_tree(groups, indent + "  ", name_fn=_branch_label)
            if lines:
                if not show_repo_header:
                    print(bold("branches"))
                print("\n".join(lines))
            else:
                print(f"{indent}  (none)")

            if show_repo_header:
                print()  # blank line between repos
        return

    blocks = []
    for name in names:
        if name in archived:
            # Archived branches have no worktree; show name only.
            blocks.append(f"## {name} (archived)")
            continue
        wt_path, commit_hash, repo = worktrees[name]
        label = "selected" if name == sel else None
        aka = rev_aliases.get(name)
        blocks.append(worktree_info(repo, name, wt_path, commit_hash, label, aka))

    if blocks:
        print(colorize("\n\n".join(blocks)))


def _title_from_branch(name: str) -> str:
    """Derive a human-readable title from a branch name."""
    return name.replace("-", " ").replace("_", " ").strip().capitalize()


def _slugify(name: str) -> str:
    """Turn a human-readable name into a valid branch slug."""
    import re

    slug = name.lower()
    slug = re.sub(r"[\s_]+", "-", slug)  # spaces/underscores -> hyphens
    slug = re.sub(r"[^a-z0-9\-/.]", "", slug)  # strip invalid chars
    slug = re.sub(r"-{2,}", "-", slug)  # collapse repeated hyphens
    return slug.strip("-.")


def _run_worktree_init_extensions(repo: str, branch: str, wt_path: Path) -> None:
    """Discover and run worktree-init extensions for the repo.

    Handles the full discover -> register -> run pipeline. If any
    worktree-init check fails, prints a warning but does not abort
    branch creation (setup failures are non-fatal).
    """
    import asyncio

    from codehome.checks.registry import CheckContext, CheckRegistry
    from codehome.checks.runner import run_group
    from codehome.extensions.discovery import discover_extensions
    from codehome.extensions.state import load_state, merge_discovered, save_state
    from codehome.paths import ROOT

    result = discover_extensions(repo, branch)
    if not result.extensions:
        return

    old_state = load_state(repo, ROOT)
    new_state = merge_discovered(old_state, result.extensions)
    save_state(repo, ROOT, new_state)

    registry = CheckRegistry()
    from codehome.extensions.loader import load_extensions

    load_extensions(repo, branch, ROOT, registry)

    if not registry.group("worktree-init"):
        return

    ctx = CheckContext(
        root=wt_path,
        repo=repo,
        branch=branch,
    )

    report = asyncio.run(run_group("worktree-init", registry=registry, ctx=ctx))
    if not report.ok:
        for r in report.results:
            if r.outcome in ("fail", "timeout") and r.message:
                warn(r.message)


def create_branch(
    name: str,
    description: str,
    repo: str = "bag",
    no_issue: bool = False,
    remote: bool = False,
) -> dict[str, Any]:
    """Create worktree + Linear issue. Returns a result dict.

    Reusable helper used by ``v branch create`` (CLI) and ``POST /api/branches`` (API).
    Raises BranchError on validation/conflict/internal failures instead of
    calling die() so non-CLI callers can handle errors gracefully.

    Returns dict with keys: name, qualified, worktree_path, issue_id.
    """
    from codehome.config import load_repos
    from codehome.paths import prod_ref

    raw = name
    if remote:
        # Remote branch names must match exactly for tracking; validate
        # that slugification wouldn't change the name.
        slugified = _slugify(raw)
        if slugified != raw:
            raise BranchError(
                f"branch name '{raw}' would be slugified to '{slugified}' -- remote branch names must be exact"
            )
        name = raw
    else:
        name = _slugify(raw)
        if not name:
            raise BranchError(f"invalid branch name: '{raw}' (nothing left after slugification)")
        if name != raw:
            print(f"  slugified: '{raw}' -> '{name}'")

    # Validate repo exists.
    repos = load_repos()
    if repo not in repos:
        available = ", ".join(sorted(repos)) if repos else "(none configured)"
        raise BranchError(f"unknown repo '{repo}' (available: {available})")
    repo_cfg = repos[repo]

    # Validate with git's own rules.
    rc = subprocess.run(
        ["git", "check-ref-format", "--branch", name],
        capture_output=True,
    ).returncode
    if rc != 0:
        raise BranchError(f"invalid branch name: '{name}'")

    # Duplicate detection across ALL repos (branch names are globally unique).
    for r_name in repos:
        anchor = repo_anchor(r_name)
        if anchor.exists() and name in list_worktrees(r_name):
            raise BranchError(f"branch '{name}' already exists (repo: {r_name})", category="conflict")
    if branch_dir(repo, name).exists():
        raise BranchError(
            f"orphaned branch dir for '{name}' exists -- run: v branch finalize --cancel",
            category="conflict",
        )

    if not _sync_quiet(repo):
        warn("fetch failed, proceeding with local state")

    from codehome.deploy_shared import remove_staging_worktree

    remove_staging_worktree(repo)

    bd = branch_dir(repo, name)
    bd.mkdir(parents=True, exist_ok=True)

    anchor = repo_anchor(repo)

    # Resolve the starting ref: remote tracking branch, another branch's HEAD, or production.
    if remote:
        # Verify the remote branch exists after fetch.
        check = subprocess.run(
            ["git", "-C", str(anchor), "rev-parse", "--verify", f"origin/{name}"],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            raise BranchError(f"remote branch 'origin/{name}' not found. Run 'git fetch' or check the branch name.")
        # Use origin/{name} so git automatically sets up tracking.
        ref = f"origin/{name}"
    else:
        ref = prod_ref(repo)

    wt_path = worktree_path(repo, name)
    rc = git_passthrough(anchor, "worktree", "add", str(wt_path), "-b", name, ref)
    if rc != 0:
        if bd.exists() and not any(bd.iterdir()):
            bd.rmdir()
        raise BranchError(f"git worktree add failed (exit code {rc})", category="internal")
    # Run worktree-init extensions (e.g. env files, symlinks, git hooks).
    _run_worktree_init_extensions(repo, name, wt_path)

    test_file = tests_file(repo, name)
    if not test_file.exists():
        test_file.write_text('{"tests": []}\n')
    docs_dir = docs_path(repo, name)
    docs_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = docs_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()
    review_file = bd / "review.md"
    if not review_file.exists():
        review_file.touch()
    todo_dir = bd / "todo"
    todo_dir.mkdir(parents=True, exist_ok=True)
    (todo_dir / ".done").mkdir(exist_ok=True)
    (todo_dir / ".defer").mkdir(exist_ok=True)
    (todo_dir / ".obsolete").mkdir(exist_ok=True)
    scripts_dir = bd / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    # Copy service template from repo root if it exists, so the server
    # can auto-discover and register services for this branch.
    svc_template = repo_dir(repo) / ".services.template.json"
    svc_config = bd / ".services.json"
    if svc_template.is_file() and not svc_config.exists():
        shutil.copy2(svc_template, svc_config)
    progress = bd / "progress.md"
    if not progress.exists():
        progress.touch()
    # Branch context file: injected into Claude's system prompt via --append-system-prompt-file.
    context = bd / "context.md"
    if not context.exists():
        lines = [f"# {name}\n"]
        if description:
            lines.append(f"\n{description}\n")
        lines.append("\n## Architecture\n\n## Key Files\n\n## Decisions\n")
        context.write_text("".join(lines))

    # Create Linear issue and link it.
    issue_id = None
    if not no_issue:
        from codehome.linear_shared import create_issue, save_issue_link

        title = _title_from_branch(name)
        team = repo_cfg.default_linear_team or "VELENT"
        issue = create_issue(title, team=team, description=description)
        if issue:
            save_issue_link(repo, name, issue["identifier"], issue["id"], issue["team"])
            issue_id = issue["identifier"]
        else:
            warn("failed to create Linear issue")

    # Record branch metadata for child-branch diff resolution or remote adoption.
    branch_meta = bd / "branch.json"
    if remote:
        branch_meta.write_text(json.dumps({"remote_adopted": True}, indent=2) + "\n")

    # Emit branch.create.DONE event.
    from codehome.bus import Event, fire_sync
    from codehome.session import get_process_id

    session_id = get_process_id()
    fire_sync(
        Event(
            name="branch.create.DONE",
            payload={"session": session_id, "repo": repo, "branch": name, "data": {"description": description}},
            audit=True,
        )
    )

    # Initialize git tracking for the branch directory (metadata, not code).
    from codehome.branch_git import commit_branch_dir

    commit_branch_dir(repo, name, "v branch create: branch created")

    return {
        "name": name,
        "qualified": f"{repo}:{name}",
        "worktree_path": str(wt_path),
        "issue_id": issue_id,
    }


def _teardown_docker_containers(qualified: str) -> None:
    """Stop and remove Docker containers/volumes for a branch's compose project.

    Called before rename so that containers named after the old project name
    don't become orphans. Failures are non-fatal (warns but doesn't abort).
    """
    from codehome.serve.docker import COMPOSE_FILE, compose_down, compose_project_name

    project = compose_project_name(qualified)

    # Check if any containers exist for this project before attempting teardown.
    result = subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), "ps", "-q"],
        capture_output=True,
        text=True,
    )
    containers = [c for c in result.stdout.strip().splitlines() if c.strip()]
    if not containers:
        return

    print(f"  docker: stopping containers for project '{project}'...")
    ok, msg = compose_down(qualified, remove_volumes=True)
    if ok:
        print(f"  docker: removed {len(containers)} container(s) and volumes")
    else:
        warn(f"docker cleanup failed (containers may be orphaned): {msg}")


def rename_branch(qualified: str, new_name: str) -> dict[str, str]:
    """Rename a branch: move worktree, rename git branch, transfer metadata.

    Reusable helper used by ``v branch rename`` (CLI) and
    ``POST /api/branches/.../rename`` (API).
    Raises BranchError on validation/conflict/internal failures instead of
    calling die() so non-CLI callers can handle errors gracefully.

    Returns dict with keys: old_qualified, new_qualified.
    """
    # Parse qualified name (repo:branch).
    if ":" not in qualified:
        raise BranchError(f"expected repo:branch format, got '{qualified}'")
    repo, old = qualified.split(":", 1)

    # Reject qualified names as the new name -- only bare branch names allowed.
    if ":" in new_name:
        bare = new_name.split(":", 1)[1]
        raise BranchError(
            f"new name should not contain ':' (got '{new_name}')\n"
            f"  hint: use just the branch name: v branch rename {qualified} {bare}"
        )

    new = _slugify(new_name)
    if not new:
        raise BranchError(f"invalid branch name: '{new_name}' (nothing left after slugification)")

    for name in (old, new):
        if name in PROTECTED_BRANCHES:
            raise BranchError(f"cannot rename protected branch '{name}'")

    if old == new:
        raise BranchError("new name is the same as the old name")

    # Validate the new name with git's own rules.
    rc = subprocess.run(
        ["git", "check-ref-format", "--branch", new],
        capture_output=True,
    ).returncode
    if rc != 0:
        raise BranchError(f"invalid branch name: '{new}'")

    new_bd = branch_dir(repo, new)
    if new_bd.exists():
        raise BranchError(f"'{new}' already exists", category="conflict")

    # Check all repos for name collision (branch names are globally unique).
    from codehome.config import load_repos

    for r_name in load_repos():
        if r_name == repo:
            continue
        anchor_r = repo_anchor(r_name)
        if anchor_r.exists() and new in list_worktrees(r_name):
            raise BranchError(f"branch '{new}' already exists (repo: {r_name})", category="conflict")

    from codehome.paths import STAGING_MERGE_STATE

    if STAGING_MERGE_STATE.exists():
        state = json.loads(STAGING_MERGE_STATE.read_text())
        if state.get("branch") == old:
            raise BranchError(f"staging merge in progress for '{old}' -- resolve it first", category="conflict")

    anchor = repo_anchor(repo)

    print(f"[{repo}:{old} -> {repo}:{new}]")

    # Tear down Docker containers for the old project name before renaming.
    # Without this, containers from the old name survive the rename and become
    # orphans (the new name produces a different compose project name).
    _teardown_docker_containers(qualified)

    old_wt = worktree_path(repo, old)
    new_wt = worktree_path(repo, new)
    new_bd.mkdir(parents=True, exist_ok=True)
    unlock_worktree(repo, old)  # health check #9 may have locked it

    rc = git_passthrough(anchor, "worktree", "move", str(old_wt), str(new_wt))
    if rc != 0:
        new_bd.rmdir()
        raise BranchError(f"git worktree move failed (exit code {rc})", category="internal")

    rc = git_passthrough(anchor, "branch", "-m", old, new)
    if rc != 0:
        # Rollback: move worktree back.
        git_passthrough(anchor, "worktree", "move", str(new_wt), str(old_wt))
        new_bd.rmdir()
        raise BranchError(f"branch rename failed; worktree moved back to {old}", category="internal")

    old_bd = branch_dir(repo, old)
    transfer_metadata(old_bd, new_bd, move=True, verbose=True)

    if old_bd.exists():
        shutil.rmtree(old_bd)

    from codehome.git import git as git_fn

    remote_ref = git_fn(anchor, "rev-parse", "--verify", f"origin/{old}", check=False)
    if remote_ref:
        git_passthrough(worktree_path(repo, new), "push", "origin", f":{old}", f"{new}", "-u")
        print(f"  remote: deleted origin/{old}, pushed origin/{new}")

    aliases = load_aliases(repo)
    aliases[old] = new
    save_aliases(repo, aliases)
    print(f"  alias: {old}={new}")

    return {
        "old_qualified": f"{repo}:{old}",
        "new_qualified": f"{repo}:{new}",
    }


def cmd_rename(args: argparse.Namespace) -> None:
    """Rename a worktree and its branch.

    Moves worktree, renames branch locally + on origin, moves
    companion files, creates alias old->new so stale references
    resolve. The old name should be qualified (repo:branch) or resolved
    from the active session. The new name is a plain branch name.
    """
    from codehome.resolution import resolve

    # Resolve old branch (uses -B flag or session).
    ctx = resolve(getattr(args, "branch", None) or args.old)
    qualified = f"{ctx.repo}:{ctx.branch}"

    try:
        result = rename_branch(qualified, args.new)
    except BranchError as e:
        die(str(e))

    print(f"renamed: {result['old_qualified']} -> {result['new_qualified']}")
