"""Tests for the check registry and decorator.

Covers:

- Registration and lookup (get, group, groups, all).
- Duplicate name detection.
- Group filtering and sorting.
- @register_check decorator (async enforcement, side-effect registration).
- CheckContext construction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codehome.checks.registry import CheckContext, CheckEntry, CheckRegistry
from codehome.checks.result import CheckResult

# -- CheckContext ------------------------------------------------------------


class TestCheckContext:
    def test_defaults(self) -> None:
        ctx = CheckContext(root=Path("/project"))
        assert ctx.root == Path("/project")
        assert ctx.cwd == Path("/project")
        assert ctx.staged_files is None

    def test_cwd_defaults_to_root(self) -> None:
        ctx = CheckContext(root=Path("/project"))
        assert ctx.cwd == ctx.root

    def test_explicit_cwd(self) -> None:
        ctx = CheckContext(root=Path("/project"), cwd=Path("/project/dashboard"))
        assert ctx.cwd == Path("/project/dashboard")

    def test_with_staged_files(self) -> None:
        ctx = CheckContext(root=Path("/project"), staged_files=("a.py", "b.ts"))
        assert ctx.staged_files == ("a.py", "b.ts")


# -- CheckEntry --------------------------------------------------------------


class TestCheckEntry:
    def test_repr(self) -> None:
        entry = CheckEntry(
            name="ruff-check",
            group="precommit",
            timeout=30,
            depends_on=("ruff-format",),
            advisory=True,
        )
        r = repr(entry)
        assert "ruff-check" in r
        assert "precommit" in r
        assert "depends_on" in r
        assert "advisory=True" in r


# -- CheckRegistry -----------------------------------------------------------


async def _dummy_check(ctx: CheckContext) -> CheckResult:
    """Minimal async check function for registration tests."""
    return CheckResult(name="dummy", outcome="pass", duration_ms=0.0)


async def _another_check(ctx: CheckContext) -> CheckResult:
    return CheckResult(name="another", outcome="pass", duration_ms=0.0)


class TestCheckRegistry:
    def _fresh_registry(self) -> CheckRegistry:
        return CheckRegistry()

    def test_register_and_get(self) -> None:
        reg = self._fresh_registry()
        reg.register("ruff-check", "precommit", 30, ".", (), False, _dummy_check)
        entry = reg.get("ruff-check")
        assert entry is not None
        assert entry.name == "ruff-check"
        assert entry.group == "precommit"
        assert entry.timeout == 30
        assert entry.fn is _dummy_check

    def test_get_missing(self) -> None:
        reg = self._fresh_registry()
        assert reg.get("nonexistent") is None

    def test_duplicate_raises(self) -> None:
        reg = self._fresh_registry()
        reg.register("ruff-check", "precommit", 30, ".", (), False, _dummy_check)
        with pytest.raises(ValueError, match="duplicate"):
            reg.register("ruff-check", "precommit", 30, ".", (), False, _another_check)

    def test_group_filtering(self) -> None:
        reg = self._fresh_registry()
        reg.register("b-check", "gate", 60, ".", (), False, _dummy_check)
        reg.register("a-check", "gate", 60, ".", (), False, _another_check)
        reg.register("pre-check", "precommit", 10, ".", (), False, _dummy_check)

        gate_entries = reg.group("gate")
        assert len(gate_entries) == 2
        # Sorted by name.
        assert gate_entries[0].name == "a-check"
        assert gate_entries[1].name == "b-check"

        pre_entries = reg.group("precommit")
        assert len(pre_entries) == 1

    def test_group_empty(self) -> None:
        reg = self._fresh_registry()
        assert reg.group("nonexistent") == ()

    def test_groups(self) -> None:
        reg = self._fresh_registry()
        reg.register("a", "beta", 10, ".", (), False, _dummy_check)
        reg.register("b", "alpha", 10, ".", (), False, _another_check)
        assert reg.groups() == ("alpha", "beta")

    def test_all(self) -> None:
        reg = self._fresh_registry()
        reg.register("z-check", "g1", 10, ".", (), False, _dummy_check)
        reg.register("a-check", "g2", 20, ".", (), False, _another_check)
        entries = reg.all()
        assert len(entries) == 2
        assert entries[0].name == "a-check"
        assert entries[1].name == "z-check"

    def test_contains(self) -> None:
        reg = self._fresh_registry()
        reg.register("x", "g", 10, ".", (), False, _dummy_check)
        assert "x" in reg
        assert "y" not in reg

    def test_len(self) -> None:
        reg = self._fresh_registry()
        assert len(reg) == 0
        reg.register("x", "g", 10, ".", (), False, _dummy_check)
        assert len(reg) == 1

    def test_clear(self) -> None:
        reg = self._fresh_registry()
        reg.register("x", "g", 10, ".", (), False, _dummy_check)
        reg.clear()
        assert len(reg) == 0
        assert reg.get("x") is None


# -- @register_check decorator ----------------------------------------------


class TestRegisterCheckDecorator:
    def test_sync_function_raises(self) -> None:
        from codehome.checks.registry import register_check

        # We test against the global _registry, so we need to be careful.
        # Instead, just verify the TypeError is raised.
        def sync_fn(ctx: CheckContext) -> CheckResult:  # type: ignore[empty-body]
            pass

        with pytest.raises(TypeError, match="async function"):
            register_check("sync-test", group="test", timeout=10)(sync_fn)  # type: ignore[arg-type]
