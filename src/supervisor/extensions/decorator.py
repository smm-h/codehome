"""Extension decorator: mark async functions as extension checks.

Unlike :func:`~supervisor.checks.registry.register_check`, the
``@extension`` decorator does NOT register the function into any global
registry.  It only attaches metadata as ``_extension_meta`` on the
function object.  Actual registration into the CheckRegistry happens
later via the loader, after discovery and state merging.

Usage::

    @extension(
        "lint-cycles",
        group="gate",
        timeout=30,
        cwd="bag.veliu.com/",
        description="Import cycle detection for bag frontend",
    )
    async def lint_cycles(ctx: CheckContext) -> CheckResult:
        ...
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from supervisor.checks.result import CheckResult


def extension(
    name: str,
    *,
    group: str,
    timeout: int,
    cwd: str = ".",
    depends_on: tuple[str, ...] | list[str] = (),
    advisory: bool = False,
    description: str = "",
) -> Callable[
    [Callable[..., Awaitable[CheckResult]]],
    Callable[..., Awaitable[CheckResult]],
]:
    """Decorate an async function as an extension check.

    Stores metadata as ``fn._extension_meta`` but does NOT register
    the function anywhere.  The discovery + loader pipeline handles
    registration at runtime.

    Raises ``TypeError`` if the decorated function is not async.
    """
    deps = tuple(depends_on)

    def decorator(
        fn: Callable[..., Awaitable[CheckResult]],
    ) -> Callable[..., Awaitable[CheckResult]]:
        if not inspect.iscoroutinefunction(fn):
            msg = (
                f"extension {name!r}: @extension requires an async function "
                f"(got {fn.__qualname__}, defined in {fn.__module__})"
            )
            raise TypeError(msg)

        # Attach metadata dict to the function for later discovery.
        fn._extension_meta = {  # type: ignore[attr-defined]
            "name": name,
            "group": group,
            "timeout": timeout,
            "cwd": cwd,
            "depends_on": deps,
            "advisory": advisory,
            "description": description,
        }
        return fn

    return decorator
