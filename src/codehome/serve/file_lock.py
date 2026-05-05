"""JSON file locking using fcntl advisory locks.

Provides:

1. ``json_file_lock`` -- async context manager for read-modify-write cycles.
2. ``write_json_locked`` -- synchronous one-shot write under an exclusive lock.

Both ensure the parent directory exists and use ``fcntl.LOCK_EX`` to
serialize concurrent access.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def _locked_read(path: Path) -> tuple[Any, Any]:
    """Open file, acquire exclusive lock, read JSON, return (fd, data).

    The file descriptor is kept open (and locked) so the caller can
    write back and release later.  Returns an empty dict if the file
    doesn't exist or contains invalid JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Open for read+write (create if missing).
    fd = path.open("a+")
    fd.seek(0)
    fcntl.flock(fd, fcntl.LOCK_EX)

    content = fd.read()
    if not content.strip():
        data: Any = {}
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {}
    return fd, data


def _write_and_unlock(fd: Any, data: Any) -> None:
    """Write data back to the locked file and release the lock."""
    fd.seek(0)
    fd.truncate()
    fd.write(json.dumps(data, indent=2) + "\n")
    fd.flush()
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()


def _unlock_only(fd: Any) -> None:
    """Release the lock without writing (used on error)."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except Exception:
        pass


def write_json_locked(path: str | Path, data: Any) -> None:
    """Atomically write *data* as JSON to *path* under an exclusive lock.

    Use this when the caller manages its own in-memory state and only
    needs a locked write (as opposed to a full read-modify-write cycle).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open "a+" to create if missing without truncating existing content.
    # Truncate only after the lock is held -- prevents data loss if another
    # process holds the lock or this process crashes between open and write.
    with path.open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data, indent=2) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@asynccontextmanager
async def json_file_lock(path: str | Path) -> AsyncGenerator[Any, None]:  # noqa: dead-code
    """Async context manager for atomic JSON file read-modify-write.

    Usage::

        async with json_file_lock("path/to/data.json") as data:
            data["key"] = "value"
            # data is written back on exit

    The lock is held for the duration of the ``async with`` block.
    If an exception occurs, the original file contents are preserved.
    """
    path = Path(path)
    fd, data = await asyncio.to_thread(_locked_read, path)
    try:
        yield data
        # Write back the (possibly modified) data.
        await asyncio.to_thread(_write_and_unlock, fd, data)
    except BaseException:
        # On error, release lock without writing.
        await asyncio.to_thread(_unlock_only, fd)
        raise
