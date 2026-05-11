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


class TestDeferredSdkImport:
    """Deferred services whose handler module imports from _sdk."""

    def test_handler_importing_sdk_resolves(self, tmp_path: Path) -> None:
        """A handler that does ``from _sdk import ...`` should work."""
        plugin = tmp_path / "sdk_plugin"
        plugin.mkdir()

        # _sdk.py provides a helper used by handlers.py.
        (plugin / "_sdk.py").write_text(
            "def helper():\n"
            "    return 'from sdk'\n"
        )
        (plugin / "handlers.py").write_text(
            "from _sdk import helper\n"
            "\n"
            "def my_service():\n"
            "    return helper()\n"
        )

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "sdk.service": (plugin, "my_service", "sdk_plugin", "uses _sdk"),
        })

        result = reg.call("sdk.service")
        assert result == "from sdk"

    def test_sdk_not_leaked_after_resolve(self, tmp_path: Path) -> None:
        """_sdk should not remain in sys.modules after resolution."""
        import sys

        plugin = tmp_path / "sdk_plugin2"
        plugin.mkdir()
        (plugin / "_sdk.py").write_text("VALUE = 99\n")
        (plugin / "handlers.py").write_text(
            "from _sdk import VALUE\n"
            "\n"
            "def svc():\n"
            "    return VALUE\n"
        )

        # Ensure _sdk is not in sys.modules before.
        sys.modules.pop("_sdk", None)

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "sdk2.svc": (plugin, "svc", "sdk_plugin2", "desc"),
        })
        assert reg.call("sdk2.svc") == 99
        assert "_sdk" not in sys.modules

    def test_sdk_restored_if_previously_set(self, tmp_path: Path) -> None:
        """If _sdk was already in sys.modules, it is restored after resolve."""
        import sys
        import types

        plugin = tmp_path / "sdk_plugin3"
        plugin.mkdir()
        (plugin / "_sdk.py").write_text("X = 1\n")
        (plugin / "handlers.py").write_text(
            "from _sdk import X\n"
            "\n"
            "def svc():\n"
            "    return X\n"
        )

        # Plant a fake _sdk that should be restored.
        sentinel = types.ModuleType("_sdk")
        sentinel.MARKER = "original"  # type: ignore[attr-defined]
        sys.modules["_sdk"] = sentinel

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "sdk3.svc": (plugin, "svc", "sdk_plugin3", "desc"),
        })
        assert reg.call("sdk3.svc") == 1
        assert sys.modules.get("_sdk") is sentinel

        # Clean up.
        sys.modules.pop("_sdk", None)

    def test_no_sdk_file_still_works(self, tmp_path: Path) -> None:
        """Plugins without _sdk.py should resolve normally."""
        plugin = tmp_path / "no_sdk_plugin"
        plugin.mkdir()
        (plugin / "handlers.py").write_text(
            "def svc():\n"
            "    return 'no sdk needed'\n"
        )

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "nosdk.svc": (plugin, "svc", "no_sdk_plugin", "desc"),
        })
        assert reg.call("nosdk.svc") == "no sdk needed"

    def test_sdk_load_failure_prevents_resolution(self, tmp_path: Path) -> None:
        """If _sdk.py itself fails to import, resolution should fail gracefully."""
        plugin = tmp_path / "bad_sdk_plugin"
        plugin.mkdir()
        (plugin / "_sdk.py").write_text("raise RuntimeError('sdk broken')\n")
        (plugin / "handlers.py").write_text(
            "from _sdk import something\n"
            "\n"
            "def svc():\n"
            "    return something()\n"
        )

        reg = ServiceRegistry()
        reg.set_deferred_services({
            "badsdk.svc": (plugin, "svc", "bad_sdk_plugin", "desc"),
        })
        assert reg.get_typed("badsdk.svc", object) is None


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
