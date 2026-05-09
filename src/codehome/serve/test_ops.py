"""Test suite resolution: resolve test file paths and Supabase env for test runs.

Extracted from routers/services.py so route handlers stay thin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codehome.serve.env import resolve_supabase_env

if TYPE_CHECKING:
    from pathlib import Path


def resolve_test_file(suite: str) -> Path | None:
    """Resolve a test suite name to a test file path, or None if not found.

    Checks repo-level suites (*.tests.json) and then branch-level (tests.json).
    """
    from codehome.service_protocols import ProjectLayout
    from codehome.state.service_registry import services

    layout = services.get_typed("core.layout", ProjectLayout)
    if layout is None:
        return None

    tests_dir = layout.repo_dir("bag") / "tests"

    if suite.endswith(".tests.json"):
        test_file = tests_dir / suite
    else:
        repo_file = tests_dir / f"{suite}.tests.json"
        branch_file = layout.repo_dir("bag") / "branches" / suite / "tests.json"
        test_file = repo_file if repo_file.exists() else branch_file

    return test_file if test_file.exists() else None


def resolve_test_env(branch: str) -> dict[str, str]:
    """Build Supabase env overrides for test execution.

    Returns an empty dict if Supabase is not running for the branch.
    """
    sb_env = resolve_supabase_env(branch)
    if not sb_env:
        return {}
    return {
        "SUPABASE_URL": f"http://127.0.0.1:{sb_env['api_port']}",
        "SUPABASE_ANON_KEY": str(sb_env["anon_key"]),
        "SUPABASE_SERVICE_ROLE_KEY": str(sb_env["service_role_key"]),
    }


def tests_dir() -> Path:
    """Return the repo-level tests directory."""
    from codehome.service_protocols import ProjectLayout
    from codehome.state.service_registry import services

    layout = services.get_typed("core.layout", ProjectLayout)
    if layout is None:
        raise RuntimeError("ProjectLayout not registered (core plugin not loaded)")
    return layout.repo_dir("bag") / "tests"
