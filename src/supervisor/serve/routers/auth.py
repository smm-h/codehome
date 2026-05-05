"""Auth endpoints: login, logout, user management, password change."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from starlette.requests import Request

from supervisor.http_client import TOKEN_FILE
from supervisor.serve.auth import (
    load_users,
    save_users,
)
from supervisor.serve.auth_deps import get_current_user, require_admin
from supervisor.serve.auth_ops import (
    authenticate_user,
)
from supervisor.serve.auth_ops import (
    change_password as ops_change_password,
)
from supervisor.serve.auth_ops import (
    create_user as ops_create_user,
)
from supervisor.serve.csrf import generate_csrf_token
from supervisor.serve.rate_limit import limiter

log = logging.getLogger(__name__)

# -- Public (no auth) -----------------------------------------------------

public_router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@public_router.post("/api/auth/login")
@limiter.limit("5/minute")  # type: ignore[untyped-decorator]
async def login(req: LoginRequest, response: Response, request: Request) -> object:
    """Authenticate with username/password, receive a JWT."""
    config = request.app.state.config
    try:
        token = authenticate_user(req.username, req.password, config.jwt_secret)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Invalid credentials") from None

    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    # CSRF double-submit cookie: JS-readable so the frontend can echo it
    # back in the X-CSRF-Token header on state-changing requests.
    response.set_cookie(
        key="csrf_token",
        value=generate_csrf_token(),
        httponly=False,
        samesite="lax",
        max_age=86400,
    )

    # Write token to CLI token file so browser login also authenticates the CLI.
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token)
    except OSError:
        log.warning("Could not write CLI token file %s", TOKEN_FILE)

    return {"ok": True, "token": token}


# -- Authenticated ---------------------------------------------------------

router = APIRouter()


@router.post("/api/auth/logout")
async def logout(response: Response) -> object:
    """Clear the session cookie."""
    response.delete_cookie(key="session")
    return {"ok": True}


@router.get("/api/auth/me")
async def auth_me(user: dict[str, Any] = Depends(get_current_user)) -> object:
    """Return the current user's identity from their token."""
    return {"username": user["sub"], "role": user["role"]}


# -- User management endpoints --------------------------------------------


@router.get("/api/users")
async def list_users(_admin: dict[str, Any] = Depends(require_admin)) -> object:
    """List all users (admin only). Password hashes are excluded."""
    users = load_users()
    return [{"username": u["username"], "role": u["role"], "created_at": u.get("created_at", "")} for u in users]


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str


@router.post("/api/users")
async def create_user(req: CreateUserRequest, _admin: dict[str, Any] = Depends(require_admin)) -> object:
    """Create a new user (admin only)."""
    try:
        ops_create_user(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ok": True}


@router.delete("/api/users/{username}")
async def delete_user(
    username: str,
    admin: dict[str, Any] = Depends(require_admin),
) -> object:
    """Delete a user (admin only). Cannot delete yourself."""
    if admin["sub"] == username:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    users = load_users()
    new_users = [u for u in users if u["username"] != username]
    if len(new_users) == len(users):
        raise HTTPException(status_code=404, detail=f"User not found: {username}")

    save_users(new_users)
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    password: str


@router.patch("/api/users/{username}/password")
async def change_password(
    username: str,
    req: ChangePasswordRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Change a user's password. Admin can change any; non-admin only their own."""
    try:
        ops_change_password(
            username,
            req.password,
            requesting_user=user["sub"],
            requesting_role=user.get("role", ""),
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"ok": True}
