"""Background fetch scheduler: periodically runs git fetch --prune for all repos.

Detects new and deleted remote branches, broadcasts SSE events so the
dashboard can auto-refresh without manual intervention.
"""

from __future__ import annotations

import asyncio
import datetime
import subprocess
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.serve.logging_config import get_logger
log = get_logger(component="fetch_scheduler")

# Minimum seconds between fetches for the same repo (even if manually triggered).
_COOLDOWN_SECS = 60


def _git_fetch(anchor: Path) -> bool:
    """Run git fetch --prune (blocking). Returns True on success."""
    result = subprocess.run(
        ["git", "-C", str(anchor), "fetch", "--prune"],
        capture_output=True,
        timeout=60,
    )
    return result.returncode == 0


def _get_remote_branches(anchor: Path) -> set[str]:
    """Return the set of remote branch names (excluding infrastructure refs)."""
    from codehome.supervisor.ops.remote_branches import EXCLUDE_BRANCHES

    result = subprocess.run(
        ["git", "-C", str(anchor), "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return set()
    branches = set()
    for line in result.stdout.strip().splitlines():
        name = line.strip().removeprefix("origin/")
        if name and name not in EXCLUDE_BRANCHES:
            branches.add(name)
    return branches


def _get_branch_info(anchor: Path, branch: str) -> dict[str, str]:
    """Get committer metadata for a single remote branch."""
    ref = f"refs/remotes/origin/{branch}"
    fmt = "%(committername)%09%(committeremail:trim)%09%(subject)"
    result = subprocess.run(
        ["git", "-C", str(anchor), "for-each-ref", f"--format={fmt}", ref],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"committer_name": "", "committer_email": "", "subject": ""}
    parts = result.stdout.strip().split("\t", 2)
    if len(parts) < 3:
        return {"committer_name": parts[0] if parts else "", "committer_email": "", "subject": ""}
    return {
        "committer_name": parts[0],
        "committer_email": parts[1],
        "subject": parts[2],
    }


def _path_exists(anchor: Path) -> bool:
    """Check if a path exists (blocking helper for asyncio.to_thread)."""
    return anchor.exists()


class FetchScheduler:
    """Periodically fetches all repos and broadcasts branch change events."""

    def __init__(self, interval: float = 300) -> None:
        self.interval = interval
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # {repo: set of branch names} -- last known state per repo.
        self._known_branches: dict[str, set[str]] = {}
        # {repo: monotonic timestamp} -- cooldown tracking.
        self._last_fetch: dict[str, float] = {}
        # Protects _known_branches and _last_fetch from concurrent access
        # when manual triggers race with the background loop.
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """Start the periodic background fetch task."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Cancel the background task if running."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        """Background loop: fetch all repos on the configured interval."""
        # Initial fetch on startup.
        await self.fetch_all()
        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break
            await self.fetch_all()

    async def fetch_all(self) -> dict[str, dict[str, Any]]:
        """Fetch all configured repos. Returns {repo: diff_result}."""
        from codehome.supervisor.repo_config import load_repos as _load_repos

        repos = await asyncio.to_thread(_load_repos)
        results: dict[str, dict[str, Any]] = {}
        for repo_name in repos:
            try:
                diff = await self.fetch_repo(repo_name)
                if diff is not None:
                    results[repo_name] = diff
            except Exception:
                log.exception("fetch_repo_error", repo=repo_name)
        return results

    async def fetch_repo(self, repo_name: str, *, force: bool = False) -> dict[str, Any] | None:
        """Fetch a single repo. Returns diff dict or None if on cooldown.

        Set force=True to bypass the cooldown (still respects the minimum).
        Acquires _lock to prevent concurrent access to shared state when
        manual triggers race with the background loop.
        """
        async with self._lock:
            now = time.monotonic()
            last = self._last_fetch.get(repo_name, 0)
            if not force and (now - last < _COOLDOWN_SECS):
                return None

            from codehome.supervisor.paths import repo_anchor

            anchor = await asyncio.to_thread(repo_anchor, repo_name)
            if not await asyncio.to_thread(_path_exists, anchor):
                return None

            # Snapshot before fetch.
            before = await asyncio.to_thread(_get_remote_branches, anchor)

            # Run fetch.
            success = await asyncio.to_thread(_git_fetch, anchor)
            self._last_fetch[repo_name] = time.monotonic()

            if not success:
                log.warning("git_fetch_failed", repo=repo_name)

            # Get branches after fetch (whether it succeeded or not).
            after = await asyncio.to_thread(_get_remote_branches, anchor)

            # On first run, seed known branches without broadcasting diffs.
            if repo_name not in self._known_branches:
                self._known_branches[repo_name] = after
                log.info("fetch_initial", repo=repo_name, branch_count=len(after))
                ts = datetime.datetime.now(datetime.UTC).isoformat()
                await bus_fire(
                    Event(
                        name="remote.fetch.DONE",
                        payload={
                            "repo": repo_name,
                            "new_count": 0,
                            "deleted_count": 0,
                            "timestamp": ts,
                        },
                    )
                )
                return {"new": [], "deleted": [], "new_count": 0, "deleted_count": 0}

            # Compute diff against last known state.
            new_branches = after - before
            deleted_branches = before - after
            self._known_branches[repo_name] = after

            # Broadcast individual new branch events with committer info.
            new_details: list[dict[str, Any]] = []
            for branch in sorted(new_branches):
                info = await asyncio.to_thread(_get_branch_info, anchor, branch)
                payload: dict[str, object] = {
                    "repo": repo_name,
                    "branch": branch,
                    "committer_name": info["committer_name"],
                    "committer_email": info["committer_email"],
                    "subject": info["subject"],
                }
                new_details.append(payload)
                await bus_fire(Event(name="remote.branch.create", payload=payload))

            # Broadcast individual deleted branch events.
            deleted_list: list[str] = []
            for branch in sorted(deleted_branches):
                deleted_list.append(branch)
                await bus_fire(
                    Event(
                        name="remote.branch.delete",
                        payload={
                            "repo": repo_name,
                            "branch": branch,
                        },
                    )
                )

            # Summary event for UI refresh triggers.
            ts = datetime.datetime.now(datetime.UTC).isoformat()
            await bus_fire(
                Event(
                    name="remote.fetch.DONE",
                    payload={
                        "repo": repo_name,
                        "new_count": len(new_branches),
                        "deleted_count": len(deleted_branches),
                        "timestamp": ts,
                    },
                )
            )

            if new_branches or deleted_branches:
                log.info(
                    "fetch_diff",
                    repo=repo_name,
                    new=len(new_branches),
                    deleted=len(deleted_branches),
                )

            return {
                "new": new_details,
                "deleted": deleted_list,
                "new_count": len(new_branches),
                "deleted_count": len(deleted_branches),
            }

    def cooldown_remaining(self, repo_name: str) -> float:
        """Seconds until this repo can be fetched again (0 if ready)."""
        last = self._last_fetch.get(repo_name, 0)
        remaining = _COOLDOWN_SECS - (time.monotonic() - last)
        return max(0, remaining)


# Singleton instance created at import; started by the server lifespan.
fetch_scheduler = FetchScheduler()
