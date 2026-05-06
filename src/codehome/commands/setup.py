"""v auth setup: one-time interactive wizard to bootstrap server configuration."""

import argparse
import getpass
import json
import os
import secrets
from datetime import UTC, datetime

import bcrypt

from codehome.paths import SUPERVISOR_DIR, codehome_home
from codehome.utils import die

# Writes go to ~/.codehome/ (new canonical location).
_HOME = codehome_home()
CONFIG_FILE = _HOME / "config.json"
USERS_FILE = _HOME / "users.json"


def _prompt_username() -> str:
    default = os.getlogin()
    username = input(f"Admin username [{default}]: ").strip()
    return username or default


def _prompt_password() -> str:
    while True:
        pw = getpass.getpass("Admin password (min 8 chars): ")
        if len(pw) < 8:
            print("Password must be at least 8 characters.")
            continue
        pw2 = getpass.getpass("Confirm password: ")
        if pw != pw2:
            print("Passwords do not match.")
            continue
        return pw


def _prompt_port() -> int:
    while True:
        raw = input("Server port [9100]: ").strip()
        if not raw:
            return 9100
        try:
            port = int(raw)
        except ValueError:
            print("Invalid number.")
            continue
        if not (1024 <= port <= 65535):
            print("Port must be between 1024 and 65535.")
            continue
        return port


def _prompt_jwt_secret() -> str:
    generated = secrets.token_urlsafe(32)
    print(f"Generated JWT secret: {generated}")
    override = input("JWT secret (press Enter to accept): ").strip()
    return override or generated


def cmd_setup(args: argparse.Namespace) -> None:
    """One-time server bootstrap wizard. Creates .supervisor/config.json and users.json."""
    if CONFIG_FILE.exists():
        die("Setup already complete. Delete .supervisor/config.json to re-run.")

    print("=== v setup: server bootstrap ===\n")

    username = _prompt_username()
    if not username:
        die("Username cannot be empty.")

    password = _prompt_password()
    port = _prompt_port()
    jwt_secret = _prompt_jwt_secret()

    # Hash password with bcrypt.
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    _HOME.mkdir(parents=True, exist_ok=True)

    config = {
        "port": port,
        "jwt_secret": jwt_secret,
        "data_dir": ".supervisor",
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")

    users = [
        {
            "username": username,
            "password_hash": pw_hash,
            "role": "admin",
            "created_at": datetime.now(UTC).isoformat(),
        }
    ]
    USERS_FILE.write_text(json.dumps(users, indent=2) + "\n")

    print("\nSetup complete.")
    print(f"  Config: {CONFIG_FILE}")
    print(f"  Users:  {USERS_FILE}")
    print(f"  Port:   {port}")
    print(f"  Admin:  {username}")
