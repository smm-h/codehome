"""Async runner: execute all checks in a group with dependency-aware scheduling.

The runner is the execution engine of the check framework.  Given a
group name, it:

1. Pulls all checks for that group from the registry.
2. Validates ``depends_on`` references (every dependency must exist
   within the same group).
3. Topologically sorts checks into *waves* -- checks within a wave
   run concurrently via ``asyncio.gather``; waves execute sequentially.
4. Skips checks whose non-advisory dependencies failed.
5. Enforces per-check timeouts.

Relation to :mod:`codehome.tests.framework.runner`: the test runner
executes *step sequences* (linear, stateful) while this runner executes
*independent checks* (DAG-scheduled, stateless).  The API surface is
intentionally simpler.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import TYPE_CHECKING

from codehome.checks.registry import CheckContext, CheckRegistry, _registry
from codehome.checks.result import CheckResult, GroupReport

if TYPE_CHECKING:
    from codehome.checks.registry import CheckEntry


# -- Subprocess helper -------------------------------------------------------


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill subprocess and its entire process group (Posix) or just the process (Windows).

    Called from finally blocks to prevent orphaned child processes when a
    check times out or is cancelled.  Uses os.killpg on Posix to kill the
    whole group spawned via start_new_session=True.
    """
    if proc.returncode is not None:
        return  # already exited
    pid = proc.pid
    if pid is None:
        return
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: int | None = None,
) -> tuple[int, str, str]:
    """Run a command via ``asyncio.create_subprocess_exec``.

    Returns ``(returncode, stdout, stderr)`` with decoded text output.
    This is a utility for check functions that need to shell out to
    external tools (ruff, mypy, tsc, etc.).

    The subprocess is spawned in its own process group (``start_new_session=True``
    on Posix) so that on timeout or cancellation the entire child tree can be
    killed via ``os.killpg``, preventing orphaned processes.

    *timeout_s* is in seconds; ``None`` means no timeout (the caller's
    own ``asyncio.wait_for`` wrapper provides the outer bound).
    """
    # start_new_session=True creates a new process group on Posix so
    # we can kill all descendants with os.killpg on cleanup.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_s,
        )
    except (TimeoutError, asyncio.CancelledError):
        _kill_process_tree(proc)
        try:
            await proc.wait()
        except ProcessLookupError:
            pass
        raise
    finally:
        # Defensive: if communicate() raised any other exception or the
        # process is still alive for any reason, clean it up.
        _kill_process_tree(proc)
    # After communicate(), returncode is always set; assert for mypy.
    assert proc.returncode is not None
    rc: int = proc.returncode
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    # Negative return codes mean the process was killed by a signal.
    # Include signal info in stderr so callers see why it died.
    if rc < 0:
        sig_num = -rc
        sig_name = signal.Signals(sig_num).name if sig_num in signal.Signals._value2member_map_ else f"signal {sig_num}"
        stderr = f"[killed by {sig_name} ({sig_num})]\n{stderr}"
    return (rc, stdout, stderr)


# -- Public entry point ------------------------------------------------------


async def run_group(
    group: str,
    *,
    registry: CheckRegistry | None = None,
    ctx: CheckContext | None = None,
) -> GroupReport:
    """Execute every check in *group* and return a :class:`GroupReport`.

    Checks are topologically sorted into dependency waves and executed
    concurrently within each wave.  A check is skipped if any of its
    non-advisory dependencies did not pass.

    If *registry* is ``None``, the module-level singleton is used.
    If *ctx* is ``None``, a default context rooted at the current
    working directory is created.
    """
    reg = registry if registry is not None else _registry

    if ctx is None:
        ctx = CheckContext(root=Path.cwd())

    entries = reg.group(group)
    if not entries:
        return GroupReport(group=group, results=())

    # Validate depends_on references.
    names_in_group = {e.name for e in entries}
    for entry in entries:
        for dep in entry.depends_on:
            if dep not in names_in_group:
                msg = f"check {entry.name!r} depends on {dep!r} which is not registered in group {group!r}"
                raise ValueError(msg)

    # Topo-sort into waves.
    waves = _topo_sort(entries)

    # Execute waves sequentially, checks within a wave concurrently.
    completed: dict[str, CheckResult] = {}
    for wave in waves:
        coros = [_run_check(entry, ctx, completed, reg) for entry in wave]
        results = await asyncio.gather(*coros)
        for result in results:
            completed[result.name] = result

    # Build the final results tuple in registration order (sorted by name)
    # so output is deterministic.
    ordered = tuple(completed[e.name] for e in entries)

    # Embed advisory names so callers don't need to recompute them.
    advisory_names = frozenset(e.name for e in entries if e.advisory)

    # Embed source tags so the formatter can distinguish core vs extension
    # checks without needing the registry.
    source_map = MappingProxyType({e.name: e.source for e in entries})

    return GroupReport(group=group, results=ordered, advisory_names=advisory_names, source_map=source_map)


