"""Tests for loader crash hardening: _sdk.py and namespace mount failures.

Verifies that a broken _sdk.py or a broken namespace mount does not crash
the entire CLI -- the broken plugin is skipped gracefully and other plugins
continue to load.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from codehome.plugins import registry
from codehome.plugins.loader import load_all_plugins


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_state_dir(root: Path) -> None:
    """Create the .codehome/ directory that save_state writes into."""
    (root / ".codehome").mkdir(parents=True, exist_ok=True)


def _setup_plugin(
    root: Path,
    name: str,
    *,
    handlers_content: str = "",
    toml_content: str = "",
    sdk_content: str | None = None,
    namespace: str = "",
    namespace_root: str = "",
    init_content: str | None = None,
) -> Path:
    """Create a plugin directory under root/plugins/ with manifest and handlers.

    Returns the plugin directory path.
    """
    plugin_dir = root / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if not toml_content:
        ns_line = f'namespace = "{namespace}"\n' if namespace else ""
        ns_root_line = f'namespace_root = "{namespace_root}"\n' if namespace_root else ""
        toml_content = (
            f'name = "{name}"\n'
            f'version = "0.1.0"\n'
            f'description = "Test plugin {name}"\n'
            f"{ns_line}"
            f"{ns_root_line}"
            "\n"
            "[[commands]]\n"
            f'name = "{name}-cmd"\n'
            f'handler = "handle_{name.replace("-", "_")}"\n'
            f'description = "Command for {name}"\n'
        )

    (plugin_dir / "plugin.toml").write_text(toml_content)

    if not handlers_content:
        handlers_content = "def register_cli(subparsers):\n    pass\n"
    (plugin_dir / "handlers.py").write_text(handlers_content)

    if sdk_content is not None:
        (plugin_dir / "_sdk.py").write_text(sdk_content)

    if init_content is not None:
        (plugin_dir / "__init__.py").write_text(init_content)

    return plugin_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Reset the plugin registry before and after each test."""
    registry.clear()
    yield
    registry.clear()


@pytest.fixture(autouse=True)
def _clean_sys_modules() -> Iterator[None]:
    """Remove any test namespace modules from sys.modules after each test."""
    yield
    # Clean up codehome.* namespace modules created during tests.
    to_remove = [k for k in sys.modules if k.startswith("codehome.") and "test" in k.lower()]
    for k in to_remove:
        sys.modules.pop(k, None)
    sys.modules.pop("_sdk", None)


# ===========================================================================
# 1a. Broken _sdk.py
# ===========================================================================


