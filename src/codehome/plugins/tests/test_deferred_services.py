"""Tests for deferred (lazy) service resolution in ServiceRegistry.

Covers:

- Deferred services are lazily resolved on first get()/get_typed()/call().
- Unknown service names still return None / raise LookupError.
- Once resolved, subsequent get() calls return the cached handler.
- has() returns True for deferred (not yet resolved) services.
- clear() wipes both eager and deferred entries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codehome.state.service_registry import ServiceRegistry


@pytest.fixture()
def registry() -> ServiceRegistry:
    """Return a fresh ServiceRegistry for each test."""
    return ServiceRegistry()


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    """Create a minimal plugin directory with a handlers.py."""
    plugin = tmp_path / "test_plugin"
    plugin.mkdir()
    handlers = plugin / "handlers.py"
    handlers.write_text(
        "def my_service(*args, **kwargs):\n"
        "    return 'hello from service'\n"
        "\n"
        "greeting = 'not callable but valid'\n"
    )
    return plugin


class TestDeferredServiceResolution:
    """Deferred services are lazily resolved on first access."""

    def test_get_typed_resolves_deferred(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.set_deferred_services({
            "test.service": (plugin_dir, "my_service", "test_plugin", "A test service"),
        })

        # Not yet resolved -- no eager entry.
        assert "test.service" not in registry._services

        # get_typed triggers lazy resolution.
        handler = registry.get_typed("test.service", object)
        assert handler is not None
        assert handler() == "hello from service"

    def test_get_handler_resolves_deferred(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.set_deferred_services({
            "test.service": (plugin_dir, "my_service", "test_plugin", "A test service"),
        })

        handler = registry.get_handler("test.service")
        assert callable(handler)
        assert handler() == "hello from service"

    def test_call_resolves_deferred(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.set_deferred_services({
            "test.service": (plugin_dir, "my_service", "test_plugin", "A test service"),
        })

        result = registry.call("test.service")
        assert result == "hello from service"

    def test_has_returns_true_for_deferred(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.set_deferred_services({
            "test.service": (plugin_dir, "my_service", "test_plugin", "desc"),
        })
        assert registry.has("test.service")

    def test_non_callable_handler_attribute(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        """Handler reference can point to a non-callable attribute (e.g. an instance)."""
        registry.set_deferred_services({
            "test.greeting": (plugin_dir, "greeting", "test_plugin", "A greeting"),
        })

        handler = registry.get_typed("test.greeting", object)
        assert handler == "not callable but valid"


class TestUnknownServices:
    """Unknown service names behave correctly."""

    def test_get_typed_returns_none(self, registry: ServiceRegistry) -> None:
        assert registry.get_typed("nonexistent.service", object) is None

    def test_get_handler_raises(self, registry: ServiceRegistry) -> None:
        with pytest.raises(LookupError, match="nonexistent.service"):
            registry.get_handler("nonexistent.service")

    def test_call_raises(self, registry: ServiceRegistry) -> None:
        with pytest.raises(LookupError, match="nonexistent.service"):
            registry.call("nonexistent.service")

    def test_has_returns_false(self, registry: ServiceRegistry) -> None:
        assert not registry.has("nonexistent.service")


class TestCachingAfterResolution:
    """Once resolved, subsequent accesses return the cached handler."""

    def test_cached_after_first_get_typed(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.set_deferred_services({
            "test.service": (plugin_dir, "my_service", "test_plugin", "desc"),
        })

        handler1 = registry.get_typed("test.service", object)
        handler2 = registry.get_typed("test.service", object)
        assert handler1 is handler2

    def test_deferred_entry_removed_after_resolution(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.set_deferred_services({
            "test.service": (plugin_dir, "my_service", "test_plugin", "desc"),
        })

        assert "test.service" in registry._deferred
        registry.get_typed("test.service", object)
        assert "test.service" not in registry._deferred
        assert "test.service" in registry._services


class TestClearWipesBoth:
    """clear() removes both eager and deferred entries."""

    def test_clear(
        self, registry: ServiceRegistry, plugin_dir: Path
    ) -> None:
        registry.register("eager.svc", lambda: None, plugin="p", description="d")
        registry.set_deferred_services({
            "deferred.svc": (plugin_dir, "my_service", "test_plugin", "d"),
        })

        assert registry.has("eager.svc")
        assert registry.has("deferred.svc")

        registry.clear()

        assert not registry.has("eager.svc")
        assert not registry.has("deferred.svc")


class TestDeferredWithModuleRef:
    """Handler references with colon-separated module:function work."""

    def test_colon_separated_handler_ref(self, tmp_path: Path) -> None:
        plugin = tmp_path / "mod_plugin"
        plugin.mkdir()
        (plugin / "custom_mod.py").write_text(
            "def special_handler():\n"
            "    return 42\n"
        )

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "mod.special": (plugin, "custom_mod:special_handler", "mod_plugin", "desc"),
        })

        result = reg.call("mod.special")
        assert result == 42


class TestDeferredResolutionFailures:
    """Graceful handling of resolution failures."""

    def test_missing_module_file(self, tmp_path: Path) -> None:
        plugin = tmp_path / "bad_plugin"
        plugin.mkdir()
        # No handlers.py created.

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "bad.service": (plugin, "missing_func", "bad_plugin", "desc"),
        })

        assert reg.get_typed("bad.service", object) is None

    def test_missing_function_in_module(self, tmp_path: Path) -> None:
        plugin = tmp_path / "partial_plugin"
        plugin.mkdir()
        (plugin / "handlers.py").write_text("x = 1\n")

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "partial.service": (plugin, "nonexistent_func", "partial_plugin", "desc"),
        })

        assert reg.get_typed("partial.service", object) is None

    def test_eager_registration_skips_deferred(self, tmp_path: Path) -> None:
        """If a service is already eagerly registered, deferred entry is skipped."""
        plugin = tmp_path / "skip_plugin"
        plugin.mkdir()
        (plugin / "handlers.py").write_text("def func(): return 'deferred'\n")

        reg = ServiceRegistry()
        reg.register("skip.svc", lambda: "eager", plugin="p", description="d")
        reg.set_deferred_services({
            "skip.svc": (plugin, "func", "skip_plugin", "d"),
        })

        # Deferred entry should NOT be stored since service is already registered.
        assert "skip.svc" not in reg._deferred
        assert reg.call("skip.svc") == "eager"
