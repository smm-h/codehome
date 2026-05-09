"""Tests for PortAllocator -- allocation, release, Supabase slots, persistence."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from codehome.serve.ports import (
    _DEFAULTS,
    MAX_SUPABASE_STACKS,
    SUPABASE_BASE_PORTS,
    PortAllocator,
)


@pytest.fixture
def state_file(tmp_path):
    """Point the module-level _STATE_FILE_READ/WRITE at a temp location."""
    path = tmp_path / "serve-ports.json"
    with (
        patch("codehome.serve.ports._STATE_FILE_READ", path),
        patch("codehome.serve.ports._STATE_FILE_WRITE", path),
    ):
        yield path


@pytest.fixture
def alloc(state_file):
    """Fresh PortAllocator backed by a temp state file."""
    return PortAllocator()


# -- allocate ----------------------------------------------------------------


def test_allocate_preferred_default_when_free(state_file):
    """When the preferred port is free, allocate returns it."""
    with patch.object(PortAllocator, "_is_occupied", return_value=False):
        alloc = PortAllocator()
        port = alloc.allocate("mybranch/vite")
    assert port == _DEFAULTS["vite"]


def test_allocate_returns_existing_on_repeat_call(alloc):
    """Calling allocate twice with the same key returns the same port."""
    with patch.object(PortAllocator, "_is_occupied", return_value=False):
        first = alloc.allocate("b/vite")
        second = alloc.allocate("b/vite")
    assert first == second


def test_allocate_falls_back_when_preferred_occupied(state_file):
    """If the preferred port is occupied, a fallback port is used."""
    with patch.object(PortAllocator, "_is_occupied", return_value=True):
        alloc = PortAllocator()
        port = alloc.allocate("b/vite")
    assert port != _DEFAULTS["vite"]


def test_allocate_falls_back_when_preferred_already_allocated(state_file):
    """If the preferred port is already allocated to another key, fall back."""
    with patch.object(PortAllocator, "_is_occupied", return_value=False):
        alloc = PortAllocator()
        first = alloc.allocate("branch-a/vite")
        assert first == _DEFAULTS["vite"]
        second = alloc.allocate("branch-b/vite")
    # Second key shares the same base type, but the preferred port is taken.
    assert second != _DEFAULTS["vite"]


# -- release ------------------------------------------------------------------


def test_release_removes_allocation(alloc):
    """After release, the key is gone and get_port returns None."""
    with patch.object(PortAllocator, "_is_occupied", return_value=False):
        alloc.allocate("b/vite")
    alloc.release("b/vite")
    assert alloc.get_port("b/vite") is None


def test_release_nonexistent_key_is_noop(alloc):
    """Releasing a key that was never allocated does not raise."""
    alloc.release("nonexistent/key")  # should not raise


# -- allocate_supabase_slot ---------------------------------------------------


def test_allocate_supabase_slot_0_first(alloc):
    """First Supabase allocation gets slot 0."""
    slot, _ports = alloc.allocate_supabase_slot("branch-a")
    assert slot == 0


def test_allocate_supabase_slot_1_when_0_taken(alloc):
    """Second branch gets slot 1."""
    alloc.allocate_supabase_slot("branch-a")
    slot, _ = alloc.allocate_supabase_slot("branch-b")
    assert slot == 1


def test_allocate_supabase_slot_returns_existing_for_same_branch(alloc):
    """Re-allocating the same branch returns the same slot."""
    slot1, ports1 = alloc.allocate_supabase_slot("branch-a")
    slot2, ports2 = alloc.allocate_supabase_slot("branch-a")
    assert slot1 == slot2
    assert ports1 == ports2


def test_allocate_supabase_slot_raises_at_max(alloc):
    """Exceeding MAX_SUPABASE_STACKS raises RuntimeError."""
    for i in range(MAX_SUPABASE_STACKS):
        alloc.allocate_supabase_slot(f"branch-{i}")
    with pytest.raises(RuntimeError, match="Maximum"):
        alloc.allocate_supabase_slot("one-too-many")


# -- slot recycling -----------------------------------------------------------


def test_slot_recycling_lowest_free_reused(alloc):
    """After releasing a low slot, the next allocation reuses it."""
    alloc.allocate_supabase_slot("a")  # slot 0
    alloc.allocate_supabase_slot("b")  # slot 1
    alloc.allocate_supabase_slot("c")  # slot 2
    alloc.release_supabase_slot("b")  # frees slot 1
    slot, _ = alloc.allocate_supabase_slot("d")
    assert slot == 1


# -- _slot_ports --------------------------------------------------------------


def test_slot_ports_offset_correctness(alloc):
    """Every port in _slot_ports equals base + slot * 100."""
    for slot in range(MAX_SUPABASE_STACKS):
        ports = alloc._slot_ports(slot)
        assert len(ports) == len(SUPABASE_BASE_PORTS)
        for name, base in SUPABASE_BASE_PORTS.items():
            assert ports[name] == base + slot * 100, (
                f"slot={slot}, port={name}: expected {base + slot * 100}, got {ports[name]}"
            )


# -- all_allocations ----------------------------------------------------------


def test_all_allocations_correct_shape(alloc):
    """all_allocations returns compose and supabase dicts."""
    with patch.object(PortAllocator, "_is_occupied", return_value=False):
        alloc.allocate("x/vite")
    alloc.allocate_supabase_slot("my-branch")
    result = alloc.all_allocations()
    assert "compose" in result
    assert "supabase" in result
    assert result["compose"]["x/vite"] == _DEFAULTS["vite"]
    assert "my-branch" in result["supabase"]
    assert set(result["supabase"]["my-branch"].keys()) == set(SUPABASE_BASE_PORTS.keys())


# -- persistence --------------------------------------------------------------


def test_persistence_round_trip(state_file):
    """Allocations survive a save/load cycle through a new instance."""
    with patch.object(PortAllocator, "_is_occupied", return_value=False):
        a = PortAllocator()
        a.allocate("br/vite")
        a.allocate_supabase_slot("sb-branch")

    # New instance should load the same state from disk.
    b = PortAllocator()
    assert b.get_port("br/vite") == _DEFAULTS["vite"]
    sb = b.get_supabase_slot("sb-branch")
    assert sb is not None
    assert sb[0] == 0


def test_persistence_handles_corrupt_file(state_file):
    """A corrupt state file is silently ignored; allocator starts empty."""
    state_file.write_text("NOT VALID JSON {{{")
    alloc = PortAllocator()
    assert alloc.all_allocations() == {"compose": {}, "supabase": {}, "compose_native": {}}
