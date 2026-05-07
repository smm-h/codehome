"""Background task registry for plugin-contributed recurring tasks.

Plugins register task factories via the module-level ``background_tasks``
singleton.  The server lifespan calls ``start_all()`` at boot and
``stop_all()`` at shutdown.

Task classes must implement ``start()`` and ``stop()`` methods (the
``BackgroundTask`` protocol).  A factory is a zero-argument callable
that returns a ``BackgroundTask`` instance -- instantiation is deferred
to ``start_all()`` so import-time side effects are avoided.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

from codehome.serve.logging_config import get_logger

log = get_logger(component="background_tasks")


@runtime_checkable
class BackgroundTask(Protocol):
    """Protocol for background tasks managed by the registry."""

    def start(self) -> None: ...
    def stop(self) -> None: ...


class BackgroundTaskRegistry:
    """Registry for plugin-contributed background tasks.

    Tasks are registered as factories (callables returning BackgroundTask
    instances).  The registry instantiates and starts them during
    ``start_all()``, and stops them during ``stop_all()``.
    """

    def __init__(self) -> None:
        # Registered: name -> (factory, feature_gate)
        self._registered: dict[str, tuple[Callable[[], BackgroundTask], str | None]] = {}
        # Running: name -> instance (only populated after start_all)
        self._running: dict[str, BackgroundTask] = {}

    def register(
        self,
        name: str,
        factory: Callable[[], BackgroundTask],
        *,
        feature: str | None = None,
    ) -> None:
        """Register a background task factory.

        If *feature* is set, the task only starts when that feature flag
        is enabled at ``start_all()`` time.

        Raises ValueError if a task with the same name is already registered.
        """
        if name in self._registered:
            msg = f"Background task {name!r} is already registered"
            raise ValueError(msg)
        self._registered[name] = (factory, feature)
        log.debug("task_registered", name=name, feature=feature)

    def start_all(self) -> None:
        """Instantiate and start all registered tasks, respecting feature gates."""
        from codehome import features

        for name, (factory, feature_gate) in self._registered.items():
            if name in self._running:
                continue
            if feature_gate is not None and not features.enabled(feature_gate):
                log.debug("task_skipped", name=name, feature=feature_gate)
                continue
            try:
                instance = factory()
                instance.start()
                self._running[name] = instance
                log.info("task_started", name=name)
            except Exception:
                log.exception("task_start_failed", name=name)

    def stop_all(self) -> None:
        """Stop all running tasks."""
        for name in list(self._running):
            try:
                self._running[name].stop()
                log.info("task_stopped", name=name)
            except Exception:
                log.exception("task_stop_failed", name=name)
        self._running.clear()

    def list_tasks(self) -> list[dict[str, object]]:
        """List registered tasks with their running status."""
        result: list[dict[str, object]] = []
        for name, (_factory, feature_gate) in self._registered.items():
            result.append({
                "name": name,
                "feature": feature_gate,
                "running": name in self._running,
            })
        return result


# Module-level singleton: plugins register into this during load_all_plugins(),
# the server lifespan starts/stops it.
background_tasks = BackgroundTaskRegistry()
