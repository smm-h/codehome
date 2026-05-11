"""Plugin state persistence: track enabled/disabled status globally.

State is stored in ``.codehome/plugins-state.json`` (under the super/
project root) and records which plugins are known, their metadata, and
whether each is enabled or disabled.  The state file is the authority
for enable/disable toggles; the on-disk plugin directories are the
authority for what plugins exist.

``merge_discovered()`` reconciles the two: new plugins default to the
``enabled`` value from their manifest, removed plugins are pruned,
existing plugins preserve their enabled/disabled toggle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from codehome.shared.state_store import load_state as _load
from codehome.shared.state_store import save_state as _save

if TYPE_CHECKING:
    from pathlib import Path

    from codehome.plugins.manifest import PluginManifest


def _state_path(root: Path) -> Path:
    """Return the path to the global plugins state file."""
    return root / ".codehome" / "plugins-state.json"


def load_state(root: Path) -> dict[str, Any]:
    """Read the plugins state file.

    Returns an empty structure if the file doesn't exist or is malformed.
    """
    return _load(_state_path(root), "plugins")


def save_state(root: Path, state: dict[str, Any]) -> None:
    """Write the plugins state file."""
    _save(_state_path(root), state)


def merge_discovered(
    old_state: dict[str, Any],
    discovered: list[tuple[Path, PluginManifest]],
) -> dict[str, Any]:
    """Reconcile persisted state with freshly discovered plugins.

    Rules:
    - New plugins (on disk but not in state): default to
      ``enabled=manifest.enabled`` (from the TOML).
    - Existing plugins (on disk AND in state): preserve enabled toggle,
      update metadata from the discovered manifest.
    - Removed plugins (in state but not on disk): pruned from state.

    Returns a new state dict (does not mutate *old_state*).
    """
    old_plugins = old_state.get("plugins", {})
    new_plugins: dict[str, dict[str, Any]] = {}

    for plugin_dir, manifest in discovered:
        old_entry = old_plugins.get(manifest.name)
        # Preserve enabled toggle from old state; default to manifest
        # value for newly discovered plugins.
        enabled = old_entry.get("enabled", manifest.enabled) if old_entry is not None else manifest.enabled

        new_plugins[manifest.name] = {
            "name": manifest.name,
            "description": manifest.description,
            "enabled": enabled,
            "plugin_dir": str(plugin_dir),
        }

    return {
        "plugins": new_plugins,
        "last_scanned": datetime.now(UTC).isoformat(),
    }