class TestBrokenSdkPy:
    """Tests that a plugin with a broken _sdk.py is skipped gracefully."""

    def test_broken_sdk_skips_plugin(self, tmp_path: Path) -> None:
        """A plugin whose _sdk.py raises ImportError is skipped entirely."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(
            tmp_path,
            "broken-sdk",
            sdk_content="from nonexistent_module_xyz import something\n",
        )

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 0
        assert any("broken-sdk" in e and "_sdk.py" in e for e in errors)
        assert registry.get("broken-sdk") is None

    def test_broken_sdk_does_not_block_other_plugins(self, tmp_path: Path) -> None:
        """Other plugins load successfully even when one has a broken _sdk.py."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(tmp_path, "good-plugin")
        _setup_plugin(
            tmp_path,
            "broken-sdk",
            sdk_content="raise RuntimeError('boom')\n",
        )

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 1
        assert registry.get("good-plugin") is not None
        assert registry.get("broken-sdk") is None
        assert any("broken-sdk" in e and "_sdk.py" in e for e in errors)

    def test_broken_sdk_cleans_up_sys_modules(self, tmp_path: Path) -> None:
        """After a broken _sdk.py, the '_sdk' key is removed from sys.modules."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(
            tmp_path,
            "broken-sdk",
            sdk_content="raise ValueError('bad sdk')\n",
        )

        load_all_plugins(tmp_path)

        assert "_sdk" not in sys.modules

    def test_broken_sdk_skips_handlers_routes_checks(self, tmp_path: Path) -> None:
        """When _sdk.py fails, handlers.py/routes.py/checks.py are not attempted."""
        _ensure_state_dir(tmp_path)
        toml = (
            'name = "full-broken"\n'
            'version = "0.1.0"\n'
            'description = "Broken sdk with all files"\n'
            "\n"
            "[dashboard]\n"
            'group = "root"\n'
            'route = "/full-broken"\n'
            "\n"
            "[[commands]]\n"
            'name = "cmd"\n'
            'handler = "handle_cmd"\n'
            'description = "A command"\n'
            "\n"
            "[[checks]]\n"
            'name = "my-check"\n'
            'handler = "run_check"\n'
            'group = "gate"\n'
            "timeout = 10\n"
        )
        plugin_dir = _setup_plugin(
            tmp_path,
            "full-broken",
            toml_content=toml,
            sdk_content="raise ImportError('nope')\n",
        )
        # Write handlers, routes, checks that would cause additional errors
        # if they were imported. If they ARE imported, we'd see those errors.
        (plugin_dir / "routes.py").write_text(
            "from types import SimpleNamespace\nrouter = SimpleNamespace(routes=[])\n"
        )
        (plugin_dir / "checks.py").write_text(
            "async def run_check(ctx):\n    pass\n"
        )

        _loaded_count, errors = load_all_plugins(tmp_path)

        # Only the _sdk.py error should be present, not handlers/routes/checks errors.
        sdk_errors = [e for e in errors if "full-broken" in e]
        assert len(sdk_errors) == 1
        assert "_sdk.py" in sdk_errors[0]


# ===========================================================================
# 1b. Broken namespace mount
# ===========================================================================


class TestBrokenNamespaceMount:
    """Tests that a plugin with a broken namespace mount is skipped gracefully."""

    def test_broken_namespace_init_skips_plugin(self, tmp_path: Path) -> None:
        """A namespace plugin whose __init__.py raises is skipped."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(
            tmp_path,
            "broken-ns",
            namespace="broken_ns_test",
            init_content="raise ImportError('broken init')\n",
        )

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 0
        assert any("broken-ns" in e and "namespace" in e for e in errors)
        assert registry.get("broken-ns") is None

    def test_broken_namespace_does_not_block_other_plugins(self, tmp_path: Path) -> None:
        """Other plugins load successfully even when one namespace mount fails."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(tmp_path, "good-plugin")
        _setup_plugin(
            tmp_path,
            "broken-ns",
            namespace="broken_ns_test2",
            init_content="raise RuntimeError('bad namespace')\n",
        )

        loaded_count, errors = load_all_plugins(tmp_path)

        assert loaded_count == 1
        assert registry.get("good-plugin") is not None
        assert registry.get("broken-ns") is None
        assert any("broken-ns" in e and "namespace" in e for e in errors)

    def test_broken_namespace_cleans_up_sys_modules(self, tmp_path: Path) -> None:
        """After a broken namespace mount, the partial module is removed from sys.modules."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(
            tmp_path,
            "broken-ns",
            namespace="broken_ns_cleanup_test",
            init_content="raise ImportError('fail')\n",
        )

        load_all_plugins(tmp_path)

        assert "codehome.broken_ns_cleanup_test" not in sys.modules

    def test_good_namespace_still_mounts_when_sibling_fails(self, tmp_path: Path) -> None:
        """A good namespace plugin mounts even when a sibling namespace plugin fails."""
        _ensure_state_dir(tmp_path)
        _setup_plugin(
            tmp_path,
            "bad-ns",
            namespace="bad_ns_sibling_test",
            init_content="raise ImportError('fail')\n",
        )
        _setup_plugin(
            tmp_path,
            "good-ns",
            namespace="good_ns_sibling_test",
            init_content="# good namespace\n",
        )

        loaded_count, errors = load_all_plugins(tmp_path)

        # good-ns loaded, bad-ns skipped.
        assert registry.get("good-ns") is not None
        assert registry.get("bad-ns") is None
        assert "codehome.good_ns_sibling_test" in sys.modules
        assert "codehome.bad_ns_sibling_test" not in sys.modules

        # Cleanup.
        sys.modules.pop("codehome.good_ns_sibling_test", None)
