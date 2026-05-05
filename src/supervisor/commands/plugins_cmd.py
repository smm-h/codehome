"""``v plugins`` -- manage the plugin system.

Subcommands:

- ``rescan``: Discover plugins on disk and update state file.
- ``list``: Show discovered plugins with status.
- ``enable <name>``: Enable a discovered plugin.
- ``disable <name>``: Disable a discovered plugin.
- ``install <name>``: Install a plugin from the plugin-store.
- ``install --list``: Show available plugins in the plugin-store.
- ``update <name>``: Re-install a plugin from the plugin-store.
- ``update --all``: Re-install all user-installed plugins.

Plugins are self-contained packages that declare commands, checks, and
dashboard integrations via a ``plugin.toml`` manifest.  They are
discovered from ``plugins/`` directories and managed via state files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from supervisor.paths import ROOT
from supervisor.utils import die

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def cmd_plugins(args: argparse.Namespace) -> None:
    """Dispatch to plugins subcommands.

    ``v plugins rescan``        -- discover plugins and update state
    ``v plugins list``          -- show discovered plugins
    ``v plugins enable <name>`` -- enable a plugin
    ``v plugins disable <name>``-- disable a plugin
    ``v plugins install <name>``-- install a plugin from the plugin-store
    ``v plugins install --list``-- show available plugins in the plugin-store
    ``v plugins update <name>`` -- re-install a plugin from the plugin-store
    ``v plugins update --all`` -- re-install all user-installed plugins
    """
    from supervisor.cli_helpers import dispatch_subcommand

    dispatch_subcommand(
        args,
        "plugins_command",
        {
            "rescan": _cmd_rescan,
            "list": _cmd_list,
            "enable": _cmd_enable,
            "disable": _cmd_disable,
            "install": _cmd_install,
            "update": _cmd_update,
        },
    )


def _cmd_rescan(args: argparse.Namespace) -> None:
    """Discover plugins on disk and update the state file."""
    from supervisor.plugins.discovery import discover_plugins
    from supervisor.plugins.state import load_state, merge_discovered, save_state
    from supervisor.plugins.subscription_parser import (
        parse_plugin_subscriptions,
        write_events_toml,
    )

    result = discover_plugins(ROOT)
    old_state = load_state(ROOT)
    old_names = set(old_state.get("plugins", {}).keys())

    new_state = merge_discovered(old_state, result.plugins)
    save_state(ROOT, new_state)

    new_names = set(new_state["plugins"].keys())
    added = new_names - old_names
    removed = old_names - new_names

    total = len(result.plugins)
    print(f"Discovered {total} plugin(s) ({len(added)} new, {len(removed)} removed, {len(result.errors)} error(s)).")

    # List each discovered plugin for agent-friendly output.
    if result.plugins:
        print()
        for _plugin_dir, manifest in result.plugins:
            toggle = "on" if new_state["plugins"][manifest.name].get("enabled", True) else "off"
            print(f"  [{toggle:>3}] {manifest.name} v{manifest.version} -- {manifest.description}")

    if result.errors:
        print()
        for err in result.errors:
            print(f"  [error] {err}")

    # Scan @on() decorator subscriptions and write events.toml per plugin.
    sub_total = 0
    sub_errors: list[str] = []
    for plugin_dir, manifest in result.plugins:
        try:
            subs = parse_plugin_subscriptions(plugin_dir)
        except (TypeError, ValueError) as exc:
            sub_errors.append(f"{manifest.name}: {exc}")
            continue
        write_events_toml(plugin_dir, subs)
        sub_total += len(subs)

    if sub_total or sub_errors:
        print()
        print(f"Event subscriptions: {sub_total} found, {len(sub_errors)} error(s).")
    for err in sub_errors:
        print(f"  [error] {err}")


def _cmd_list(args: argparse.Namespace) -> None:
    """Show discovered plugins."""
    from supervisor.plugins.state import load_state

    state = load_state(ROOT)
    plugins = state.get("plugins", {})

    if not plugins:
        print("No plugins found. Run `v plugins rescan`.")
        return

    for name, info in sorted(plugins.items()):
        toggle = "on" if info.get("enabled", True) else "off"
        version = info.get("version", "?")
        desc = info.get("description", "")
        desc_suffix = f" -- {desc}" if desc else ""
        print(f"  [{toggle:>3}] {name} v{version}{desc_suffix}")


def _cmd_enable(args: argparse.Namespace) -> None:
    """Enable a discovered plugin by name."""
    _set_enabled(args, enabled=True)


def _cmd_disable(args: argparse.Namespace) -> None:
    """Disable a discovered plugin by name."""
    name = args.name
    force = getattr(args, "force", False)

    if not force:
        dependents = _find_dependents(name)
        if dependents:
            names = ", ".join(sorted(dependents))
            die(f"cannot disable {name!r}: depended on by {names} (use --force to override)")

    _set_enabled(args, enabled=False)


def _find_dependents(plugin_name: str) -> list[str]:
    """Return names of enabled plugins that declare *plugin_name* as a dependency."""
    from supervisor.plugins.discovery import discover_plugins
    from supervisor.plugins.state import load_state

    state = load_state(ROOT)
    plugins_state = state.get("plugins", {})

    # Discover from disk to get manifests with dependency info.
    result = discover_plugins(ROOT)

    dependents = []
    for _plugin_dir, manifest in result.plugins:
        # Only check enabled plugins (no point warning about disabled ones).
        entry = plugins_state.get(manifest.name, {})
        if not entry.get("enabled", True):
            continue
        # Skip the plugin being disabled itself.
        if manifest.name == plugin_name:
            continue
        if plugin_name in manifest.dependencies:
            dependents.append(manifest.name)

    return dependents


def _set_enabled(args: argparse.Namespace, *, enabled: bool) -> None:
    """Toggle the enabled state of a plugin in the state file."""
    from supervisor.plugins.state import load_state, save_state

    name = args.name

    state = load_state(ROOT)
    plugins = state.get("plugins", {})

    if name not in plugins:
        known = ", ".join(sorted(plugins.keys())) if plugins else "(none)"
        die(f"plugin {name!r} not found. Known: {known}")

    plugins[name]["enabled"] = enabled
    save_state(ROOT, state)

    action = "Enabled" if enabled else "Disabled"
    print(f"{action} plugin {name!r}.")


# ---------------------------------------------------------------------------
# install subcommand
# ---------------------------------------------------------------------------

# Default git remote URL for fetching plugins when plugin-store/ is not local.
_DEFAULT_STORE_REPO = "git@gp:smm-h/superv.git"


def _cmd_install(args: argparse.Namespace) -> None:
    """Install a plugin from the plugin-store, or list available plugins.

    Two modes:

    ``v plugins install --list``
        Parse ``plugin-store/index.toml`` (local or fetched from GitHub)
        and display available plugins in a table.

    ``v plugins install <name>``
        Copy the named plugin from the local ``plugin-store/`` directory
        (development mode) or fetch it via git sparse checkout (installed
        mode) into ``~/.superv/plugins/<name>/``.  Validates that the
        installed directory contains a ``plugin.toml``, then triggers a
        rescan so the plugin is immediately registered.
    """
    if getattr(args, "list_available", False):
        _install_list()
        return

    name = getattr(args, "name", None)
    if not name:
        die("plugin name required. Use --list to see available plugins.")

    _install_plugin(name)


def _install_list() -> None:
    """Display available plugins from the plugin-store index."""
    import tomllib

    index_path = ROOT / "plugin-store" / "index.toml"

    data: dict[str, Any] | None
    if index_path.is_file():
        # Local development mode: read from disk.
        data = tomllib.loads(index_path.read_text())
    else:
        # Installed mode: fetch from GitHub raw content API.
        data = _fetch_remote_index()
        if data is None:
            die("could not fetch plugin-store index (no local plugin-store/ and remote fetch failed)")

    plugins = data.get("plugins", [])
    if not plugins:
        print("No plugins listed in the plugin-store index.")
        return

    # Check which are already installed in ~/.superv/plugins/.
    from supervisor.paths import superv_home

    user_plugins_dir = superv_home() / "plugins"

    print(f"Available plugins ({len(plugins)}):\n")
    for entry in plugins:
        name = entry.get("name", "?")
        version = entry.get("version", "?")
        desc = entry.get("description", "")
        installed = (user_plugins_dir / name / "plugin.toml").is_file()
        tag = " [installed]" if installed else ""
        desc_suffix = f" -- {desc}" if desc else ""
        print(f"  {name} v{version}{tag}{desc_suffix}")


def _fetch_remote_index() -> dict[str, Any] | None:
    """Fetch index.toml from the GitHub raw content API.

    Returns parsed TOML dict, or None on failure.
    """
    import tomllib
    import urllib.request
    import urllib.error

    url = "https://raw.githubusercontent.com/smm-h/superv/main/plugin-store/index.toml"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read()
        return tomllib.loads(raw.decode())
    except (urllib.error.URLError, OSError, tomllib.TOMLDecodeError):
        return None


def _install_plugin(name: str) -> None:
    """Install a single plugin by name into ~/.superv/plugins/<name>/."""
    import shutil
    from pathlib import Path

    from supervisor.paths import superv_home

    user_plugins_dir = superv_home() / "plugins"
    dest = user_plugins_dir / name

    if (dest / "plugin.toml").is_file():
        die(f"plugin {name!r} is already installed at {dest}")

    # Strategy 1: local plugin-store/ directory (development/editable install).
    # The plugin-store/index.toml lists paths; the actual plugin source lives
    # in the project's plugins/ directory, not plugin-store/<name>/.
    # In development mode, plugins/ has the source. In the plugin-store
    # distribution model, plugin-store/<name>/ would hold a copy.
    # Check both locations.
    local_source = _find_local_source(name)

    if local_source is not None:
        _copy_plugin(local_source, dest)
        print(f"Installed {name!r} from local source ({local_source})")
    else:
        # Strategy 2: git sparse checkout from remote.
        _install_via_sparse_checkout(name, dest)
        print(f"Installed {name!r} from remote plugin-store")

    # Validate the installed plugin has a manifest.
    if not (dest / "plugin.toml").is_file():
        # Clean up broken install.
        shutil.rmtree(dest, ignore_errors=True)
        die(f"installed plugin {name!r} has no plugin.toml -- removed")

    # Trigger a rescan so the new plugin is immediately registered.
    print("Running rescan...")
    import argparse as _ap

    _cmd_rescan(_ap.Namespace())
    print(f"\nPlugin {name!r} is ready to use.")


def _find_local_source(name: str) -> Path | None:
    """Find the plugin source directory locally.

    Checks two locations in order:
    1. plugin-store/<name>/  (distribution copy, if present)
    2. plugins/<name>/       (development source, for editable installs)

    Returns the path if a plugin.toml is found, else None.
    """
    from pathlib import Path

    # Check plugin-store/<name>/ first (the distribution path).
    store_path = ROOT / "plugin-store" / name
    if (store_path / "plugin.toml").is_file():
        return store_path

    # Fall back to plugins/<name>/ (development mode).
    dev_path = ROOT / "plugins" / name
    if (dev_path / "plugin.toml").is_file():
        return dev_path

    return None


def _copy_plugin(src: Path, dest: Path) -> None:
    """Copy a plugin directory to the destination, creating parents."""
    import shutil

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


def _install_via_sparse_checkout(name: str, dest: Path) -> None:
    """Fetch a single plugin from the remote repo via git sparse checkout.

    Uses --filter=blob:none --sparse --depth 1 to minimize bandwidth,
    then copies the target directory out and cleans up the temp clone.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    # Determine the repo URL: check config.toml, then fall back to default.
    repo_url = _get_store_repo_url()

    tmpdir = Path(tempfile.mkdtemp(prefix="superv-plugin-"))
    try:
        # Shallow sparse clone.
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--sparse", "--depth", "1", repo_url, str(tmpdir / "repo")],
            check=True,
            capture_output=True,
            text=True,
        )

        repo_dir = tmpdir / "repo"

        # Set sparse-checkout to only fetch the plugin directory.
        # Try plugin-store/<name>/ first, then plugins/<name>/.
        for sparse_path in [f"plugin-store/{name}", f"plugins/{name}"]:
            subprocess.run(
                ["git", "sparse-checkout", "set", sparse_path],
                cwd=str(repo_dir),
                check=True,
                capture_output=True,
                text=True,
            )

            source = repo_dir / sparse_path
            if (source / "plugin.toml").is_file():
                _copy_plugin(source, dest)
                return

        die(f"plugin {name!r} not found in remote plugin-store")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        die(f"git sparse checkout failed: {stderr}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# update subcommand
# ---------------------------------------------------------------------------


def _cmd_update(args: argparse.Namespace) -> None:
    """Update a plugin (or all) by re-installing from the plugin-store.

    ``v plugins update <name>``
        Delete the installed copy at ``~/.superv/plugins/<name>/`` and
        re-install from source (local plugin-store or sparse checkout).

    ``v plugins update --all``
        Re-install every plugin currently in ``~/.superv/plugins/``.
    """
    import shutil

    from supervisor.paths import superv_home

    update_all = getattr(args, "update_all", False)
    name = getattr(args, "name", None)

    if not update_all and not name:
        die("plugin name required. Use --all to update all installed plugins.")

    user_plugins_dir = superv_home() / "plugins"

    if update_all:
        names = _list_installed_plugins(user_plugins_dir)
        if not names:
            print("No user-installed plugins found in ~/.superv/plugins/.")
            return
        print(f"Updating {len(names)} plugin(s): {', '.join(sorted(names))}\n")
    else:
        assert name is not None  # guarded by the check above
        dest = user_plugins_dir / name
        if not (dest / "plugin.toml").is_file():
            die(f"plugin {name!r} is not installed at {dest}")
        names = [name]

    errors: list[str] = []
    for plugin_name in sorted(names):
        dest = user_plugins_dir / plugin_name
        print(f"Updating {plugin_name!r}...")
        # Remove existing installation.
        shutil.rmtree(dest, ignore_errors=True)
        try:
            # Re-install using the same logic as install.
            _install_plugin(plugin_name)
        except SystemExit:
            # _install_plugin calls die() on failure, which raises SystemExit.
            # Catch it so --all can continue with remaining plugins.
            errors.append(plugin_name)

    if errors:
        failed = ", ".join(errors)
        die(f"failed to update: {failed}")


def _list_installed_plugins(user_plugins_dir: Path) -> list[str]:
    """Return names of plugins installed in the user plugins directory."""
    if not user_plugins_dir.is_dir():
        return []
    return [
        d.name
        for d in sorted(user_plugins_dir.iterdir())
        if d.is_dir() and (d / "plugin.toml").is_file()
    ]


def _get_store_repo_url() -> str:
    """Read the plugin store repo URL from config, or return the default.

    Checks ~/.superv/config.toml for a ``plugin_store_repo`` key.
    """
    import tomllib
    from supervisor.paths import superv_home

    config_path = superv_home() / "config.toml"
    if config_path.is_file():
        try:
            data = tomllib.loads(config_path.read_text())
            url: str | None = data.get("plugin_store_repo")
            if url and isinstance(url, str):
                return url
        except (tomllib.TOMLDecodeError, OSError):
            pass

    return _DEFAULT_STORE_REPO
