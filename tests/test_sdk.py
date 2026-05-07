"""Smoke tests for the plugin SDK lazy-import machinery."""

from __future__ import annotations

import pytest

from codehome.sdk import PluginNotInstalledError, _plugin_name_from_module


def test_core_symbol_accessible() -> None:
    """A core SDK symbol resolves without error.

    PluginNotInstalledError is defined directly in sdk.py (not lazy),
    so it always works regardless of server state.
    """
    assert issubclass(PluginNotInstalledError, ImportError)


def test_plugin_not_installed_error_on_missing_plugin() -> None:
    """Accessing a symbol backed by a non-existent plugin raises PluginNotInstalledError."""
    # Temporarily inject a fake mapping that points to a non-existent plugin module.
    from codehome import sdk

    original = sdk._LAZY_IMPORTS.copy()
    sdk._LAZY_IMPORTS["_test_fake_symbol"] = ("codehome.nonexistent_plugin.foo", "bar")
    # Clear any cached value.
    sdk.__dict__.pop("_test_fake_symbol", None)
    try:
        with pytest.raises(PluginNotInstalledError, match="nonexistent_plugin"):
            sdk.__getattr__("_test_fake_symbol")
    finally:
        del sdk._LAZY_IMPORTS["_test_fake_symbol"]
        sdk.__dict__.pop("_test_fake_symbol", None)
        sdk._LAZY_IMPORTS.update(original)


def test_plugin_name_from_module_detects_plugin() -> None:
    """_plugin_name_from_module returns the plugin name for non-core modules."""
    assert _plugin_name_from_module("codehome.telemac.build") == "telemac"
    assert _plugin_name_from_module("codehome.publisher.dispatch") == "publisher"


def test_plugin_name_from_module_returns_none_for_core() -> None:
    """_plugin_name_from_module returns None for core codehome packages."""
    assert _plugin_name_from_module("codehome.bus.dispatch") is None
    assert _plugin_name_from_module("codehome.state.service_registry") is None


def test_unknown_attribute_raises_attribute_error() -> None:
    """Accessing a truly unknown name raises AttributeError, not ImportError."""
    from codehome import sdk

    with pytest.raises(AttributeError, match="no attribute"):
        sdk.__getattr__("_totally_nonexistent_9999")
