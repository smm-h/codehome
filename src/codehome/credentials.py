"""Per-user encrypted token store for arbitrary API providers.

Tokens are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256).
The encryption key is derived from the server's JWT secret via PBKDF2,
following the same pattern as github_tokens.py.

Storage: ~/.codehome/connections/{username}.json -- a dict mapping
provider name to encrypted token string. Each user gets their own file.
Falls back to .codehome/connections/ for legacy installs.
"""

import json
import threading
from pathlib import Path

from cryptography.fernet import InvalidToken

from codehome.paths import STATE_DIR, codehome_home
from codehome.serve.github_tokens import _get_fernet
from codehome.serve.logging_config import get_logger

logger = get_logger(component="connections")

# New location for encrypted connection tokens; legacy fallback below.
_CONNECTIONS_DIR = codehome_home() / "connections"
_LEGACY_CONNECTIONS_DIR = STATE_DIR / "connections"

# Guards read-modify-write on per-user files.
_lock = threading.Lock()


def _user_file(username: str) -> Path:
    """Path to a user's connection token file (new location, for writes)."""
    return _CONNECTIONS_DIR / f"{username}.json"


def _resolve_user_file(username: str) -> Path:
    """Resolve user token file: prefer new location, fall back to legacy."""
    new = _CONNECTIONS_DIR / f"{username}.json"
    if new.is_file():
        return new
    legacy = _LEGACY_CONNECTIONS_DIR / f"{username}.json"
    if legacy.is_file():
        return legacy
    return new  # default to new location (file does not exist yet)


def _load_user_tokens(username: str) -> dict[str, str]:
    """Load the encrypted tokens dict for a user from disk."""
    path = _resolve_user_file(username)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _save_user_tokens(username: str, tokens: dict[str, str]) -> None:
    """Write the encrypted tokens dict for a user to disk."""
    _CONNECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _user_file(username)
    path.write_text(json.dumps(tokens, indent=2) + "\n")
    path.chmod(0o600)  # owner read/write only -- contains secrets


def store_token(username: str, provider: str, token: str, jwt_secret: str) -> None:
    """Store an encrypted API token for a user+provider pair."""
    f = _get_fernet(jwt_secret)
    encrypted = f.encrypt(token.encode()).decode()
    with _lock:
        tokens = _load_user_tokens(username)
        tokens[provider] = encrypted
        _save_user_tokens(username, tokens)


def get_token(username: str, provider: str, jwt_secret: str) -> str | None:
    """Retrieve a decrypted API token. Returns None if not stored.

    For the "github" provider, falls back to the legacy github_tokens.py
    store if no token exists here, so existing GitHub tokens work without
    re-entering them.
    """
    tokens = _load_user_tokens(username)
    encrypted = tokens.get(provider)
    if encrypted:
        f = _get_fernet(jwt_secret)
        try:
            return f.decrypt(encrypted.encode()).decode()
        except InvalidToken:
            logger.warning("connection_decrypt_failed", username=username, provider=provider)
            return None

    # Fallback: check legacy github_tokens.py for backward compatibility.
    if provider == "github":
        from codehome.serve.github_tokens import get_github_token

        return get_github_token(username, jwt_secret)

    return None


def delete_token(username: str, provider: str) -> bool:
    """Delete a stored token. Returns True if it existed."""
    with _lock:
        tokens = _load_user_tokens(username)
        if provider not in tokens:
            # Still clean up legacy store even if not in the new store.
            if provider == "github":
                from codehome.serve.github_tokens import delete_github_token

                return delete_github_token(username)
            return False
        del tokens[provider]
        _save_user_tokens(username, tokens)

    # Also clean up legacy store so stale tokens don't linger.
    if provider == "github":
        from codehome.serve.github_tokens import delete_github_token

        delete_github_token(username)

    return True


def list_connections(username: str) -> list[str]:  # noqa: dead-code
    """List provider names that have tokens stored for this user."""
    return list(_load_user_tokens(username).keys())


def has_token(username: str, provider: str) -> bool:
    """Check if a token exists without decrypting.

    For the "github" provider, also checks the legacy github_tokens.py store.
    """
    tokens = _load_user_tokens(username)
    if provider in tokens:
        return True
    if provider == "github":
        from codehome.serve.github_tokens import has_github_token

        return has_github_token(username)
    return False
