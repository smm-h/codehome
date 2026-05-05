"""``v check`` -- unified check runner CLI command.

Three modes:

1. ``v check`` (no args) -- list registered groups and their checks.
2. ``v check <group>`` -- run all checks in a group, print report, exit 0/1.
3. ``v check install-hooks`` -- write managed git hooks for pre-commit/pre-push.

This module only wires the CLI surface; actual check definitions are
registered via ``@register_check`` in Phase 3/4 modules.
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codehome.resolution import BranchContext

# Import check modules to trigger @register_check side effects.
import codehome.checks.gate
import codehome.checks.precommit  # noqa: F401
from codehome.checks import (
    CheckContext,
    _registry,
    format_report,
    run_group,
)
from codehome.paths import ROOT, branch_dir as _branch_dir
from codehome.utils import die

if TYPE_CHECKING:
    import argparse

# Hook scripts written by ``v check install-hooks``.
# The sentinel comment allows us to detect whether the hook was
# installed by us (safe to overwrite) or by the user (needs confirmation).
_HOOK_SENTINEL = "Managed by v check install-hooks. Do not edit."

_PRE_COMMIT_HOOK = f"""\
#!/bin/sh
# {_HOOK_SENTINEL}
exec codehome check precommit
"""

_PRE_PUSH_HOOK = f"""\
#!/bin/sh
# {_HOOK_SENTINEL}
exec codehome check gate
"""


def _list_groups() -> None:
    """Print all registered groups and their check names."""
    groups = _registry.groups()
    if not groups:
        print("No check groups registered.")
        print("  (check modules have not been loaded yet)")
        return

    print("Available groups:\n")
    for group_name in groups:
        entries = _registry.group(group_name)
        names = ", ".join(e.name for e in entries)
        print(f"  {group_name} ({len(entries)} checks)")
        print(f"    {names}")
        print()


def _load_extensions_if_available() -> BranchContext | None:
    """Load repo/branch extensions into the check registry if a branch is active.

    Returns the resolved branch context (with .repo and .branch) if a
    branch is selected, or None otherwise.  The caller uses this to
    populate CheckContext with repo/branch info for extension checks.

    Silently skips if no branch is selected (VB unset).  Prints a
    one-line hint if the repo has never been scanned for extensions.
    """
    from codehome.cli_helpers import resolve_optional
    from codehome.extensions.loader import load_extensions
    from codehome.extensions.state import load_state

    # Build a minimal namespace -- resolve_optional reads args.branch.
    ns = type("_Ns", (), {"branch": None})()
    ctx = resolve_optional(ns)
    if ctx is None:
        return None

    # Skip silently if the repo has never been scanned for extensions.
    state = load_state(ctx.repo, ROOT)
    if state.get("last_scanned") is None:
        return ctx

    load_extensions(ctx.repo, ctx.branch, ROOT, _registry)
    return ctx


def _run_group(group: str) -> None:
    """Run all checks in *group*, print formatted report, exit 0 or 1."""
    # Load extensions before validating the group name so extension-only
    # groups are recognised.  Also capture the resolved branch context
    # so we can populate CheckContext for extension checks that need it.
    branch_ctx = _load_extensions_if_available()

    known = _registry.groups()
    if group not in known:
        avail = ", ".join(known) if known else "(none registered)"
        die(f"unknown group {group!r}, available: {avail}")

    # Build context: project root, staged files for precommit,
    # and repo/branch info when a branch is active.
    staged: tuple[str, ...] | None = None
    if group == "precommit":
        staged = _get_staged_files()

    repo = branch_ctx.repo if branch_ctx else None
    branch = branch_ctx.branch if branch_ctx else None
    branch_dir_path = _branch_dir(repo, branch) if repo and branch else None
    ctx = CheckContext(
        root=ROOT,
        staged_files=staged,
        repo=repo,
        branch=branch,
        branch_dir=branch_dir_path,
    )

    report = asyncio.run(run_group(group, ctx=ctx))
    output = format_report(report)
    print(output)

    sys.exit(0 if report.ok else 1)


def _get_staged_files() -> tuple[str, ...]:
    """Return list of staged file paths via git diff --cached."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=d"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return ()
        return tuple(line for line in result.stdout.splitlines() if line.strip())
    except FileNotFoundError:
        return ()


def _install_hooks(*, force: bool = False) -> None:
    """Write pre-commit and pre-push hooks into the repo's .git/hooks/.

    When *force* is True, overwrite existing hooks without prompting.
    When stdin is not a TTY, non-managed hooks are skipped (no prompt).
    """
    # Discover the .git directory.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (ROOT / git_dir).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        die("could not find .git directory")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hooks = [
        ("pre-commit", _PRE_COMMIT_HOOK),
        ("pre-push", _PRE_PUSH_HOOK),
    ]

    for hook_name, hook_content in hooks:
        hook_path = hooks_dir / hook_name
        if hook_path.exists():
            existing = hook_path.read_text()
            if _HOOK_SENTINEL not in existing and not force:
                # Existing hook not managed by us -- prompt or skip.
                if not sys.stdin.isatty():
                    print(
                        f"  Skipped {hook_name} (existing non-managed hook; use --force to overwrite).",
                    )
                    continue
                print(
                    f"warning: {hook_path} exists and is NOT managed by v.",
                    file=sys.stderr,
                )
                print(
                    f"  Overwriting will replace the existing {hook_name} hook.",
                    file=sys.stderr,
                )
                answer = input(f"  Overwrite {hook_name}? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    print(f"  Skipped {hook_name}.")
                    continue

        hook_path.write_text(hook_content)
        # chmod +x
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  Installed {hook_path}")

    print("Done. Hooks will run: v check precommit (pre-commit), v check gate (pre-push).")


def cmd_check(args: argparse.Namespace) -> None:
    """Run a check group or list available groups.

    ``v check``               -- list all registered groups and checks
    ``v check <group>``       -- run all checks in the named group
    ``v check install-hooks`` -- install managed git hooks (pre-commit, pre-push)

    Exit codes: 0 = all checks passed (or listing mode), 1 = any check failed.
    """
    # Git pre-push hooks inherit non-blocking stdout pipes; large reports
    # then fail with BlockingIOError mid-print. Force blocking writes.
    try:
        os.set_blocking(sys.stdout.fileno(), True)
    except (OSError, ValueError):
        pass

    group: str | None = getattr(args, "check_group", None)

    if group is None:
        _list_groups()
        return

    # Dispatch "install-hooks" BEFORE group validation so it can never be
    # shadowed by a real check group with the same name.  The --force flag
    # is only meaningful here (ignored when running a check group).
    if group == "install-hooks":
        _install_hooks(force=getattr(args, "force", False))
        return

    if getattr(args, "force", False):
        die("--force is only valid with 'v check install-hooks'")

    _run_group(group)
