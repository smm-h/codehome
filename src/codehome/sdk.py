"""Plugin SDK -- the stable API surface for plugins.

Plugins should import ONLY from this module. Direct imports from
codehome.* internals are deprecated and will be enforced by lint
in Phase 4.

Provides: paths (core), formatting, events (bus), credentials,
state (Config/State/Files), services (RPC), locking, CLI helpers,
subprocesses, notifications, background tasks, SDUI commands,
and FastAPI auth dependencies.

All re-exports are lazy: modules are only imported when the symbol is
first accessed, via module-level __getattr__ with globals() caching.
"""

from __future__ import annotations

import importlib
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class PluginNotInstalledError(ImportError):
    """Raised when an SDK symbol requires a plugin that isn't installed."""


# Core codehome packages (not provided by plugins).
_CORE_PACKAGES = frozenset({
    "bus", "checks", "cli", "cli_utils", "commands", "config", "conductor",
    "credentials", "dispatch", "dynamic_import", "features", "http_client",
    "mcp", "paths", "plugins", "pty", "serve", "service_protocols",
    "session", "shared", "state", "subprocesses", "utils",
})


def _plugin_name_from_module(module_path: str) -> str | None:
    """Return the plugin name if *module_path* is plugin-provided, else None.

    Plugin modules live under ``codehome.<plugin>.*`` where ``<plugin>``
    is NOT one of the core packages.
    """
    parts = module_path.split(".")
    if len(parts) >= 2 and parts[0] == "codehome" and parts[1] not in _CORE_PACKAGES:
        return parts[1]
    return None


# Mapping: symbol_name -> (module_path, attribute_name)
# When attr_name is None, the module itself is returned (module re-export).
# When attr_name differs from symbol_name, it acts as a rename
# (e.g. list_repos -> load_repos).
_LAZY_IMPORTS: dict[str, tuple[str, str | None]] = {
    # -- Bus (event system) --
    "Event": ("codehome.bus", "Event"),
    "fire": ("codehome.bus", "fire"),
    "fire_sync": ("codehome.bus", "fire_sync"),
    "on": ("codehome.bus", "on"),
    # -- CLI construction & helpers --
    "build_parser": ("codehome.cli", "build_parser"),
    "dispatch_subcommand": ("codehome.cli_utils", "dispatch_subcommand"),
    # -- Credentials (token store) --
    "delete_token": ("codehome.credentials", "delete_token"),
    "get_token": ("codehome.credentials", "get_token"),
    "has_token": ("codehome.credentials", "has_token"),
    "list_connections": ("codehome.credentials", "list_connections"),
    "store_token": ("codehome.credentials", "store_token"),
    # -- Config --
    "load_server_config": ("codehome.config", "load_server_config"),
    # -- Dispatch --
    "dispatched": ("codehome.dispatch", "dispatched"),
    "server_running": ("codehome.dispatch", "server_running"),
    "server_url": ("codehome.dispatch", "server_url"),
    # -- HTTP client --
    "TOKEN_FILE": ("codehome.http_client", "TOKEN_FILE"),
    # -- Paths (core) --
    "PROTECTED_BRANCHES": ("codehome.paths", "PROTECTED_BRANCHES"),
    "ROOT": ("codehome.paths", "ROOT"),
    "STAGING_MERGE_STATE": ("codehome.paths", "STAGING_MERGE_STATE"),
    "SUPERVISOR_DIR": ("codehome.paths", "SUPERVISOR_DIR"),
    "resolve_global": ("codehome.paths", "resolve_global"),
    "codehome_home": ("codehome.paths", "codehome_home"),
    # -- Background tasks (for plugin-contributed recurring tasks) --
    "BackgroundTask": ("codehome.serve.background", "BackgroundTask"),
    "BackgroundTaskRegistry": ("codehome.serve.background", "BackgroundTaskRegistry"),
    "background_tasks": ("codehome.serve.background", "background_tasks"),
    # -- Notifications (channel protocol, registry, shared vocabulary) --
    "DEFAULT_PREFERENCES": ("codehome.serve.notifications", "DEFAULT_PREFERENCES"),
    "EVENT_TO_CATEGORY": ("codehome.serve.notifications", "EVENT_TO_CATEGORY"),
    "NotificationChannel": ("codehome.serve.notifications", "NotificationChannel"),
    "NotificationRegistry": ("codehome.serve.notifications", "NotificationRegistry"),
    "notification_registry": ("codehome.serve.notifications", "notification_registry"),
    # -- Serve --
    "read_server_url": ("codehome.serve", "read_server_url"),
    # -- FastAPI auth dependencies (for plugin routes) --
    "get_current_user": ("codehome.serve.auth_deps", "get_current_user"),
    "get_gh_token": ("codehome.serve.dependencies", "get_gh_token"),
    # -- SDUI streaming command types + CLI runner --
    "run_command": ("codehome.serve.sdui.cli_runner", "run_command"),
    "CommandError": ("codehome.serve.sdui.commands", "CommandError"),
    "CommandProgress": ("codehome.serve.sdui.commands", "CommandProgress"),
    "CommandResult": ("codehome.serve.sdui.commands", "CommandResult"),
    # -- Session --
    "get_process_id": ("codehome.session", "get_process_id"),
    # -- State (storage APIs) --
    "ConfigStore": ("codehome.state", "ConfigStore"),
    "FileStore": ("codehome.state", "FileStore"),
    "Scope": ("codehome.state", "Scope"),
    "StateStore": ("codehome.state", "StateStore"),
    "lock": ("codehome.state", "lock"),
    "services": ("codehome.state", "services"),
    # -- Subprocesses --
    "OutputCallback": ("codehome.subprocesses", "OutputCallback"),
    "parse_numstat_line": ("codehome.subprocesses", "parse_numstat_line"),
    "run_gh": ("codehome.subprocesses", "run_gh"),
    "run_git": ("codehome.subprocesses", "run_git"),
    "run_streaming": ("codehome.subprocesses", "run_streaming"),
    # -- Utils --
    "USE_COLOR": ("codehome.utils", "USE_COLOR"),
    "atomic_json_write": ("codehome.utils", "atomic_json_write"),
    "blue": ("codehome.utils", "blue"),
    "bold": ("codehome.utils", "bold"),
    "cyan": ("codehome.utils", "cyan"),
    "die": ("codehome.utils", "die"),
    "dim": ("codehome.utils", "dim"),
    "green": ("codehome.utils", "green"),
    "load_json": ("codehome.utils", "load_json"),
    "magenta": ("codehome.utils", "magenta"),
    "open_url": ("codehome.utils", "open_url"),
    "red": ("codehome.utils", "red"),
    "render_box_table": ("codehome.utils", "render_box_table"),
    "warn": ("codehome.utils", "warn"),
    "yellow": ("codehome.utils", "yellow"),
}


