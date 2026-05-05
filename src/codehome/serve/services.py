"""Service registry with state machine and dependency graph."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


# Valid state transitions.
_TRANSITIONS = {
    State.STOPPED: {State.STARTING},
    State.STARTING: {State.RUNNING, State.FAILED, State.STOPPED},
    State.RUNNING: {State.STOPPING, State.FAILED},
    State.STOPPING: {State.STOPPED, State.FAILED},
    State.FAILED: {State.STARTING, State.STOPPED},
}


@dataclass
class ServiceInstance:
    key: str  # e.g. "bag:navchat/supabase"
    service_type: str  # "supabase", "compose", or "compose-native"
    branch: str  # e.g. "bag:navchat"
    display_name: str  # e.g. "Supabase" or "Vite"
    depends_on: list[str] = field(default_factory=list)  # keys of dependencies
    state: State = State.STOPPED
    port: int | None = None
    error: str | None = None
    started_at: float | None = None  # time.time() when entered RUNNING
    metadata: dict[str, Any] = field(default_factory=dict)  # type-specific data
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # guards state transitions

    def transition(self, new_state: State, error: str | None = None) -> None:
        """Transition to a new state. Raises ValueError on invalid transition."""
        if new_state not in _TRANSITIONS.get(self.state, set()):
            msg = f"Invalid transition: {self.state.value} -> {new_state.value}"
            raise ValueError(msg)
        self.state = new_state
        self.error = error if new_state == State.FAILED else None
        if new_state == State.RUNNING:
            self.started_at = time.time()
        elif new_state == State.STOPPED:
            self.started_at = None

    @property
    def uptime(self) -> float | None:
        if self.state == State.RUNNING and self.started_at:
            return time.time() - self.started_at
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "type": self.service_type,
            "branch": self.branch,
            "name": self.display_name,
            "state": self.state.value,
            "port": self.port,
            "error": self.error,
            "uptime": self.uptime,
            "depends_on": self.depends_on,
            "metadata": self.metadata,
        }


class ServiceManager:
    """Registry of all service instances with dependency enforcement."""

    def __init__(self) -> None:
        self._services: dict[str, ServiceInstance] = {}

    def register(self, instance: ServiceInstance) -> None:
        """Register a service instance."""
        if instance.key in self._services:
            msg = f"Service '{instance.key}' is already registered"
            raise ValueError(msg)
        self._services[instance.key] = instance

    def unregister(self, key: str) -> None:
        """Remove a service instance."""
        self._services.pop(key, None)

    def get(self, key: str) -> ServiceInstance | None:
        return self._services.get(key)

    def list_all(self) -> list[ServiceInstance]:
        return list(self._services.values())

    def list_for_branch(self, branch: str) -> list[ServiceInstance]:
        return [s for s in self._services.values() if s.branch == branch]

    def can_start(self, key: str) -> tuple[bool, str | None]:
        """Check if a service's dependencies are satisfied.

        Returns (True, None) if startable, or (False, "reason") if blocked.
        """
        svc = self._services.get(key)
        if not svc:
            return False, f"Unknown service: {key}"
        if svc.state not in (State.STOPPED, State.FAILED):
            return False, f"Service is {svc.state.value}, must be stopped or failed to start"
        for dep_key in svc.depends_on:
            dep = self._services.get(dep_key)
            if not dep or dep.state != State.RUNNING:
                dep_name = dep.display_name if dep else dep_key
                return False, f"Requires {dep_name} to be running"
        return True, None

    def dependents_of(self, key: str) -> list[ServiceInstance]:
        """Return services that depend on the given key."""
        return [s for s in self._services.values() if key in s.depends_on]


# Singleton instance.
services = ServiceManager()
