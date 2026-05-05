"""Connections endpoints: list, connect, disconnect, and test provider tokens."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from supervisor.serve.auth_deps import get_current_user
from supervisor.serve.connections import delete_token, get_token, store_token
from supervisor.serve.providers import available_providers, get_provider

router = APIRouter()


@router.get("/api/connections")
async def list_connections(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """List all providers with connection status for the current user."""
    return available_providers(user["sub"])


class ConnectRequest(BaseModel):
    token: str


@router.post("/api/connections/{provider}")
async def connect_provider(
    provider: str,
    req: ConnectRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Store a token after validating it with the provider's test_connection."""
    prov = get_provider(provider)
    if not prov:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    # Validate the token before storing it.
    # test_connection() does blocking I/O (subprocess call) -- run off the event loop.
    if not await asyncio.to_thread(prov.test_connection, req.token):
        raise HTTPException(status_code=400, detail="Connection test failed -- token is invalid or expired")

    config = request.app.state.config
    store_token(user["sub"], provider, req.token, config.jwt_secret)
    return {"ok": True}


@router.delete("/api/connections/{provider}")
async def disconnect_provider(
    provider: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Remove a stored token for a provider."""
    prov = get_provider(provider)
    if not prov:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    deleted = delete_token(user["sub"], provider)
    if not deleted:
        raise HTTPException(status_code=404, detail="No token configured for this provider")
    return {"ok": True}


@router.post("/api/connections/{provider}/test")
async def check_connection(
    provider: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Test an existing stored connection by decrypting the token and calling the provider."""
    prov = get_provider(provider)
    if not prov:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    config = request.app.state.config
    token = get_token(user["sub"], provider, config.jwt_secret)
    if not token:
        raise HTTPException(status_code=404, detail="No token configured for this provider")

    # test_connection() does blocking I/O (subprocess call) -- run off the event loop.
    ok = await asyncio.to_thread(prov.test_connection, token)
    if ok:
        return {"ok": True}
    return {"ok": False, "error": "Connection test failed"}


@router.get("/api/connections/{provider}/enrich")
async def enrich_branch(
    provider: str,
    branch: str,
    repo: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Run a provider's enrich_branch for a specific branch.

    Used by the branch inspector to fetch provider-specific metadata
    (e.g. Figma design file info) without baking it into the main
    inspect endpoint.
    """
    prov = get_provider(provider)
    if not prov:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    config = request.app.state.config
    token = get_token(user["sub"], provider, config.jwt_secret)
    if not token:
        # No token stored -- return empty rather than 404, since the
        # frontend gracefully hides the section when data is null.
        return None

    return await asyncio.to_thread(prov.enrich_branch, branch, repo, token)
