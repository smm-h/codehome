"""Dependency staleness detection for containerized services.

Compares the host lockfile hash against the fingerprint stored inside a
Docker volume's node_modules.  The container entrypoint (docker-entrypoint.sh)
writes node_modules/.package-lock-fingerprint on npm ci; if the host lockfile
changes after the container started, the volume is stale.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)

# Timeout for `docker exec` to read the fingerprint file (seconds).
_DOCKER_EXEC_TIMEOUT = 10

# Fingerprint path inside the container, written by docker-entrypoint.sh.
_CONTAINER_FINGERPRINT_PATH = "/app/node_modules/.package-lock-fingerprint"

# Lockfile name to hash on the host side.
_LOCKFILE_NAME = "package-lock.json"


class DepsStatus(TypedDict):
    stale: bool
    host_hash: str | None
    volume_hash: str | None
    error: str | None


def check_deps_staleness(
    container_name: str,
    host_app_dir: Path,
) -> DepsStatus:
    """Check whether a container's node_modules volume is stale.

    Args:
        container_name: Docker container name (e.g. "bag-navchat-vite-1").
        host_app_dir: Host path to the app directory containing package-lock.json.

    Returns:
        DepsStatus dict with stale flag, hashes, and optional error.

    """
    # 1. Hash the host lockfile.
    lockfile = host_app_dir / _LOCKFILE_NAME
    if not lockfile.exists():
        return DepsStatus(
            stale=True,
            host_hash=None,
            volume_hash=None,
            error=f"Lockfile not found: {lockfile}",
        )

    host_hash = _md5_file(lockfile)

    # 2. Read the fingerprint from the running container's volume.
    volume_hash, read_error = _read_container_fingerprint(container_name)

    if read_error:
        return DepsStatus(
            stale=True,
            host_hash=host_hash,
            volume_hash=None,
            error=read_error,
        )

    return DepsStatus(
        stale=host_hash != volume_hash,
        host_hash=host_hash,
        volume_hash=volume_hash,
        error=None,
    )


def resolve_container_name(project_name: str, compose_service: str) -> str:
    """Build the Docker Compose container name from project and service.

    Docker Compose convention: {project}-{service}-1.
    """
    return f"{project_name}-{compose_service}-1"


def resolve_host_app_dir(worktree: str | Path, app_dir: str | None) -> Path | None:
    """Resolve the host-side app directory containing the lockfile.

    Returns None if app_dir is not set (service doesn't use node_modules).
    """
    if not app_dir:
        return None
    return Path(worktree) / app_dir


def _md5_file(path: Path) -> str:
    """Compute the MD5 hex digest of a file, matching the entrypoint's md5sum."""
    # MD5 is used here to match the entrypoint's md5sum, not for security.
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_container_fingerprint(container_name: str) -> tuple[str | None, str | None]:
    """Read the fingerprint file from inside a running container.

    Returns (hash, None) on success, or (None, error_message) on failure.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", container_name, "cat", _CONTAINER_FINGERPRINT_PATH],
            capture_output=True,
            text=True,
            timeout=_DOCKER_EXEC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f"docker exec timed out after {_DOCKER_EXEC_TIMEOUT}s"
    except FileNotFoundError:
        return None, "docker CLI not found"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Distinguish between container-not-running and fingerprint-not-found.
        if "No such container" in stderr or "is not running" in stderr:
            return None, "Container is not running"
        if "No such file" in stderr:
            return None, "Fingerprint file not found (npm ci may not have run)"
        return None, stderr or f"docker exec exited with code {result.returncode}"

    return result.stdout.strip(), None
