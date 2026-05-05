"""DRY helpers for CLI flag construction, branch resolution, and dispatch.

Flag factories eliminate repeated add_argument() calls across commands.
Resolution helpers wrap the common resolve-from-args pattern.
Push guards centralise the pre-push safety checks (locked worktree, design markers).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from supervisor.resolution import BranchContext


# ---------------------------------------------------------------------------
# Flag factories
# ---------------------------------------------------------------------------


def add_branch_flag(
    parser: argparse.ArgumentParser,
    *,
    visible: bool = True,
    required: bool = False,
) -> None:
    """Add -B/--branch flag to *parser*.

    visible=True  -> shows in help with 'branch name (default: selected)'
    visible=False -> suppressed from help (for commands where branch is implicit)
    required=True -> makes the flag mandatory (e.g. ``v git history``)
    """
    kwargs: dict[str, Any] = {"metavar": "BRANCH"}
    if visible:
        kwargs["help"] = "branch name (default: selected)"
    else:
        kwargs["help"] = argparse.SUPPRESS
    if required:
        kwargs["required"] = True
    parser.add_argument("-B", "--branch", **kwargs)


def add_dry_run_flag(parser: argparse.ArgumentParser) -> None:
    """Add -D/--dry-run flag (dest='dry_run')."""
    parser.add_argument(
        "-D",
        "--dry-run",
        action="store_true",
        help="show what would happen",
    )


def add_deploy_flags(parser: argparse.ArgumentParser) -> None:
    """Add the common flags shared by stage/prod/demo deploy commands.

    Adds: -D/--dry-run, --no-advance, --skip-native-check, -B/--branch (suppressed).
    """
    add_dry_run_flag(parser)
    parser.add_argument("--no-advance", action="store_true", help="skip Linear auto-advance")
    parser.add_argument(
        "--skip-native-check",
        action="store_true",
        help="push even if native changes lack a binary build",
    )
    add_branch_flag(parser, visible=False)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def resolve_from_args(args: argparse.Namespace, **kwargs: Any) -> BranchContext:
    """Resolve branch from *args.branch* (fatal if no branch selected)."""
    from supervisor.resolution import resolve

    return resolve(getattr(args, "branch", None), **kwargs)


def resolve_optional(args: argparse.Namespace) -> BranchContext | None:
    """Resolve branch from *args.branch* (returns None if no branch selected)."""
    from supervisor.resolution import active_context, resolve

    explicit = getattr(args, "branch", None)
    if explicit:
        return resolve(explicit)
    return active_context()


# ---------------------------------------------------------------------------
# Pre-push guard
# ---------------------------------------------------------------------------


def push_guards(args: argparse.Namespace) -> BranchContext:
    """Run common guards for push/deploy commands.

    Resolve branch, check worktree is not locked, check no design markers.
    Return the resolved BranchContext.
    """
    ctx = resolve_from_args(args)

    from supervisor.git import require_unlocked

    require_unlocked(ctx.repo, ctx.branch)

    from supervisor.service_protocols import DesignHasMarkers
    from supervisor.state.service_registry import services

    has_markers = services.get_typed("design.has_markers", DesignHasMarkers)  # type: ignore[type-abstract]
    if has_markers and has_markers(ctx.worktree):
        from supervisor.utils import die

        die("V_DESIGN markers present -- run: v design stop")
    return ctx


# ---------------------------------------------------------------------------
# Dispatch helper for nested sub-commands
# ---------------------------------------------------------------------------


def dispatch_subcommand(
    args: argparse.Namespace,
    dest: str,
    handlers: dict[str, Callable[..., None]],
) -> None:
    """Dispatch to a subcommand handler.

    *dest*:     the subparser dest name (e.g. ``'branch_command'``)
    *handlers*: mapping of subcommand name -> handler callable
    """
    sub = getattr(args, dest, None)
    if sub is None:
        # No subcommand given -- list what's available and exit.
        from supervisor.utils import die

        available = ", ".join(handlers.keys())
        die(f"subcommand required: {available}")

    handler = handlers.get(sub)
    if handler is None:
        from supervisor.utils import die

        die(f"unknown subcommand: {sub}")
    handler(args)
