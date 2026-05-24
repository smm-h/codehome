"""Auth endpoints: login, logout, user management, password change."""

import logging
from typing import Any

from pydantic import BaseModel
from wesktop import Router, HTTPError, Request, JSONResponse, set_cookie, delete_cookie

from codehome.http_client import TOKEN_FILE
from codehome.serve.auth import (
    load_users,
    save_users,
)
from codehome.serve.auth_deps import get_current_user, require_admin
from codehome.serve.auth_ops import (
    authenticate_user,
)
from codehome.serve.auth_ops import (
    change_password as ops_change_password,
)
from codehome.serve.auth_ops import (
    create_user as ops_create_user,
)
from codehome.serve.csrf import generate_csrf_token

log = logging.getLogger(__name__)

# -- Public (no auth) -----------------------------------------------------

public_router = Router()


class LoginRequest(BaseModel):
    username: str
    password: str


@public_router.post("/api/auth/login")
async def login(request: Request) -> object:
    """Authenticate with username/password, receive a JWT."""
    req = request.json_as(LoginRequest)
    config = request.state.config
    try:
        token = authenticate_user(req.username, req.password, config.jwt_secret)
    except PermissionError:
        raise HTTPError(401, "Invalid credentials") from None

    # Write token to CLI token file so browser login also authenticates the CLI.
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token)
    except OSError:
        log.warning("Could not write CLI token file %s", TOKEN_FILE)

    return JSONResponse(
        {"ok": True, "token": token},
        cookies=[
            set_cookie("session", token, httponly=True, samesite="lax", max_age=86400),
            set_cookie("csrf_token", generate_csrf_token(), httponly=False, samesite="lax", max_age=86400),
        ],
    )


# -- Authenticated ---------------------------------------------------------
# Routes below are mounted with router-level deps={"user": get_current_user}
# in server.py.  Per-route deps override when admin access is needed.

router = Router()


@router.post("/api/auth/logout")
async def logout(request: Request, user: dict[str, Any] = ...) -> object:
    """Clear the session cookie."""
    return JSONResponse(
        {"ok": True},
        cookies=[delete_cookie("session")],
    )


@router.get("/api/auth/me")
async def auth_me(request: Request, user: dict[str, Any] = ...) -> object:
    """Return the current user's identity from their token."""
    log.info("auth_me called, from_token_file=%s, user=%s", getattr(request.state, "_auth_from_token_file", False), user.get("sub"))
    if getattr(request.state, "_auth_from_token_file", False):
        token = TOKEN_FILE.read_text().strip()
        return JSONResponse(
            {"username": user["sub"], "role": user["role"]},
            cookies=[
                set_cookie("session", token, httponly=True, samesite="lax", max_age=86400),
                set_cookie("csrf_token", generate_csrf_token(), httponly=False, samesite="lax", max_age=86400),
            ],
        )
    return {"username": user["sub"], "role": user["role"]}


# -- User management endpoints --------------------------------------------


@router.get("/api/users", deps={"user": require_admin})
async def list_users(request: Request, user: dict[str, Any] = ...) -> object:
    """List all users (admin only). Password hashes are excluded."""
    users = load_users()
    return [{"username": u["username"], "role": u["role"], "created_at": u.get("created_at", "")} for u in users]


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str


@router.post("/api/users", deps={"user": require_admin})
async def create_user(request: Request, user: dict[str, Any] = ...) -> object:
    """Create a new user (admin only)."""
    req = request.json_as(CreateUserRequest)
    try:
        ops_create_user(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPError(409, str(e)) from None
    return {"ok": True}


@router.delete("/api/users/{username}", deps={"user": require_admin})
async def delete_user(request: Request, user: dict[str, Any] = ...) -> object:
    """Delete a user (admin only). Cannot delete yourself."""
    username = request.path_params["username"]
    if user["sub"] == username:
        raise HTTPError(400, "Cannot delete yourself")

    users = load_users()
    new_users = [u for u in users if u["username"] != username]
    if len(new_users) == len(users):
        raise HTTPError(404, f"User not found: {username}")

    save_users(new_users)
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    password: str


@router.patch("/api/users/{username}/password")
async def change_password(request: Request, user: dict[str, Any] = ...) -> object:
    """Change a user's password. Admin can change any; non-admin only their own."""
    username = request.path_params["username"]
    req = request.json_as(ChangePasswordRequest)
    try:
        ops_change_password(
            username,
            req.password,
            requesting_user=user["sub"],
            requesting_role=user.get("role", ""),
        )
    except PermissionError as e:
        raise HTTPError(403, str(e)) from None
    except LookupError as e:
        raise HTTPError(404, str(e)) from None
    return {"ok": True}
