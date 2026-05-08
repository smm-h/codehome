"""Plugin registry: name -> LoadedPlugin singleton map.

Thin module-level registry that holds all loaded plugins and their
contributions.  Mirrors the singleton pattern from
:mod:`codehome.checks.registry` but stores plugin records rather
than individual check entries.

A plugin is considered *enabled* if it is present in the registry
(loading implies enabling).  Passthrough tracking is derived from
each plugin's manifest at registration time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codehome.plugins.manifest import PluginManifest

_log = logging.getLogger(__name__)

# -- LoadedPlugin ------------------------------------------------------------


@dataclass(frozen=True)
class LoadedPlugin:
    """Immutable record of a fully loaded plugin.

    Attributes:
        name: Unique plugin identifier (matches directory name).
        version: SemVer string from the plugin manifest.
        description: Human-readable summary of what the plugin does.
        plugin_dir: Absolute path to the plugin's directory on disk.
        manifest: The parsed PluginManifest instance.
        cli_registrar: The ``register_cli`` function from the plugin's
            ``handlers.py``, or ``None`` if the plugin has no CLI.
        router: A FastAPI ``APIRouter`` from the plugin's ``routes.py``,
            or ``None`` if the plugin exposes no HTTP endpoints.

    """

    name: str
    version: str
    description: str
    plugin_dir: str
    manifest: PluginManifest
    cli_registrar: Any = None
    router: Any = None
    public_router: Any = None


# -- Module-level state (private) -------------------------------------------

_plugins: dict[str, LoadedPlugin] = {}
_passthrough: set[str] = set()


# -- Public API --------------------------------------------------------------


def register(plugin: LoadedPlugin) -> None:
    """Store a loaded plugin.  Overwrites any previous entry with the same name."""
    if plugin.name in _plugins:
        _log.warning("plugin %r re-registered (overwriting previous entry)", plugin.name)
    _plugins[plugin.name] = plugin
    # Track plugins whose manifest declares passthrough CLI arg support.
    if getattr(plugin.manifest, "passthrough", False):
        _passthrough.add(plugin.name)


def get(name: str) -> LoadedPlugin | None:
    """Return the plugin for *name*, or ``None`` if not registered."""
    return _plugins.get(name)


def list_plugins() -> list[LoadedPlugin]:
    """Return all registered plugins, sorted by name."""
    return sorted(_plugins.values(), key=lambda p: p.name)


def is_enabled(name: str) -> bool:
    """Return ``True`` if *name* is registered (loaded = enabled)."""
    return name in _plugins


def passthrough_commands() -> frozenset[str]:
    """Return names of plugins that accept passthrough CLI args."""
    return frozenset(_passthrough)


def clear() -> None:
    """Reset all state.  Useful for test isolation."""
    _plugins.clear()
    _passthrough.clear()
