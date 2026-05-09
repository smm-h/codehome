"""Tests for codehome.serve.env — Supabase environment resolution."""

from __future__ import annotations

import pytest

from codehome.serve.env import resolve_supabase_env
from codehome.serve.services import ServiceManager, State


@pytest.fixture
def env_mgr(monkeypatch):
    """Patch the module-level `services` singleton with a fresh manager."""
    import codehome.serve.env as env_mod

    mgr = ServiceManager()
    monkeypatch.setattr(env_mod, "services", mgr)
    return mgr


# ---------------------------------------------------------------------------
# 1. No supabase service registered
# ---------------------------------------------------------------------------


class TestNoService:
    def test_returns_none_when_no_service_registered(self, env_mgr):
        result = resolve_supabase_env("mybranch")
        assert result is None

    def test_returns_none_for_different_branch(self, env_mgr, make_service):
        # Register supabase for a different branch.
        svc = make_service(
            key="other/supabase",
            service_type="supabase",
            branch="other",
            state=State.STARTING,
        )
        svc.transition(State.RUNNING)
        svc.metadata = {"connection": {"api_url": "http://127.0.0.1:54321"}}
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result is None


# ---------------------------------------------------------------------------
# 2. Service exists but is not RUNNING
# ---------------------------------------------------------------------------


class TestNotRunning:
    @pytest.mark.parametrize("state", [State.STOPPED, State.STARTING, State.STOPPING])
    def test_returns_none_for_non_running_state(self, env_mgr, make_service, state):
        svc = make_service(
            key="mybranch/supabase",
            service_type="supabase",
            branch="mybranch",
            state=state,
        )
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result is None

    def test_returns_none_for_failed_state(self, env_mgr, make_service):
        svc = make_service(
            key="mybranch/supabase",
            service_type="supabase",
            branch="mybranch",
            state=State.STARTING,
        )
        svc.transition(State.FAILED, error="crash")
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result is None


# ---------------------------------------------------------------------------
# 3. RUNNING but no connection metadata
# ---------------------------------------------------------------------------


class TestRunningNoConnection:
    def test_returns_none_when_metadata_empty(self, env_mgr, make_service):
        svc = make_service(
            key="mybranch/supabase",
            service_type="supabase",
            branch="mybranch",
            state=State.STARTING,
        )
        svc.transition(State.RUNNING)
        # metadata is {} by default — no "connection" key.
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result is None

    def test_returns_none_when_connection_key_is_empty_dict(self, env_mgr, make_service):
        svc = make_service(
            key="mybranch/supabase",
            service_type="supabase",
            branch="mybranch",
            state=State.STARTING,
            metadata={"connection": {}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result is None


# ---------------------------------------------------------------------------
# 4. RUNNING with connection metadata — happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_correct_dict(self, env_mgr, make_service):
        svc = make_service(
            key="mybranch/supabase",
            service_type="supabase",
            branch="mybranch",
            state=State.STARTING,
            metadata={
                "connection": {
                    "api_url": "http://127.0.0.1:54321",
                    "anon_key": "eyJ0eXAi.anon",
                    "service_role_key": "eyJ0eXAi.service",
                },
            },
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result == {
            "api_port": 54321,
            "anon_key": "eyJ0eXAi.anon",
            "service_role_key": "eyJ0eXAi.service",
        }

    def test_missing_keys_default_to_empty_string(self, env_mgr, make_service):
        svc = make_service(
            key="mybranch/supabase",
            service_type="supabase",
            branch="mybranch",
            state=State.STARTING,
            metadata={
                "connection": {
                    "api_url": "http://127.0.0.1:54321",
                    # anon_key and service_role_key intentionally missing.
                },
            },
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("mybranch")
        assert result["anon_key"] == ""
        assert result["service_role_key"] == ""

    def test_branch_name_with_slashes(self, env_mgr, make_service):
        """Branch names like 'bag:feat/auth' produce key 'bag:feat/auth/supabase'."""
        branch = "bag:feat/auth"
        svc = make_service(
            key=f"{branch}/supabase",
            service_type="supabase",
            branch=branch,
            state=State.STARTING,
            metadata={
                "connection": {
                    "api_url": "http://127.0.0.1:60000",
                    "anon_key": "key-a",
                    "service_role_key": "key-s",
                },
            },
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env(branch)
        assert result is not None
        assert result["api_port"] == 60000


# ---------------------------------------------------------------------------
# 5. Port extraction from api_url
# ---------------------------------------------------------------------------


class TestPortExtraction:
    def test_standard_url(self, env_mgr, make_service):
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://127.0.0.1:54321"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 54321

    def test_non_default_port(self, env_mgr, make_service):
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://127.0.0.1:12345"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 12345

    def test_localhost_url(self, env_mgr, make_service):
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://localhost:9999"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 9999


# ---------------------------------------------------------------------------
# 6. Port extraction fallback to 54321
# ---------------------------------------------------------------------------


class TestPortFallback:
    def test_fallback_when_api_url_missing(self, env_mgr, make_service):
        """No api_url key at all -> rsplit on empty string -> ValueError -> 54321."""
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"anon_key": "k"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 54321

    def test_fallback_when_api_url_empty(self, env_mgr, make_service):
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": ""}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 54321

    def test_fallback_when_port_is_not_numeric(self, env_mgr, make_service):
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://127.0.0.1:notaport"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 54321


# ---------------------------------------------------------------------------
# 7. Port extraction with malformed URLs
# ---------------------------------------------------------------------------


class TestMalformedUrl:
    def test_url_without_port(self, env_mgr, make_service):
        """URL like 'http://localhost' has no colon-port suffix -> ValueError."""
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://localhost"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        # rsplit(":", 1)[-1] gives "//localhost" -> int() fails -> fallback.
        assert result["api_port"] == 54321

    def test_url_with_trailing_slash(self, env_mgr, make_service):
        """URL like 'http://127.0.0.1:54321/' -> rsplit gives '54321/' -> int fails."""
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://127.0.0.1:54321/"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        # "54321/" is not a valid int -> falls back to 54321 by coincidence.
        assert result["api_port"] == 54321

    def test_url_with_path(self, env_mgr, make_service):
        """URL like 'http://127.0.0.1:54321/v1/rest' -> rsplit gives path part."""
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "http://127.0.0.1:54321/v1/rest"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        # rsplit(":", 1)[-1] = "54321/v1/rest" -> int fails -> fallback.
        assert result["api_port"] == 54321

    def test_completely_garbage_url(self, env_mgr, make_service):
        svc = make_service(
            key="b/supabase",
            service_type="supabase",
            branch="b",
            state=State.STARTING,
            metadata={"connection": {"api_url": "not-a-url-at-all"}},
        )
        svc.transition(State.RUNNING)
        env_mgr.register(svc)
        result = resolve_supabase_env("b")
        assert result["api_port"] == 54321