def __getattr__(name: str):
    """Lazy import: resolve symbols on first access, then cache in globals()."""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            mod = importlib.import_module(module_path)
        except ModuleNotFoundError:
            plugin = _plugin_name_from_module(module_path)
            if plugin is not None:
                raise PluginNotInstalledError(
                    f"'{name}' requires the '{plugin}' plugin "
                    f"({module_path}). Install the plugin or check "
                    f"your [plugins] paths configuration."
                ) from None
            raise
        if attr_name is None:
            val = mod  # module re-export
        else:
            val = getattr(mod, attr_name)
        # Cache in module globals so __getattr__ isn't called again
        globals()[name] = val
        return val
    raise AttributeError(f"module 'codehome.sdk' has no attribute {name!r}")


def load_sibling(name: str, caller_file: str, *, cache: bool = False) -> types.ModuleType:
    """Dynamically import a sibling module from the same directory as *caller_file*.

    Plugins consist of loose .py files (not packages), so normal relative
    imports don't work.  This helper replaces the per-plugin ``_sibling()``
    pattern with a single SDK function.

    *cache*: if True, store the module in ``sys.modules`` so subsequent
    calls return the same object (useful for singletons like orchestrators).
    """
    import importlib.util
    import sys
    from pathlib import Path

    directory = Path(caller_file).resolve().parent
    path = directory / f"{name}.py"
    module_name = f"_plugin_{directory.name}_{name}"
    if cache and module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load sibling module {path}")
    mod = importlib.util.module_from_spec(spec)
    # Always register before exec_module: Python 3.13's @dataclass (and other
    # metaclass-based decorators) calls sys.modules.get(cls.__module__) during
    # class creation, which fails with AttributeError if the module isn't
    # registered yet.
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # On failure, don't leave a broken module in sys.modules.
        sys.modules.pop(module_name, None)
        raise
    if not cache:
        # Non-cached modules can still be found by name if needed, but
        # remove them to avoid accumulating stale refs on repeated loads.
        sys.modules.pop(module_name, None)
    return mod


def get_plugin_cache_dir(plugin_name: str, key: str) -> Path:
    """Return the cache directory for a plugin, creating it if needed.

    Checks the new location (~/.codehome/<plugin>/<key>) first,
    falls back to the legacy location (.supervisor/<plugin>/<key>).
    If neither exists, returns the new location.

    References to codehome_home and SUPERVISOR_DIR resolve lazily via
    module-level __getattr__ at call time.
    """
    from pathlib import Path as _Path

    new = codehome_home() / plugin_name / key
    if new.exists():
        return new
    legacy = SUPERVISOR_DIR / plugin_name / key
    if legacy.exists():
        return legacy
    return new


__all__ = [
    "BackgroundTask",
    "BackgroundTaskRegistry",
    "DEFAULT_PREFERENCES",
    "EVENT_TO_CATEGORY",
    "NotificationChannel",
    "NotificationRegistry",
    "PluginNotInstalledError",
    "PROTECTED_BRANCHES",
    "ROOT",
    "STAGING_MERGE_STATE",
    "SUPERVISOR_DIR",
    "TOKEN_FILE",
    "USE_COLOR",
    "OutputCallback",
    "CommandError",
    "CommandProgress",
    "CommandResult",
    "ConfigStore",
    "Event",
    "FileStore",
    "Scope",
    "StateStore",
    "atomic_json_write",
    "background_tasks",
    "blue",
    "bold",
    "build_parser",
    "cyan",
    "delete_token",
    "die",
    "dim",
    "dispatch_subcommand",
    "dispatched",
    "fire",
    "fire_sync",
    "get_current_user",
    "get_gh_token",
    "get_plugin_cache_dir",
    "get_token",
    "get_process_id",
    "green",
    "has_token",
    "list_connections",
    "load_json",
    "load_server_config",
    "load_sibling",
    "lock",
    "magenta",
    "notification_registry",
    "on",
    "open_url",
    "parse_numstat_line",
    "read_server_url",
    "red",
    "render_box_table",
    "resolve_global",
    "run_command",
    "run_gh",
    "run_git",
    "run_streaming",
    "server_running",
    "server_url",
    "services",
    "store_token",
    "codehome_home",
    "warn",
    "yellow",
]
