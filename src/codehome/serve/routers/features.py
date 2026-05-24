"""Feature flag API routes."""

from wesktop import Router, HTTPError, Request

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.features import DEFAULTS, _write_overrides, all_flags, reload

router = Router()
authed_router = Router()


@router.get("/api/features")
async def get_flags(request: Request) -> dict[str, bool]:
    return all_flags()


@authed_router.post("/api/features/reload")
async def reload_flags(request: Request) -> dict[str, bool]:
    reload()
    return all_flags()


@authed_router.put("/api/features")
async def update_flags(request: Request) -> dict[str, bool]:
    """Partial-update feature flag overrides.

    Only keys present in DEFAULTS are accepted; unknown keys are ignored.
    The incoming values are merged into the existing overrides file.
    """
    body: dict[str, bool] = request.json or {}

    # Read current overrides from disk (or empty dict if missing/malformed).
    from codehome.features import _load_overrides

    current = _load_overrides()

    # Merge only recognised keys.
    for key, value in body.items():
        if key in DEFAULTS:
            current[key] = value

    _write_overrides(current)
    reload()
    flags = all_flags()
    await bus_fire(Event(name="feature.changed", payload={"flags": flags}))
    return flags
