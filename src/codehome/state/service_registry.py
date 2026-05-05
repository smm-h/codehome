"""Plugin-to-plugin service registry.

Plugins register callable services. Other plugins call them by name.
Replaces ad-hoc importlib hacks with explicit, discoverable contracts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Registry for plugin-to-plugin callable services."""

    def __init__(self) -> None:
        self._services: dict[str, _ServiceEntry] = {}

    def register(self, name: str, handler: Callable[..., Any], *, plugin: str, description: str = "") -> None:
        """Register a callable service.

        Args:
            name: Dotted service name (e.g., "publisher.dispatch")
            handler: The callable to invoke
            plugin: Owning plugin name
            description: Human-readable description

        """
        if name in self._services:
            raise ValueError(f"Service {name!r} already registered by {self._services[name].plugin!r}")
        self._services[name] = _ServiceEntry(
            name=name,
            handler=handler,
            plugin=plugin,
            description=description,
        )
        logger.debug("Registered service %s (plugin: %s)", name, plugin)

    def get_handler(self, name: str) -> Callable[..., Any]:
        """Return the raw callable for a registered service (without invoking it).

        Used by the streaming command endpoint to obtain async generator
        handlers that must be iterated, not called-and-awaited.
        """
        entry = self._services.get(name)
        if entry is None:
            raise LookupError(f"Service {name!r} not registered. Available: {sorted(self._services)}")
        return entry.handler

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a registered service by name."""
        entry = self._services.get(name)
        if entry is None:
            raise LookupError(f"Service {name!r} not registered. Available: {sorted(self._services)}")
        return entry.handler(*args, **kwargs)

    def get_typed(self, name: str, protocol: type[T]) -> T | None:
        """Get a registered service handler, cast to the given Protocol type.

        Returns None if the service is not registered.  The caller gets
        full IDE autocomplete and mypy type checking via the Protocol.

        Usage:
            from codehome.service_protocols import PublisherDispatchArgv

            dispatch = services.get_typed("publisher.dispatch_argv", PublisherDispatchArgv)
            if dispatch:
                result = dispatch(["apple", "status"])
        """
        entry = self._services.get(name)
        if entry is None:
            return None
        # The registry stores Callable[..., Any] but the caller
        # constrains the type via the Protocol.  Structural
        # compatibility is verified at the call site by mypy.
        return entry.handler  # type: ignore[return-value]

    def has(self, name: str) -> bool:
        """Check if a service is registered."""
        return name in self._services

    def list(self) -> list[dict[str, str]]:
        """List all registered services."""
        return [{"name": e.name, "plugin": e.plugin, "description": e.description} for e in self._services.values()]

    def clear(self) -> None:
        """Clear all registrations. For testing."""
        self._services.clear()


class _ServiceEntry:
    __slots__ = ("description", "handler", "name", "plugin")

    def __init__(self, name: str, handler: Callable[..., Any], plugin: str, description: str) -> None:
        self.name = name
        self.handler = handler
        self.plugin = plugin
        self.description = description


# Module-level singleton
services = ServiceRegistry()
