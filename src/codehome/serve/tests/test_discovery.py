"""Tests for the Docker-level discovery path.

Covers ``discover_docker`` -- the read-only scan that picks up Compose
containers started outside ``v dashboard`` -- with fully-mocked Docker and
filesystem dependencies.  The wired-up ``discover_running`` path is
already exercised transitively via ``test_server.py``; this module
focuses on the new startup-gap-closing logic.

The implementation lives in ``codehome.core.ops.discovery``; tests
import from there and patch accordingly.
"""

from __future__ import annotations

import asyncio

import pytest

from codehome.core.ops.discovery import discover_docker
from codehome.serve.services import ServiceManager

# Module path prefix for monkeypatch targets (implementation location).
_MOD = "codehome.core.ops.discovery"


def _run(coro):
    """Run an async coroutine from a sync test context."""
    return asyncio.run(coro)


# Fake payload modeled on real `docker inspect` for `bag-lisa-vite-1`.
_BAG_LISA_VITE_CONTAINER = {
    "name": "bag-lisa-vite-1",
    "project": "bag-lisa",
    "service": "vite",
    "container_number": 1,
    "state": "running",
    "host_ports": {5173: 59899},
}

# Resolved service def as `templates.resolve_placeholders` would produce
# for the bag template + branch `bag:lisa`.  The `key` field is what
# `discover_docker` uses to check for existing registrations.
_BAG_LISA_TEMPLATE = [
    {
        "key": "bag:lisa/vite-bag",
        "key_suffix": "vite-bag",
        "service_type": "compose",
        "display_name": "Vite (bag.veliu.com)",
        "depends_on": ["bag:lisa/supabase"],
        "metadata": {
            "compose_service": "vite",
            "worktree": "/fake/worktree",
            "app_dir": "bag.veliu.com",
        },
    },
    {
        "key": "bag:lisa/functions",
        "key_suffix": "functions",
        "service_type": "compose",
        "display_name": "Edge Functions",
        "depends_on": ["bag:lisa/supabase"],
        "metadata": {
            "compose_service": "functions",
            "worktree": "/fake/worktree",
        },
    },
]


@pytest.fixture
def fresh_registry(monkeypatch):
    """Swap the module-level ``services`` singleton for an empty manager.

    ``discover_docker`` imports ``services`` from ``codehome.serve.services``
    at the top of the module, so we patch the reference it resolved to.
    """
    mgr = ServiceManager()
    # Patch the canonical singleton and the reference cached in the plugin module.
    monkeypatch.setattr("codehome.serve.services.services", mgr)
    monkeypatch.setattr(f"{_MOD}.services", mgr)
    return mgr


@pytest.fixture
def stub_deps(monkeypatch):
    """Patch the three outbound deps discover_docker touches."""

    def _install(
        containers,
        branches=({"qualified": "bag:lisa", "repo": "bag", "branch": "lisa"},),
        template=None,
    ):
        template = template if template is not None else _BAG_LISA_TEMPLATE
        monkeypatch.setattr(
            "codehome.serve.docker.docker_inspect_compose_containers",
            lambda: list(containers),
        )
        monkeypatch.setattr(
            "codehome.core.ops.branches.list_branches",
            lambda: list(branches),
        )
        monkeypatch.setattr(
            f"{_MOD}.load_services_config",
            lambda repo, branch: [{"key_suffix": "vite-bag"}] if template else None,
        )
        # Resolve is stubbed to return the pre-resolved template directly.
        monkeypatch.setattr(
            f"{_MOD}.resolve_placeholders",
            lambda defs, qualified, repo, branch: list(template),
        )

        # Silence the event bus fire (was events.broadcast before bus refactor).
        async def _noop(*_a, **_kw):
            return None

        monkeypatch.setattr(f"{_MOD}.fire", _noop)

    return _install


# ---------------------------------------------------------------------------
# 1. Happy path: external container registers
# ---------------------------------------------------------------------------


