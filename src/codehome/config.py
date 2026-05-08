"""Server configuration: load config.json for the dev server.

Server config lives at ~/.codehome/config.json (or .codehome/config.json
in legacy layouts), created by `v auth setup`.

Repo configuration has moved to the core plugin:
  codehome.core.repo_config
"""

import json
from dataclasses import dataclass

from codehome.paths import resolve_global

SERVER_CONFIG_FILE = resolve_global("config.json")


# ---------------------------------------------------------------------------
# Server config (created by `v auth setup`)
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    port: int
    jwt_secret: str
    data_dir: str
    sentry_dsn: str = ""  # Optional Sentry DSN; empty means disabled.


def server_config_exists() -> bool:
    """Check whether the server config file exists."""
    return SERVER_CONFIG_FILE.is_file()


def load_server_config() -> ServerConfig | None:
    """Load server config from ~/.codehome/config.json.

    Returns None if the file doesn't exist (caller decides what to do).
    Raises ValueError if the file contains malformed JSON.
    """
    if not SERVER_CONFIG_FILE.is_file():
        return None
    text = SERVER_CONFIG_FILE.read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in {SERVER_CONFIG_FILE}: {exc}") from exc
    return ServerConfig(
        port=int(data["port"]),
        jwt_secret=str(data["jwt_secret"]),
        data_dir=str(data["data_dir"]),
        sentry_dsn=str(data.get("sentry_dsn", "")),
    )
