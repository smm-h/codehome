"""Tests for ServiceInstance state machine and ServiceManager registry."""

import time
from unittest.mock import patch

import pytest

from codehome.serve.services import _TRANSITIONS, State

# ---------------------------------------------------------------------------
# 1. Valid state transitions
# ---------------------------------------------------------------------------

# Build a flat list of every (from, to) pair defined in _TRANSITIONS.
_VALID_PAIRS = [(src, dst) for src, dsts in _TRANSITIONS.items() for dst in dsts]


@pytest.mark.parametrize(("src", "dst"), _VALID_PAIRS, ids=[f"{s.value}->{d.value}" for s, d in _VALID_PAIRS])
def test_valid_transition(make_service, src, dst):
    svc = make_service(state=src)
    svc.transition(dst)
    assert svc.state is dst


# ---------------------------------------------------------------------------
# 2. Invalid state transitions
# ---------------------------------------------------------------------------

_ALL_STATES = list(State)

_INVALID_PAIRS = [(src, dst) for src in _ALL_STATES for dst in _ALL_STATES if dst not in _TRANSITIONS.get(src, set())]


@pytest.mark.parametrize(("src", "dst"), _INVALID_PAIRS, ids=[f"{s.value}->{d.value}" for s, d in _INVALID_PAIRS])
def test_invalid_transition_raises(make_service, src, dst):
    svc = make_service(state=src)
    with pytest.raises(ValueError, match="Invalid transition"):
        svc.transition(dst)


# ---------------------------------------------------------------------------
# 3. transition() side effects
# ---------------------------------------------------------------------------


class TestTransitionSideEffects:
    def test_running_sets_started_at(self, make_service):
        svc = make_service(state=State.STARTING)
        assert svc.started_at is None
        svc.transition(State.RUNNING)
        assert svc.started_at is not None
        assert svc.started_at <= time.time()

    def test_stopped_clears_started_at(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.RUNNING)
        assert svc.started_at is not None
        svc.transition(State.STOPPING)
        svc.transition(State.STOPPED)
        assert svc.started_at is None

    def test_failed_stores_error(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.FAILED, error="port conflict")
        assert svc.error == "port conflict"

    def test_non_failed_clears_error(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.FAILED, error="crash")
        assert svc.error == "crash"
        # Transition back to STARTING (valid from FAILED) should clear error.
        svc.transition(State.STARTING)
        assert svc.error is None

    def test_failed_without_error_message_stores_none(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.FAILED)
        assert svc.state is State.FAILED
        # error is None because no error string was passed.
        assert svc.error is None


# ---------------------------------------------------------------------------
# 4. uptime
# ---------------------------------------------------------------------------


class TestUptime:
    def test_uptime_returns_seconds_when_running(self, make_service):
        svc = make_service(state=State.STARTING)
        with patch("codehome.serve.services.time") as mock_time:
            mock_time.time.return_value = 1000.0
            svc.transition(State.RUNNING)
            mock_time.time.return_value = 1042.5
            assert svc.uptime == 42.5

    def test_uptime_none_when_stopped(self, make_service):
        svc = make_service(state=State.STOPPED)
        assert svc.uptime is None

    def test_uptime_none_when_starting(self, make_service):
        svc = make_service(state=State.STARTING)
        assert svc.uptime is None

    def test_uptime_none_after_stop(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.RUNNING)
        svc.transition(State.STOPPING)
        svc.transition(State.STOPPED)
        assert svc.uptime is None


# ---------------------------------------------------------------------------
# 5. to_dict()
# ---------------------------------------------------------------------------


class TestToDict:
    def test_includes_all_keys(self, make_service):
        svc = make_service(
            key="bag:feat/supa",
            service_type="supabase",
            branch="bag:feat",
            display_name="Supabase",
        )
        svc.port = 54321
        d = svc.to_dict()
        assert d["key"] == "bag:feat/supa"
        assert d["type"] == "supabase"
        assert d["branch"] == "bag:feat"
        assert d["name"] == "Supabase"
        assert d["state"] == "stopped"
        assert d["port"] == 54321
        assert d["error"] is None
        assert d["uptime"] is None
        assert d["depends_on"] == []

    def test_to_dict_reflects_running_state(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.RUNNING)
        d = svc.to_dict()
        assert d["state"] == "running"
        assert d["uptime"] is not None

    def test_to_dict_reflects_error(self, make_service):
        svc = make_service(state=State.STARTING)
        svc.transition(State.FAILED, error="OOM")
        d = svc.to_dict()
        assert d["state"] == "failed"
        assert d["error"] == "OOM"


# ---------------------------------------------------------------------------
# 6. ServiceManager register / get / unregister
# ---------------------------------------------------------------------------


class TestManagerCRUD:
    def test_register_and_get(self, mgr, make_service):
        svc = make_service(key="a/b")
        mgr.register(svc)
        assert mgr.get("a/b") is svc

    def test_get_unknown_returns_none(self, mgr):
        assert mgr.get("nope") is None

    def test_unregister_removes(self, mgr, make_service):
        svc = make_service(key="a/b")
        mgr.register(svc)
        mgr.unregister("a/b")
        assert mgr.get("a/b") is None

    def test_unregister_unknown_is_noop(self, mgr):
        # Should not raise.
        mgr.unregister("nonexistent")

    def test_register_duplicate_raises(self, mgr, make_service):
        svc1 = make_service(key="a/b", display_name="First")
        svc2 = make_service(key="a/b", display_name="Second")
        mgr.register(svc1)
        with pytest.raises(ValueError, match="already registered"):
            mgr.register(svc2)
        assert mgr.get("a/b").display_name == "First"


