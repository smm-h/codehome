"""v auth setup: one-time interactive wizard to bootstrap server configuration."""

import argparse
import getpass
import json
import os
import secrets
from datetime import UTC, datetime

import bcrypt

from codehome.paths import codehome_home
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
    """One-time server bootstrap wizard. Creates ~/.codehome/config.json and users.json."""
    if CONFIG_FILE.exists():
        die("Setup already complete. Delete ~/.codehome/config.json to re-run.")

    flag_username = getattr(args, "username", None)
    flag_password = getattr(args, "password", None)
    flag_port = getattr(args, "port", None)
    flag_jwt_secret = getattr(args, "jwt_secret", None)
    non_interactive = flag_username and flag_password

    if flag_password and len(flag_password) < 8:
        die("Password must be at least 8 characters.")

    print("=== v setup: server bootstrap ===\n")

    username = flag_username or _prompt_username()
    if not username:
        die("Username cannot be empty.")

    password = flag_password or _prompt_password()
    port = flag_port if flag_port is not None else (9100 if non_interactive else _prompt_port())
    jwt_secret = flag_jwt_secret or secrets.token_urlsafe(32)

    # Hash password with bcrypt.
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    _HOME.mkdir(parents=True, exist_ok=True)

    config = {
        "port": port,
        "jwt_secret": jwt_secret,
        "data_dir": ".codehome",
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
