"""Tests for namespace mounting during CLI startup.

Verifies that _register_plugin_commands() mounts plugin namespaces
into sys.modules so cross-plugin imports work when lazy handlers
are invoked.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from codehome.plugins.discovery import DiscoveryResult
from codehome.plugins.manifest import CommandDecl, PluginManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin_with_namespace(
    tmp_path: Path,
    name: str,
    namespace: str,
) -> tuple[Path, PluginManifest]:
    """Create a minimal plugin directory with a namespace and return (dir, manifest)."""
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    # Create an __init__.py so the namespace loader finds it.
    (plugin_dir / "__init__.py").write_text("")
    # Create a handlers.py stub so build_commands doesn't complain.
    (plugin_dir / "handlers.py").write_text(f"def handle_{name.replace('-', '_')}(args): pass\n")

    manifest = PluginManifest(
        name=name,
        description=f"Test plugin {name}",
        namespace=namespace,
        commands=(
            CommandDecl(
                name=f"{name}-cmd",
                handler=f"handle_{name.replace('-', '_')}",
                description=f"Command for {name}",
            ),
        ),
    )
    return plugin_dir, manifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_namespace(request: pytest.FixtureRequest) -> Iterator[None]:
    """Remove test namespaces from sys.modules after each test."""
    before = set(sys.modules)
    yield
    for key in set(sys.modules) - before:
        if key.startswith("codehome."):
            sys.modules.pop(key, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNamespaceMountDuringCLI:
    """Verify that _register_plugin_commands mounts namespaces."""

    def test_namespace_mounted_after_register(self, tmp_path: Path) -> None:
        """A plugin with a namespace declaration is mounted into sys.modules
        after _register_plugin_commands runs."""
        plugin_dir, manifest = _make_plugin_with_namespace(
            tmp_path, "fakens", "fakens"
        )
        pkg_name = "codehome.fakens"

        # Ensure it's not already there.
        sys.modules.pop(pkg_name, None)

        discovery_result = DiscoveryResult(
            plugins=[(plugin_dir, manifest)],
            errors=[],
        )

        # Build a subparser to pass to _register_plugin_commands.
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")

        from codehome.cli import _register_plugin_commands

        with patch(
            "codehome.plugins.discovery.discover_plugins",
            return_value=discovery_result,
        ):
            errors, _passthrough = _register_plugin_commands(sub)

        assert pkg_name in sys.modules, (
            f"expected {pkg_name!r} in sys.modules after _register_plugin_commands"
        )
        assert not errors

    def test_disabled_namespace_not_mounted(self, tmp_path: Path) -> None:
        """A disabled plugin's namespace should not be mounted."""
        plugin_dir = tmp_path / "disabled-ns"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "__init__.py").write_text("")
        (plugin_dir / "handlers.py").write_text("def handle_disabled_ns(args): pass\n")

        manifest = PluginManifest(
            name="disabled-ns",
            description="Disabled plugin",
            enabled=False,
            namespace="disabledns",
            commands=(
                CommandDecl(
                    name="disabled-ns-cmd",
                    handler="handle_disabled_ns",
                    description="Disabled command",
                ),
            ),
        )

        pkg_name = "codehome.disabledns"
        sys.modules.pop(pkg_name, None)

        discovery_result = DiscoveryResult(
            plugins=[(plugin_dir, manifest)],
            errors=[],
        )

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")

        from codehome.cli import _register_plugin_commands

        with patch(
            "codehome.plugins.discovery.discover_plugins",
            return_value=discovery_result,
        ):
            _register_plugin_commands(sub)

        assert pkg_name not in sys.modules, (
            f"disabled plugin namespace {pkg_name!r} should not be in sys.modules"
        )

    def test_namespace_mounted_before_commands_built(self, tmp_path: Path) -> None:
        """Namespace mounting happens before build_commands so that lazy
        handlers can resolve cross-plugin imports."""
        plugin_dir, manifest = _make_plugin_with_namespace(
            tmp_path, "earlyns", "earlyns"
        )
        pkg_name = "codehome.earlyns"
        sys.modules.pop(pkg_name, None)

        discovery_result = DiscoveryResult(
            plugins=[(plugin_dir, manifest)],
            errors=[],
        )

        # Track when namespace appears vs when build_commands is called.
        mount_order: list[str] = []

        def tracking_build(commands, sub, plugin_dir):
            # At this point, namespace should already be mounted.
            if pkg_name in sys.modules:
                mount_order.append("namespace_before_build")
            else:
                mount_order.append("namespace_missing_at_build")
            # Import the real build_commands.
            from codehome.plugins.cli_builder import build_commands as real_build
            return real_build(commands, sub, plugin_dir)

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")

        from codehome.cli import _register_plugin_commands

        with (
            patch("codehome.plugins.discovery.discover_plugins", return_value=discovery_result),
            patch("codehome.plugins.cli_builder.build_commands", side_effect=tracking_build),
        ):
            _register_plugin_commands(sub)

        assert "namespace_before_build" in mount_order, (
            "namespace must be mounted before build_commands is called"
        )
