"""Shared subprocess utilities for the serve layer.

Provides safe wrappers around git and gh CLI calls with consistent
timeout handling, error recovery, and logging. Replaces the duplicated
_run() / _gh() / gh_repo() patterns across multiple serve modules.
"""

import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from codehome.serve.logging_config import get_logger

# Type alias for the line callback used by streaming subprocess helpers.
# Called with (stream: "stdout"|"stderr", line: str) for each output line.
OutputCallback = Callable[[str, str], None]

logger = get_logger(component="subprocess_utils")


def run_git(wt: Path, *args: str, timeout: int = 30) -> tuple[str, str, int]:
    """Run a git command in a worktree with a timeout.

    Returns (stdout, stderr, returncode). Never raises on git failure.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(wt), *args],
            capture_output=True,
            timeout=timeout,
        )
        return (
            result.stdout.decode("utf-8", errors="replace").rstrip("\n"),
            result.stderr.decode("utf-8", errors="replace").rstrip("\n"),
            result.returncode,
        )
    except subprocess.TimeoutExpired:
        return "", "git command timed out", 1
    except OSError as e:
        return "", str(e), 1


def run_gh(*args: str, timeout: int = 30, gh_token: str | None = None) -> tuple[str, str, int]:
    """Run a gh CLI command with a timeout.

    When *gh_token* is provided it is injected as ``GH_TOKEN`` in the
    subprocess environment so the ``gh`` CLI authenticates as that user.

    Returns (stdout, stderr, returncode). Never raises on failure.
    """
    env: dict[str, str] | None = None
    if gh_token:
        env = os.environ.copy()
        env["GH_TOKEN"] = gh_token

    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except FileNotFoundError:
        return "", "gh CLI not found", 1
    except subprocess.TimeoutExpired:
        return "", "gh command timed out", 1
    except OSError as e:
        return "", str(e), 1


def safe_gh_repo(repo: str) -> str | None:
    """Resolve owner/repo string, returning None instead of exiting.

    Wraps ``gh_repo()`` from ``codehome.supervisor.git`` which calls ``sys.exit()``
    on failure (via ``die()``). Returns the owner/repo string on success
    or None if resolution fails.
    """
    from codehome.supervisor.git import gh_repo

    try:
        return gh_repo(repo)
    except SystemExit:
        logger.warning("gh_repo_failed", repo=repo)
        return None


def parse_numstat_line(line: str) -> tuple[int, int, str] | None:
    """Parse a single git diff --numstat output line.

    Handles binary files where insertions/deletions are "-".
    Returns (insertions, deletions, filepath) or None for unparseable lines.
    """
    parts = line.split("\t", 2)
    if len(parts) != 3:
        return None
    ins_str, del_str, filepath = parts
    ins = int(ins_str) if ins_str != "-" else 0
    dels = int(del_str) if del_str != "-" else 0
    return ins, dels, filepath


def run_streaming(
    cmd: list[str],
    callback: OutputCallback,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> tuple[bool, str]:
    """Run a subprocess with line-by-line output streaming.

    Spawns the process with Popen, reads stdout/stderr in separate daemon
    threads, and invokes *callback(stream, line)* for each line. Returns
    (success, message) following the same contract as compose_up/compose_down.

    ANSI color codes are preserved in the output lines. If *env* is provided,
    it is merged with os.environ.
    """
    merged_env = {**os.environ, **(env or {})} if env is not None else None
    collected_stderr: list[str] = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=merged_env,
        )
    except FileNotFoundError as e:
        return False, str(e)

    def _drain(stream: str, pipe: object) -> None:
        """Read lines from a pipe and forward them to the callback."""
        import io

        assert isinstance(pipe, io.TextIOWrapper)
        for raw_line in pipe:
            line = raw_line.rstrip("\n\r")
            if not line:
                continue
            if stream == "stderr":
                collected_stderr.append(line)
            callback(stream, line)

    stdout_thread = threading.Thread(target=_drain, args=("stdout", proc.stdout), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=("stderr", proc.stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        return False, f"timed out after {timeout}s"
    finally:
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

    if proc.returncode != 0:
        msg = "\n".join(collected_stderr).strip() or f"exit {proc.returncode}"
        return False, msg
    return True, "OK"
