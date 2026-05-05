"""Shared state persistence helpers for JSON-backed state files.

Both plugins and extensions use the same pattern: a JSON file with a
collection key (e.g. ``"plugins"`` or ``"extensions"``) mapping names
to metadata dicts, plus a ``"last_scanned"`` timestamp.  This module
provides the common load/save logic so each subsystem only needs to
implement its own ``merge_discovered()`` (which differs by input type
and serialization format).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def load_state(path: Path, collection_key: str) -> dict[str, Any]:
    """Read a JSON state file, returning a safe default on error.

    Returns ``{collection_key: {}, "last_scanned": None}`` when the file
    is missing, empty, or contains malformed JSON.
    """
    empty: dict[str, Any] = {collection_key: {}, "last_scanned": None}
    if not path.exists():
        return empty
    try:
        data: dict[str, Any] = json.loads(path.read_text())
        # Ensure expected top-level key exists.
        if collection_key not in data:
            data[collection_key] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return empty


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write *state* as JSON to *path*, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
