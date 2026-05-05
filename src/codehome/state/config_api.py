"""Config API -- read-only access to plugin TOML configuration files."""

import tomllib
from pathlib import Path
from typing import Any

from codehome.state.scopes import Scope, resolve_path


class ConfigStore:
    """Read-only access to plugin TOML configuration.

    Config files are human-edited; the runtime only reads them.
    """

    def __init__(self, plugin: str, scope: Scope, **kwargs: str | None) -> None:
        self._dir = resolve_path(plugin, scope, **kwargs)

    def read(self, name: str = "config") -> dict[str, Any]:
        """Read a TOML config file. Returns {} if not found."""
        path = self._dir / f"{name}.toml"
        if not path.exists():
            return {}
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def path(self, name: str = "config") -> Path:
        """Return the path to the config file (for $EDITOR)."""
        return self._dir / f"{name}.toml"
