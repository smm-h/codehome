"""Plugin storage APIs -- Config (TOML), State (JSON), Files (opaque), Locks."""

from codehome.state.config_api import ConfigStore
from codehome.state.files_api import FileStore
from codehome.state.lock import lock
from codehome.state.scopes import Scope, resolve_path
from codehome.state.service_registry import ServiceRegistry, services
from codehome.state.state_api import StateStore

__all__ = [
    "ConfigStore",
    "FileStore",
    "Scope",
    "ServiceRegistry",
    "StateStore",
    "lock",
    "resolve_path",
    "services",
]
