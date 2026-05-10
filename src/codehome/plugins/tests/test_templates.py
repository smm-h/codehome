"""Tests for argument template loading and resolution.

Covers:

- All 3 built-in templates exist with correct arguments.
- Resolving a single template ("branch" -> list of ArgumentDecl).
- Resolving multiple templates ("branch", "dry-run" -> combined list).
- Template that includes other templates ("deploy-flags").
- Unknown template name -> clear error.
- Circular include detection.
- User-defined template overrides a built-in.
- User-defined template adds new templates.
- Deduplication: later template's argument wins on name collision.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from codehome.plugins.manifest import ArgumentDecl
from codehome.plugins.templates import (
    _TemplateEntry,
    _build_registry,
    _BUILTIN_TEMPLATES,
    resolve_includes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registry_from_raw(
    raw: dict[str, dict[str, Any]],
) -> dict[str, _TemplateEntry]:
    """Build a registry from raw template dicts (convenience wrapper)."""
    return _build_registry(raw)


def _names(args: tuple[ArgumentDecl, ...]) -> list[str]:
    """Extract argument names for concise assertions."""
    return [a.name for a in args]


# ===========================================================================
# 1. Built-in templates exist
# ===========================================================================


class TestBuiltinTemplates:
    """The 3 built-in templates exist and contain the correct arguments."""

    def test_builtins_have_three_entries(self) -> None:
        registry = _build_registry()
        assert set(registry) == {"branch", "dry-run", "deploy-flags"}

    def test_branch_template(self) -> None:
        registry = _build_registry()
        args = resolve_includes(("branch",), _registry_override=registry)

        assert len(args) == 1
        arg = args[0]
        assert arg.name == "--branch"
        assert arg.short == "-B"
        assert arg.help == "branch name (default: selected)"
        assert arg.metavar == "BRANCH"
        assert arg.required is False
        assert arg.hidden is False

    def test_dry_run_template(self) -> None:
        registry = _build_registry()
        args = resolve_includes(("dry-run",), _registry_override=registry)

        assert len(args) == 1
        arg = args[0]
        assert arg.name == "--dry-run"
        assert arg.short == "-D"
        assert arg.action == "store_true"
        assert arg.help == "show what would happen"

    def test_deploy_flags_template(self) -> None:
        registry = _build_registry()
        args = resolve_includes(("deploy-flags",), _registry_override=registry)

        names = _names(args)
        # deploy-flags includes dry-run and branch, plus its own args.
        # --branch from deploy-flags overrides the one from the branch
        # template (hidden=True variant).
        assert "--dry-run" in names
        assert "--branch" in names
        assert "--no-advance" in names
        assert "--skip-native-check" in names

    def test_deploy_flags_branch_is_hidden(self) -> None:
        """deploy-flags overrides --branch to be hidden."""
        registry = _build_registry()
        args = resolve_includes(("deploy-flags",), _registry_override=registry)

        branch_arg = next(a for a in args if a.name == "--branch")
        assert branch_arg.hidden is True


# ===========================================================================
# 2. Single template resolution
# ===========================================================================


class TestResolveSingle:
    """Resolving a single template returns its ArgumentDecl instances."""

    def test_resolve_branch(self) -> None:
        registry = _build_registry()
        args = resolve_includes(("branch",), _registry_override=registry)

        assert len(args) == 1
        assert isinstance(args[0], ArgumentDecl)
        assert args[0].name == "--branch"

    def test_resolve_dry_run(self) -> None:
        registry = _build_registry()
        args = resolve_includes(("dry-run",), _registry_override=registry)

        assert len(args) == 1
        assert isinstance(args[0], ArgumentDecl)
        assert args[0].name == "--dry-run"

    def test_resolve_returns_tuple(self) -> None:
        registry = _build_registry()
        result = resolve_includes(("branch",), _registry_override=registry)
        assert isinstance(result, tuple)

    def test_resolve_empty_includes(self) -> None:
        registry = _build_registry()
        args = resolve_includes((), _registry_override=registry)
        assert args == ()


# ===========================================================================
# 3. Multiple template resolution
# ===========================================================================


class TestResolveMultiple:
    """Resolving multiple templates returns the combined argument list."""

    def test_branch_and_dry_run(self) -> None:
        registry = _build_registry()
        args = resolve_includes(("branch", "dry-run"), _registry_override=registry)

        names = _names(args)
        assert "--branch" in names
        assert "--dry-run" in names
        assert len(args) == 2

    def test_order_preserved(self) -> None:
        """Arguments appear in the order templates are listed."""
        registry = _build_registry()
        args = resolve_includes(("dry-run", "branch"), _registry_override=registry)

        assert args[0].name == "--dry-run"
        assert args[1].name == "--branch"


# ===========================================================================
# 4. Nested includes
# ===========================================================================


class TestNestedIncludes:
    """Templates that include other templates expand recursively."""

    def test_deploy_flags_includes_dry_run_and_branch(self) -> None:
        """deploy-flags includes dry-run + branch; all args appear."""
        registry = _build_registry()
        args = resolve_includes(("deploy-flags",), _registry_override=registry)

        names = _names(args)
        # From dry-run:
        assert "--dry-run" in names
        # From deploy-flags own (overriding branch include):
        assert "--branch" in names
        assert "--no-advance" in names
        assert "--skip-native-check" in names

    def test_nested_custom_template(self) -> None:
        """A custom template that includes branch works."""
        custom_raw: dict[str, dict[str, Any]] = {
            "my-combo": {
                "includes": ["branch"],
                "arguments": [
                    {"name": "--verbose", "action": "store_true", "help": "be loud"},
                ],
            },
        }
        registry = _build_registry(custom_raw)
        args = resolve_includes(("my-combo",), _registry_override=registry)

        names = _names(args)
        assert "--branch" in names
        assert "--verbose" in names

    def test_deep_nesting(self) -> None:
        """Three levels of nesting: c includes b, b includes a."""
        custom_raw: dict[str, dict[str, Any]] = {
            "a": {"arguments": [{"name": "--alpha", "help": "a"}]},
            "b": {
                "includes": ["a"],
                "arguments": [{"name": "--beta", "help": "b"}],
            },
            "c": {
                "includes": ["b"],
                "arguments": [{"name": "--gamma", "help": "c"}],
            },
        }
        registry = _build_registry(custom_raw)
        args = resolve_includes(("c",), _registry_override=registry)

        names = _names(args)
        assert names == ["--alpha", "--beta", "--gamma"]


# ===========================================================================
# 5. Error handling
# ===========================================================================


class TestErrors:
    """Unknown template names and circular includes produce clear errors."""

    def test_unknown_template_raises(self) -> None:
        registry = _build_registry()
        with pytest.raises(ValueError, match="unknown argument template: 'nonexistent'"):
            resolve_includes(("nonexistent",), _registry_override=registry)

    def test_unknown_template_in_includes_raises(self) -> None:
        """A template that includes a nonexistent template raises."""
        custom_raw: dict[str, dict[str, Any]] = {
            "broken": {
                "includes": ["does-not-exist"],
                "arguments": [],
            },
        }
        registry = _build_registry(custom_raw)
        with pytest.raises(ValueError, match="unknown argument template"):
            resolve_includes(("broken",), _registry_override=registry)

    def test_circular_include_raises(self) -> None:
        """Circular include is detected."""
        custom_raw: dict[str, dict[str, Any]] = {
            "a": {"includes": ["b"], "arguments": []},
            "b": {"includes": ["a"], "arguments": []},
        }
        registry = _build_registry(custom_raw)
        with pytest.raises(ValueError, match="circular template include"):
            resolve_includes(("a",), _registry_override=registry)

    def test_self_referencing_include_raises(self) -> None:
        """A template that includes itself is circular."""
        custom_raw: dict[str, dict[str, Any]] = {
            "self-ref": {"includes": ["self-ref"], "arguments": []},
        }
        registry = _build_registry(custom_raw)
        with pytest.raises(ValueError, match="circular template include"):
            resolve_includes(("self-ref",), _registry_override=registry)

    def test_argument_missing_name_raises(self) -> None:
        """An argument entry without 'name' raises ValueError."""
        custom_raw: dict[str, dict[str, Any]] = {
            "bad": {"arguments": [{"help": "no name field"}]},
        }
        with pytest.raises(ValueError, match="missing 'name'"):
            _build_registry(custom_raw)


# ===========================================================================
# 6. User overrides
# ===========================================================================


class TestUserOverrides:
    """User-defined templates override built-ins and can add new ones."""

    def test_user_overrides_builtin(self) -> None:
        """A user template named 'branch' replaces the built-in."""
        user_raw: dict[str, dict[str, Any]] = {
            "branch": {
                "arguments": [
                    {
                        "name": "--branch",
                        "short": "-b",
                        "help": "custom branch help",
                    },
                ],
            },
        }
        registry = _build_registry(user_raw)
        args = resolve_includes(("branch",), _registry_override=registry)

        assert len(args) == 1
        assert args[0].short == "-b"
        assert args[0].help == "custom branch help"

    def test_user_adds_new_template(self) -> None:
        """A user template with a new name is added alongside builtins."""
        user_raw: dict[str, dict[str, Any]] = {
            "verbose": {
                "arguments": [
                    {
                        "name": "--verbose",
                        "short": "-v",
                        "action": "store_true",
                        "help": "increase output verbosity",
                    },
                ],
            },
        }
        registry = _build_registry(user_raw)

        # Builtins still present.
        assert "branch" in registry
        assert "dry-run" in registry
        assert "deploy-flags" in registry

        # New template works.
        args = resolve_includes(("verbose",), _registry_override=registry)
        assert len(args) == 1
        assert args[0].name == "--verbose"

    def test_user_override_does_not_affect_other_builtins(self) -> None:
        """Overriding one built-in leaves the others intact."""
        user_raw: dict[str, dict[str, Any]] = {
            "branch": {
                "arguments": [
                    {"name": "--branch", "help": "custom"},
                ],
            },
        }
        registry = _build_registry(user_raw)

        # dry-run should still be the built-in version.
        dry_args = resolve_includes(("dry-run",), _registry_override=registry)
        assert dry_args[0].short == "-D"
        assert dry_args[0].help == "show what would happen"


# ===========================================================================
# 7. Deduplication
# ===========================================================================


class TestDeduplication:
    """When multiple templates define the same argument name, last wins."""

    def test_later_template_wins(self) -> None:
        """If branch and deploy-flags both define --branch, the later wins."""
        registry = _build_registry()
        # branch first, then deploy-flags.
        args = resolve_includes(
            ("branch", "deploy-flags"), _registry_override=registry
        )

        branch_args = [a for a in args if a.name == "--branch"]
        assert len(branch_args) == 1
        # deploy-flags defines --branch with hidden=True, which should win.
        assert branch_args[0].hidden is True

    def test_explicit_order_matters(self) -> None:
        """Reversing order changes which definition wins."""
        registry = _build_registry()
        # deploy-flags first (hidden branch), then branch (visible).
        args = resolve_includes(
            ("deploy-flags", "branch"), _registry_override=registry
        )

        branch_args = [a for a in args if a.name == "--branch"]
        assert len(branch_args) == 1
        # branch template listed second, so its visible --branch wins.
        assert branch_args[0].hidden is False


# ===========================================================================
# 8. User config file loading (integration)
# ===========================================================================


class TestUserConfigLoading:
    """Test loading templates from a config.toml file on disk."""

    def test_load_from_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Templates are loaded from [arg-templates] in config.toml."""
        config_dir = tmp_path / ".codehome"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(textwrap.dedent("""\
            [arg-templates.verbose]
            [[arg-templates.verbose.arguments]]
            name = "--verbose"
            short = "-v"
            action = "store_true"
            help = "increase verbosity"
        """))

        # Point codehome_home() at our tmp dir.
        monkeypatch.setenv("CODEHOME_HOME", str(config_dir))

        # Reset and reload.
        from codehome.plugins import templates

        templates._reset_registry()
        try:
            registry = templates._get_registry()

            # Built-ins still present.
            assert "branch" in registry
            assert "dry-run" in registry
            assert "deploy-flags" in registry

            # User template loaded.
            assert "verbose" in registry
            args = resolve_includes(("verbose",), _registry_override=registry)
            assert args[0].name == "--verbose"
            assert args[0].short == "-v"
        finally:
            templates._reset_registry()

    def test_missing_config_file_uses_builtins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a config.toml, only built-in templates are available."""
        empty_dir = tmp_path / "empty-home"
        empty_dir.mkdir()
        monkeypatch.setenv("CODEHOME_HOME", str(empty_dir))

        from codehome.plugins import templates

        templates._reset_registry()
        try:
            registry = templates._get_registry()
            assert set(registry) == {"branch", "dry-run", "deploy-flags"}
        finally:
            templates._reset_registry()

    def test_config_without_arg_templates_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A config.toml without [arg-templates] uses only builtins."""
        config_dir = tmp_path / ".codehome"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('[plugins]\npaths = []\n')
        monkeypatch.setenv("CODEHOME_HOME", str(config_dir))

        from codehome.plugins import templates

        templates._reset_registry()
        try:
            registry = templates._get_registry()
            assert set(registry) == {"branch", "dry-run", "deploy-flags"}
        finally:
            templates._reset_registry()