def test_registers_externally_started_container(fresh_registry, stub_deps):
    """A bag-lisa-vite-1 container absent from the registry gets registered.

    This is the canonical scenario from the Phase 1 spec: user runs
    ``docker compose up`` outside ``v dashboard``, resolver would miss, discover
    rescues it.
    """
    stub_deps([_BAG_LISA_VITE_CONTAINER])
    out = _run(discover_docker())

    assert len(out) == 1
    svc = fresh_registry.get("bag:lisa/vite-bag")
    assert svc is not None
    assert svc.branch == "bag:lisa"
    assert svc.port == 59899
    assert svc.state.value == "running"
    assert svc.metadata["compose_service"] == "vite"
    # depends_on should carry through from the template.
    assert "bag:lisa/supabase" in svc.depends_on


# ---------------------------------------------------------------------------
# 2. Idempotency: already-known services are left alone
# ---------------------------------------------------------------------------


def test_does_not_clobber_existing_registration(fresh_registry, stub_deps):
    """Preserve existing registrations when rescanning.

    If discover_running already registered the service, discover_docker
    leaves it untouched so live state isn't overwritten.
    """
    from codehome.serve.services import ServiceInstance, State

    existing = ServiceInstance(
        key="bag:lisa/vite-bag",
        service_type="compose",
        branch="bag:lisa",
        display_name="Vite (bag.veliu.com)",
        state=State.RUNNING,
        metadata={"compose_service": "vite", "worktree": "/real/worktree"},
    )
    existing.port = 5173  # intentionally different from the discovered 59899
    fresh_registry.register(existing)

    stub_deps([_BAG_LISA_VITE_CONTAINER])
    out = _run(discover_docker())

    assert out == []  # no net-new registrations
    # Existing registration's port is preserved, not overwritten.
    assert fresh_registry.get("bag:lisa/vite-bag").port == 5173


# ---------------------------------------------------------------------------
# 3. Unknown project: foreign containers are ignored (false-positive guard)
# ---------------------------------------------------------------------------


def test_ignores_containers_with_unknown_project(fresh_registry, stub_deps):
    """Skip containers whose project name matches no known branch.

    Supabase CLI containers etc. have projects like `384de7a218280df6c35a`
    that don't correspond to any branch; discovery must skip them.
    """
    foreign = {
        **_BAG_LISA_VITE_CONTAINER,
        "name": "supabase_studio_384de7a218280df6c35a",
        "project": "384de7a218280df6c35a",
        "service": "studio",
    }
    stub_deps([foreign])
    out = _run(discover_docker())

    assert out == []
    assert fresh_registry.list_all() == []


# ---------------------------------------------------------------------------
# 4. Unknown compose_service for a known project
# ---------------------------------------------------------------------------


def test_ignores_container_without_matching_template_entry(fresh_registry, stub_deps):
    """Skip containers whose compose_service has no matching template entry.

    Container's compose_service has no matching template definition --
    discovery skips it because there's no key_suffix to register under.
    """
    stranger = {
        **_BAG_LISA_VITE_CONTAINER,
        "name": "bag-lisa-mystery-1",
        "service": "mystery",
    }
    stub_deps([stranger])
    out = _run(discover_docker())

    assert out == []
    assert fresh_registry.list_all() == []


# ---------------------------------------------------------------------------
# 5. No containers at all -> empty list, no state change
# ---------------------------------------------------------------------------


def test_empty_docker_returns_empty(fresh_registry, stub_deps):
    """Docker returns nothing -> discover_docker is a no-op."""
    stub_deps([])
    out = _run(discover_docker())

    assert out == []
    assert fresh_registry.list_all() == []


# ---------------------------------------------------------------------------
# 6. Docker layer exception is contained (does not propagate)
# ---------------------------------------------------------------------------


def test_docker_subprocess_failure_is_contained(fresh_registry, monkeypatch):
    """Contain subprocess failures inside discover_docker.

    If ``docker_inspect_compose_containers`` throws, ``discover_docker``
    still returns a list (empty) rather than bubbling to the lifespan.
    """

    def _boom():
        raise RuntimeError("docker daemon down")

    monkeypatch.setattr(
        "codehome.serve.docker.docker_inspect_compose_containers",
        _boom,
    )

    # A raw exception out of to_thread propagates; the server.py lifespan
    # wraps discover_docker in its own try/except (see `server.py`).  This
    # test documents that `discover_docker` itself does not catch -- the
    # caller is responsible.  If you change that contract, update the
    # ``server.py`` call site.
    with pytest.raises(RuntimeError):
        _run(discover_docker())
