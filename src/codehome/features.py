"""Boolean feature flags with per-machine overrides via features.json.

Reads from ~/.codehome/features.json (preferred) or .supervisor/features.json (fallback).
Writes always go to ~/.codehome/features.json.
"""

import json
import threading

from codehome.paths import resolve_global, codehome_home

# Dual-read: resolve_global checks ~/.codehome/ first, falls back to .supervisor/.
FEATURES_FILE = resolve_global("features.json")
# Writes always target the new canonical location.
_FEATURES_WRITE = codehome_home() / "features.json"

DEFAULTS: dict[str, bool] = {
    "conductor": True,
    "database": True,
    "monitoring": True,
    "plugins": True,
    "push": True,
    "slack": True,
    "terminal": True,
}

_lock = threading.Lock()
_overrides: dict[str, bool] | None = None


def _load_overrides() -> dict[str, bool]:
    """Read overrides from disk, returning {} on missing/malformed file."""
    if not FEATURES_FILE.is_file():
        return {}
    try:
        data = json.loads(FEATURES_FILE.read_text())
        return {k: bool(v) for k, v in data.items() if k in DEFAULTS}
    except (json.JSONDecodeError, AttributeError):
        return {}


def _ensure_loaded() -> dict[str, bool]:
    global _overrides
    if _overrides is None:
        with _lock:
            if _overrides is None:
                _overrides = _load_overrides()
    return _overrides


def enabled(name: str) -> bool:
    """Check whether a feature flag is enabled."""
    overrides = _ensure_loaded()
    if name in overrides:
        return overrides[name]
    return DEFAULTS.get(name, False)


def all_flags() -> dict[str, bool]:
    """Return the effective state of every known flag."""
    overrides = _ensure_loaded()
    return {name: overrides.get(name, default) for name, default in DEFAULTS.items()}


def reload() -> None:
    """Force re-read of overrides from disk."""
    global _overrides
    with _lock:
        _overrides = _load_overrides()


def _write_overrides(data: dict[str, bool]) -> None:
    """Write override flags to ~/.codehome/features.json."""
    _FEATURES_WRITE.parent.mkdir(parents=True, exist_ok=True)
    _FEATURES_WRITE.write_text(json.dumps(data, indent=2) + "\n")
