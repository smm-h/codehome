"""Re-export stubs for commands that moved to plugins/core/commands/.

The ``_load_core_command`` helper searches config.toml plugin paths
(and the ROOT/plugins fallback) for the named module inside
``plugins/core/commands/``, so these stubs work whether the codehome
package lives alongside the plugins or in a separate repository.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

_cache: dict[str, ModuleType] = {}


def _load_core_command(module_name: str, filename: str) -> ModuleType:
    """Lazily load a core plugin command module by filename.

    Results are cached so repeated calls return the same module object.
    """
    if module_name in _cache:
        return _cache[module_name]

    from codehome.dynamic_import import import_module_from_path
    from codehome.plugins.discovery import _read_plugin_paths_from_config
    from codehome.paths import ROOT

    config_paths = _read_plugin_paths_from_config()
    search_dirs: list[Path] = config_paths if config_paths else [ROOT / "plugins"]

    for d in search_dirs:
        candidate = d / "core" / "commands" / filename
        if candidate.is_file():
            mod = import_module_from_path(module_name, candidate)
            _cache[module_name] = mod
            return mod

    msg = f"core plugin {filename} not found in any plugin directory"
    raise ImportError(msg)
