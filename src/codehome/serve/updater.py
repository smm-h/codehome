"""Periodic update checker that compares local version against latest git tag."""

import asyncio
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from codehome.serve.logging_config import get_logger

log = get_logger(component="updater")

# Project root: four levels up from this file (serve/ -> codehome/ -> src/ -> codehome/).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _read_current_version() -> str:
    """Read the version from pyproject.toml at the project root."""
    pyproject = _PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0"
    text = pyproject.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def _fetch_latest_tag() -> str | None:
    """Fetch tags from origin and return the latest semver tag, or None."""
    try:
        subprocess.run(
            ["git", "fetch", "--tags", "--quiet"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Find the first tag that looks like a semver version (with optional leading 'v').
    for line in result.stdout.strip().splitlines():
        tag = line.strip()
        if re.match(r"^v?\d+\.\d+\.\d+", tag):
            return tag.lstrip("v")
    return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints."""
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts)


class UpdateChecker:
    """Checks for available updates by comparing pyproject.toml version to git tags.

    Runs an initial check on startup and then re-checks every ``interval``
    seconds (default: 6 hours).  Results are cached so the API endpoint
    can return them instantly.
    """

    def __init__(self, interval: float = 6 * 3600) -> None:
        self.interval = interval
        self.current: str = _read_current_version()
        self.latest: str | None = None
        self.update_available: bool = False
        self.last_checked: float = 0
        self._task: asyncio.Task[None] | None = None

    def check(self) -> dict[str, Any]:
        """Perform a synchronous update check (called from a thread)."""
        self.current = _read_current_version()
        latest = _fetch_latest_tag()
        self.last_checked = time.time()

        if latest is None:
            # Could not determine latest -- keep previous state.
            log.debug("update_check_skipped", reason="no_tags_found")
            return self.status()

        self.latest = latest
        self.update_available = _parse_version(latest) > _parse_version(self.current)
        log.info(
            "update_check_complete",
            current=self.current,
            latest=self.latest,
            update_available=self.update_available,
        )
        return self.status()

    def status(self) -> dict[str, Any]:
        """Return the last check result as a JSON-friendly dict."""
        return {
            "current": self.current,
            "latest": self.latest,
            "update_available": self.update_available,
            "last_checked": self.last_checked,
        }

    async def run(self) -> None:
        """Background loop: check immediately, then every ``self.interval`` seconds."""
        while True:
            try:
                await asyncio.to_thread(self.check)
            except Exception:
                log.exception("update_check_error")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        """Start the periodic background check task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        """Cancel the background task if running."""
        if self._task and not self._task.done():
            self._task.cancel()
