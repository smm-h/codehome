"""State API -- programmatic JSON state with atomic writes and Pydantic typing."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from codehome.state.scopes import Scope, resolve_path

if TYPE_CHECKING:
    from collections.abc import Iterator


class StateStore:
    """Programmatic JSON state with atomic read-modify-write.

    State files are written by code (not humans). Atomic writes via
    tmp-file + rename prevent partial reads. The ``atomic`` context
    manager adds file-level locking for read-modify-write cycles.
    """

    def __init__(self, plugin: str, scope: Scope, **kwargs: str | None) -> None:
        self._dir = resolve_path(plugin, scope, **kwargs)

    @property
    def dir(self) -> Path:
        return self._dir

    def read(self, name: str = "state") -> dict[str, Any] | None:
        """Read state. Returns None if the file does not exist."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return result

    def read_typed(self, name: str, model: type[BaseModel]) -> BaseModel | None:
        """Read state into a Pydantic model. Returns None if not found."""
        data = self.read(name)
        if data is None:
            return None
        return model.model_validate(data)

    def write(self, name: str, data: dict[str, Any] | BaseModel) -> None:
        """Atomic write (tmp + rename). Creates directory if needed."""
        if isinstance(data, BaseModel):
            data = data.model_dump()
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{name}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    @contextmanager
    def atomic(self, name: str = "state") -> Iterator[dict[str, Any]]:
        """Context manager for atomic read-modify-write with file locking.

        Usage::

            with store.atomic("counters") as data:
                data["hits"] = data.get("hits", 0) + 1
            # data is written back atomically on exit
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{name}.json"
        lock_path = path.with_suffix(".lock")

        with lock_path.open("w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                yield current
                # Caller modifies `current` in place; write it back.
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
                tmp.replace(path)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
