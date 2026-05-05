"""Git operation commands: v git diff, v git changes, v git compare, v git explain, v git rebase, v git push."""

import argparse
import datetime
import json
import shutil
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

from codehome.git import (
    commits_behind,
    exclude_args,
    git,
    git_passthrough,
    ignorable_patterns,
    staleness_tag,
)
from codehome.migrations import MIGRATION_RE, detect_stale_migrations, remigrate_file
from codehome.paths import MIGRATIONS_DIR, REBASE_STATE, base_ref, branch_dir, repo_anchor
from codehome.resolution import resolve
from codehome.summary import (
    _parse_file_statuses,
    _uncommitted_file_data,
    branch_summary,
    parse_commits,
    parse_numstat,
    section_commits_chronological,
    section_files_annotated,
    section_header,
    uncommitted_stats,
)
from codehome.utils import claude_prompt, colorize, die, warn


def _print_git_help(_args: argparse.Namespace) -> None:
    """Print help for the git command group (bare ``v git``)."""
    from codehome.cli import build_parser

    parser = build_parser()
    if parser._subparsers is None:
        return
    for action in parser._subparsers._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "git" in choices:
            choices["git"].print_help()
            return


def _init_delta(args: argparse.Namespace) -> bool:
    """Check if delta is available when color output is requested."""
    if not getattr(args, "color", False):
        return False
    if shutil.which("delta"):
        return True
    warn("delta not found in PATH, falling back to plain diff")
    return False


_DIFF_LINE_LIMIT = 500


def _diff_stats(wt: Path, repo: str, branch: str) -> tuple[int, int]:
    """Return (line_count, file_count) for the raw diff vs base branch."""
    excl = exclude_args()
    raw = git(wt, "diff", f"{base_ref(repo, branch)}...HEAD", "--", ".", *excl)
    lines = raw.count("\n") if raw else 0
    # Count files from diff headers (--- a/... lines).
    files = raw.count("\n--- a/") + raw.count("\n--- /dev/null") if raw else 0
    return lines, files


def cmd_diff(args: argparse.Namespace) -> None:
    """Show branch summary vs base branch (commits, files, full diff).

    Compares against the repo's base branch. Groups files by area. Filters
    noise (package-lock.json, .env.local). -c pipes through delta.
    If the diff exceeds 500 lines, prints a size warning instead (use --full
    to bypass).
    """
    use_delta = _init_delta(args)
    ctx = resolve(getattr(args, "branch", None))
    tag = staleness_tag(ctx.branch, ctx.repo)
    use_tree = getattr(args, "tree", False)

    # Size gate: skip large diffs unless --full is set.
    if not getattr(args, "full", False):
        line_count, file_count = _diff_stats(ctx.worktree, ctx.repo, ctx.branch)
        if line_count > _DIFF_LINE_LIMIT:
            print(
                f"Diff is too large ({line_count} lines across {file_count} files). "
                "Run `v git changes` for a summary instead."
            )
            return

    result = branch_summary(ctx.worktree, ctx.branch, ctx.repo, tag, delta=use_delta, tree=use_tree)
    if result:
        print(result)


def cmd_changes(args: argparse.Namespace) -> None:
    """List changed files with stats and status -- no inline diffs.

    Like `v git diff` but files only — no patch. Each file shows lines
    added/removed, status (new/modified/deleted), grouped by area.
    Uncommitted files are merged into the main table.
    """
    ctx = resolve(getattr(args, "branch", None))
    tag = staleness_tag(ctx.branch, ctx.repo)
    use_tree = getattr(args, "tree", False)
    excl = exclude_args()
    file_entries, total_added, total_removed = parse_numstat(ctx.worktree, excl, ctx.repo, ctx.branch)
    statuses = _parse_file_statuses(ctx.worktree, excl, ctx.repo, ctx.branch)
    uc_stats = uncommitted_stats(ctx.worktree)
    has_uncommitted = uc_stats[0] + uc_stats[3] > 0

    # Merge uncommitted files into the main file list.
    uc_by_path: dict[str, str] = {}
    if has_uncommitted:
        uc_entries, _uc_statuses = _uncommitted_file_data(ctx.worktree)
        committed_paths = {path for path, _ in file_entries}
        uc_by_path = {path: suffix.strip() for path, suffix in uc_entries}

        # Add uncommitted-only files.
        for path, suffix in uc_entries:
            if path not in committed_paths:
                file_entries.append((path, suffix))
                statuses[path] = "U"

    parts = [
        section_header(
            ctx.repo,
            ctx.branch,
            ctx.worktree,
            tag,
            file_entries,
            total_added,
            total_removed,
            wt_uncommitted=uc_stats if has_uncommitted else None,
        ),
        section_files_annotated(file_entries, statuses, ctx.repo, tree=use_tree, uncommitted=uc_by_path or None),
    ]
    print(colorize("\n".join(parts)))


