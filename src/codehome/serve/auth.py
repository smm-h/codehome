"""Authentication helpers: user storage, password hashing, JWT tokens.

Pure functions with no FastAPI dependencies -- keeps auth logic testable
and reusable outside the server context.
"""

import json
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from codehome.paths import resolve_global

USERS_FILE = resolve_global("users.json")


def load_users() -> list[dict[str, str]]:
    """Read all users from .supervisor/users.json."""
    if not USERS_FILE.is_file():
        return []
    return json.loads(USERS_FILE.read_text())  # type: ignore[no-any-return]


def save_users(users: list[dict[str, str]]) -> None:
    """Write users list back to .supervisor/users.json."""
    USERS_FILE.write_text(json.dumps(users, indent=2) + "\n")


def find_user(username: str) -> dict[str, str] | None:
    """Look up a user by username. Returns the user dict or None."""
    for user in load_users():
        if user["username"] == username:
            return user
    return None


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def create_token(username: str, role: str, secret: str, expires_hours: int = 720) -> str:
    """Create a signed JWT with sub, role, exp, and iat claims."""
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str, secret: str) -> dict[str, str] | None:
    """Decode and validate a JWT. Returns claims dict or None on any error."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError, Exception):
        return None
