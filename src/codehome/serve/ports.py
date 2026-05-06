"""Port allocation for Compose services and Supabase slot management."""

from __future__ import annotations

import json
import socket

from codehome.paths import resolve_global, codehome_home
from codehome.serve.file_lock import write_json_locked

# Read: dual-path fallback (new ~/.codehome/ then legacy .supervisor/).
# Write: always to the canonical new location (~/.codehome/).
_STATE_FILE_READ = resolve_global("serve-ports.json")
_STATE_FILE_WRITE = codehome_home() / "serve-ports.json"

# Preferred default ports per service type (last segment of the key).
_DEFAULTS = {
    "vite": 8080,
    "tdd-vite-bag": 8180,
    "tdd-vite-orders": 8181,
}

# Supabase base ports: each slot offsets all 10 by slot * 100.
SUPABASE_BASE_PORTS = {
    "api_port": 54321,
    "db_port": 54322,
    "shadow_port": 54320,
    "pooler_port": 54329,
    "studio_port": 54323,
    "inbucket_port": 54324,
    "inbucket_smtp": 54325,
    "inbucket_pop3": 54326,
    "analytics_port": 54327,
    "inspector_port": 8083,
}

MAX_SUPABASE_STACKS = 3


class PortAllocator:
    def __init__(self) -> None:
        self._compose: dict[str, int] = {}  # service_key -> port
        self._supabase_slots: dict[str, int] = {}  # branch -> slot number
        # compose-native: service_key -> {port, compose_file, compose_service}
        # Stored separately so discover_running can probe the right compose file.
        self._compose_native: dict[str, dict[str, object]] = {}
        self._load()

    def allocate(self, service_key: str) -> int:
        """Allocate a port for a Compose service. Tries preferred default first."""
        if service_key in self._compose:
            return self._compose[service_key]
        base_type = service_key.rsplit("/", 1)[-1]
        preferred = _DEFAULTS.get(base_type)
        if preferred and not self._is_occupied(preferred) and preferred not in self._compose.values():
            port = preferred
        else:
            port = self._find_free()
        self._compose[service_key] = port
        self._save()
        return port

    def release(self, service_key: str) -> None:
        """Release a Compose port allocation."""
        if self._compose.pop(service_key, None) is not None:
            self._save()

    def get_port(self, service_key: str) -> int | None:
        """Get the allocated port for a service, or None."""
        return self._compose.get(service_key)

    def allocate_native(
        self,
        service_key: str,
        compose_file: str,
        compose_service: str,
    ) -> int:
        """Allocate a port for a compose-native service.

        Persists the compose_file and compose_service alongside the port
        so discover_running can probe the correct compose project on restart.
        """
        existing = self._compose_native.get(service_key)
        if existing:
            return int(existing["port"])  # type: ignore[call-overload,no-any-return]
        port = self._find_free()
        self._compose_native[service_key] = {
            "port": port,
            "compose_file": compose_file,
            "compose_service": compose_service,
        }
        self._save()
        return port

    def release_native(self, service_key: str) -> None:
        """Release a compose-native port allocation."""
        if self._compose_native.pop(service_key, None) is not None:
            self._save()

    def get_native(self, service_key: str) -> dict[str, object] | None:
        """Get compose-native allocation {port, compose_file, compose_service} or None."""
        return self._compose_native.get(service_key)

    def allocate_supabase_slot(self, branch: str) -> tuple[int, dict[str, int]]:
        """Allocate a Supabase slot for a branch. Returns (slot, {port_name: port})."""
        if branch in self._supabase_slots:
            slot = self._supabase_slots[branch]
            return slot, self._slot_ports(slot)
        if len(self._supabase_slots) >= MAX_SUPABASE_STACKS:
            msg = f"Maximum {MAX_SUPABASE_STACKS} simultaneous Supabase stacks reached. Stop one first."
            raise RuntimeError(msg)
        # Find lowest free slot.
        used = set(self._supabase_slots.values())
        slot = 0
        while slot in used:
            slot += 1
        self._supabase_slots[branch] = slot
        self._save()
        return slot, self._slot_ports(slot)

    def release_supabase_slot(self, branch: str) -> None:
        """Release a Supabase slot."""
        if self._supabase_slots.pop(branch, None) is not None:
            self._save()

    def get_supabase_slot(self, branch: str) -> tuple[int, dict[str, int]] | None:
        """Get the slot and ports for a branch, or None."""
        slot = self._supabase_slots.get(branch)
        if slot is None:
            return None
        return slot, self._slot_ports(slot)

    def all_allocations(self) -> dict[str, object]:
        """Return full port map for the /api/port-allocations endpoint."""
        supabase = {}
        for branch, slot in self._supabase_slots.items():
            supabase[branch] = self._slot_ports(slot)
        return {
            "compose": dict(self._compose),
            "compose_native": dict(self._compose_native),
            "supabase": supabase,
        }

    def _slot_ports(self, slot: int) -> dict[str, int]:
        """Calculate all 10 Supabase ports for a given slot."""
        return {name: base + slot * 100 for name, base in SUPABASE_BASE_PORTS.items()}

    def _find_free(self) -> int:
        """Let the OS pick a free port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port: int = s.getsockname()[1]
            return port

    def _is_occupied(self, port: int) -> bool:
        """Check if something is already listening on the port."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True

    def _load(self) -> None:
        """Load allocations from disk (dual-path fallback)."""
        try:
            data = json.loads(_STATE_FILE_READ.read_text())
            self._compose = data.get("compose", {})
            self._compose_native = data.get("compose_native", {})
            self._supabase_slots = {k: int(v) for k, v in data.get("supabase_slots", {}).items()}
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass

    def _save(self) -> None:
        """Persist allocations to disk with advisory file locking."""
        write_json_locked(
            _STATE_FILE_WRITE,
            {
                "compose": self._compose,
                "compose_native": self._compose_native,
                "supabase_slots": self._supabase_slots,
            },
        )


# Singleton.
ports = PortAllocator()