# -- Topological sort -------------------------------------------------------


def _topo_sort(entries: tuple[CheckEntry, ...]) -> list[list[CheckEntry]]:
    """Sort checks into dependency waves.

    Wave 0 contains checks with no dependencies.  Wave N contains
    checks whose dependencies are all in waves 0..N-1.

    Raises ``RuntimeError`` if a cycle is detected.
    """
    by_name: dict[str, CheckEntry] = {e.name: e for e in entries}
    assigned: dict[str, int] = {}
    remaining = set(by_name.keys())
    wave_num = 0

    while remaining:
        # Find checks whose dependencies are all already assigned.
        ready = [name for name in remaining if all(dep in assigned for dep in by_name[name].depends_on)]

        if not ready:
            msg = f"dependency cycle detected among checks: {sorted(remaining)}"
            raise RuntimeError(msg)

        for name in ready:
            assigned[name] = wave_num
            remaining.remove(name)
        wave_num += 1

    # Group entries by wave number.
    max_wave = max(assigned.values()) if assigned else 0
    waves: list[list[CheckEntry]] = [[] for _ in range(max_wave + 1)]
    for name, wave_idx in assigned.items():
        waves[wave_idx].append(by_name[name])

    # Sort within each wave by name for deterministic order.
    for wave in waves:
        wave.sort(key=lambda e: e.name)

    return waves


# -- Single check execution -------------------------------------------------


async def _run_check(
    entry: CheckEntry,
    ctx: CheckContext,
    completed: dict[str, CheckResult],
    registry: CheckRegistry,
) -> CheckResult:
    """Execute a single check, handling dependencies, timeouts, and errors.

    Dependency rule: if any non-advisory dependency has outcome != "pass",
    this check is skipped.  Advisory dependencies are allowed to fail
    without blocking downstream checks.
    """
    # Check dependencies -- skip if any required (non-advisory) dep failed.
    for dep_name in entry.depends_on:
        dep_result = completed.get(dep_name)
        if dep_result is not None and dep_result.outcome != "pass":
            # Look up whether the dependency itself is advisory.
            # Advisory deps are allowed to fail without blocking.
            dep_entry = registry.get(dep_name)
            if dep_entry is not None and dep_entry.advisory:
                continue
            return CheckResult(
                name=entry.name,
                outcome="skip",
                duration_ms=0.0,
                message=f"dependency {dep_name!r} failed",
            )

    # Resolve per-check working directory from entry.cwd (relative to
    # ctx.root).  Each check gets its own ctx copy so parallel checks
    # don't share mutable state.
    resolved_cwd = ctx.root / entry.cwd
    check_ctx = CheckContext(
        root=ctx.root,
        cwd=resolved_cwd,
        staged_files=ctx.staged_files,
        repo=ctx.repo,
        branch=ctx.branch,
        branch_dir=ctx.branch_dir,
    )

    start = perf_counter()
    try:
        result = await asyncio.wait_for(entry.fn(check_ctx), timeout=entry.timeout)
    except (TimeoutError, asyncio.CancelledError):
        duration_ms = (perf_counter() - start) * 1000
        # The check's run_command should already have killed its children
        # via start_new_session + _kill_process_tree, but we can't assume
        # every check uses run_command.  Nothing more to clean up at this
        # layer -- run_command's finally block is the primary defence.
        return CheckResult(
            name=entry.name,
            outcome="timeout",
            duration_ms=duration_ms,
            message=f"killed after {entry.timeout}s timeout",
        )
    except Exception as exc:
        duration_ms = (perf_counter() - start) * 1000
        return CheckResult(
            name=entry.name,
            outcome="fail",
            duration_ms=duration_ms,
            message=str(exc),
        )
    else:
        duration_ms = (perf_counter() - start) * 1000
        # The check function returned a CheckResult -- use it, but
        # override duration_ms with our own measurement for accuracy.
        return CheckResult(
            name=result.name,
            outcome=result.outcome,
            duration_ms=duration_ms,
            message=result.message,
            fix=result.fix,
        )
