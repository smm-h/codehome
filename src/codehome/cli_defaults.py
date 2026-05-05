"""CLI defaults -- hardcoded baseline with optional file overrides.

Defaults live in DEFAULTS below.  Users override per-key by writing
.supervisor/cli_defaults.json (gitignored, never committed).
"""

from __future__ import annotations

import json

from codehome.paths import resolve_global

DEFAULTS: dict[str, object] = {
    "logs.tail": 100,
    "inspect.wait_ms": 2000,
    "telemac.push.remote_path": "~/Downloads/",
    "telemac.pull.local_path": None,  # None = require explicit arg
    "services.poll_timeout_s": 120,
}

# Module-level cache: loaded once on first access (CLI is short-lived).
_overrides_cache: dict[str, object] | None = None


def _load_overrides() -> dict[str, object]:
    """Read overrides from disk, returning {} on missing/malformed file."""
    global _overrides_cache
    if _overrides_cache is not None:
        return _overrides_cache

    defaults_file = resolve_global("cli_defaults.json")
    if not defaults_file.is_file():
        _overrides_cache = {}
        return _overrides_cache
    try:
        data = json.loads(defaults_file.read_text())
        result = {}
        for k, v in data.items():
            if k not in DEFAULTS:
                continue
            default = DEFAULTS[k]
            # Accept the override only if its type matches the default's type.
            # None defaults accept any type (the default signals "no value").
            if default is not None and not isinstance(v, type(default)):
                continue
            result[k] = v
        _overrides_cache = result
        return _overrides_cache
    except (json.JSONDecodeError, AttributeError, OSError):
        _overrides_cache = {}
        return _overrides_cache


def get(key: str) -> object:
    """Return the value for *key* -- override if present, else default."""
    overrides = _load_overrides()
    if key in overrides:
        return overrides[key]
    return DEFAULTS[key]
