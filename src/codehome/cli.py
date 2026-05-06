"""CLI entry point: argparse setup + command dispatch.

Lazy imports: only the needed command module is loaded per invocation,
keeping startup under 100ms. Commands that legitimately consume extra
args (Playwright flags, claude flags) are listed in _PASSTHROUGH_COMMANDS;
all others reject unexpected arguments to prevent silent misuse.

Dispatch pattern: each leaf parser calls set_defaults(_cmd=(...)) with a
(module_path, func_name) tuple. main() reads args._cmd, imports the
module lazily, and calls the handler. This supports nested command groups
without needing a flat command->handler dict at dispatch time.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from codehome import utils
from codehome.serve import DEFAULT_PORT


def _register_plugin_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> list[str]:
    """Load plugins and let each register its CLI subparser.

    Called at the end of :func:`build_parser` so that plugin commands
    appear alongside core commands.  Each plugin with a ``cli_registrar``
    (i.e. a ``register_cli`` function in its ``handlers.py``) gets to
    call ``sub.add_parser(...)`` and wire up its own argument tree.

    Errors are non-fatal: a broken plugin never prevents the rest of the
    CLI from working.  Use ``v plugins list`` to inspect load errors.

    Returns the list of error strings from plugin loading and CLI registration.
    """
    from codehome.plugins import registry
    from codehome.plugins.loader import load_all_plugins

    _loaded, errors = load_all_plugins()
    # Errors are non-fatal; `v plugins list` will show them.

    # Snapshot core command names so we can reject plugin collisions.
    core_commands = set(sub.choices) if hasattr(sub, "choices") else set()

    for plugin in registry.list_plugins():
        if plugin.cli_registrar is not None:
            # Check for collision with core commands before registration.
            plugin_cmd_names = {cmd.name for cmd in plugin.manifest.commands}
            collisions = plugin_cmd_names & core_commands
            if collisions:
                names = ", ".join(sorted(collisions))
                errors.append(f"plugin '{plugin.name}' skipped: command name(s) {names} conflict with core commands")
                continue
            try:
                plugin.cli_registrar(sub)
                core_commands.update(plugin_cmd_names)
            except Exception:
                import logging

                logging.getLogger(__name__).debug("plugin '%s' CLI registration failed", plugin.name, exc_info=True)
                errors.append(f"plugin '{plugin.name}' CLI registration failed")

    return errors


def build_parser() -> argparse.ArgumentParser:
    from importlib.metadata import version

    parser = argparse.ArgumentParser(
        prog="v",  # displayed as v (the recommended alias for supervisor)
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

    # superv migrate: copy state from .supervisor/ to ~/.codehome/.
    p_migrate = sub.add_parser("migrate", help="migrate state from .supervisor/ to ~/.codehome/")
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
    plugin_errors = _register_plugin_commands(sub)
    parser._plugin_errors = plugin_errors  # type: ignore[attr-defined]  # expose to main() for warning

    return parser


def _load_commands() -> dict[str, object]:
    """Import and return the command dispatch table.

    Separated from main() so the doc generator can access handler
    docstrings without running the CLI.
    """
    table = {}
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

    # Include plugin commands so the doc generator can list them.
    # Plugins are already loaded by build_parser() -> _register_plugin_commands(),
    # so the registry is populated by the time gendocs calls _load_commands().
    from codehome.plugins import registry

    for plugin in registry.list_plugins():
        if plugin.cli_registrar is not None:
            # Use the register_cli function as the handler entry; the doc
            # generator uses its docstring for richer command descriptions.
            table[plugin.name] = plugin.cli_registrar

    return table


# Commands that legitimately consume extra_args (Playwright flags, claude flags, etc.).
# All other commands reject unexpected arguments to prevent silent misuse.
_PASSTHROUGH_COMMANDS: set[str] = set()


def main() -> None:
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

    # Merge plugin passthrough commands with the core set.
    from codehome.plugins import registry

    all_passthrough = _PASSTHROUGH_COMMANDS | registry.passthrough_commands()

    # Reject unexpected arguments for commands that don't use them.
    # This prevents e.g. `v branch finalize test --cancel` silently ignoring "test"
    # and operating on the session branch instead.
    if remaining and args.command not in all_passthrough:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")

    if args.no_color:
        utils.USE_COLOR = False

    # Resolve the handler from the _cmd default set on leaf parsers.
    # Core commands: _cmd is a (module_path, func_name) tuple.
    # Plugin commands: _cmd is a callable set directly by register_cli.
    cmd = getattr(args, "_cmd", None)
    if cmd is None:
        parser.print_help()
        sys.exit(0)

    if callable(cmd):
        # Plugin handlers are stored as direct callables because their
        # modules are loaded from arbitrary file paths, not installed
        # packages that importlib.import_module can resolve.
        handler = cmd
    else:
        module_path, func_name = cmd
        mod = importlib.import_module(module_path)
        handler = getattr(mod, func_name)

    handler(args)


if __name__ == "__main__":
    main()