def cmd_commits(args: argparse.Namespace) -> None:
    """Show commits on a branch vs base branch."""
    ctx = resolve(getattr(args, "branch", None))
    recent = getattr(args, "recent", False)
    commits = parse_commits(ctx.worktree, ctx.repo, ctx.branch, recent_first=recent)
    print(colorize(section_commits_chronological(commits)))


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two branches (diffstat + patch)."""
    from codehome.aliases import require_worktree, resolve_alias
    from codehome.resolution import parse_qualified

    use_delta = _init_delta(args)

    # Both args are qualified names (repo:branch).
    repo_a, branch_a = parse_qualified(args.branch_a)
    repo_b, branch_b = parse_qualified(args.branch_b)
    branch_a = resolve_alias(repo_a, branch_a)
    branch_b = resolve_alias(repo_b, branch_b)
    require_worktree(repo_a, branch_a)
    require_worktree(repo_b, branch_b)

    # Use first branch's repo for the anchor clone.
    anchor = repo_anchor(repo_a)
    tags = [f"{n} {staleness_tag(n, repo_a)}" for n in (branch_a, branch_b) if n != "production"]
    header = f"[{branch_a} vs {branch_b}]"
    if tags:
        header += " " + ", ".join(tags)
    print(header, flush=True)
    excl = exclude_args()
    git_passthrough(anchor, "diff", f"{branch_a}..{branch_b}", "--stat", "--", ".", *excl)
    print(flush=True)
    if use_delta:
        from codehome.git import pipe_through_delta

        diff = git(anchor, "diff", f"{branch_a}..{branch_b}", "--", ".", *excl)
        pipe_through_delta(diff)
    else:
        git_passthrough(anchor, "diff", f"{branch_a}..{branch_b}", "--", ".", *excl)


def cmd_explain(args: argparse.Namespace) -> None:
    """AI-explain branch changes by piping summary through claude -p.

    Pipes diff summary through `claude -p` for an AI-generated
    explanation. Will not work inside Claude Code. If the diff exceeds
    500 lines, refuses to send it unless --full is set.
    """
    ctx = resolve(getattr(args, "branch", None))

    # Size gate: don't waste AI context on huge diffs unless --full.
    if not getattr(args, "full", False):
        line_count, file_count = _diff_stats(ctx.worktree, ctx.repo, ctx.branch)
        if line_count > _DIFF_LINE_LIMIT:
            print(
                f"Diff is too large ({line_count} lines across {file_count} files). "
                "Run `v git changes` for a summary instead."
            )
            return

    tag = staleness_tag(ctx.branch, ctx.repo)
    text = branch_summary(ctx.worktree, ctx.branch, ctx.repo, tag)
    prompt = (
        "You receive a branch summary (commits, changed files, and full diff) "
        "for a feature branch vs production. Explain what the branch does and why "
        "in a clear, concise narrative. Be specific about intent, not just mechanics. "
        "No preamble, no markdown headings."
    )
    result = claude_prompt(prompt, text)
    if result.returncode != 0:
        sys.stderr.write(f"claude failed: {result.stderr}\n")
        sys.exit(result.returncode)
    print(result.stdout.strip())


def _detect_skip_worktree(wt: Path) -> list[str]:
    """Return list of files with --skip-worktree flag set."""
    output = git(wt, "ls-files", "-v", check=False)
    return [line[2:] for line in output.splitlines() if line.startswith("S ")]


def _remigrate(wt: Path, repo: str, *, dry_run: bool = False) -> list[tuple[str, str]]:
    """Auto-remigrate stale migrations. Returns list of (old, new) renames."""
    stale = detect_stale_migrations(wt, repo)
    if not stale:
        return []

    # Block if there are staged changes (prevents accidental bundling).
    if not dry_run:
        staged = git(wt, "diff", "--cached", "--name-only")
        if staged.strip():
            die("staged changes exist -- commit or unstage them before rebasing")

    now = datetime.datetime.now(datetime.UTC)
    renames: list[tuple[str, str]] = []
    prefix = "[dry-run] " if dry_run else ""

    for i, filename in enumerate(stale):
        ts = (now + datetime.timedelta(seconds=i)).strftime("%Y%m%d%H%M%S")
        old, new = remigrate_file(wt, filename, dry_run=dry_run, timestamp=ts)
        renames.append((old, new))
        print(f"{prefix}remigrate: {old} -> {new}")

    if not dry_run and renames:
        for old, new in renames:
            git(wt, "add", f"{MIGRATIONS_DIR}/{old}")
            git(wt, "add", f"{MIGRATIONS_DIR}/{new}")

        if len(renames) == 1:
            old, new = renames[0]
            msg = f"remigrate: {old} -> {new}"
        else:
            lines = [f"remigrate: bump {len(renames)} stale migration(s)", ""]
            for old, new in renames:
                lines.append(f"  {old} -> {new}")
            msg = "\n".join(lines)

        git(wt, "commit", "-m", msg)
        print("remigrate: committed")

    return renames


def _git_dir(wt: Path) -> Path:
    """Return the actual .git directory for a worktree.

    Worktrees use a .git *file* pointing to the real git dir, not a directory.
    """
    dot_git = wt / ".git"
    if dot_git.is_file():
        # .git file contains "gitdir: <path>"
        content = dot_git.read_text().strip()
        if content.startswith("gitdir: "):
            return Path(content[len("gitdir: ") :])
    return dot_git


def _is_rebase_in_progress(wt: Path) -> bool:
    """Check if a git rebase is currently in progress."""
    gd = _git_dir(wt)
    return (gd / "rebase-merge").is_dir() or (gd / "rebase-apply").is_dir()


def _rebase_head_message(wt: Path) -> str:
    """Get the commit message of the current failing rebase commit."""
    return git(wt, "log", "--format=%s", "-1", "REBASE_HEAD", check=False).strip()


def _all_conflicts_are_migrations(wt: Path) -> bool:
    """Check if all unmerged (conflicting) files are migration files."""
    status = git(wt, "status", "--porcelain")
    unmerged = []
    for line in status.splitlines():
        # Unmerged entries have X/Y codes like UU, AA, DD, AU, UA, DU, UD
        xy = line[:2]
        if "U" in xy or xy in {"AA", "DD"}:
            filepath = line[3:].split(" -> ")[-1]
            unmerged.append(filepath)
    if not unmerged:
        return False
    for filepath in unmerged:
        name = Path(filepath).name
        if not filepath.startswith(MIGRATIONS_DIR + "/") or not MIGRATION_RE.match(name):
            return False
    return True


def _auto_resolve_remigrate(wt: Path) -> bool:
    """Auto-resolve consecutive remigrate conflicts by skipping them.

    Loops through rebase conflicts, skipping commits whose message starts
    with "remigrate:" and whose conflicts are all migration files.
    Returns True if the rebase completed, False if a non-remigrate conflict
    was hit.
    """
    while _is_rebase_in_progress(wt):
        msg = _rebase_head_message(wt)
        if not msg.startswith("remigrate:"):
            return False
        if not _all_conflicts_are_migrations(wt):
            return False
        rc = git_passthrough(wt, "rebase", "--skip")
        print("Skipped remigrate commit (inherited migration timestamps)")
        if rc != 0 and _is_rebase_in_progress(wt):
            # Another conflict appeared -- loop will re-check
            continue
        if rc == 0:
            return True
    # Rebase finished (no more rebase in progress)
    return True


def _target_subjects(wt: Path, ref: str) -> set[str]:
    """Collect all commit subjects on the target branch."""
    out = git(wt, "log", "--format=%s", ref, check=False).strip()
    return set(out.splitlines()) if out else set()


def _auto_skip_duplicates(wt: Path, ref: str) -> bool:
    """Auto-skip commits whose subject already exists on the target branch.

    When a branch chain is rebased, production commits can appear on both
    sides with different SHAs and slightly different patch context.  Git's
    --no-reapply-cherry-picks can't detect these because the patch-ids
    diverge.  This function detects them by subject-line matching and
    auto-skips them, then delegates to _auto_resolve_remigrate for any
    remigrate conflicts that follow.

    Returns True if the rebase completed (or no rebase is in progress).
    Returns False when a real (non-duplicate, non-remigrate) conflict is hit.
    """
    subjects = _target_subjects(wt, ref)
    skipped = 0

    while _is_rebase_in_progress(wt):
        msg = _rebase_head_message(wt)
        if not msg:
            break

        # Delegate remigrate conflicts to the specialised resolver.
        if msg.startswith("remigrate:") and _all_conflicts_are_migrations(wt):
            if _auto_resolve_remigrate(wt):
                break  # rebase finished
            continue  # remigrate resolver stopped on a non-remigrate conflict

        if msg not in subjects:
            return False  # real conflict

        git_passthrough(wt, "rebase", "--skip")
        skipped += 1
        print(f"  skipped duplicate: {msg}")

    if skipped:
        print(f"Auto-skipped {skipped} duplicate commit(s)")
    return True


def _save_rebase_state(
    wt: Path,
    branch: str,
    repo: str,
    skip_worktree_files: dict[str, bytes | None],
    stashed: bool,
    meta_path: str,
    meta: dict[str, Any] | None,
) -> None:
    """Persist rebase state so continue/abort can restore it."""
    state = {
        "worktree": str(wt),
        "branch": branch,
        "repo": repo,
        "skip_worktree_files": {
            k: v.decode("latin-1") if v is not None else None for k, v in skip_worktree_files.items()
        },
        "stashed": stashed,
        "meta_path": meta_path,
        "meta": meta,
    }
    REBASE_STATE.write_text(json.dumps(state, indent=2) + "\n")


def _load_rebase_state() -> dict[str, Any] | None:
    """Load rebase state from disk, or None if no state file exists."""
    if not REBASE_STATE.is_file():
        return None
    try:
        result: dict[str, Any] = json.loads(REBASE_STATE.read_text())
        return result
    except (json.JSONDecodeError, OSError):
        return None


def _restore_skip_worktree(wt: Path, skip_worktree_files: dict[str, bytes | None]) -> None:
    """Restore skip-worktree files from saved state."""
    for f, contents in skip_worktree_files.items():
        p = wt / f
        if contents is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(contents)
        git(wt, "update-index", "--skip-worktree", f)


def _conflict_file_list(wt: Path) -> list[str]:
    """Return list of files with unresolved conflicts."""
    status = git(wt, "status", "--porcelain")
    conflicts = []
    for line in status.splitlines():
        xy = line[:2]
        if "U" in xy or xy in {"AA", "DD"}:
            filepath = line[3:].split(" -> ")[-1]
            conflicts.append(filepath)
    return conflicts


def _cmd_rebase_continue(args: argparse.Namespace) -> None:
    """Resume a paused rebase after conflict resolution."""
    state = _load_rebase_state()
    if not state:
        die("no rebase in progress -- nothing to continue")

    wt = Path(state["worktree"])

    ref = base_ref(state["repo"], state["branch"])

    # If current conflict is a remigrate, auto-resolve before staging/continuing.
    if _is_rebase_in_progress(wt):
        resolved = _auto_resolve_remigrate(wt)
        if resolved:
            rc = 0
        else:
            git_passthrough(wt, "add", "-u")
            rc = git_passthrough(wt, "rebase", "--continue")
    else:
        git_passthrough(wt, "add", "-u")
        rc = git_passthrough(wt, "rebase", "--continue")

    # Auto-skip duplicates and remigrate conflicts after continue.
    if rc != 0 and _is_rebase_in_progress(wt) and (_auto_skip_duplicates(wt, ref) or _auto_resolve_remigrate(wt)):
        rc = 0

    if rc != 0 and _is_rebase_in_progress(wt):
        conflicts = _conflict_file_list(wt)
        n = len(conflicts)
        lines = [f"Rebase paused: conflicts in {n} file(s):"]
        lines.extend(f"  {f}" for f in conflicts)
        lines.append(f"\nResolve in: {wt}")
        lines.append("Then run: v git rebase continue")
        lines.append("Or abort: v git rebase abort")
        die("\n".join(lines))

    # Guard: rebase exited non-zero but no conflict in progress -- unexpected failure
    if rc != 0:
        die("Rebase failed unexpectedly. Check git status and resolve manually.")

    # Success: restore skip-worktree, pop stash, clean up.
    # Decode saved skip-worktree data back to bytes.
    saved = {k: v.encode("latin-1") if v is not None else None for k, v in state["skip_worktree_files"].items()}
    _restore_skip_worktree(wt, saved)

    if state["stashed"]:
        git(wt, "stash", "pop", "--quiet", check=False)

    REBASE_STATE.unlink(missing_ok=True)
    print("Rebase completed.")


def _cmd_rebase_abort(args: argparse.Namespace) -> None:
    """Abort a paused rebase and restore original state."""
    state = _load_rebase_state()
    if not state:
        die("no rebase in progress -- nothing to abort")

    wt = Path(state["worktree"])

    git_passthrough(wt, "rebase", "--abort")

    # Restore skip-worktree files
    saved = {k: v.encode("latin-1") if v is not None else None for k, v in state["skip_worktree_files"].items()}
    _restore_skip_worktree(wt, saved)

    if state["stashed"]:
        git(wt, "stash", "pop", "--quiet", check=False)

    REBASE_STATE.unlink(missing_ok=True)
    print("Rebase aborted.")


def cmd_rebase(args: argparse.Namespace) -> None:
    """Rebase a branch onto latest production, auto-remigrating stale migrations.

    Fetches production, rebases branch. Refuses locked worktrees
    (merged to production). Stale migrations (timestamps older than
    production's latest) are auto-renamed with fresh timestamps and
    committed before rebasing. --no-remigrate blocks.

    Subcommands:
      continue  Resume after resolving conflicts
      abort     Cancel rebase and restore original state
    """
    # Dispatch subcommands.
    if getattr(args, "subcommand", None) == "continue":
        return _cmd_rebase_continue(args)
    if getattr(args, "subcommand", None) == "abort":
        return _cmd_rebase_abort(args)

    # Guard: refuse a new rebase if one is already paused.
    if REBASE_STATE.is_file():
        die("a rebase is already in progress\n  resume: v git rebase continue\n  cancel: v git rebase abort")

    from codehome.commands.branch import _sync

    ctx = resolve(getattr(args, "branch", None))
    from codehome.git import require_unlocked

    require_unlocked(ctx.repo, ctx.branch)
    print(f"[{ctx.branch}]")

    _sync(ctx.repo)

    # Load branch.json metadata.
    meta_path = branch_dir(ctx.repo, ctx.branch) / "branch.json"
    meta: dict[str, Any] | None = None
    try:
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        pass

    stale = detect_stale_migrations(ctx.worktree, ctx.repo)

    if args.dry_run:
        if stale:
            print(f"would remigrate {len(stale)} stale migration(s):")
            for f in stale:
                print(f"  {f}")
        behind = commits_behind(ctx.branch, ctx.repo)
        if behind:
            print(f"would rebase {ctx.branch} ({behind} commits behind)")
        else:
            print(f"{ctx.branch} is already up to date")
        return None

    if stale:
        if args.no_remigrate:
            # Old behavior: block and tell the user.
            lines = [
                f"{len(stale)} stale migration(s) -- timestamps <= production's latest:",
            ]
            lines.extend(f"  {f}" for f in stale)
            lines.append("")
            lines.append("Rebasing will leave these in the middle of production's history,")
            lines.append("causing ordering issues when Supabase applies migrations.")
            lines.append("  rerun without --no-remigrate to auto-fix")
            die("\n".join(lines))
        else:
            _remigrate(ctx.worktree, ctx.repo)

    # Handle dirty files: auto-stash ignorable ones, block on real changes.
    wt = ctx.worktree
    dirty_output = git(wt, "status", "--porcelain")
    stashed_ignorable = False
    if dirty_output:
        import fnmatch

        patterns = ignorable_patterns()
        dirty_files = []
        for line in dirty_output.splitlines():
            filepath = line[3:].split(" -> ")[0]
            dirty_files.append(filepath)
        non_ignorable = [f for f in dirty_files if not any(fnmatch.fnmatch(f, p) for p in patterns)]
        if non_ignorable:
            lines = ["uncommitted changes block rebase -- commit these files first:"]
            for f in non_ignorable:
                lines.append(f"  {f}")
            die("\n".join(lines))
        # All dirty files are ignorable -- auto-stash them around the rebase.
        git(wt, "stash", "--quiet")
        stashed_ignorable = True

    sw_files = _detect_skip_worktree(wt)
    saved: dict[str, bytes | None] = {}
    for f in sw_files:
        p = wt / f
        saved[f] = p.read_bytes() if p.exists() else None
        git(wt, "update-index", "--no-skip-worktree", f)
        git(wt, "checkout", "--", f, check=False)

    ref = base_ref(ctx.repo, ctx.branch)

    # Skip commits already present on the target (avoids self-conflicts
    # when production commits exist on both sides with different SHAs).
    no_reapply = "--no-reapply-cherry-picks"

    rc = git_passthrough(wt, "rebase", no_reapply, ref)

    # Try auto-skipping duplicate commits (subject already on target),
    # which also handles remigrate conflicts internally.
    if rc != 0 and _is_rebase_in_progress(wt) and (_auto_skip_duplicates(wt, ref) or _auto_resolve_remigrate(wt)):
        rc = 0

    if rc != 0 and _is_rebase_in_progress(wt):
        _save_rebase_state(
            wt,
            ctx.branch,
            ctx.repo,
            saved,
            stashed_ignorable,
            str(meta_path),
            meta,
        )
        conflicts = _conflict_file_list(wt)
        n = len(conflicts)
        lines = [f"Rebase paused: conflicts in {n} file(s):"]
        for f in conflicts:
            lines.append(f"  {f}")
        lines.append(f"\nResolve in: {wt}")
        lines.append("Then run: v git rebase continue")
        lines.append("Or abort: v git rebase abort")
        die("\n".join(lines))

    # Success: restore skip-worktree and pop stash.
    _restore_skip_worktree(wt, saved)
    if stashed_ignorable:
        git(wt, "stash", "pop", "--quiet", check=False)
    return None


def cmd_push(args: argparse.Namespace) -> int | None:
    """Push branch to remote with --force-with-lease.

    Resolves branch from session or -B flag, pushes with --force-with-lease
    and auto-sets upstream (-u). Emits a branch.push event and records
    the push timestamp in push.json.
    """
    from datetime import datetime

    from codehome.bus import Event, fire_sync
    from codehome.git import gh_repo as _gh_repo
    from codehome.session import get_process_id
    from codehome.utils import atomic_json_write, load_json, open_url

    ctx = resolve(getattr(args, "branch", None))
    from codehome.git import require_unlocked

    require_unlocked(ctx.repo, ctx.branch)

    from codehome.deploy_shared import native_change_gate

    rc = native_change_gate(ctx, args)
    if rc is not None:
        return rc

    if args.dry_run:
        print(f"(dry-run) would push {ctx.qualified} to origin with --force-with-lease")
        return None

    print(f"[{ctx.qualified}]")
    rc = git_passthrough(ctx.worktree, "push", "--force-with-lease", "-u", "origin", ctx.branch)
    if rc != 0:
        die("push failed")

    # Record last push timestamp.
    now = datetime.now(UTC).isoformat()
    push_file = ctx.branch_dir / "push.json"
    data = load_json(push_file, default={})
    data["last_push"] = now
    data["push_count"] = data.get("push_count", 0) + 1
    atomic_json_write(push_file, data)

    # Auto-commit push metadata to the context repo.
    git(ctx.branch_dir, "add", "push.json")
    git(ctx.branch_dir, "commit", "-m", "Update push metadata", "--", "push.json", check=False)

    # Open branch on GitHub.
    url = f"https://github.com/{_gh_repo(ctx.repo)}/tree/{ctx.branch}"
    open_url(url)

    # Emit event.
    session_id = get_process_id()
    fire_sync(
        Event(
            name="branch.push",
            payload={"session": session_id, "repo": ctx.repo, "branch": ctx.branch, "data": {"force_with_lease": True}},
            audit=True,
        )
    )
    return None
