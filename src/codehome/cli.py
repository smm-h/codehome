"""CLI entry point: argparse setup + command dispatch.

Lazy imports: only the needed command module is loaded per invocation,
keeping startup under 100ms. Commands that legitimately consume extra
args (Playwright flags, claude flags) are listed in _PASSTHROUGH_COMMANDS;
all others reject unexpected arguments to prevent silent misuse.

Dispatch pattern: each leaf parser calls set_defaults(_cmd=(...)) with a
(module_path, func_name) tuple. main() reads args._cmd, imports the
module lazily, and calls the handler. Plugin commands use LazyHandler
callables built from TOML manifests -- no plugin Python is imported
until the command is actually invoked.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

from codehome import utils
from codehome.serve import DEFAULT_PORT

_log = logging.getLogger(__name__)


def _register_plugin_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> tuple[list[str], set[str]]:
    """Discover plugins and build argparse trees from TOML manifests.

    No plugin Python is imported -- only TOML manifests are read and
    argparse subparsers are built declaratively.  Handler functions are
    wrapped in :class:`~codehome.plugins.cli_builder.LazyHandler` so
    they are imported only when the user actually invokes the command.

    Returns:
        A tuple of (error messages, passthrough command names).
    """
    from codehome.plugins.cli_builder import build_commands
    from codehome.plugins.discovery import discover_plugins
    from codehome.plugins.loader import _mount_plugin_namespaces

    errors: list[str] = []
    passthrough: set[str] = set()

    try:
        result = discover_plugins()
    except Exception:
        _log.debug("plugin discovery failed", exc_info=True)
        errors.append("plugin discovery failed (see debug log)")
        return errors, passthrough

    errors.extend(result.errors)

    # Mount plugin namespaces (codehome.<ns>) so cross-plugin imports
    # resolve when lazy handlers are eventually invoked.  Uses a
    # lightweight state dict derived from manifests to avoid state-file
    # I/O during CLI startup.
    state = {"plugins": {m.name: {"enabled": m.enabled} for _, m in result.plugins}}
    _mount_plugin_namespaces(result.plugins, state, errors)

    # Snapshot core command names so we can reject plugin collisions.
    core_commands = set(sub.choices) if hasattr(sub, "choices") else set()

    for plugin_dir, manifest in result.plugins:
        if not manifest.enabled:
            continue

        # Check for collision with core commands before registration.
        plugin_cmd_names = {cmd.name for cmd in manifest.commands}
        collisions = plugin_cmd_names & core_commands
        if collisions:
            names = ", ".join(sorted(collisions))
            errors.append(
                f"plugin '{manifest.name}' skipped: command name(s) "
                f"{names} conflict with core commands"
            )
            continue

        try:
            build_commands(manifest.commands, sub, plugin_dir)
            core_commands.update(plugin_cmd_names)
        except Exception:
            _log.debug(
                "plugin '%s' CLI registration failed",
                manifest.name,
                exc_info=True,
            )
            errors.append(f"plugin '{manifest.name}' CLI registration failed")
            continue

        # Track passthrough plugins.
        if manifest.passthrough:
            # Passthrough uses the top-level command names from the plugin.
            passthrough.update(plugin_cmd_names)

    return errors, passthrough


def build_parser() -> argparse.ArgumentParser:
    from importlib.metadata import version

    parser = argparse.ArgumentParser(
        prog="v",  # displayed as v (the recommended alias for codehome)
        description="Worktree manager for Veliu repos",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('codehome')}")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    sub = parser.add_subparsers(dest="command")

    # -- plugins management group ----------------------------------------------
    p_plug = sub.add_parser("plugins", help="manage the plugin system")
    p_plug.set_defaults(_cmd=("codehome.commands.plugins_cmd", "cmd_plugins"))
    plug_sub = p_plug.add_subparsers(dest="plugins_command")

    plug_sub.add_parser("rescan", help="discover plugins on disk and update state")
    plug_sub.add_parser("list", help="show discovered plugins")
    p_plug_enable = plug_sub.add_parser("enable", help="enable a plugin")
    p_plug_enable.add_argument("name", help="plugin name to enable")
    p_plug_disable = plug_sub.add_parser("disable", help="disable a plugin")
    p_plug_disable.add_argument("name", help="plugin name to disable")
    p_plug_disable.add_argument("--force", action="store_true", help="disable even if other plugins depend on this one")
    p_plug_install = plug_sub.add_parser("install", help="install a plugin from the plugin-store")
    p_plug_install.add_argument("name", nargs="?", default=None, help="plugin name to install")
    p_plug_install.add_argument("--list", dest="list_available", action="store_true", help="list available plugins in the plugin-store")
    p_plug_update = plug_sub.add_parser("update", help="re-install a plugin from the plugin-store")
    p_plug_update.add_argument("name", nargs="?", default=None, help="plugin name to update")
    p_plug_update.add_argument("--all", dest="update_all", action="store_true", help="update all user-installed plugins")

    # -- core commands ---------------------------------------------------------

    # superv home: show/create ~/.codehome/ skeleton.
    p_home = sub.add_parser("home", help="show codehome home directory and create skeleton")
    p_home.set_defaults(_cmd=("codehome.commands.home_cmd", "cmd_home"))

    # superv init: create a managed project under ~/.codehome/projects/.
    p_init = sub.add_parser("init", help="initialize a managed project under ~/.codehome/projects/")
    p_init.set_defaults(_cmd=("codehome.commands.init_cmd", "cmd_init"))
    p_init.add_argument("name", help="project name (used as directory name)")
    p_init.add_argument("--remote", required=True, help="git remote URL to clone")
    p_init.add_argument("--base", default="production", help="base branch name (default: production)")

    # superv migrate: copy state from .codehome/ to ~/.codehome/.
    p_migrate = sub.add_parser("migrate", help="migrate state from .codehome/ to ~/.codehome/")
    p_migrate.set_defaults(_cmd=("codehome.commands.migrate_cmd", "cmd_migrate"))

    # -- auth group ------------------------------------------------------------
    p_auth = sub.add_parser("auth", help="authentication and server setup")
    auth_sub = p_auth.add_subparsers(dest="auth_command")

    p_al = auth_sub.add_parser("login", help="authenticate with the dev server")
    p_al.set_defaults(_cmd=("codehome.commands.login", "cmd_login"))
    p_al.add_argument("--browser", action="store_true", help="open login page in browser")
    p_al.add_argument("--token", metavar="TOKEN", help="store a token directly (programmatic/browser flow)")

    p_alo = auth_sub.add_parser("logout", help="remove stored authentication token")
    p_alo.set_defaults(_cmd=("codehome.commands.login", "cmd_logout"))

    p_as = auth_sub.add_parser("setup", help="one-time server bootstrap wizard")
    p_as.set_defaults(_cmd=("codehome.commands.setup", "cmd_setup"))
    p_as.add_argument("--username", help="admin username (skip interactive prompt)")
    p_as.add_argument("--password", help="admin password (skip interactive prompt)")
    p_as.add_argument("--port", type=int, help="server port (default: 9100)")
    p_as.add_argument("--jwt-secret", help="JWT secret (auto-generated if omitted)")

    # -- server lifecycle ------------------------------------------------------
    p_server = sub.add_parser("server", help="start the dev server")
    p_server.set_defaults(_cmd=("codehome.commands.server_cmd", "cmd_server"))
    p_server.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"server port (default: {DEFAULT_PORT})")
    p_server.add_argument("--dev", action="store_true", help="enable dev mode (Granian reload + Vite HMR)")
    p_server.add_argument("--stable", action="store_true", help="stable mode (default; kept for explicitness)")
    p_server.add_argument("--skip-rebuild", action="store_true", help="skip automatic frontend rebuild check")
    server_sub = p_server.add_subparsers(dest="server_command")

    p_stop = server_sub.add_parser("stop", help="stop the running server")
    p_stop.add_argument("--cleanup", action="store_true", help="stop all managed services before shutting down")
    server_sub.add_parser("status", help="show server status")
    server_sub.add_parser("restart", help="restart the server")
    server_sub.add_parser("check", help="run diagnostic checks on the running server")

    # -- dynamically registered plugin commands --------------------------------
    plugin_errors, plugin_passthrough = _register_plugin_commands(sub)
    parser._plugin_errors = plugin_errors  # type: ignore[attr-defined]  # expose to main() for warning
    parser._plugin_passthrough = plugin_passthrough  # type: ignore[attr-defined]

    return parser


def _load_commands() -> dict[str, object]:
    """Import and return the command dispatch table.

    Separated from main() so the doc generator can access handler
    docstrings without running the CLI.
    """
    table: dict[str, object] = {}
    _imports = [
        ("codehome.commands.home_cmd", {"home": "cmd_home"}),
        ("codehome.commands.init_cmd", {"init": "cmd_init"}),
        ("codehome.commands.migrate_cmd", {"migrate": "cmd_migrate"}),
        ("codehome.commands.plugins_cmd", {"plugins": "cmd_plugins"}),
    ]
    for module_path, cmd_map in _imports:
        mod = importlib.import_module(module_path)
        for cmd_name, attr_name in cmd_map.items():
            table[cmd_name] = getattr(mod, attr_name)

    # Plugin commands are now registered via TOML manifests.
    # The doc generator can discover them through discover_plugins().
    from codehome.plugins.discovery import discover_plugins

    try:
        result = discover_plugins()
        for _plugin_dir, manifest in result.plugins:
            if manifest.enabled and manifest.commands:
                for cmd in manifest.commands:
                    table[cmd.name] = cmd.description or cmd.name
    except Exception:
        pass  # Non-fatal for doc generation.

    return table


# Commands that legitimately consume extra_args (Playwright flags, claude flags, etc.).
# All other commands reject unexpected arguments to prevent silent misuse.
_PASSTHROUGH_COMMANDS: set[str] = set()


def main() -> None:
    # Fast-path: handle --version before any plugin discovery so it
    # works even when plugin TOML files or config are broken.
    if "--version" in sys.argv[1:]:
        from importlib.metadata import version

        print(f"v {version('codehome')}")
        sys.exit(0)

    parser = build_parser()

    # Install audit subscriber on the bus singleton so CLI commands that
    # fire events get JSONL audit logging. SSE subscriber is server-only.
    from codehome.bus import bus
    from codehome.bus import registry as _bus_registry
    from codehome.bus.subscribers.audit import install_audit_subscriber

    install_audit_subscriber(bus, _bus_registry)

    plugin_errors = getattr(parser, "_plugin_errors", [])
    if plugin_errors:
        n = len(plugin_errors)
        sys.stderr.write(f"warning: {n} plugin error(s):\n")
        for err in plugin_errors:
            sys.stderr.write(f"  {err}\n")

    # parse_known_args so passthrough commands can receive extra flags.
    args, remaining = parser.parse_known_args()
    args.extra_args = remaining

    # Passthrough set from manifest-driven discovery (no plugin Python import).
    plugin_passthrough: set[str] = getattr(parser, "_plugin_passthrough", set())
    all_passthrough = _PASSTHROUGH_COMMANDS | plugin_passthrough

    # Reject unexpected arguments for commands that don't use them.
    # This prevents e.g. `v branch finalize test --cancel` silently ignoring "test"
    # and operating on the session branch instead.
    if remaining and args.command not in all_passthrough:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")

    if args.no_color:
        utils.USE_COLOR = False

    # Resolve the handler from the _cmd default set on leaf parsers.
    # Core commands: _cmd is a (module_path, func_name) tuple.
    # Plugin commands: _cmd is a LazyHandler callable from cli_builder.
    cmd = getattr(args, "_cmd", None)
    if cmd is None:
        parser.print_help()
        sys.exit(0)

    if callable(cmd):
        # Plugin handlers (LazyHandler) and group-command help printers
        # are stored as direct callables.
        handler = cmd
    else:
        module_path, func_name = cmd
        mod = importlib.import_module(module_path)
        handler = getattr(mod, func_name)

    handler(args)


if __name__ == "__main__":
    main()
