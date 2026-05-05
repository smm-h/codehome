"""Authentication commands: v login, v logout.

Manages a JWT token stored at ~/.superv/token (with fallback to
~/.supervisor/token for reads).  The token is obtained from the dev
server's /api/auth/login endpoint.
"""

import argparse
import getpass
import json
import urllib.error
import urllib.request

from codehome.http_client import TOKEN_FILE, _resolve_token_file
from codehome.serve import read_server_url
from codehome.utils import die, open_url


def _save_token(token: str) -> None:
    """Write token to disk, creating parent dir if needed."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)


def _require_server() -> str:
    """Return the server URL or die with a helpful message."""
    url = read_server_url()
    if not url:
        die("Server is not running. Start it with: v server")
    return url


def cmd_login(args: argparse.Namespace) -> None:
    """Authenticate with the dev server."""
    # Direct token mode: store the provided token without contacting server.
    token = getattr(args, "token", None)
    if token:
        _save_token(token)
        print("Token saved.")
        return

    # Browser mode: open the dashboard login page.
    if getattr(args, "browser", False):
        url = _require_server()
        login_url = f"{url}/login"
        open_url(login_url)
        print("Log in via the browser, then run: v auth login --token <token>")
        return

    # Interactive mode: prompt for credentials and POST to server.
    url = _require_server()
    username = input("Username: ")
    password = getpass.getpass("Password: ")

    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{url}/api/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            die("Invalid username or password")
        die(f"Login failed (HTTP {exc.code})")
    except (urllib.error.URLError, OSError):
        die(f"Could not reach server at {url}")

    token = data.get("token", "")
    if not token:
        die("Server did not return a token")

    _save_token(token)
    print("Logged in.")


def cmd_logout(args: argparse.Namespace) -> None:
    """Remove the stored authentication token."""
    tf = _resolve_token_file()
    if not tf.exists():
        print("Not logged in")
        return
    tf.unlink()
    print("Logged out")
