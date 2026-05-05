"""Standardized file locking for plugins.

Replaces ad-hoc locking mechanisms in the codebase with one
consistent approach: fcntl flock on a sidecar .lock file.
"""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from supervisor.state.scopes import Scope, resolve_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def lock(
    plugin: str,
    name: str,
    scope: Scope,
    *,
    repo: str | None = None,
    branch: str | None = None,
    timeout: float | None = None,
) -> Iterator[Path]:
    """Acquire an exclusive file lock.

    Args:
        plugin: Plugin name (determines storage directory).
        name: Lock name (becomes ``<name>.lock`` on disk).
        scope: Where the lock file lives (GLOBAL, REPO, BRANCH, SECRET).
        repo: Required for REPO / BRANCH scopes.
        branch: Required for BRANCH scope.
        timeout: If set, raise ``TimeoutError`` after this many seconds
            instead of blocking indefinitely.

    Yields:
        The ``Path`` of the lock file (rarely needed by callers).

    Example::

        with lock("publisher", "deploy", Scope.GLOBAL):
            # exclusive section
            ...

    """
    dir_path = resolve_path(plugin, scope, repo=repo, branch=branch)
    dir_path.mkdir(parents=True, exist_ok=True)
    lock_path = dir_path / f"{name}.lock"

    fd = lock_path.open("w")
    try:
        if timeout is not None:
            # Poll with non-blocking attempts until deadline.
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Lock {plugin}/{name} not acquired within {timeout}s") from None
                    time.sleep(0.1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX)

        yield lock_path
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
