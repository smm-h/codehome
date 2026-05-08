"""User preferences persistence.

Two layers:

1. **Namespaced preferences** -- per-user files in
   `.codehome/preferences/{username}.json`, keyed by namespace (e.g.
   "branch-table").  Used by dashboard components for their own state.

2. **User preferences** -- a single `.codehome/preferences.json` file,
   keyed by username, holding flat settings (theme, language, etc.) with
   built-in defaults.
"""

import json
import threading
from pathlib import Path
from typing import Any

from codehome.paths import resolve_global, codehome_home
from codehome.serve.file_lock import write_json_locked

# -- Namespaced preferences (per-user files) --------------------------------

# Guards read-modify-write on per-user namespaced preference files.
_ns_lock = threading.Lock()

# Read: dual-path fallback. Write: canonical new location.
_PREFS_DIR_READ = resolve_global("preferences")
_PREFS_DIR_WRITE = codehome_home() / "preferences"


def _user_file_read(username: str) -> Path:
    """Path to read a user's namespaced-preferences JSON file (dual-path)."""
    return _PREFS_DIR_READ / f"{username}.json"


def _user_file_write(username: str) -> Path:
    """Path to write a user's namespaced-preferences JSON file (canonical)."""
    return _PREFS_DIR_WRITE / f"{username}.json"


def _load(username: str) -> dict[str, Any]:
    """Read the full namespaced preferences dict for a user."""
    path = _user_file_read(username)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _save(username: str, data: dict[str, Any]) -> None:
    """Write the full namespaced preferences dict for a user with file locking."""
    write_json_locked(_user_file_write(username), data)


def get_preference(username: str, key: str) -> Any:
    """Read a single preference namespace. Returns None if not set."""
    return _load(username).get(key)


def set_preference(username: str, key: str, value: Any) -> None:
    """Write a single preference namespace (merges into existing file)."""
    with _ns_lock:
        data = _load(username)
        data[key] = value
        _save(username, data)


def delete_preference(username: str, key: str) -> None:
    """Remove a single preference namespace."""
    with _ns_lock:
        data = _load(username)
        if key in data:
            del data[key]
            _save(username, data)


# -- User preferences (single shared file with defaults) --------------------

# Guards read-modify-write on the shared preferences.json file.
_user_prefs_lock = threading.Lock()

_PREFS_FILE_READ = resolve_global("preferences.json")
_PREFS_FILE_WRITE = codehome_home() / "preferences.json"

DEFAULT_PREFERENCES: dict[str, str | int] = {
    "metrics_variant": "sparkline",  # "chartjs" | "sparkline" | "canvas"
    "theme": "auto",  # "auto" | "dark" | "light"
    "language": "en",  # "en" | "it"
    "autonomy": 2,  # 0-4, controls conductor auto-decision level
    "accent_color": "indigo",  # accent palette id (see dashboard/src/lib/accent.ts)
    "nav_layout": "second-row",
    "home_preset": "status-board",
    "plugin_display_mode": "chrome",
}

# Allowed values per preference key -- used for validation.
ALLOWED_VALUES: dict[str, set[str] | set[int]] = {
    "metrics_variant": {"chartjs", "sparkline", "canvas"},
    "theme": {"auto", "dark", "light"},
    "language": {"en", "it"},
    "autonomy": {0, 1, 2, 3, 4},
    "accent_color": {
        "indigo",
        "blue",
        "cyan",
        "teal",
        "green",
        "amber",
        "orange",
        "rose",
        "red",
        "purple",
    },
    "nav_layout": {"second-row", "dropdown", "inline-swap", "flat-tabs"},
    "home_preset": {"activity", "status-board", "compact"},
    "plugin_display_mode": {"chrome", "seamless"},
}


def load_preferences() -> dict[str, dict[str, Any]]:
    """Read all user preferences (dual-path fallback)."""
    if not _PREFS_FILE_READ.is_file():
        return {}
    try:
        return json.loads(_PREFS_FILE_READ.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def save_preferences(prefs: dict[str, dict[str, Any]]) -> None:
    """Write the full preferences dict (all users) to canonical location."""
    write_json_locked(_PREFS_FILE_WRITE, prefs)


def get_user_preferences(username: str) -> dict[str, Any]:
    """Return one user's preferences, filling in defaults for missing keys."""
    all_prefs = load_preferences()
    stored = all_prefs.get(username, {})
    return {**DEFAULT_PREFERENCES, **stored}


def set_user_preferences(username: str, updates: dict[str, Any]) -> None:
    """Merge updates into a user's preferences and persist.

    Only keys present in DEFAULT_PREFERENCES are accepted; unknown keys
    are silently dropped.  Values are validated against ALLOWED_VALUES.
    """
    with _user_prefs_lock:
        all_prefs = load_preferences()
        current = all_prefs.get(username, {})
        for key, value in updates.items():
            if key not in DEFAULT_PREFERENCES:
                continue
            if key in ALLOWED_VALUES and value not in ALLOWED_VALUES[key]:
                continue
            current[key] = value
        all_prefs[username] = current
        save_preferences(all_prefs)


# -- Global preferences (shared across all users) ---------------------------

# Guards read-modify-write on the global preferences file.
_global_prefs_lock = threading.Lock()

_GLOBAL_PREFS_READ = resolve_global("global_preferences.json")
_GLOBAL_PREFS_WRITE = codehome_home() / "global_preferences.json"


def _load_global_all() -> dict[str, Any]:
    """Read the entire global preferences file (dual-path fallback)."""
    if not _GLOBAL_PREFS_READ.is_file():
        return {}
    try:
        return json.loads(_GLOBAL_PREFS_READ.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _save_global_all(data: dict[str, Any]) -> None:
    """Write the entire global preferences dict to canonical location."""
    write_json_locked(_GLOBAL_PREFS_WRITE, data)


def load_global_preferences(namespace: str) -> dict[str, Any]:
    """Read a single namespace from the global preferences file."""
    val = _load_global_all().get(namespace, {})
    if isinstance(val, dict):
        return val
    return {}


def save_global_preferences(namespace: str, data: dict[str, Any]) -> None:
    """Write a single namespace into the global preferences file."""
    with _global_prefs_lock:
        all_data = _load_global_all()
        all_data[namespace] = data
        _save_global_all(all_data)
