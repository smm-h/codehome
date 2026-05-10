"""Tests for the plugin infrastructure.

Covers:

- Manifest parsing (valid minimal, full, missing fields, edge cases).
- State merge semantics (new plugins, preserved toggles, pruning).
- Topological sort (no deps, linear chain, cycles, missing deps).
- Registry operations (register, get, list, is_enabled, clear, passthrough, overwrite).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from codehome.plugins import registry
from codehome.plugins.discovery import discover_plugins
from codehome.plugins.loader import _topological_sort, load_all_plugins
from codehome.plugins.manifest import (
    ArgumentDecl,
    CommandDecl,
    DashboardDecl,
    PluginManifest,
    parse_manifest,
)
from codehome.plugins.registry import LoadedPlugin
from codehome.plugins.state import load_state, merge_discovered, save_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(plugin_dir: Path, content: str) -> None:
    """Write a ``plugin.toml`` inside *plugin_dir*."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(content)


def _minimal_manifest_toml(name: str = "test-plugin", version: str = "0.1.0") -> str:
    """Return a minimal valid plugin.toml string with one command."""
    return (
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        f'description = "A test plugin"\n'
        "\n"
        "[[commands]]\n"
        'name = "hello"\n'
        'handler = "handle_hello"\n'
        'description = "Say hello"\n'
    )


