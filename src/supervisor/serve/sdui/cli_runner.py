"""Run streaming command handlers in CLI context.

Consumes the async generator, printing progress to stderr and
the final result/error to stdout. Same handler serves both
CLI and dashboard.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

from supervisor.serve.sdui.commands import CommandError, CommandProgress, CommandResult


async def _run_async(handler: Callable[[dict[str, Any]], AsyncIterator[CommandProgress | CommandResult | CommandError]], args: dict[str, Any]) -> int:
    """Run a streaming command handler and print output.

    Returns 0 on success (CommandResult), 1 on error (CommandError or exception).
    """
    try:
        async for msg in handler(args):
            if isinstance(msg, CommandProgress):
                print(f"  {msg.message}", file=sys.stderr)
            elif isinstance(msg, CommandResult):
                if msg.toast:
                    print(msg.toast)
                return 0
            elif isinstance(msg, CommandError):
                print(f"Error: {msg.message}", file=sys.stderr)
                if msg.detail:
                    print(f"  {msg.detail}", file=sys.stderr)
                return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    # Generator exhausted without yielding a terminal message
    return 0


def run_command(handler: Callable[[dict[str, Any]], AsyncIterator[CommandProgress | CommandResult | CommandError]], args: dict[str, Any]) -> int:
    """Run a streaming command handler synchronously. Returns exit code.

    Bridges async generator handlers into synchronous CLI entry points,
    so plugins can reuse the same handler for both dashboard SSE and CLI.
    """
    return asyncio.run(_run_async(handler, args))
