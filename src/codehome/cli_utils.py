"""Generic argparse utilities for CLI dispatch.

These helpers have zero domain dependencies -- they only use codehome.utils
for error formatting.  Plugin-specific helpers (branch flags, deploy flags,
resolution) stay in their respective plugins.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable


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
        from codehome.utils import die

        available = ", ".join(handlers.keys())
        die(f"subcommand required: {available}")

    handler = handlers.get(sub)
    if handler is None:
        from codehome.utils import die

        die(f"unknown subcommand: {sub}")
    handler(args)