# ---------------------------------------------------------------------------
# 7. list_all and list_for_branch
# ---------------------------------------------------------------------------


class TestListing:
    def test_list_all(self, mgr, make_service):
        mgr.register(make_service(key="a/1", branch="a"))
        mgr.register(make_service(key="b/1", branch="b"))
        assert len(mgr.list_all()) == 2

    def test_list_all_empty(self, mgr):
        assert mgr.list_all() == []

    def test_list_for_branch_filters(self, mgr, make_service):
        mgr.register(make_service(key="a/1", branch="a"))
        mgr.register(make_service(key="a/2", branch="a"))
        mgr.register(make_service(key="b/1", branch="b"))
        result = mgr.list_for_branch("a")
        assert len(result) == 2
        assert all(s.branch == "a" for s in result)

    def test_list_for_branch_no_match(self, mgr, make_service):
        mgr.register(make_service(key="a/1", branch="a"))
        assert mgr.list_for_branch("z") == []


# ---------------------------------------------------------------------------
# 8. can_start — unknown service
# ---------------------------------------------------------------------------


class TestCanStartUnknown:
    def test_unknown_service_returns_false(self, mgr):
        ok, reason = mgr.can_start("ghost")
        assert ok is False
        assert "Unknown service" in reason


# ---------------------------------------------------------------------------
# 9. can_start — wrong state
# ---------------------------------------------------------------------------


class TestCanStartWrongState:
    @pytest.mark.parametrize("state", [State.RUNNING, State.STARTING, State.STOPPING])
    def test_non_startable_state(self, mgr, make_service, state):
        svc = make_service(key="x/y", state=state)
        mgr.register(svc)
        ok, reason = mgr.can_start("x/y")
        assert ok is False
        assert state.value in reason


# ---------------------------------------------------------------------------
# 10. can_start — dependency not running
# ---------------------------------------------------------------------------


class TestCanStartDepNotRunning:
    def test_dep_exists_but_stopped(self, mgr, make_service):
        dep = make_service(key="dep/svc", display_name="Database", state=State.STOPPED)
        svc = make_service(key="app/svc", depends_on=["dep/svc"])
        mgr.register(dep)
        mgr.register(svc)
        ok, reason = mgr.can_start("app/svc")
        assert ok is False
        assert "Database" in reason

    def test_dep_not_registered(self, mgr, make_service):
        svc = make_service(key="app/svc", depends_on=["missing/dep"])
        mgr.register(svc)
        ok, reason = mgr.can_start("app/svc")
        assert ok is False
        # Falls back to the dep key when dep is not registered.
        assert "missing/dep" in reason


# ---------------------------------------------------------------------------
# 11. can_start — dependency running
# ---------------------------------------------------------------------------


class TestCanStartDepRunning:
    def test_dep_running_allows_start(self, mgr, make_service):
        dep = make_service(key="dep/svc", state=State.STARTING)
        dep.transition(State.RUNNING)
        svc = make_service(key="app/svc", depends_on=["dep/svc"])
        mgr.register(dep)
        mgr.register(svc)
        ok, reason = mgr.can_start("app/svc")
        assert ok is True
        assert reason is None

    def test_multiple_deps_all_running(self, mgr, make_service):
        dep_a = make_service(key="dep/a", state=State.STARTING)
        dep_a.transition(State.RUNNING)
        dep_b = make_service(key="dep/b", state=State.STARTING)
        dep_b.transition(State.RUNNING)
        svc = make_service(key="app/svc", depends_on=["dep/a", "dep/b"])
        mgr.register(dep_a)
        mgr.register(dep_b)
        mgr.register(svc)
        ok, reason = mgr.can_start("app/svc")
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# 12. can_start — FAILED state is startable
# ---------------------------------------------------------------------------


class TestCanStartFromFailed:
    def test_failed_service_can_start(self, mgr, make_service):
        svc = make_service(key="x/y", state=State.STARTING)
        svc.transition(State.FAILED, error="crash")
        mgr.register(svc)
        ok, reason = mgr.can_start("x/y")
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# 13. dependents_of — reverse dependency lookup
# ---------------------------------------------------------------------------


class TestDependentsOf:
    def test_finds_dependents(self, mgr, make_service):
        dep = make_service(key="dep/svc")
        child_a = make_service(key="a/svc", depends_on=["dep/svc"])
        child_b = make_service(key="b/svc", depends_on=["dep/svc"])
        unrelated = make_service(key="c/svc")
        mgr.register(dep)
        mgr.register(child_a)
        mgr.register(child_b)
        mgr.register(unrelated)
        dependents = mgr.dependents_of("dep/svc")
        keys = {s.key for s in dependents}
        assert keys == {"a/svc", "b/svc"}

    def test_no_dependents(self, mgr, make_service):
        svc = make_service(key="lonely/svc")
        mgr.register(svc)
        assert mgr.dependents_of("lonely/svc") == []

    def test_dependents_of_unregistered_key(self, mgr):
        assert mgr.dependents_of("ghost") == []
