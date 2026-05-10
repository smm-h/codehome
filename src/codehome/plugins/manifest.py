"""Plugin manifest types and parser.

Each plugin ships a ``plugin.toml`` that declares metadata, commands,
checks, and optional dashboard integration.  This module defines the
frozen dataclass types that represent a parsed manifest and the
``parse_manifest`` function that reads + validates one from disk.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Declared types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArgumentDecl:
    """A CLI argument declared by a plugin command.

    Attributes:
        name: Argument name. Positionals don't start with ``-``.
            Optionals start with ``--``.
        short: Short form (e.g. ``-B``). Empty string means no short form.
        help: Help text.
        type: One of ``str``, ``int``, ``float``, ``path``, ``bool``.
        required: Whether the argument is required.
        default: Default value. ``None`` means no default set.
        choices: Valid choices. Empty means unconstrained.
        nargs: One of ``""``, ``?``, ``*``, ``+``, ``remainder``.
        action: One of ``""``, ``store_true``, ``store_false``, ``append``,
            ``count``.
        dest: Destination attribute name. Empty means argparse derives it.
        metavar: Metavar for help display. Empty means argparse default.
        hidden: If ``True``, help is suppressed (argparse.SUPPRESS).
        mutex_group: Name of a mutually exclusive group. Arguments with the
            same non-empty mutex_group on the same command are placed in an
            argparse mutually exclusive group. Empty means no group.

    """

    name: str
    short: str = ""
    help: str = ""
    type: str = "str"
    required: bool = False
    default: object = None
    choices: tuple[str, ...] = ()
    nargs: str = ""
    action: str = ""
    dest: str = ""
    metavar: str = ""
    hidden: bool = False
    mutex_group: str = ""


@dataclass(frozen=True)
class CommandDecl:
    """A CLI command declared by a plugin.

    Attributes:
        name: Kebab-case command identifier (e.g. ``screen-start``).
        handler: Function name in the plugin's ``handlers.py``.  Empty
            string means the command is a group parent; the framework
            auto-prints help when invoked without a subcommand.
        description: Human-readable help text.
        arguments: Declared CLI arguments for this command.
        subcommands: Nested subcommands (makes CommandDecl recursive).
        includes: Names of argument templates to include (resolved at
            parser-build time).

    """

    name: str
    handler: str = ""
    description: str = ""
    arguments: tuple[ArgumentDecl, ...] = ()
    subcommands: tuple[CommandDecl, ...] = ()
    includes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckDecl:
    """A check declared by a plugin.

    Attributes:
        name: Kebab-case check identifier.
        group: Check group (gate, precommit, pre-stage, pre-production, worktree-init).
        timeout: Maximum execution time in seconds.
        handler: Function name in the plugin's ``checks.py``.
        description: Human-readable help text.
        cwd: Working directory relative to the project root.
        depends_on: Names of checks that must pass before this one runs.
        advisory: If ``True``, failure is a warning, not an error.

    """

    name: str
    group: str
    timeout: int
    handler: str
    description: str = ""
    cwd: str = "."
    depends_on: tuple[str, ...] = ()
    advisory: bool = False


@dataclass(frozen=True)
class ServiceDecl:
    """A callable service declared by a plugin (v2).

    Services are named entry points that other plugins (or the framework)
    can invoke.

    Attributes:
        name: Dotted service identifier (e.g. ``publisher.dispatch_argv``).
        handler: Function name in the plugin's handler module.
        description: Human-readable summary.

    """

    name: str
    handler: str
    description: str = ""


@dataclass(frozen=True)
class SubscriptionDecl:
    """An event subscription declared by a plugin (v2).

    Declares that the plugin wants to receive a particular event.  The
    actual wiring is done via ``events.toml``; this declaration exists
    so the loader can validate that subscriptions reference real handlers.

    Attributes:
        event: Event name to subscribe to.
        handler: Function name that handles the event.
        module: Source module containing the handler (default ``handlers``).

    """

    event: str
    handler: str
    module: str = "handlers"


@dataclass(frozen=True)
class DashboardDecl:
    """Dashboard integration declared by a plugin.

    Attributes:
        group: ``"root"`` (top-level page) or ``"branch"`` (tab within branch view).
        route: Dashboard route path (e.g. ``/telemac``).
        icon: Icon identifier for the navigation entry.
        label: Navigation label.
        event_types: SSE event types this dashboard panel subscribes to.

    """

    group: str
    route: str
    icon: str = ""
    label: str = ""
    event_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginManifest:
    """Parsed representation of a plugin's ``plugin.toml``.

    Attributes:
        name: Plugin identifier (must be unique across all plugins).
        version: Semantic version string.
        description: Human-readable summary.
        enabled: Whether the plugin is active (can be overridden by state).
        requires: System-level dependencies (e.g. ``ssh``, ``mkcert``).
        python_deps: PyPI package specifiers (e.g. ``pyyaml>=6.0``).
        dependencies: Other plugin names this plugin depends on.
        passthrough: Whether the CLI allows extra unparsed args for this plugin.
        commands: CLI commands declared by the plugin.
        checks: Checks declared by the plugin.
        dashboard: Dashboard integration, or ``None`` if not declared.
        reads_state: Cross-plugin state keys this plugin reads (v2).
        services: Callable services declared by the plugin (v2).
        subscriptions: Event subscriptions declared by the plugin (v2).

    """

    name: str
    version: str
    description: str = ""
    enabled: bool = True
    requires: tuple[str, ...] = ()
    python_deps: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    passthrough: bool = False
    commands: tuple[CommandDecl, ...] = ()
    checks: tuple[CheckDecl, ...] = ()
    dashboard: DashboardDecl | None = None
    # v2 fields -- optional, default to empty for backward compat with v1 manifests.
    reads_state: tuple[str, ...] = ()
    services: tuple[ServiceDecl, ...] = ()
    subscriptions: tuple[SubscriptionDecl, ...] = ()
    # Namespace mounting: if set, the plugin is importable as codehome.<namespace>.
    # namespace_root selects a subdirectory within the plugin to mount (default: plugin root).
    namespace: str = ""
    namespace_root: str = ""
    # If True, mount routes at API root (endpoints define their own /api/ paths)
    # instead of the default /api/p/<name>/ prefix.
    root_routes: bool = False
    # Structured dependency sections from [deps] and [system] TOML tables.
    # python_deps remains as a convenience alias (maps to deps_required).
    deps_required: tuple[str, ...] = ()
    deps_optional: tuple[str, ...] = ()
    system_required: tuple[str, ...] = ()
    system_optional: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KNOWN_CHECK_GROUPS = frozenset(
    {
        "gate",
        "precommit",
        "pre-stage",
        "pre-production",
        "worktree-init",
        "lisa-explore",
        "lisa-flows",
    }
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _require(data: dict[str, Any], key: str, path: Path) -> str:
    """Extract a required string field, raising on absence."""
    value: object = data.get(key)
    if not value or not isinstance(value, str):
        msg = f"{path}: missing or empty required field '{key}'"
        raise ValueError(msg)
    return value


def _parse_argument(raw: dict[str, Any], path: Path) -> ArgumentDecl:
    """Parse a single argument declaration from a TOML dict."""
    return ArgumentDecl(
        name=_require(raw, "name", path),
        short=raw.get("short", ""),
        help=raw.get("help", ""),
        type=raw.get("type", "str"),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        choices=tuple(raw.get("choices", ())),
        nargs=raw.get("nargs", ""),
        action=raw.get("action", ""),
        dest=raw.get("dest", ""),
        metavar=raw.get("metavar", ""),
        hidden=bool(raw.get("hidden", False)),
        mutex_group=raw.get("mutex_group", ""),
    )


def _parse_commands(raw_list: list[dict[str, Any]], path: Path) -> tuple[CommandDecl, ...]:
    """Parse the ``[[commands]]`` array from the manifest.

    Handles recursive subcommand nesting and argument declarations.
    """
    result: list[CommandDecl] = []
    for i, entry in enumerate(raw_list):
        try:
            # Parse nested arguments.
            raw_args = entry.get("arguments", [])
            arguments = tuple(_parse_argument(a, path) for a in raw_args)

            # Parse nested subcommands (recursive).
            raw_subs = entry.get("subcommands", [])
            subcommands = _parse_commands(raw_subs, path)

            # Parse includes (template names).
            includes = tuple(entry.get("includes", ()))

            result.append(
                CommandDecl(
                    name=_require(entry, "name", path),
                    handler=entry.get("handler", ""),
                    description=entry.get("description", ""),
                    arguments=arguments,
                    subcommands=subcommands,
                    includes=includes,
                )
            )
        except (TypeError, KeyError) as exc:
            msg = f"{path}: invalid [[commands]] entry at index {i}: {exc}"
            raise ValueError(msg) from exc
    return tuple(result)


def _parse_checks(raw_list: list[dict[str, Any]], path: Path) -> tuple[CheckDecl, ...]:
    """Parse the ``[[checks]]`` array from the manifest."""
    result: list[CheckDecl] = []
    for i, entry in enumerate(raw_list):
        try:
            name = _require(entry, "name", path)
            group = _require(entry, "group", path)

            if group not in _KNOWN_CHECK_GROUPS:
                logger.warning("%s: check '%s' declares unknown group '%s'", path, name, group)

            result.append(
                CheckDecl(
                    name=name,
                    group=group,
                    timeout=int(entry.get("timeout", 30) or 30),
                    handler=_require(entry, "handler", path),
                    description=entry.get("description", ""),
                    cwd=entry.get("cwd", "."),
                    depends_on=tuple(entry.get("depends_on", ())),
                    advisory=bool(entry.get("advisory", False)),
                )
            )
        except (TypeError, KeyError) as exc:
            msg = f"{path}: invalid [[checks]] entry at index {i}: {exc}"
            raise ValueError(msg) from exc
    return tuple(result)


def _parse_dashboard(raw: dict[str, Any], path: Path) -> DashboardDecl:
    """Parse the ``[dashboard]`` section from the manifest."""
    return DashboardDecl(
        group=_require(raw, "group", path),
        route=_require(raw, "route", path),
        icon=raw.get("icon", ""),
        label=raw.get("label", ""),
        event_types=tuple(raw.get("event_types", ())),
    )


def _parse_services(raw_list: list[dict[str, Any]], path: Path) -> tuple[ServiceDecl, ...]:
    """Parse the ``[[services]]`` array from the manifest (v2)."""
    result: list[ServiceDecl] = []
    for i, entry in enumerate(raw_list):
        try:
            result.append(
                ServiceDecl(
                    name=_require(entry, "name", path),
                    handler=_require(entry, "handler", path),
                    description=entry.get("description", ""),
                )
            )
        except (TypeError, KeyError) as exc:
            msg = f"{path}: invalid [[services]] entry at index {i}: {exc}"
            raise ValueError(msg) from exc
    return tuple(result)


def _parse_subscriptions(raw_list: list[dict[str, Any]], path: Path) -> tuple[SubscriptionDecl, ...]:
    """Parse the ``[[subscriptions]]`` array from the manifest (v2)."""
    result: list[SubscriptionDecl] = []
    for i, entry in enumerate(raw_list):
        try:
            result.append(
                SubscriptionDecl(
                    event=_require(entry, "event", path),
                    handler=_require(entry, "handler", path),
                    module=entry.get("module", "handlers"),
                )
            )
        except (TypeError, KeyError) as exc:
            msg = f"{path}: invalid [[subscriptions]] entry at index {i}: {exc}"
            raise ValueError(msg) from exc
    return tuple(result)


def parse_manifest(plugin_dir: Path) -> PluginManifest:
    """Read and validate a ``plugin.toml`` manifest from *plugin_dir*.

    Args:
        plugin_dir: Directory containing the ``plugin.toml`` file.

    Returns:
        A fully-populated :class:`PluginManifest`.

    Raises:
        FileNotFoundError: If ``plugin.toml`` does not exist.
        ValueError: If required fields are missing or entries are malformed.

    """
    path = plugin_dir / "plugin.toml"
    if not path.is_file():
        msg = f"plugin manifest not found: {path}"
        raise FileNotFoundError(msg)

    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            msg = f"{path}: invalid TOML: {exc}"
            raise ValueError(msg) from exc

    # Required top-level fields.
    name = _require(data, "name", path)
    version = _require(data, "version", path)

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        logger.warning("%s: version '%s' is not semantic (expected X.Y.Z)", path, version)

    # Optional top-level fields.
    description = data.get("description", "")
    enabled = bool(data.get("enabled", True))
    requires = tuple(data.get("requires", ()))
    python_deps = tuple(data.get("python_deps", ()))
    dependencies = tuple(data.get("dependencies", ()))
    passthrough = bool(data.get("passthrough", False))

    # Nested sections.
    commands = _parse_commands(data.get("commands", []), path)
    checks = _parse_checks(data.get("checks", []), path)

    dashboard: DashboardDecl | None = None
    if "dashboard" in data:
        dashboard = _parse_dashboard(data["dashboard"], path)

    # v2 fields -- absent in v1 manifests, default to empty.
    reads_state = tuple(data.get("reads_state", ()))
    services = _parse_services(data.get("services", []), path)
    subscriptions = _parse_subscriptions(data.get("subscriptions", []), path)

    # Namespace mounting fields.
    namespace = data.get("namespace", "")
    namespace_root = data.get("namespace_root", "")

    # Root routes: mount at API root instead of /api/p/<name>/.
    root_routes = bool(data.get("root_routes", False))

    # Structured [deps] and [system] sections (nested TOML tables).
    deps_section = data.get("deps", {})
    deps_required = tuple(deps_section.get("required", ()))
    deps_optional = tuple(deps_section.get("optional", ()))
    system_section = data.get("system", {})
    system_required = tuple(system_section.get("required", ()))
    system_optional = tuple(system_section.get("optional", ()))

    return PluginManifest(
        name=name,
        version=version,
        description=description,
        enabled=enabled,
        requires=requires,
        python_deps=python_deps,
        dependencies=dependencies,
        passthrough=passthrough,
        commands=commands,
        checks=checks,
        dashboard=dashboard,
        reads_state=reads_state,
        services=services,
        subscriptions=subscriptions,
        namespace=namespace,
        namespace_root=namespace_root,
        root_routes=root_routes,
        deps_required=deps_required,
        deps_optional=deps_optional,
        system_required=system_required,
        system_optional=system_optional,
    )
