"""Auth operations for the server API.

Extracted from routers/auth.py so route handlers stay thin. Functions here
have no framework dependencies (no Request, Response, HTTPError).
Errors raise plain Python exceptions that the router maps to HTTP codes.
"""

from datetime import UTC, datetime

from codehome.serve.auth import (
    create_token,
    find_user,
    hash_password,
    load_users,
    save_users,
    verify_password,
)


def authenticate_user(username: str, password: str, jwt_secret: str) -> str:
    """Verify credentials and return a signed JWT.

    Raises PermissionError if credentials are invalid.
    """
    user = find_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise PermissionError("Invalid credentials")

    return create_token(user["username"], user["role"], jwt_secret)


def create_user(username: str, password: str, role: str) -> None:
    """Create a new user.

    Raises ValueError if the username already exists.
    """
    if find_user(username):
        raise ValueError(f"User already exists: {username}")

    users = load_users()
    users.append(
        {
            "username": username,
            "password_hash": hash_password(password),
            "role": role,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    save_users(users)


def change_password(
    target_username: str,
    new_password: str,
    *,
    requesting_user: str,
    requesting_role: str,
) -> None:
    """Change a user's password. Admin can change any; non-admin only their own.

    Raises PermissionError if a non-admin tries to change another user's password.
    Raises LookupError if the target user doesn't exist.
    """
    if requesting_role != "admin" and requesting_user != target_username:
        raise PermissionError("Cannot change another user's password")

    users = load_users()
    found = False
    for u in users:
        if u["username"] == target_username:
            u["password_hash"] = hash_password(new_password)
            found = True
            break

    if not found:
        raise LookupError(f"User not found: {target_username}")

    save_users(users)
