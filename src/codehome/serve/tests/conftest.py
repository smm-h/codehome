"""Shared fixtures for serve tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from codehome.serve.services import ServiceInstance, ServiceManager, State
from codehome.state.service_registry import services


# ---------------------------------------------------------------------------
# MinimalLayout -- shared ProjectLayout stub for all serve tests
# ---------------------------------------------------------------------------


class _MinimalLayout:
    """Minimal ProjectLayout implementation backed by real tmp_path directories.

    Only methods actually called by serve/ code are implemented.
    Everything else raises NotImplementedError so missing coverage is obvious.
    """

    def __init__(self, base: Path) -> None:
        self._base = base
        self._repos_dir = base / "repos"
        self._repos_dir.mkdir(exist_ok=True)

    @property
    def REPOS_DIR(self) -> Path:
        return self._repos_dir

    def repo_dir(self, repo: str) -> Path:
        d = self._repos_dir / repo
        d.mkdir(exist_ok=True)
        return d

    def branch_dir(self, repo: str, branch: str) -> Path:
        d = self._repos_dir / repo / "branches" / branch
        d.mkdir(parents=True, exist_ok=True)
        return d

    def worktree_path(self, repo: str, branch: str) -> Path:
        d = self._repos_dir / repo / "branches" / branch / "worktree"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def repo_branches(self, repo: str) -> Path:
        d = self._repos_dir / repo / "branches"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def repo_anchor(self, repo: str) -> Path:
        raise NotImplementedError

    def base_ref(self, repo: str, branch: str) -> str:
        raise NotImplementedError

    def prod_ref(self, repo: str) -> str:
        raise NotImplementedError

    def staging_worktree(self, repo: str) -> Path:
        raise NotImplementedError

    def branch_name_from_wt(self, wt_path: Path) -> str:
        raise NotImplementedError

    def tests_file(self, repo: str, branch: str) -> Path:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _register_minimal_layout(tmp_path: Path):
    """Register a MinimalLayout as core.layout for every serve test.

    Saves and restores any previously-registered core.layout entry so that
    importing the real app (which registers SupervisorLayout) doesn't clash.
    """
    prev_entry = services._services.pop("core.layout", None)
    layout = _MinimalLayout(tmp_path)
    services.register("core.layout", layout, plugin="test", description="test layout")
    yield layout
    services._services.pop("core.layout", None)
    if prev_entry is not None:
        services._services["core.layout"] = prev_entry


# ---------------------------------------------------------------------------
# General-purpose fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mgr():
    """Fresh ServiceManager instance (not the singleton)."""
    return ServiceManager()


@pytest.fixture
def make_service():
    """Create a ServiceInstance with defaults."""

    def _make(
        key="test/svc",
        service_type="compose",
        branch="test",
        display_name="Test",
        state=State.STOPPED,
        depends_on=None,
        metadata=None,
    ):
        return ServiceInstance(
            key=key,
            service_type=service_type,
            branch=branch,
            display_name=display_name,
            depends_on=depends_on or [],
            state=state,
            metadata=metadata or {},
        )

    return _make
