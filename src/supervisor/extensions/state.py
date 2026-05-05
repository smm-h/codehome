"""Extension state persistence: track enabled/disabled status per repo.

State is stored in ``repos/{repo}/.extensions-state.json`` and records
which extensions are known, their metadata, and whether each is enabled
or disabled.  The state file is the authority for enable/disable toggles;
the on-disk extension files are the authority for what extensions exist.

``merge_discovered()`` reconciles the two: new extensions default to
enabled, removed extensions are pruned, existing extensions preserve
their enabled/disabled toggle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from supervisor.shared.state_store import load_state as _load
from supervisor.shared.state_store import save_state as _save

if TYPE_CHECKING:
    from pathlib import Path

    from supervisor.extensions.types import ExtensionEntry


def _state_path(repo: str, root: Path) -> Path:
    """Return the path to the state file for a repo.

    Uses repo_dir() to support both legacy (repos/) and new (~/.superv/projects/) layouts.
    The *root* parameter is kept for API compatibility but is no longer used for path derivation.
    """
    from supervisor.paths import repo_dir

    return repo_dir(repo) / ".extensions-state.json"


def load_state(repo: str, root: Path) -> dict[str, Any]:
    """Read the extensions state file for *repo*.

    Returns an empty structure if the file doesn't exist or is malformed.
    """
    return _load(_state_path(repo, root), "extensions")


def save_state(repo: str, root: Path, state: dict[str, Any]) -> None:
    """Write the extensions state file for *repo*."""
    _save(_state_path(repo, root), state)


def merge_discovered(
    old_state: dict[str, Any],
    discovered: list[ExtensionEntry],
) -> dict[str, Any]:
    """Reconcile persisted state with freshly discovered extensions.

    Rules:
    - New extensions (on disk but not in state): default to enabled=True.
    - Existing extensions (on disk AND in state): preserve enabled toggle,
      update metadata from the discovered entry.
    - Removed extensions (in state but not on disk): pruned from state.

    Returns a new state dict (does not mutate *old_state*).
    """
    old_exts = old_state.get("extensions", {})
    new_exts: dict[str, dict[str, Any]] = {}

    for entry in discovered:
        old_entry = old_exts.get(entry.name)
        # Preserve enabled toggle from old state; default to True for new.
        enabled = old_entry.get("enabled", True) if old_entry is not None else True

        # Serialize all ExtensionEntry fields (except fn which is not
        # JSON-serializable and is re-populated at load time).
        new_exts[entry.name] = {
            "name": entry.name,
            "group": entry.group,
            "timeout": entry.timeout,
            "cwd": entry.cwd,
            "depends_on": list(entry.depends_on),
            "advisory": entry.advisory,
            "description": entry.description,
            "source": entry.source,
            "file": entry.file,
            "enabled": enabled,
        }

    return {
        "extensions": new_exts,
        "last_scanned": datetime.now(UTC).isoformat(),
    }
