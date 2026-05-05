"""Push notification API routes.

Provides endpoints for:
- Registering/unregistering push subscriptions
- Retrieving the public VAPID key
- Managing per-user notification preferences
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from codehome.serve.auth_deps import get_current_user
from codehome.serve.dependencies import get_push_manager
from codehome.serve.push import PushManager

router = APIRouter(prefix="/api/push", tags=["push"])


# ---- Request/Response models ----


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict[str, str]
    # Optional fields from the PushSubscription spec.
    expirationTime: float | None = None


class UnsubscribeRequest(BaseModel):
    endpoint: str


class PreferencesUpdate(BaseModel):
    pipeline_failure: bool | None = None
    agent_question: bool | None = None
    pr_activity: bool | None = None
    conductor_completion: bool | None = None
    service_state: bool | None = None
    branch_activity: bool | None = None


# ---- Public router (no auth, for VAPID key retrieval) ----

public_router = APIRouter(prefix="/api/push", tags=["push"])


@public_router.get("/vapid-key")
async def get_vapid_key(pm: PushManager = Depends(get_push_manager)) -> object:
    """Return the public VAPID key for push subscription registration."""
    return {"public_key": pm.public_key}


# ---- Authenticated endpoints ----


@router.post("/subscribe")
async def subscribe(
    body: SubscribeRequest,
    user: dict[str, Any] = Depends(get_current_user),
    pm: PushManager = Depends(get_push_manager),
) -> object:
    """Register a push subscription for the current user."""
    # Reconstruct the subscription_info dict expected by pywebpush.
    subscription_info = {
        "endpoint": body.endpoint,
        "keys": body.keys,
    }
    user_id = user.get("sub", "anonymous")
    pm.subscribe(user_id, subscription_info)  # type: ignore[arg-type]
    return {"ok": True}


@router.delete("/subscribe")
async def unsubscribe(
    body: UnsubscribeRequest,
    user: dict[str, Any] = Depends(get_current_user),
    pm: PushManager = Depends(get_push_manager),
) -> object:
    """Unregister a push subscription for the current user."""
    user_id = user.get("sub", "anonymous")
    pm.unsubscribe(user_id, body.endpoint)
    return {"ok": True}


@router.get("/preferences")
async def get_preferences(
    user: dict[str, Any] = Depends(get_current_user),
    pm: PushManager = Depends(get_push_manager),
) -> object:
    """Get the current user's notification preferences."""
    user_id = user.get("sub", "anonymous")
    return pm.get_preferences(user_id)


@router.put("/preferences")
async def update_preferences(
    body: PreferencesUpdate,
    user: dict[str, Any] = Depends(get_current_user),
    pm: PushManager = Depends(get_push_manager),
) -> object:
    """Update the current user's notification preferences."""
    user_id = user.get("sub", "anonymous")
    # Only include explicitly set fields.
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    pm.set_preferences(user_id, updates)
    return pm.get_preferences(user_id)
