"""Files API -- opaque directory storage where the plugin owns the shape."""

from pathlib import Path

from codehome.state.scopes import Scope, resolve_path


class FileStore:
    """Opaque file storage. The plugin owns the directory structure.

    Unlike ConfigStore (read-only TOML) and StateStore (JSON with locking),
    FileStore simply provides a directory path and lets the plugin manage
    its own files however it sees fit (binary blobs, nested dirs, etc.).
    """

    def __init__(self, plugin: str, scope: Scope, **kwargs: str | None) -> None:
        self._dir = resolve_path(plugin, scope, **kwargs)

    @property
    def path(self) -> Path:
        """Return the plugin's storage directory. Creates it if needed."""
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def file(self, name: str) -> Path:
        """Return path to a specific file in the storage directory."""
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / name