def _make_loaded_plugin(
    name: str,
    *,
    passthrough: bool = False,
    version: str = "1.0.0",
    description: str = "",
) -> LoadedPlugin:
    """Build a LoadedPlugin with a stubbed manifest."""
    manifest = PluginManifest(
        name=name,
        version=version,
        description=description,
        passthrough=passthrough,
    )
    return LoadedPlugin(
        name=name,
        version=version,
        description=description,
        plugin_dir="/fake/" + name,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Reset the plugin registry before and after each test."""
    registry.clear()
    yield
    registry.clear()


# ===========================================================================
# 1. Manifest parsing
# ===========================================================================


class TestManifestParsing:
    """Tests for parse_manifest() and the dataclass types it produces."""

    def test_valid_minimal_manifest(self, tmp_path: Path) -> None:
        """A manifest with just name, version, description, and one command parses OK."""
        plugin_dir = tmp_path / "my-plugin"
        _write_toml(plugin_dir, _minimal_manifest_toml("my-plugin", "1.0.0"))

        m = parse_manifest(plugin_dir)

        assert m.name == "my-plugin"
        assert m.version == "1.0.0"
        assert m.description == "A test plugin"
        assert m.enabled is True  # default
        assert m.requires == ()
        assert m.dependencies == ()
        assert m.passthrough is False
        assert len(m.commands) == 1
        assert m.commands[0].name == "hello"
        assert m.commands[0].handler == "handle_hello"
        assert m.commands[0].description == "Say hello"
        assert m.checks == ()
        assert m.dashboard is None

    def test_valid_full_manifest(self, tmp_path: Path) -> None:
        """A manifest with all optional sections parses every field correctly."""
        plugin_dir = tmp_path / "full-plugin"
        toml_content = """\
name = "full-plugin"
version = "2.3.1"
description = "A fully loaded plugin"
enabled = false
requires = ["ssh", "mkcert"]
dependencies = ["core-plugin"]
passthrough = true

[[commands]]
name = "run"
handler = "handle_run"
description = "Run the thing"

[[commands]]
name = "stop"
handler = "handle_stop"

[[checks]]
name = "lint-check"
group = "precommit"
timeout = 30
handler = "check_lint"
description = "Lint the plugin"
cwd = "src/"
depends_on = ["format-check"]
advisory = true

[dashboard]
group = "branch"
route = "/full"
icon = "zap"
label = "Full Plugin"
event_types = ["build-started", "build-finished"]
"""
        _write_toml(plugin_dir, toml_content)

        m = parse_manifest(plugin_dir)

        assert m.name == "full-plugin"
        assert m.version == "2.3.1"
        assert m.description == "A fully loaded plugin"
        assert m.enabled is False
        assert m.requires == ("ssh", "mkcert")
        assert m.dependencies == ("core-plugin",)
        assert m.passthrough is True

        # Commands
        assert len(m.commands) == 2
        assert m.commands[0] == CommandDecl(
            name="run",
            handler="handle_run",
            description="Run the thing",
        )
        assert m.commands[1].name == "stop"
        assert m.commands[1].handler == "handle_stop"
        assert m.commands[1].description == ""  # defaults

        # Checks
        assert len(m.checks) == 1
        chk = m.checks[0]
        assert chk.name == "lint-check"
        assert chk.group == "precommit"
        assert chk.timeout == 30
        assert chk.handler == "check_lint"
        assert chk.description == "Lint the plugin"
        assert chk.cwd == "src/"
        assert chk.depends_on == ("format-check",)
        assert chk.advisory is True

        # Dashboard
        assert m.dashboard is not None
        assert m.dashboard == DashboardDecl(
            group="branch",
            route="/full",
            icon="zap",
            label="Full Plugin",
            event_types=("build-started", "build-finished"),
        )

    def test_missing_required_field_name(self, tmp_path: Path) -> None:
        """Omitting the required 'name' field raises ValueError."""
        plugin_dir = tmp_path / "bad-plugin"
        _write_toml(
            plugin_dir,
            'version = "1.0.0"\ndescription = "no name"\n',
        )

        with pytest.raises(ValueError, match="name"):
            parse_manifest(plugin_dir)

    def test_missing_required_field_version(self, tmp_path: Path) -> None:
        """Omitting the required 'version' field raises ValueError."""
        plugin_dir = tmp_path / "bad-plugin"
        _write_toml(
            plugin_dir,
            'name = "bad"\ndescription = "no version"\n',
        )

        with pytest.raises(ValueError, match="version"):
            parse_manifest(plugin_dir)

    def test_missing_command_handler(self, tmp_path: Path) -> None:
        """A command entry missing 'handler' raises ValueError."""
        plugin_dir = tmp_path / "bad-cmd"
        toml_content = """\
name = "bad-cmd"
version = "1.0.0"

[[commands]]
name = "whoops"
description = "forgot the handler"
"""
        _write_toml(plugin_dir, toml_content)

        with pytest.raises(ValueError, match="handler"):
            parse_manifest(plugin_dir)

    def test_missing_command_name(self, tmp_path: Path) -> None:
        """A command entry missing 'name' raises ValueError."""
        plugin_dir = tmp_path / "bad-cmd"
        toml_content = """\
name = "bad-cmd"
version = "1.0.0"

[[commands]]
handler = "handle_it"
"""
        _write_toml(plugin_dir, toml_content)

        with pytest.raises(ValueError, match="name"):
            parse_manifest(plugin_dir)

    def test_empty_commands_list(self, tmp_path: Path) -> None:
        """A manifest with no [[commands]] array at all is valid."""
        plugin_dir = tmp_path / "no-cmds"
        _write_toml(
            plugin_dir,
            'name = "no-cmds"\nversion = "1.0.0"\ndescription = "No commands"\n',
        )

        m = parse_manifest(plugin_dir)

        assert m.commands == ()
        assert m.name == "no-cmds"

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        """Unknown top-level keys are silently ignored (forward compatibility)."""
        plugin_dir = tmp_path / "future-plugin"
        toml_content = """\
name = "future-plugin"
version = "1.0.0"
description = "Has future keys"
some_future_key = "should be ignored"
another_future_section = { nested = true }
"""
        _write_toml(plugin_dir, toml_content)

        m = parse_manifest(plugin_dir)

        assert m.name == "future-plugin"
        assert m.version == "1.0.0"
        # No error raised, extra keys simply not present on the dataclass.
        assert not hasattr(m, "some_future_key")

    def test_no_plugin_toml_raises_file_not_found(self, tmp_path: Path) -> None:
        """parse_manifest on a directory without plugin.toml raises FileNotFoundError."""
        plugin_dir = tmp_path / "empty-dir"
        plugin_dir.mkdir()

        with pytest.raises(FileNotFoundError, match=r"plugin\.toml"):
            parse_manifest(plugin_dir)

    def test_invalid_toml_syntax(self, tmp_path: Path) -> None:
        """Malformed TOML content raises ValueError."""
        plugin_dir = tmp_path / "bad-toml"
        _write_toml(plugin_dir, "this is not [valid toml {{{}}")

        with pytest.raises(ValueError, match="invalid TOML"):
            parse_manifest(plugin_dir)


# ===========================================================================
# 2. State merge semantics
# ===========================================================================


class TestStateMerge:
    """Tests for load_state(), save_state(), and merge_discovered()."""

    def test_load_state_missing_file(self, tmp_path: Path) -> None:
        """load_state returns empty structure when no state file exists."""
        state = load_state(tmp_path)

        assert state == {"plugins": {}, "last_scanned": None}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """save_state writes JSON that load_state reads back identically."""
        original = {
            "plugins": {
                "alpha": {"name": "alpha", "enabled": True},
            },
            "last_scanned": "2026-01-01T00:00:00+00:00",
        }

        save_state(tmp_path, original)
        loaded = load_state(tmp_path)

        assert loaded == original

    def test_load_state_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt JSON in the state file returns the empty default."""
        state_dir = tmp_path / ".codehome"
        state_dir.mkdir()
        (state_dir / "plugins-state.json").write_text("{not valid json")

        state = load_state(tmp_path)

        assert state == {"plugins": {}, "last_scanned": None}

    def test_new_plugin_defaults_to_manifest_enabled(self, tmp_path: Path) -> None:
        """A plugin appearing on disk for the first time gets manifest's enabled value."""
        old_state: dict[str, Any] = {"plugins": {}, "last_scanned": None}

        # Plugin with enabled=True in manifest.
        manifest_enabled = PluginManifest(
            name="new-enabled",
            version="1.0.0",
            enabled=True,
        )
        # Plugin with enabled=False in manifest.
        manifest_disabled = PluginManifest(
            name="new-disabled",
            version="1.0.0",
            enabled=False,
        )

        discovered = [
            (tmp_path / "new-enabled", manifest_enabled),
            (tmp_path / "new-disabled", manifest_disabled),
        ]

        merged = merge_discovered(old_state, discovered)

        assert merged["plugins"]["new-enabled"]["enabled"] is True
        assert merged["plugins"]["new-disabled"]["enabled"] is False

    def test_existing_plugin_preserves_toggle(self) -> None:
        """An existing plugin keeps its persisted enabled/disabled state."""
        old_state = {
            "plugins": {
                "my-plugin": {
                    "name": "my-plugin",
                    "enabled": False,  # user disabled it
                },
            },
            "last_scanned": None,
        }
        # Manifest says enabled=True, but the user override should win.
        manifest = PluginManifest(
            name="my-plugin",
            version="2.0.0",
            enabled=True,
        )
        discovered = [(Path("/fake/my-plugin"), manifest)]

        merged = merge_discovered(old_state, discovered)

        assert merged["plugins"]["my-plugin"]["enabled"] is False
        # Metadata should be updated from the manifest.
        assert merged["plugins"]["my-plugin"]["version"] == "2.0.0"

    def test_removed_plugin_pruned(self) -> None:
        """A plugin in state but no longer on disk is pruned from merged state."""
        old_state = {
            "plugins": {
                "gone-plugin": {"name": "gone-plugin", "enabled": True},
                "still-here": {"name": "still-here", "enabled": True},
            },
            "last_scanned": None,
        }
        # Only "still-here" was discovered on disk.
        manifest = PluginManifest(name="still-here", version="1.0.0")
        discovered = [(Path("/fake/still-here"), manifest)]

        merged = merge_discovered(old_state, discovered)

        assert "gone-plugin" not in merged["plugins"]
        assert "still-here" in merged["plugins"]

    def test_empty_old_state_all_appear_enabled(self) -> None:
        """When old state is empty, all discovered plugins appear with their manifest default."""
        old_state: dict[str, Any] = {"plugins": {}, "last_scanned": None}
        manifests = [
            PluginManifest(name="alpha", version="1.0.0", enabled=True),
            PluginManifest(name="beta", version="1.0.0", enabled=True),
            PluginManifest(name="gamma", version="1.0.0", enabled=True),
        ]
        discovered = [(Path(f"/fake/{m.name}"), m) for m in manifests]

        merged = merge_discovered(old_state, discovered)

        assert len(merged["plugins"]) == 3
        for name in ("alpha", "beta", "gamma"):
            assert merged["plugins"][name]["enabled"] is True

    def test_merge_sets_last_scanned(self) -> None:
        """merge_discovered always populates last_scanned with an ISO timestamp."""
        old_state: dict[str, Any] = {"plugins": {}, "last_scanned": None}
        manifest = PluginManifest(name="ts-test", version="1.0.0")
        discovered = [(Path("/fake/ts-test"), manifest)]

        merged = merge_discovered(old_state, discovered)

        assert merged["last_scanned"] is not None
        assert "T" in merged["last_scanned"]  # ISO-8601 format check


# ===========================================================================
# 3. Topological sort
# ===========================================================================


class TestTopologicalSort:
    """Tests for the dependency-ordering logic in the loader."""

    @staticmethod
    def _make_plugins(
        specs: list[tuple[str, list[str]]],
    ) -> list[tuple[Path, PluginManifest]]:
        """Build a list of (Path, PluginManifest) from (name, deps) pairs."""
        return [
            (
                Path(f"/fake/{name}"),
                PluginManifest(
                    name=name,
                    version="1.0.0",
                    dependencies=tuple(deps),
                ),
            )
            for name, deps in specs
        ]

    def test_no_dependencies_alphabetical(self) -> None:
        """Plugins with no dependencies are emitted in alphabetical order."""
        plugins = self._make_plugins(
            [
                ("charlie", []),
                ("alpha", []),
                ("bravo", []),
            ]
        )

        ordered = _topological_sort(plugins)
        names = [m.name for _, m in ordered]

        assert names == ["alpha", "bravo", "charlie"]

    def test_linear_chain(self) -> None:
        """A -> B -> C results in C, B, A order (deps first)."""
        plugins = self._make_plugins(
            [
                ("A", ["B"]),
                ("B", ["C"]),
                ("C", []),
            ]
        )

        ordered = _topological_sort(plugins)
        names = [m.name for _, m in ordered]

        assert names == ["C", "B", "A"]

    def test_diamond_dependency(self) -> None:
        """Diamond: D depends on B and C, both depend on A. A must come first."""
        plugins = self._make_plugins(
            [
                ("D", ["B", "C"]),
                ("B", ["A"]),
                ("C", ["A"]),
                ("A", []),
            ]
        )

        ordered = _topological_sort(plugins)
        names = [m.name for _, m in ordered]

        # A must be before B and C; B and C must be before D.
        assert names.index("A") < names.index("B")
        assert names.index("A") < names.index("C")
        assert names.index("B") < names.index("D")
        assert names.index("C") < names.index("D")

    def test_cycle_detection(self) -> None:
        """A cycle (A -> B -> A) raises RuntimeError."""
        plugins = self._make_plugins(
            [
                ("A", ["B"]),
                ("B", ["A"]),
            ]
        )

        with pytest.raises(RuntimeError, match="circular dependency"):
            _topological_sort(plugins)

    def test_missing_dependency_skips_plugin(self) -> None:
        """A plugin depending on a nonexistent plugin is skipped."""
        plugins = self._make_plugins(
            [
                ("A", ["nonexistent"]),
                ("B", []),
            ]
        )

        ordered = _topological_sort(plugins)
        names = [m.name for _, m in ordered]

        # A should be skipped because its dep is missing; B loads fine.
        assert "A" not in names
        assert "B" in names

    def test_transitive_missing_dep_skips_dependents(self) -> None:
        """If B is skipped (missing dep), A depending on B is also skipped."""
        plugins = self._make_plugins(
            [
                ("A", ["B"]),
                ("B", ["nonexistent"]),
                ("C", []),
            ]
        )

        ordered = _topological_sort(plugins)
        names = [m.name for _, m in ordered]

        # Both A and B skipped; C loads.
        assert "A" not in names
        assert "B" not in names
        assert "C" in names

    def test_self_cycle(self) -> None:
        """A plugin depending on itself raises RuntimeError."""
        plugins = self._make_plugins(
            [
                ("A", ["A"]),
            ]
        )

        with pytest.raises(RuntimeError, match="circular dependency"):
            _topological_sort(plugins)


# ===========================================================================
# 4. Registry operations
# ===========================================================================


class TestRegistry:
    """Tests for the module-level plugin registry."""

    def test_register_and_get(self) -> None:
        """Register a plugin, retrieve it by name."""
        plugin = _make_loaded_plugin("my-plugin", version="1.2.3")

        registry.register(plugin)
        got = registry.get("my-plugin")

        assert got is plugin
        assert got.name == "my-plugin"
        assert got.version == "1.2.3"

    def test_get_unknown_returns_none(self) -> None:
        """get() on an unregistered name returns None."""
        assert registry.get("does-not-exist") is None

    def test_is_enabled(self) -> None:
        """is_enabled() returns True for registered plugins, False otherwise."""
        plugin = _make_loaded_plugin("check-me")
        registry.register(plugin)

        assert registry.is_enabled("check-me") is True
        assert registry.is_enabled("not-registered") is False

    def test_clear_empties_everything(self) -> None:
        """clear() removes all registered plugins."""
        registry.register(_make_loaded_plugin("a"))
        registry.register(_make_loaded_plugin("b"))
        assert len(registry.list_plugins()) == 2

        registry.clear()

        assert registry.list_plugins() == []
        assert registry.is_enabled("a") is False
        assert registry.passthrough_commands() == frozenset()

    def test_list_plugins_sorted(self) -> None:
        """list_plugins() returns all registered plugins sorted by name."""
        registry.register(_make_loaded_plugin("zebra"))
        registry.register(_make_loaded_plugin("alpha"))
        registry.register(_make_loaded_plugin("middle"))

        names = [p.name for p in registry.list_plugins()]

        assert names == ["alpha", "middle", "zebra"]

    def test_passthrough_commands(self) -> None:
        """passthrough_commands() tracks plugins with passthrough=True."""
        registry.register(_make_loaded_plugin("normal", passthrough=False))
        registry.register(_make_loaded_plugin("pass-thru", passthrough=True))

        pt = registry.passthrough_commands()

        assert pt == frozenset({"pass-thru"})
        assert "normal" not in pt

    def test_register_overwrites(self) -> None:
        """Registering a plugin with the same name overwrites the previous entry."""
        v1 = _make_loaded_plugin("overwrite-me", version="1.0.0")
        v2 = _make_loaded_plugin("overwrite-me", version="2.0.0")

        registry.register(v1)
        registry.register(v2)

        got = registry.get("overwrite-me")
        assert got is not None
        assert got.version == "2.0.0"
        assert len(registry.list_plugins()) == 1


# ===========================================================================
# 5. Discovery (integration with filesystem)
# ===========================================================================


class TestDiscovery:
    """Tests for discover_plugins() scanning a temporary plugins/ directory."""

    def test_discovers_valid_plugins(self, tmp_path: Path) -> None:
        """Valid plugin directories with plugin.toml are discovered."""
        plugins_dir = tmp_path / "plugins"
        alpha_dir = plugins_dir / "alpha"
        beta_dir = plugins_dir / "beta"
        _write_toml(alpha_dir, _minimal_manifest_toml("alpha"))
        _write_toml(beta_dir, _minimal_manifest_toml("beta"))

        result = discover_plugins(tmp_path)

        names = [m.name for _, m in result.plugins]
        assert names == ["alpha", "beta"]
        assert result.errors == []

    def test_skips_hidden_and_private_dirs(self, tmp_path: Path) -> None:
        """Directories starting with . or _ are skipped."""
        plugins_dir = tmp_path / "plugins"
        _write_toml(plugins_dir / ".hidden", _minimal_manifest_toml("hidden"))
        _write_toml(plugins_dir / "_private", _minimal_manifest_toml("private"))
        _write_toml(plugins_dir / "visible", _minimal_manifest_toml("visible"))

        result = discover_plugins(tmp_path)

        names = [m.name for _, m in result.plugins]
        assert names == ["visible"]

    def test_skips_dirs_without_manifest(self, tmp_path: Path) -> None:
        """Directories without plugin.toml are silently skipped."""
        plugins_dir = tmp_path / "plugins"
        (plugins_dir / "no-manifest").mkdir(parents=True)
        _write_toml(plugins_dir / "has-manifest", _minimal_manifest_toml("has-manifest"))

        result = discover_plugins(tmp_path)

        names = [m.name for _, m in result.plugins]
        assert names == ["has-manifest"]

    def test_collects_parse_errors(self, tmp_path: Path) -> None:
        """A bad manifest produces an error but does not stop discovery."""
        plugins_dir = tmp_path / "plugins"
        _write_toml(plugins_dir / "good", _minimal_manifest_toml("good"))
        # Bad manifest: missing required fields.
        _write_toml(plugins_dir / "bad", 'description = "no name or version"')

        result = discover_plugins(tmp_path)

        names = [m.name for _, m in result.plugins]
        assert names == ["good"]
        assert len(result.errors) == 1
        assert "name" in result.errors[0]

    def test_empty_plugins_dir(self, tmp_path: Path) -> None:
        """An empty plugins/ directory returns no plugins and no errors."""
        (tmp_path / "plugins").mkdir()

        result = discover_plugins(tmp_path)

        assert result.plugins == []
        assert result.errors == []

    def test_no_plugins_dir(self, tmp_path: Path) -> None:
        """If plugins/ does not exist at all, return empty result."""
        result = discover_plugins(tmp_path)

        assert result.plugins == []
        assert result.errors == []


# ===========================================================================
# 6. Integration: load_all_plugins pipeline
# ===========================================================================


class TestLoadAllPlugins:
    """Integration tests for load_all_plugins(): discovery -> state -> sort -> import -> registry."""

    @staticmethod
    def _setup_plugin(
        root: Path,
        name: str,
        *,
        handlers_content: str = "",
        toml_content: str = "",
        dependencies: list[str] | None = None,
    ) -> Path:
        """Create a plugin directory under root/plugins/ with manifest and handlers.

        Returns the plugin directory path.
        """
        plugin_dir = root / "plugins" / name
        plugin_dir.mkdir(parents=True, exist_ok=True)

        if not toml_content:
            deps_line = ""
            if dependencies:
                deps_list = ", ".join(f'"{d}"' for d in dependencies)
                deps_line = f"dependencies = [{deps_list}]\n"
            toml_content = (
                f'name = "{name}"\n'
                f'version = "0.1.0"\n'
                f'description = "Test plugin {name}"\n'
                f"{deps_line}"
                "\n"
                "[[commands]]\n"
                f'name = "{name}-cmd"\n'
                f'handler = "handle_{name.replace("-", "_")}"\n'
                f'description = "Command for {name}"\n'
            )

        (plugin_dir / "plugin.toml").write_text(toml_content)

        if not handlers_content:
            handlers_content = "def handle_cmd(*args, **kwargs):\n    pass\n"

        (plugin_dir / "handlers.py").write_text(handlers_content)

        return plugin_dir

    @staticmethod
    def _ensure_state_dir(root: Path) -> None:
        """Create the .codehome/ directory that save_state writes into."""
        (root / ".codehome").mkdir(parents=True, exist_ok=True)

    def test_load_minimal_plugin(self, tmp_path: Path) -> None:
        """A single valid plugin is discovered, loaded, and registered."""
        self._ensure_state_dir(tmp_path)
        self._setup_plugin(tmp_path, "hello")

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 1
        assert errors == []

        # Plugin is in the registry.
        plugin = registry.get("hello")
        assert plugin is not None
        assert plugin.name == "hello"
        assert plugin.version == "0.1.0"

        # State file was written.
        state_file = tmp_path / ".codehome" / "plugins-state.json"
        assert state_file.exists()

    def test_load_with_dependency_ordering(self, tmp_path: Path) -> None:
        """Plugin B depends on A; both load successfully with A registered first."""
        self._ensure_state_dir(tmp_path)
        self._setup_plugin(tmp_path, "alpha")
        self._setup_plugin(tmp_path, "beta", dependencies=["alpha"])

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 2
        assert errors == []
        assert registry.get("alpha") is not None
        assert registry.get("beta") is not None

    def test_broken_handler_doesnt_block_others(self, tmp_path: Path) -> None:
        """A plugin with a broken handlers.py produces errors but doesn't prevent others."""
        self._ensure_state_dir(tmp_path)
        self._setup_plugin(tmp_path, "good")
        self._setup_plugin(
            tmp_path,
            "bad",
            handlers_content="this is not valid python @@!!\n",
        )

        _loaded_count, errors = load_all_plugins(tmp_path)

        # "good" loaded, "bad" did not -- but "bad" still counts as loaded
        # because the registry entry is created even when handlers.py fails
        # to import (error is recorded but plugin is not skipped).
        good_plugin = registry.get("good")
        assert good_plugin is not None

        # The bad plugin is still registered (loader doesn't skip it),
        # but an error was recorded for the import failure.
        bad_plugin = registry.get("bad")
        assert bad_plugin is not None

        # Errors mention the bad plugin's import failure.
        assert any("bad" in e for e in errors)

    def test_state_file_written(self, tmp_path: Path) -> None:
        """Loading plugins writes a valid plugins-state.json with correct structure."""
        self._ensure_state_dir(tmp_path)
        self._setup_plugin(tmp_path, "stateful")

        load_all_plugins(tmp_path)

        state_file = tmp_path / ".codehome" / "plugins-state.json"
        assert state_file.exists()

        import json

        state = json.loads(state_file.read_text())

        # Top-level keys present.
        assert "plugins" in state
        assert "last_scanned" in state
        assert state["last_scanned"] is not None

        # Plugin entry exists with expected fields.
        assert "stateful" in state["plugins"]
        entry = state["plugins"]["stateful"]
        assert entry["name"] == "stateful"
        assert entry["version"] == "0.1.0"
        assert entry["enabled"] is True

    def test_no_plugins_returns_zero(self, tmp_path: Path) -> None:
        """When no plugins/ directory exists, load_all_plugins returns 0 loaded and no errors."""
        self._ensure_state_dir(tmp_path)

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 0
        assert errors == []

    def test_disabled_plugin_not_loaded(self, tmp_path: Path) -> None:
        """A plugin with enabled=false in its manifest is not loaded into the registry."""
        self._ensure_state_dir(tmp_path)
        toml_content = (
            'name = "disabled-plugin"\n'
            'version = "1.0.0"\n'
            'description = "Should not load"\n'
            "enabled = false\n"
            "\n"
            "[[commands]]\n"
            'name = "nope"\n'
            'handler = "handle_nope"\n'
            'description = "Nope"\n'
        )
        self._setup_plugin(tmp_path, "disabled-plugin", toml_content=toml_content)

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 0
        assert errors == []
        assert registry.get("disabled-plugin") is None

    # -- Router extraction tests ------------------------------------------------

    def test_router_extracted_from_routes_py(self, tmp_path: Path) -> None:
        """A plugin with dashboard config and routes.py gets its router extracted."""
        self._ensure_state_dir(tmp_path)
        toml = (
            'name = "with-router"\n'
            'version = "0.1.0"\n'
            'description = "Has dashboard"\n'
            "\n"
            "[dashboard]\n"
            'group = "root"\n'
            'route = "/with-router"\n'
            "\n"
            "[[commands]]\n"
            'name = "cmd"\n'
            'handler = "handle_cmd"\n'
            'description = "A command"\n'
        )
        handlers = "def handle_cmd(*args, **kwargs):\n    pass\n"
        routes = "from types import SimpleNamespace\nrouter = SimpleNamespace(routes=[])\n"
        plugin_dir = self._setup_plugin(tmp_path, "with-router", toml_content=toml, handlers_content=handlers)
        (plugin_dir / "routes.py").write_text(routes)

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 1
        assert errors == []
        plugin = registry.get("with-router")
        assert plugin is not None
        assert plugin.router is not None

    def test_missing_routes_py_produces_error(self, tmp_path: Path) -> None:
        """A plugin declaring dashboard but missing routes.py records an error."""
        self._ensure_state_dir(tmp_path)
        toml = (
            'name = "no-routes"\n'
            'version = "0.1.0"\n'
            'description = "Missing routes"\n'
            "\n"
            "[dashboard]\n"
            'group = "root"\n'
            'route = "/no-routes"\n'
            "\n"
            "[[commands]]\n"
            'name = "cmd"\n'
            'handler = "handle_cmd"\n'
            'description = "A command"\n'
        )
        self._setup_plugin(tmp_path, "no-routes", toml_content=toml)

        loaded_count, errors = load_all_plugins(tmp_path)

        # Plugin still loads (with no router), but error is recorded.
        assert loaded_count == 1
        assert any("routes.py not found" in e for e in errors)

    def test_routes_py_missing_router_attribute_produces_error(self, tmp_path: Path) -> None:
        """A routes.py without a 'router' attribute records an error."""
        self._ensure_state_dir(tmp_path)
        toml = (
            'name = "bad-router"\n'
            'version = "0.1.0"\n'
            'description = "Bad routes"\n'
            "\n"
            "[dashboard]\n"
            'group = "root"\n'
            'route = "/bad-router"\n'
            "\n"
            "[[commands]]\n"
            'name = "cmd"\n'
            'handler = "handle_cmd"\n'
            'description = "A command"\n'
        )
        handlers = "def handle_cmd(*args, **kwargs):\n    pass\n"
        plugin_dir = self._setup_plugin(tmp_path, "bad-router", toml_content=toml, handlers_content=handlers)
        (plugin_dir / "routes.py").write_text("x = 42\n")

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 1
        assert any("no 'router' attribute" in e for e in errors)

    # -- Check registration tests -----------------------------------------------

    def test_checks_registered_into_check_registry(self, tmp_path: Path) -> None:
        """Plugin-declared checks are registered into the global CheckRegistry."""
        from codehome.checks.registry import _registry as check_reg

        self._ensure_state_dir(tmp_path)
        toml = (
            'name = "checker"\n'
            'version = "0.1.0"\n'
            'description = "Has checks"\n'
            "\n"
            "[[checks]]\n"
            'name = "my-lint"\n'
            'handler = "run_lint"\n'
            'group = "gate"\n'
            "timeout = 30\n"
        )
        handlers = "def handle_cmd(*args, **kwargs):\n    pass\n"
        checks_code = (
            "async def run_lint(ctx):\n"
            "    from codehome.checks.result import CheckResult\n"
            "    return CheckResult(name='my-lint', outcome='pass', duration_ms=0)\n"
        )
        plugin_dir = self._setup_plugin(tmp_path, "checker", toml_content=toml, handlers_content=handlers)
        (plugin_dir / "checks.py").write_text(checks_code)

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 1
        assert errors == []
        # Check is in the check registry.
        entry = check_reg.get("my-lint")
        assert entry is not None
        assert entry.group == "gate"
        assert entry.timeout == 30
        assert entry.source == "plugin:checker"

        # Cleanup: remove from check registry to not pollute other tests.
        check_reg._entries.pop("my-lint", None)

    def test_missing_checks_py_produces_error(self, tmp_path: Path) -> None:
        """A plugin declaring checks but missing checks.py records an error."""
        self._ensure_state_dir(tmp_path)
        toml = (
            'name = "no-checks"\n'
            'version = "0.1.0"\n'
            'description = "Missing checks"\n'
            "\n"
            "[[checks]]\n"
            'name = "phantom"\n'
            'handler = "run_phantom"\n'
            'group = "gate"\n'
            "timeout = 10\n"
        )
        self._setup_plugin(tmp_path, "no-checks", toml_content=toml)

        _loaded_count, errors = load_all_plugins(tmp_path)

        assert any("checks.py not found" in e for e in errors)

    def test_non_callable_check_handler_produces_error(self, tmp_path: Path) -> None:
        """A checks.py where the handler is not callable records an error."""
        self._ensure_state_dir(tmp_path)
        toml = (
            'name = "bad-check"\n'
            'version = "0.1.0"\n'
            'description = "Non-callable check"\n'
            "\n"
            "[[checks]]\n"
            'name = "broken-lint"\n'
            'handler = "run_lint"\n'
            'group = "gate"\n'
            "timeout = 10\n"
        )
        handlers = "def handle_cmd(*args, **kwargs):\n    pass\n"
        checks_code = "run_lint = 'not a function'\n"
        plugin_dir = self._setup_plugin(tmp_path, "bad-check", toml_content=toml, handlers_content=handlers)
        (plugin_dir / "checks.py").write_text(checks_code)

        _loaded_count, errors = load_all_plugins(tmp_path)

        assert any("not callable" in e for e in errors)



# ===========================================================================
# 7. Discovery edge cases
# ===========================================================================


class TestDiscoveryEdgeCases:
    """Tests for duplicate detection and system dependency validation."""

    def test_duplicate_plugin_names_detected(self, tmp_path: Path) -> None:
        """Two plugins with the same name in plugin.toml are both removed."""
        plugins_dir = tmp_path / "plugins"

        # Two different directories, same plugin name.
        for dir_name in ("alpha-dir", "beta-dir"):
            d = plugins_dir / dir_name
            d.mkdir(parents=True)
            (d / "plugin.toml").write_text('name = "collision"\nversion = "0.1.0"\ndescription = "Duplicate"\n')

        # discover_plugins expects the project root, not plugins/ itself.
        result = discover_plugins(tmp_path)

        # Both are removed; error is recorded.
        assert len(result.plugins) == 0
        assert any("Duplicate plugin name 'collision'" in e for e in result.errors)

    def test_duplicate_detection_keeps_non_duplicates(self, tmp_path: Path) -> None:
        """Non-duplicate plugins survive when duplicates are removed."""
        plugins_dir = tmp_path / "plugins"

        # Two dirs with same name.
        for dir_name in ("dup-a", "dup-b"):
            d = plugins_dir / dir_name
            d.mkdir(parents=True)
            (d / "plugin.toml").write_text('name = "dup"\nversion = "0.1.0"\ndescription = "Dup"\n')
        # One unique plugin.
        unique = plugins_dir / "unique"
        unique.mkdir(parents=True)
        (unique / "plugin.toml").write_text('name = "unique"\nversion = "0.1.0"\ndescription = "Unique"\n')

        # discover_plugins expects the project root, not plugins/ itself.
        result = discover_plugins(tmp_path)

        names = [m.name for _, m in result.plugins]
        assert "unique" in names
        assert "dup" not in names

    def test_system_deps_missing_binary(self, tmp_path: Path) -> None:
        """check_system_deps returns missing binaries."""
        from codehome.plugins.discovery import check_system_deps

        manifest = PluginManifest(
            name="needs-stuff",
            version="0.1.0",
            description="",
            requires=("nonexistent_binary_xyz_999",),
        )

        missing = check_system_deps(manifest)

        assert "nonexistent_binary_xyz_999" in missing

    def test_system_deps_all_present(self, tmp_path: Path) -> None:
        """check_system_deps returns empty list when all deps are present."""
        from codehome.plugins.discovery import check_system_deps

        # 'git' should be on PATH in any dev environment.
        manifest = PluginManifest(
            name="needs-git",
            version="0.1.0",
            description="",
            requires=("git",),
        )

        missing = check_system_deps(manifest)

        assert missing == []

    def test_system_deps_empty_requires(self) -> None:
        """check_system_deps with no requires returns empty list."""
        from codehome.plugins.discovery import check_system_deps

        manifest = PluginManifest(
            name="no-deps",
            version="0.1.0",
            description="",
        )

        missing = check_system_deps(manifest)

        assert missing == []
