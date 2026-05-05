"""Feature flag API routes."""

from fastapi import APIRouter, Body

from supervisor.features import DEFAULTS, _write_overrides, all_flags, reload

router = APIRouter(prefix="/api/features", tags=["features"])
authed_router = APIRouter(prefix="/api/features", tags=["features"])


@router.get("")
async def get_flags() -> dict[str, bool]:
    return all_flags()


@authed_router.post("/reload")
async def reload_flags() -> dict[str, bool]:
    reload()
    return all_flags()


@authed_router.put("")
async def update_flags(body: dict[str, bool] = Body()) -> dict[str, bool]:
    """Partial-update feature flag overrides.

    Only keys present in DEFAULTS are accepted; unknown keys are ignored.
    The incoming values are merged into the existing overrides file.
    """
    # Read current overrides from disk (or empty dict if missing/malformed).
    from supervisor.features import _load_overrides

    current = _load_overrides()

    # Merge only recognised keys.
    for key, value in body.items():
        if key in DEFAULTS:
            current[key] = value

    _write_overrides(current)
    reload()
    return all_flags()
