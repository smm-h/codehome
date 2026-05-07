"""Smoke tests for plugin discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from codehome.plugins.discovery import discover_plugins
from codehome.plugins.manifest import PluginManifest, parse_manifest


def test_discover_empty_dir(tmp_path: Path) -> None:
    """discover_plugins returns empty results when no plugins directory exists."""
    result = discover_plugins(root=tmp_path)
    assert result.plugins == []
    assert result.errors == []


def test_discover_skips_dirs_without_manifest(tmp_path: Path) -> None:
    """Directories without plugin.toml are silently skipped."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "not-a-plugin").mkdir()

    result = discover_plugins(root=tmp_path)
    assert result.plugins == []
    assert result.errors == []


def test_minimal_manifest_parsed(tmp_path: Path) -> None:
    """A minimal plugin.toml with only name and version is parsed correctly."""
    plugin_dir = tmp_path / "plugins" / "hello"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        'name = "hello"\nversion = "0.1.0"\ndescription = "A test plugin"\n'
    )

    result = discover_plugins(root=tmp_path)
    assert len(result.plugins) == 1

    path, manifest = result.plugins[0]
    assert path == plugin_dir
    assert manifest.name == "hello"
    assert manifest.version == "0.1.0"
    assert manifest.description == "A test plugin"


def test_manifest_with_commands(tmp_path: Path) -> None:
    """A manifest with a [[commands]] section parses command declarations."""
    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text(
        'name = "test"\n'
        'version = "1.0.0"\n'
        "\n"
        "[[commands]]\n"
        'name = "greet"\n'
        'handler = "cmd_greet"\n'
        'description = "Say hello"\n'
    )

    manifest = parse_manifest(plugin_dir)
    assert manifest.name == "test"
    assert len(manifest.commands) == 1
    assert manifest.commands[0].name == "greet"
    assert manifest.commands[0].handler == "cmd_greet"


def test_manifest_missing_required_field(tmp_path: Path) -> None:
    """A manifest missing the required 'name' field raises ValueError."""
    plugin_dir = tmp_path / "bad"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text('version = "0.1.0"\n')

    with pytest.raises(ValueError, match="missing or empty required field 'name'"):
        parse_manifest(plugin_dir)


def test_hidden_dirs_skipped(tmp_path: Path) -> None:
    """Directories starting with . or _ are skipped during discovery."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    for name in [".hidden", "_private"]:
        d = plugins_dir / name
        d.mkdir()
        (d / "plugin.toml").write_text('name = "x"\nversion = "0.1.0"\n')

    result = discover_plugins(root=tmp_path)
    assert result.plugins == []
