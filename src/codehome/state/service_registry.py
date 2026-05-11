"""Plugin-to-plugin service registry.

Plugins register callable services. Other plugins call them by name.
Replaces ad-hoc importlib hacks with explicit, discoverable contracts.

Lazy resolution: during CLI startup, plugin manifests declare services
via ``[[services]]`` in TOML.  These are stored as deferred entries
(plugin_dir + handler reference) and resolved on first access -- the
handler module is imported and the function registered only when
another plugin actually calls the service.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class _DeferredService:
    """Metadata for a service declared in a plugin manifest but not yet imported."""

    __slots__ = ("description", "handler_ref", "plugin_dir", "plugin_name")

    def __init__(
        self,
        plugin_dir: Path,
        handler_ref: str,
        plugin_name: str,
        description: str,
    ) -> None:
        self.plugin_dir = plugin_dir
        self.handler_ref = handler_ref
        self.plugin_name = plugin_name
        self.description = description


class ServiceRegistry:
    """Registry for plugin-to-plugin callable services."""

    def __init__(self) -> None:
        self._services: dict[str, _ServiceEntry] = {}
        self._deferred: dict[str, _DeferredService] = {}

    def set_deferred_services(
        self,
        index: dict[str, tuple[Path, str, str, str]],
    ) -> None:
        """Store a deferred service index built from plugin manifests.

        Args:
            index: Mapping of ``service_name`` to
                ``(plugin_dir, handler_ref, plugin_name, description)``.
                Entries already eagerly registered are silently skipped.

        """
        for svc_name, (plugin_dir, handler_ref, plugin_name, desc) in index.items():
            if svc_name in self._services:
                continue  # Already registered eagerly; no need to defer.
            self._deferred[svc_name] = _DeferredService(
                plugin_dir=plugin_dir,
                handler_ref=handler_ref,
                plugin_name=plugin_name,
                description=desc,
            )
        logger.debug("Deferred service index set: %d entries", len(self._deferred))

    def _resolve_deferred(self, name: str) -> bool:
        """Try to resolve a deferred service, importing its handler module.

        Returns ``True`` if the service was successfully resolved and
        registered, ``False`` otherwise.
        """
        deferred = self._deferred.pop(name, None)
        if deferred is None:
            return False

        # Parse handler reference (same format as LazyHandler in cli_builder).
        if ":" in deferred.handler_ref:
            module_name, func_name = deferred.handler_ref.split(":", 1)
        else:
            module_name = "handlers"
            func_name = deferred.handler_ref

        # Support slash-separated paths (e.g. "commands/branch:cmd_ls").
        module_file = deferred.plugin_dir / f"{module_name}.py"
        if not module_file.is_file():
            logger.error(
                "Deferred service %r: handler module not found: %s",
                name,
                module_file,
            )
            return False

        # Use the full resolved path in the spec name to avoid collisions
        # between plugins with the same directory name in different locations.
        dir_key = str(deferred.plugin_dir.resolve()).replace("/", "_").replace("\\", "_")
        spec_name = f"codehome_svc_{dir_key}_{module_name.replace('/', '_')}"

        # Re-use already-loaded module if available.
        mod = sys.modules.get(spec_name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(spec_name, module_file)
            if spec is None or spec.loader is None:
                logger.error(
                    "Deferred service %r: cannot create import spec for %s",
                    name,
                    module_file,
                )
                return False
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec_name] = mod

            # Set up the plugin's _sdk.py so ``from _sdk import ...``
            # works inside the handler module, mirroring what loader.py
            # does at plugin-load time (lines 229-249).
            sdk_path = deferred.plugin_dir / "_sdk.py"
            prev_sdk = sys.modules.get("_sdk")
            _sdk_installed = False
            if sdk_path.is_file():
                sdk_spec_name = f"_plugin_{deferred.plugin_name}__sdk"
                sdk_mod = sys.modules.get(sdk_spec_name)
                if sdk_mod is None:
                    sdk_spec = importlib.util.spec_from_file_location(
                        sdk_spec_name, sdk_path
                    )
                    if sdk_spec and sdk_spec.loader:
                        sdk_mod = importlib.util.module_from_spec(sdk_spec)
                        sys.modules[sdk_spec_name] = sdk_mod
                        try:
                            sdk_spec.loader.exec_module(sdk_mod)
                        except Exception:
                            sys.modules.pop(sdk_spec_name, None)
                            logger.exception(
                                "Deferred service %r: failed to load _sdk.py for plugin %r",
                                name,
                                deferred.plugin_name,
                            )
                            sys.modules.pop(spec_name, None)
                            return False
                if sdk_mod is not None:
                    sys.modules["_sdk"] = sdk_mod
                    _sdk_installed = True

            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(spec_name, None)
                logger.exception(
                    "Deferred service %r: failed to import %s",
                    name,
                    module_file,
                )
                return False
            finally:
                # Restore previous _sdk state.
                if _sdk_installed:
                    if prev_sdk is not None:
                        sys.modules["_sdk"] = prev_sdk
                    else:
                        sys.modules.pop("_sdk", None)

        handler = getattr(mod, func_name, None)
        if handler is None:
            logger.error(
                "Deferred service %r: function %r not found in %s",
                name,
                func_name,
                module_file,
            )
            return False

        self.register(
            name,
            handler,
            plugin=deferred.plugin_name,
            description=deferred.description,
        )
        logger.debug("Lazily resolved deferred service %r", name)
        return True

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
        # Remove from deferred index if present (now eagerly registered).
        self._deferred.pop(name, None)
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
        Lazily resolves deferred services on first access.
        """
        entry = self._services.get(name)
        if entry is None:
            if self._resolve_deferred(name):
                entry = self._services.get(name)
        if entry is None:
            raise LookupError(f"Service {name!r} not registered. Available: {sorted(self._services)}")
        return entry.handler

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a registered service by name.

        Lazily resolves deferred services on first access.
        """
        entry = self._services.get(name)
        if entry is None:
            if self._resolve_deferred(name):
                entry = self._services.get(name)
        if entry is None:
            raise LookupError(f"Service {name!r} not registered. Available: {sorted(self._services)}")
        return entry.handler(*args, **kwargs)

    def get_typed(self, name: str, protocol: type[T]) -> T | None:
        """Get a registered service handler, cast to the given Protocol type.

        Returns None if the service is not registered.  The caller gets
        full IDE autocomplete and mypy type checking via the Protocol.
        Lazily resolves deferred services on first access.

        Usage:
            from codehome.service_protocols import PublisherDispatchArgv

            dispatch = services.get_typed("publisher.dispatch_argv", PublisherDispatchArgv)
            if dispatch:
                result = dispatch(["apple", "status"])
        """
        entry = self._services.get(name)
        if entry is None:
            if self._resolve_deferred(name):
                entry = self._services.get(name)
        if entry is None:
            return None
        # The registry stores Callable[..., Any] but the caller
        # constrains the type via the Protocol.  Structural
        # compatibility is verified at the call site by mypy.
        return entry.handler  # type: ignore[return-value]

    def has(self, name: str) -> bool:
        """Check if a service is registered (or deferred for lazy resolution)."""
        return name in self._services or name in self._deferred

    def list(self) -> list[dict[str, str]]:
        """List all registered services (including deferred, unresolved ones)."""
        result = [
            {"name": e.name, "plugin": e.plugin, "description": e.description}
            for e in self._services.values()
        ]
        for svc_name, d in self._deferred.items():
            result.append({
                "name": svc_name,
                "plugin": d.plugin_name,
                "description": d.description,
            })
        return result

    def clear(self) -> None:
        """Clear all registrations (eager and deferred). For testing."""
        self._services.clear()
        self._deferred.clear()


class _ServiceEntry:
    __slots__ = ("description", "handler", "name", "plugin")

    def __init__(self, name: str, handler: Callable[..., Any], plugin: str, description: str) -> None:
        self.name = name
        self.handler = handler
        self.plugin = plugin
        self.description = description


# Module-level singleton
services = ServiceRegistry()
