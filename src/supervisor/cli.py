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

from supervisor import cli_defaults, utils


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
    from supervisor.paths import ROOT
    from supervisor.plugins import registry
    from supervisor.plugins.loader import load_all_plugins

    _loaded, errors = load_all_plugins(ROOT)
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('superv')}")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    sub = parser.add_subparsers(dest="command")

    # -- branch lifecycle group -----------------------------------------------
    from supervisor.cli_helpers import add_branch_flag, add_dry_run_flag

    p_branch = sub.add_parser("branch", help="branch lifecycle management")
    p_branch.set_defaults(_cmd=("supervisor.commands.branch", "_print_branch_help"))
    branch_sub = p_branch.add_subparsers(dest="branch_command")

    # v branch list (was: v ls)
    p_bl = branch_sub.add_parser("list", help="list branches with full status")
    p_bl.set_defaults(_cmd=("supervisor.commands.branch", "cmd_ls"))
    p_bl.add_argument("patterns", nargs="*", help="repo names or branch name patterns (wildcards supported)")
    p_bl.add_argument("-S", "--selected", action="store_true", help="show selected branch only")
    p_bl.add_argument("-M", "--markdown", action="store_true", help="full markdown output (default: bare names)")
    p_bl.add_argument("-F", "--fetch", action="store_true", help="fetch before listing for accurate staleness")
    p_bl.add_argument("-t", "--recent", action="store_true", help="sort by most recently created first")
    p_bl.add_argument("-A", "--all", action="store_true", help="include archived branches")

    # v branch alias (was: v alias)
    p_ba = branch_sub.add_parser("alias", help="list or set branch aliases")
    p_ba.set_defaults(_cmd=("supervisor.commands.branch", "cmd_alias"))
    p_ba.add_argument("name", nargs="?", help="alias name")
    p_ba.add_argument("target", nargs="?", help="target branch (e.g. fix-auth or bag:fix-auth)")
    p_ba.add_argument("--rm", action="store_true", help="remove an alias")

    # v branch rename (was: v rename)
    p_br = branch_sub.add_parser("rename", help="rename a worktree and its branch")
    p_br.set_defaults(_cmd=("supervisor.commands.branch", "cmd_rename"))
    p_br.add_argument("old", help="current branch name")
    p_br.add_argument("new", help="new branch name")

    # v branch select (was: v switch)
    p_bs = branch_sub.add_parser("select", help="activate a branch as the working selection")
    p_bs.set_defaults(_cmd=("supervisor.commands.switch", "cmd_switch"))
    p_bs.add_argument("name", help="qualified name (repo:branch)")
    p_bs.add_argument("--machine", action="store_true", help="machine-readable key=value output")

    # v branch create (was: v new)
    p_bc = branch_sub.add_parser("create", help="create a new branch + Linear issue")
    p_bc.set_defaults(_cmd=("supervisor.commands.new", "cmd_new"))
    p_bc.add_argument("name", help="qualified name (repo:branch)")
    p_bc.add_argument("description", help="issue description")
    p_bc.add_argument("--no-issue", action="store_true", help="skip Linear issue creation")
    p_bc.add_argument("--remote", action="store_true", help="adopt an existing remote branch")

    # v branch finalize (was: v fin)
    p_bf = branch_sub.add_parser("finalize", help="close branch: archive, remove worktree, update Linear")
    p_bf.set_defaults(_cmd=("supervisor.commands.fin", "cmd_fin"))
    add_branch_flag(p_bf)
    p_bf.add_argument("--cancel", action="store_true", help="abandon work (allow dirty, set Canceled)")
    p_bf.add_argument("-m", "--message", help="closing note")
    p_bf.add_argument("--force", action="store_true", help="override all guards")
    p_bf.add_argument("--keep-worktree", action="store_true", help="archive metadata but leave worktree intact")

    # v branch status (was: v status)
    p_bst = branch_sub.add_parser("status", help="show all branches with Linear state and staleness")
    p_bst.set_defaults(_cmd=("supervisor.commands.status", "cmd_status"))

    # -- unified check runner (v check) -----------------------------------------
    p_check = sub.add_parser("check", help="run lint/typecheck/build checks by group")
    p_check.set_defaults(_cmd=("supervisor.commands.check_cmd", "cmd_check"), check_group=None)
    p_check.add_argument(
        "check_group",
        nargs="?",
        default=None,
        help="group to run (e.g. precommit, gate) or 'install-hooks'",
    )
    p_check.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing hooks without prompting (install-hooks only)",
    )

    # TODO listing.
    p = sub.add_parser("todo", help="list TODO files (branch, repo, or super level)")
    p.set_defaults(_cmd=("supervisor.commands.todo", "cmd_todo"))
    p.add_argument("repo", nargs="?", default=None, help="repo name (e.g. bag) for repo-level TODOs")
    p.add_argument("-B", "--branch", metavar="BRANCH", help="branch name (default: selected)")
    p.add_argument("-S", "--super", action="store_true", help="show super-level TODOs instead of branch")
    p.add_argument("-A", "--all", action="store_true", help="include .done, .defer, .obsolete subdirs")

    # -- plugins management group ----------------------------------------------
    p_plug = sub.add_parser("plugins", help="manage the plugin system")
    p_plug.set_defaults(_cmd=("supervisor.commands.plugins_cmd", "cmd_plugins"))
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

    # -- git operations group -------------------------------------------------
    p_git = sub.add_parser("git", help="git operations (diff, push, rebase, ...)")
    p_git.set_defaults(_cmd=("supervisor.commands.git_cmd", "_print_git_help"))
    git_sub = p_git.add_subparsers(dest="git_command")

    # v git diff
    p = git_sub.add_parser("diff", help="branch summary vs production (commits, files, diff)")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_diff"))
    add_branch_flag(p)
    p.add_argument("-c", "--color", action="store_true", help="syntax-highlight diffs via delta")
    p.add_argument("--tree", action="store_true", help="show files as indented tree instead of table")
    p.add_argument("--full", action="store_true", help="bypass size gate and print full diff")

    # v git commits
    p = git_sub.add_parser("commits", help="list commits on branch vs production")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_commits"))
    add_branch_flag(p)
    p.add_argument("-r", "--recent", action="store_true", help="sort newest first (default: chronological)")

    # v git changes
    p = git_sub.add_parser("changes", help="list changed files with stats (no inline diffs)")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_changes"))
    add_branch_flag(p)
    p.add_argument("--tree", action="store_true", help="show files as indented tree instead of table")

    # v git compare
    p = git_sub.add_parser("compare", help="compare two branches (diffstat + patch)")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_compare"))
    p.add_argument("branch_a", help="first qualified branch name (repo:branch)")
    p.add_argument("branch_b", help="second qualified branch name (repo:branch)")
    p.add_argument("-c", "--color", action="store_true", help="syntax-highlight diffs via delta")

    # v git explain
    p = git_sub.add_parser("explain", help="AI-explain branch changes (calls claude -p)")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_explain"))
    add_branch_flag(p)
    p.add_argument("--full", action="store_true", help="bypass size gate and print full diff")

    # v git history
    p = git_sub.add_parser("history", help="show merge history for a branch on production")
    p.set_defaults(_cmd=("supervisor.commands.history", "cmd_history"))
    add_branch_flag(p, required=True)

    # v git push
    p = git_sub.add_parser("push", help="push branch to remote with --force-with-lease")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_push"))
    add_dry_run_flag(p)
    p.add_argument("--skip-native-check", action="store_true", help="push even if native changes lack a binary build")
    add_branch_flag(p, visible=False)

    # v git rebase
    p = git_sub.add_parser("rebase", help="rebase onto latest production (auto-remigrates stale migrations)")
    p.set_defaults(_cmd=("supervisor.commands.git_cmd", "cmd_rebase"))
    p.add_argument(
        "subcommand", nargs="?", default=None, choices=["continue", "abort"], help="continue or abort a paused rebase"
    )
    add_dry_run_flag(p)
    p.add_argument("--no-remigrate", action="store_true", help="block instead of auto-fixing stale migrations")
    add_branch_flag(p, visible=False)

    # Dev server lifecycle.
    from supervisor.serve import DEFAULT_PORT

    p_server = sub.add_parser("server", help="start the dev server")
    p_server.set_defaults(_cmd=("supervisor.commands.server_cmd", "cmd_server"))
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

    # Per-branch service management (requires a running server for most commands).
    p_services = sub.add_parser("services", help="manage per-branch services (supabase, vite, edge)")
    p_services.set_defaults(_cmd=("supervisor.commands.services_cmd", "cmd_services"))
    services_sub = p_services.add_subparsers(dest="services_command")

    p_svc_list = services_sub.add_parser("list", help="list services (all if no branch, filtered with -B)")
    add_branch_flag(p_svc_list)
    p_svc_list.add_argument(
        "--all-branches", action="store_true", help="show services across ALL branches (default when no -B given)"
    )
    p_svc_list.add_argument(
        "--group", action="store_true", help="group output by branch with a summary line per branch"
    )

    def _add_poll_flags(parser: argparse.ArgumentParser) -> None:
        """Add required --poll / --no-poll mutually exclusive group."""
        poll_group = parser.add_mutually_exclusive_group(required=True)
        poll_group.add_argument(
            "--poll",
            dest="poll",
            action="store_true",
            default=None,
            help="wait for operation to complete, polling every 2s",
        )
        poll_group.add_argument(
            "--no-poll",
            dest="poll",
            action="store_false",
            help="return immediately after the server accepts the request",
        )

    p_svc_restart = services_sub.add_parser("restart", help="restart a service for the branch")
    p_svc_restart.add_argument(
        "target",
        nargs="?",
        default=None,
        help="service suffix (e.g. functions, vite-bag)",
    )
    p_svc_restart.add_argument(
        "--all-services",
        action="store_true",
        help="restart all services in dependency order (stop reverse, then start forward)",
    )
    _add_poll_flags(p_svc_restart)
    add_branch_flag(p_svc_restart)

    p_svc_start = services_sub.add_parser("start", help="start a registered service (or all with --all-services)")
    p_svc_start.add_argument("target", nargs="?", default=None, help="service suffix (e.g. functions, vite-bag)")
    p_svc_start.add_argument(
        "--all-services", action="store_true", help="start all services for the branch in dependency order"
    )
    _add_poll_flags(p_svc_start)
    add_branch_flag(p_svc_start)

    p_svc_stop = services_sub.add_parser("stop", help="stop a running service (or all with --all-services)")
    p_svc_stop.add_argument("target", nargs="?", default=None, help="service suffix (e.g. functions, vite-bag)")
    p_svc_stop.add_argument(
        "--all-services", action="store_true", help="stop all services for the branch in reverse dependency order"
    )
    p_svc_stop.add_argument(
        "--all-branches", action="store_true", help="stop services across ALL branches (requires --all-services)"
    )
    p_svc_stop.add_argument("--force", action="store_true", help="force stop even if not cleanly running")
    _add_poll_flags(p_svc_stop)
    add_branch_flag(p_svc_stop)

    p_svc_setup = services_sub.add_parser("setup", help="register and set up all template services for a branch")
    add_branch_flag(p_svc_setup)

    p_svc_delvol = services_sub.add_parser(
        "delete-volumes", help="remove Docker volumes (all services must be stopped first)"
    )
    add_branch_flag(p_svc_delvol)
    p_svc_delvol.add_argument("--all-branches", action="store_true", help="delete volumes across ALL branches")

    p_svc_status = services_sub.add_parser("status", help="show detailed status for a single service")
    p_svc_status.add_argument("target", help="service suffix (e.g. functions, vite-bag)")
    add_branch_flag(p_svc_status)

    p_svc_logs = services_sub.add_parser("logs", help="show docker logs for a service")
    p_svc_logs.add_argument("target", help="service suffix (e.g. functions, vite-bag)")
    p_svc_logs.add_argument(
        "--tail", type=int, default=cli_defaults.get("logs.tail"), help="number of lines (default: 100)"
    )
    p_svc_logs.add_argument("-f", "--follow", action="store_true", help="follow log output")
    add_branch_flag(p_svc_logs)

    p_svc_migrate = services_sub.add_parser("migrate", help="re-run Supabase migrations")
    p_svc_migrate.add_argument("target", help="service suffix (e.g. supabase)")
    add_branch_flag(p_svc_migrate)

    p_svc_deps = services_sub.add_parser("deps", help="check or reinstall node_modules for a service")
    p_svc_deps.add_argument("target", help="service suffix (e.g. vite-bag)")
    p_svc_deps.add_argument(
        "--reinstall",
        action="store_true",
        help="force reinstall deps (stop, remove volume, recreate)",
    )
    add_branch_flag(p_svc_deps)

    p_svc_orphans = services_sub.add_parser("orphans", help="find Docker containers not tracked by the dashboard")
    p_svc_orphans.add_argument("--stop", action="store_true", help="stop all orphaned containers")

    # superv home: show/create ~/.superv/ skeleton.
    p_home = sub.add_parser("home", help="show superv home directory and create skeleton")
    p_home.set_defaults(_cmd=("supervisor.commands.home_cmd", "cmd_home"))

    # superv init: create a managed project under ~/.superv/projects/.
    p_init = sub.add_parser("init", help="initialize a managed project under ~/.superv/projects/")
    p_init.set_defaults(_cmd=("supervisor.commands.init_cmd", "cmd_init"))
    p_init.add_argument("name", help="project name (used as directory name)")
    p_init.add_argument("--remote", required=True, help="git remote URL to clone")
    p_init.add_argument("--base", default="production", help="base branch name (default: production)")

    # superv migrate: copy state from .supervisor/ to ~/.superv/.
    p_migrate = sub.add_parser("migrate", help="migrate state from .supervisor/ to ~/.superv/")
    p_migrate.set_defaults(_cmd=("supervisor.commands.migrate_cmd", "cmd_migrate"))

    # Repo listing.
    p = sub.add_parser("repos", help="list configured repositories")
    p.set_defaults(_cmd=("supervisor.commands.repos", "cmd_repos"))

    # -- authentication and server setup group ----------------------------------
    p_auth = sub.add_parser("auth", help="authentication and server setup")
    auth_sub = p_auth.add_subparsers(dest="auth_command")
    # v auth login (was: v login)
    p_al = auth_sub.add_parser("login", help="authenticate with the dev server")
    p_al.set_defaults(_cmd=("supervisor.commands.login", "cmd_login"))
    p_al.add_argument("--browser", action="store_true", help="open login page in browser")
    p_al.add_argument("--token", metavar="TOKEN", help="store a token directly (programmatic/browser flow)")
    # v auth logout (was: v logout)
    p_alo = auth_sub.add_parser("logout", help="remove stored authentication token")
    p_alo.set_defaults(_cmd=("supervisor.commands.login", "cmd_logout"))
    # v auth setup (was: v setup)
    p_as = auth_sub.add_parser("setup", help="one-time server bootstrap wizard")
    p_as.set_defaults(_cmd=("supervisor.commands.setup", "cmd_setup"))

    # -- documentation tools group ---------------------------------------------
    p_docs = sub.add_parser("docs", help="documentation tools")
    p_docs.set_defaults(_cmd=("supervisor.commands.gendocs", "_print_docs_help"))
    docs_sub = p_docs.add_subparsers(dest="docs_command")

    # v docs regenerate (was: v gendocs)
    p_docs_regen = docs_sub.add_parser("regenerate", help="generate docs/cli.md from the argparse parser")
    p_docs_regen.set_defaults(_cmd=("supervisor.commands.gendocs", "cmd_gendocs"))

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
        (
            "supervisor.commands.branch",
            {
                "branch": "_print_branch_help",
                "branch.list": "cmd_ls",
                "branch.alias": "cmd_alias",
                "branch.rename": "cmd_rename",
            },
        ),
        ("supervisor.commands.switch", {"branch.select": "cmd_switch"}),
        ("supervisor.commands.new", {"branch.create": "cmd_new"}),
        ("supervisor.commands.fin", {"branch.finalize": "cmd_fin"}),
        ("supervisor.commands.status", {"branch.status": "cmd_status"}),
        ("supervisor.commands.check_cmd", {"check": "cmd_check"}),
        ("supervisor.commands.plugins_cmd", {"plugins": "cmd_plugins"}),
        (
            "supervisor.commands.git_cmd",
            {
                "git": "_print_git_help",
                "git.diff": "cmd_diff",
                "git.changes": "cmd_changes",
                "git.commits": "cmd_commits",
                "git.compare": "cmd_compare",
                "git.explain": "cmd_explain",
                "git.rebase": "cmd_rebase",
                "git.push": "cmd_push",
            },
        ),
        ("supervisor.commands.history", {"git.history": "cmd_history"}),
        ("supervisor.commands.home_cmd", {"home": "cmd_home"}),
        ("supervisor.commands.init_cmd", {"init": "cmd_init"}),
        ("supervisor.commands.migrate_cmd", {"migrate": "cmd_migrate"}),
        ("supervisor.commands.repos", {"repos": "cmd_repos"}),
        ("supervisor.commands.gendocs", {"docs": "_print_docs_help", "docs.regenerate": "cmd_gendocs"}),
        ("supervisor.commands.services_cmd", {"services": "cmd_services"}),
        (
            "supervisor.commands.login",
            {"auth.login": "cmd_login", "auth.logout": "cmd_logout"},
        ),
        ("supervisor.commands.setup", {"auth.setup": "cmd_setup"}),
        ("supervisor.commands.server_cmd", {"server": "cmd_server"}),
        ("supervisor.commands.todo", {"todo": "cmd_todo"}),
    ]
    for module_path, cmd_map in _imports:
        mod = importlib.import_module(module_path)
        for cmd_name, attr_name in cmd_map.items():
            table[cmd_name] = getattr(mod, attr_name)

    # Include plugin commands so the doc generator can list them.
    # Plugins are already loaded by build_parser() -> _register_plugin_commands(),
    # so the registry is populated by the time gendocs calls _load_commands().
    from supervisor.plugins import registry

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
    from supervisor.bus import bus
    from supervisor.bus import registry as _bus_registry
    from supervisor.bus.subscribers.audit import install_audit_subscriber

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
    from supervisor.plugins import registry

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
