"""Comprehensive tests for the auth system.

Covers password hashing, JWT tokens, auth middleware, login/logout
endpoints, and user management.  Uses httpx.AsyncClient with ASGITransport
for integration tests, matching the patterns in test_server.py.  Does NOT
touch the get_current_user override used by test_server.py -- auth tests
use real JWT validation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from codehome.config import ServerConfig
from codehome.serve.auth import (
    create_token,
    hash_password,
    verify_password,
    verify_token,
)
from codehome.serve.auth_deps import get_current_user
from codehome.serve.server import app

# ---------------------------------------------------------------------------
# Constants used across tests.
# ---------------------------------------------------------------------------

TEST_SECRET = "test-jwt-secret-for-auth-tests"
TEST_ADMIN = {
    "username": "admin",
    "password_hash": hash_password("adminpass"),
    "role": "admin",
    "created_at": "2026-01-01T00:00:00+00:00",
}
TEST_VIEWER = {
    "username": "viewer",
    "password_hash": hash_password("viewerpass"),
    "role": "viewer",
    "created_at": "2026-01-02T00:00:00+00:00",
}
TEST_USERS = [TEST_ADMIN, TEST_VIEWER]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_users():
    """Patch load_users and save_users so tests never touch the real file.

    Each test gets its own copy of the user list so mutations are isolated.
    """
    users = [dict(u) for u in TEST_USERS]

    def _load():
        return [dict(u) for u in users]

    def _save(new_users):
        users.clear()
        users.extend(new_users)

    def _find(username):
        for u in users:
            if u["username"] == username:
                return dict(u)
        return None

    with (
        patch("codehome.serve.auth.load_users", side_effect=_load),
        patch("codehome.serve.auth.save_users", side_effect=_save),
        patch("codehome.serve.routers.auth.load_users", side_effect=_load),
        patch("codehome.serve.routers.auth.save_users", side_effect=_save),
        patch("codehome.serve.auth_ops.load_users", side_effect=_load),
        patch("codehome.serve.auth_ops.save_users", side_effect=_save),
        patch("codehome.serve.auth_ops.find_user", side_effect=_find),
        patch("codehome.serve.auth.USERS_FILE", new=Path("/dev/null")),
    ):
        yield users


@pytest.fixture(autouse=True)
def _mock_config():
    """Inject a fake ServerConfig on app.state so JWT verification works."""
    config = ServerConfig(port=9999, jwt_secret=TEST_SECRET, data_dir="/tmp")
    app.state.config = config
    return config


@pytest_asyncio.fixture
async def client():
    """Async HTTP client with NO auth override -- real JWT validation runs."""
    # Clear any leftover overrides from other test modules (e.g. test_server).
    app.dependency_overrides.pop(get_current_user, None)
    # Disable CSRF middleware in tests (tests don't send CSRF tokens).
    app.state.csrf_disabled = True
    # Prevent the _resolve_token_file fallback in get_current_user from reading
    # a real token off disk (e.g. ~/.codehome/token), which would silently
    # authenticate requests that the test expects to be rejected.
    with patch(
        "codehome.serve.auth_deps._resolve_token_file",
        return_value=Path("/dev/null/nonexistent"),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
    app.state.csrf_disabled = False
    app.dependency_overrides.pop(get_current_user, None)


def _admin_token() -> str:
    """Create a valid admin JWT for tests."""
    return create_token("admin", "admin", TEST_SECRET)


def _viewer_token() -> str:
    """Create a valid viewer JWT for tests."""
    return create_token("viewer", "viewer", TEST_SECRET)


def _auth_headers(token: str) -> dict[str, str]:
    """Return Authorization header dict for the given token."""
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. Unit tests for auth.py functions
# ===========================================================================


class TestHashPassword:
    """1. hash_password produces a valid bcrypt hash."""

    def test_produces_bcrypt_hash(self):
        hashed = hash_password("mypassword")
        # bcrypt hashes start with $2b$ (or $2a$/$2y$).
        assert hashed.startswith(("$2b$", "$2a$"))
        assert len(hashed) == 60  # bcrypt hash is always 60 chars

    def test_different_calls_produce_different_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        # Different salts -> different hashes.
        assert h1 != h2


class TestVerifyPassword:
    """2-3. verify_password correctness checks."""

    def test_correct_password_returns_true(self):
        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False


class TestCreateToken:
    """4. create_token returns a valid JWT string."""

    def test_returns_string(self):
        token = create_token("alice", "admin", "secret123")
        assert isinstance(token, str)
        # JWTs have three dot-separated parts.
        assert len(token.split(".")) == 3


class TestVerifyToken:
    """5-8. verify_token decoding and validation."""

    def test_valid_token_returns_claims(self):
        token = create_token("alice", "admin", "secret123")
        claims = verify_token(token, "secret123")
        assert claims is not None
        assert claims["sub"] == "alice"
        assert claims["role"] == "admin"
        assert "exp" in claims
        assert "iat" in claims

    def test_expired_token_returns_none(self):
        # expires_hours=0 creates a token that expires immediately.
        token = create_token("alice", "admin", "secret123", expires_hours=0)
        claims = verify_token(token, "secret123")
        assert claims is None

    def test_garbage_token_returns_none(self):
        claims = verify_token("not.a.jwt", "secret123")
        assert claims is None

    def test_empty_string_returns_none(self):
        claims = verify_token("", "secret123")
        assert claims is None

    def test_wrong_secret_returns_none(self):
        token = create_token("alice", "admin", "correct-secret")
        claims = verify_token(token, "wrong-secret")
        assert claims is None


class TestFindUser:
    """9-10. find_user lookup via the mocked user list."""

    def test_found(self):
        from codehome.serve.auth import find_user

        user = find_user("admin")
        assert user is not None
        assert user["username"] == "admin"
        assert user["role"] == "admin"

    def test_not_found(self):
        from codehome.serve.auth import find_user

        user = find_user("nonexistent")
        assert user is None


# ===========================================================================
# 2. Integration tests for auth endpoints
# ===========================================================================


class TestLogin:
    """11-13. POST /api/auth/login."""

    async def test_valid_login_returns_token_and_cookie(self, client):
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "adminpass",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "token" in data
        # Token should be a valid JWT.
        assert len(data["token"].split(".")) == 3
        # Session cookie should be set.
        assert "session" in resp.cookies

    async def test_wrong_password_returns_401(self, client):
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "wrongpass",
            },
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]

    async def test_nonexistent_user_returns_401(self, client):
        resp = await client.post(
            "/api/auth/login",
            json={
                "username": "ghost",
                "password": "anything",
            },
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]


class TestLogout:
    """14. POST /api/auth/logout."""

    async def test_logout_clears_cookie(self, client):
        # Login first to get a valid token.
        login_resp = await client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "adminpass",
            },
        )
        token = login_resp.json()["token"]

        resp = await client.post(
            "/api/auth/logout",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # The response should instruct the browser to clear the cookie
        # (max-age=0 or an expiry in the past).
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session" in set_cookie


class TestAuthMe:
    """15-16. GET /api/auth/me."""

    async def test_with_valid_token(self, client):
        token = _admin_token()
        resp = await client.get("/api/auth/me", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    async def test_without_token_returns_401(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401


class TestPingPublic:
    """17. GET /api/ping works without auth."""

    async def test_ping_no_auth(self, client):
        resp = await client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ===========================================================================
# 3. Integration tests for user management
# ===========================================================================


class TestListUsers:
    """18-19. GET /api/users."""

    async def test_admin_gets_user_list(self, client):
        resp = await client.get(
            "/api/users",
            headers=_auth_headers(_admin_token()),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2
        # Verify no password hashes are exposed.
        for user in data:
            assert "password_hash" not in user
            assert "username" in user
            assert "role" in user

    async def test_non_admin_gets_403(self, client):
        resp = await client.get(
            "/api/users",
            headers=_auth_headers(_viewer_token()),
        )
        assert resp.status_code == 403
        assert "Admin" in resp.json()["detail"]


class TestCreateUser:
    """20-21. POST /api/users."""

    async def test_admin_creates_user(self, client, _mock_users):
        resp = await client.post(
            "/api/users",
            json={"username": "newuser", "password": "newpass", "role": "viewer"},
            headers=_auth_headers(_admin_token()),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_duplicate_username_returns_409(self, client):
        resp = await client.post(
            "/api/users",
            json={"username": "admin", "password": "x", "role": "admin"},
            headers=_auth_headers(_admin_token()),
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]


class TestDeleteUser:
    """22-23. DELETE /api/users/{username}."""

    async def test_admin_deletes_other_user(self, client, _mock_users):
        resp = await client.delete(
            "/api/users/viewer",
            headers=_auth_headers(_admin_token()),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_cannot_delete_self(self, client):
        resp = await client.delete(
            "/api/users/admin",
            headers=_auth_headers(_admin_token()),
        )
        assert resp.status_code == 400
        assert "yourself" in resp.json()["detail"].lower()


class TestChangePassword:
    """24-25. PATCH /api/users/{username}/password."""

    async def test_admin_changes_any_password(self, client, _mock_users):
        resp = await client.patch(
            "/api/users/viewer/password",
            json={"password": "newviewerpass"},
            headers=_auth_headers(_admin_token()),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_non_admin_can_only_change_own(self, client, _mock_users):
        # Viewer changing their own password -- should succeed.
        resp = await client.patch(
            "/api/users/viewer/password",
            json={"password": "newpass"},
            headers=_auth_headers(_viewer_token()),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_non_admin_cannot_change_others(self, client):
        # Viewer trying to change admin's password -- should fail.
        resp = await client.patch(
            "/api/users/admin/password",
            json={"password": "hacked"},
            headers=_auth_headers(_viewer_token()),
        )
        assert resp.status_code == 403
        assert "Cannot change another" in resp.json()["detail"]


# ===========================================================================
# 4. Auth middleware tests
# ===========================================================================


class TestAuthMiddleware:
    """26-29. Auth middleware behavior on authenticated endpoints."""

    async def test_valid_bearer_token_succeeds(self, client):
        """26. Request with valid Bearer token succeeds."""
        token = _admin_token()
        resp = await client.get(
            "/api/auth/me",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200

    async def test_valid_session_cookie_succeeds(self, client):
        """27. Request with valid session cookie succeeds."""
        token = _admin_token()
        # Set the session cookie directly on the client.
        client.cookies.set("session", token)
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        # Clean up.
        client.cookies.clear()

    async def test_expired_token_returns_401(self, client):
        """28. Request with expired token returns 401."""
        token = create_token("admin", "admin", TEST_SECRET, expires_hours=0)
        resp = await client.get(
            "/api/auth/me",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 401

    async def test_no_auth_returns_401(self, client):
        """29. Request with no auth returns 401."""
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401
