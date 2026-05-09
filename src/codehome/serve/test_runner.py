"""Test suite discovery and Playwright execution."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from typing import TYPE_CHECKING, Any

from codehome.bus import Event, fire

if TYPE_CHECKING:
    from pathlib import Path


def discover_suites(repo: str = "bag") -> list[dict[str, Any]]:
    """Discover all test suites: repo-level *.tests.json + per-branch tests.json."""
    from codehome.service_protocols import ProjectLayout
    from codehome.state.service_registry import services

    layout = services.get_typed("core.layout", ProjectLayout)
    if layout is None:
        return []

    suites = []

    # Repo-level suites (*.tests.json convention).
    tests_dir = layout.repo_dir(repo) / "tests"
    if tests_dir.exists():
        for f in sorted(tests_dir.glob("*.tests.json")):
            data = _load_json(f)
            test_count = len(data.get("tests", [])) if data else 0  # type: ignore[arg-type]
            suites.append(
                {
                    "suite": f.name.removesuffix(".tests.json"),
                    "path": str(f),
                    "test_count": test_count,
                    "type": "repo",
                },
            )

    # Per-branch suites (tests.json).
    branches_dir = layout.repo_branches(repo)
    if branches_dir.exists():
        for branch_dir in sorted(branches_dir.iterdir()):
            tests_file = branch_dir / "tests.json"
            if tests_file.exists():
                data = _load_json(tests_file)
                test_count = len(data.get("tests", [])) if data else 0  # type: ignore[arg-type]
                if test_count > 0:
                    suites.append(
                        {
                            "suite": branch_dir.name,
                            "path": str(tests_file),
                            "test_count": test_count,
                            "type": "branch",
                        },
                    )

    return suites


async def run_tests(
    branch: str,
    suite: str,
    tests_dir: Path,
    test_file: Path,
    env_overrides: dict[str, str] | None = None,
    pattern: str | None = None,
    headed: bool = False,
) -> str:
    """Run a test suite via Playwright. Returns a run_id. Results stream via SSE."""
    run_id = str(uuid.uuid4())[:8]

    async def _execute() -> None:
        cmd = ["npx", "playwright", "test", str(test_file)]
        if pattern:
            cmd += ["--grep", pattern]
        if headed:
            cmd += ["--headed"]

        env = {**os.environ, **(env_overrides or {})}

        proc = await asyncio.to_thread(
            lambda: subprocess.Popen(
                cmd,
                cwd=str(tests_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ),
        )
        _active_procs[run_id] = proc

        try:
            loop = asyncio.get_event_loop()
            assert proc.stdout is not None
            while True:
                line = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    break
                await fire(
                    Event(
                        name="tests.output",
                        payload={
                            "run_id": run_id,
                            "line": line,
                            "stream": "stdout",
                        },
                    )
                )

            await asyncio.to_thread(proc.wait)
            await fire(
                Event(
                    name="tests.run.DONE",
                    payload={
                        "run_id": run_id,
                        "passed": proc.returncode == 0,
                    },
                )
            )
        finally:
            _active_procs.pop(run_id, None)

    # Prevent GC of the background task (asyncio only holds weak references).
    task = asyncio.create_task(_execute())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return run_id


def stop_test_run(run_id: str) -> bool:
    """Stop an active test run. Returns True if a process was killed."""
    proc = _active_procs.get(run_id)
    if not proc:
        return False
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return True


# Strong references to background tasks to prevent garbage collection.
_background_tasks: set[asyncio.Task[None]] = set()
# Map of run_id -> Popen for cancellation.
_active_procs: dict[str, subprocess.Popen[str]] = {}


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError, FileNotFoundError):
        return None
