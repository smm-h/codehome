"""Smoke tests for the plugin-to-plugin service registry."""

from __future__ import annotations

from typing import Protocol

import pytest

from codehome.state.service_registry import ServiceRegistry


@pytest.fixture()
def registry() -> ServiceRegistry:
    return ServiceRegistry()


def test_register_and_call(registry: ServiceRegistry) -> None:
    """A registered service can be called by name."""

    def adder(a: int, b: int) -> int:
        return a + b

    registry.register("math.add", adder, plugin="test")
    assert registry.call("math.add", 2, 3) == 5


def test_get_typed_with_protocol(registry: ServiceRegistry) -> None:
    """get_typed returns the handler cast to a Protocol type."""

    class Greeter(Protocol):
        def __call__(self, name: str) -> str: ...

    def greet(name: str) -> str:
        return f"hello {name}"

    registry.register("greet", greet, plugin="test")
    fn = registry.get_typed("greet", Greeter)

    assert fn is not None
    assert fn("world") == "hello world"


def test_get_typed_missing_returns_none(registry: ServiceRegistry) -> None:
    """get_typed returns None for unregistered services."""
    assert registry.get_typed("nonexistent", object) is None


def test_has(registry: ServiceRegistry) -> None:
    """has() reflects registration state."""
    assert not registry.has("svc")
    registry.register("svc", lambda: None, plugin="test")
    assert registry.has("svc")


def test_list(registry: ServiceRegistry) -> None:
    """list() returns metadata for all registered services."""
    registry.register("a", lambda: None, plugin="p1", description="first")
    registry.register("b", lambda: None, plugin="p2", description="second")

    items = registry.list()
    names = {item["name"] for item in items}
    assert names == {"a", "b"}


def test_duplicate_registration_raises(registry: ServiceRegistry) -> None:
    """Registering the same name twice raises ValueError."""
    registry.register("dup", lambda: None, plugin="test")
    with pytest.raises(ValueError, match="already registered"):
        registry.register("dup", lambda: None, plugin="other")


def test_call_missing_raises(registry: ServiceRegistry) -> None:
    """Calling an unregistered service raises LookupError."""
    with pytest.raises(LookupError, match="not registered"):
        registry.call("missing")
