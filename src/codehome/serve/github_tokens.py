"""Per-user GitHub token storage with symmetric encryption.

Tokens are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256).
The encryption key is derived from the server's JWT secret via PBKDF2
so no additional secret management is needed.

Storage: ~/.codehome/github-tokens.json  (encrypted values only).
Falls back to .codehome/github-tokens.json for legacy installs.
"""

import base64
import json
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from codehome.paths import STATE_DIR, codehome_home

# New location for encrypted GitHub tokens; legacy fallback below.
_TOKENS_FILE = codehome_home() / "github-tokens.json"
_LEGACY_TOKENS_FILE = STATE_DIR / "github-tokens.json"

# Guards read-modify-write on the tokens file.
_lock = threading.Lock()

# Salt for key derivation -- fixed per installation is fine since
# the JWT secret itself is high-entropy.
_KDF_SALT = b"veliu-dashboard-github-tokens-v1"


def _derive_fernet_key(jwt_secret: str) -> bytes:
    """Derive a Fernet-compatible key from the JWT secret."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(jwt_secret.encode()))


def _get_fernet(jwt_secret: str) -> Fernet:
    """Create a Fernet instance from the JWT secret."""
    return Fernet(_derive_fernet_key(jwt_secret))


def _resolve_tokens_file() -> Path:
    """Resolve the tokens file: prefer new location, fall back to legacy."""
    if _TOKENS_FILE.is_file():
        return _TOKENS_FILE
    if _LEGACY_TOKENS_FILE.is_file():
        return _LEGACY_TOKENS_FILE
    return _TOKENS_FILE  # default to new location


def _load_tokens() -> dict[str, str]:
    """Load the encrypted tokens dict from disk."""
    path = _resolve_tokens_file()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tokens(tokens: dict[str, str]) -> None:
    """Write the encrypted tokens dict to disk (always to new location)."""
    _TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKENS_FILE.write_text(json.dumps(tokens, indent=2) + "\n")
    _TOKENS_FILE.chmod(0o600)  # owner read/write only -- contains secrets


def get_github_token(username: str, jwt_secret: str) -> str | None:
    """Decrypt and return the stored GitHub token, or None if not set."""
    tokens = _load_tokens()
    encrypted = tokens.get(username)
    if not encrypted:
        return None
    f = _get_fernet(jwt_secret)
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        return None


def has_github_token(username: str) -> bool:
    """Check whether a GitHub token is configured (without decrypting)."""
    tokens = _load_tokens()
    return username in tokens


def delete_github_token(username: str) -> bool:
    """Remove the stored GitHub token. Returns True if one was deleted."""
    with _lock:
        tokens = _load_tokens()
        if username not in tokens:
            return False
        del tokens[username]
        _save_tokens(tokens)
        return True
