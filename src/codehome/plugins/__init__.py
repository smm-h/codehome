"""Plugin framework: modular, manifest-driven extensions for the v CLI.

Plugins are self-contained packages that declare commands, checks, and
dashboard integrations via a ``plugin.toml`` manifest.  They integrate
with the existing CLI and check systems but are managed independently.

Pipeline: discover -> merge state -> load -> register.
"""

from __future__ import annotations

from codehome.plugins.loader import load_all_plugins
from codehome.plugins.manifest import PluginManifest
from codehome.plugins.registry import (
    LoadedPlugin,
    get,
    is_enabled,
    list_plugins,
)

__all__ = [
    "LoadedPlugin",
    "PluginManifest",
    "get",
    "is_enabled",
    "list_plugins",
    "load_all_plugins",
]
