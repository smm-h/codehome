"""Argument template loading and resolution.

Templates are named groups of :class:`ArgumentDecl` instances that
commands can pull in via ``includes = ["branch", "dry-run"]`` in their
``plugin.toml`` manifest.  Three built-in templates ship with codehome;
users can override or extend them via ``[arg-templates]`` in
``~/.codehome/config.toml``.

Templates may include other templates (e.g. ``deploy-flags`` bundles
``branch`` and ``dry-run``).  Circular includes are detected and
rejected.
"""

from __future__ import annotations

import tomllib
from typing import Any

from codehome.paths import codehome_home
from codehome.plugins.manifest import ArgumentDecl

# ---------------------------------------------------------------------------
# Built-in default templates
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "branch": {
        "arguments": [
            {
                "name": "--branch",
                "short": "-B",
                "help": "branch name (default: selected)",
                "metavar": "BRANCH",
            },
        ],
    },
    "dry-run": {
        "arguments": [
            {
                "name": "--dry-run",
                "short": "-D",
                "action": "store_true",
                "help": "show what would happen",
            },
        ],
    },
    "deploy-flags": {
        "includes": ["dry-run", "branch"],
        "arguments": [
            {
                "name": "--branch",
                "short": "-B",
                "help": "branch name (default: selected)",
                "metavar": "BRANCH",
                "hidden": True,
            },
            {
                "name": "--no-advance",
                "action": "store_true",
                "help": "skip Linear auto-advance",
            },
            {
                "name": "--skip-native-check",
                "action": "store_true",
                "help": "push even if native changes lack a binary build",
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _raw_to_argument(raw: dict[str, Any]) -> ArgumentDecl:
    """Convert a raw dict (from TOML or built-in) to an ArgumentDecl."""
    name = raw.get("name")
    if not name or not isinstance(name, str):
        msg = f"argument template entry missing 'name': {raw!r}"
        raise ValueError(msg)
    return ArgumentDecl(
        name=name,
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
    )


def _parse_template(
    name: str,
    raw: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[ArgumentDecl, ...]]:
    """Parse a single template definition into (includes, arguments).

    Returns:
        A tuple of (included template names, argument declarations).
    """
    includes = tuple(raw.get("includes", ()))
    raw_args = raw.get("arguments", [])
    arguments = tuple(_raw_to_argument(a) for a in raw_args)
    return includes, arguments


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------


class _TemplateEntry:
    """Internal representation of a loaded template."""

    __slots__ = ("includes", "own_arguments")

    def __init__(
        self,
        includes: tuple[str, ...],
        own_arguments: tuple[ArgumentDecl, ...],
    ) -> None:
        self.includes = includes
        self.own_arguments = own_arguments


def _load_user_templates() -> dict[str, dict[str, Any]]:
    """Load ``[arg-templates]`` from ``~/.codehome/config.toml``.

    Returns an empty dict if the file doesn't exist, can't be parsed,
    or doesn't contain the section.
    """
    config_file = codehome_home() / "config.toml"
    if not config_file.is_file():
        return {}
    try:
        data = tomllib.loads(config_file.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    section = data.get("arg-templates")
    if not isinstance(section, dict):
        return {}
    return section


def _build_registry(
    user_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, _TemplateEntry]:
    """Build the full template registry (builtins + user overrides).

    User-defined templates with the same name as a built-in replace
    the built-in entirely.  User templates can also add new names.
    """
    merged_raw: dict[str, dict[str, Any]] = dict(_BUILTIN_TEMPLATES)
    if user_overrides:
        merged_raw.update(user_overrides)

    registry: dict[str, _TemplateEntry] = {}
    for name, raw in merged_raw.items():
        includes, arguments = _parse_template(name, raw)
        registry[name] = _TemplateEntry(includes=includes, own_arguments=arguments)
    return registry


# Module-level cache -- populated lazily on first call.
_registry: dict[str, _TemplateEntry] | None = None


def _get_registry() -> dict[str, _TemplateEntry]:
    """Return the cached template registry, loading on first access."""
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = _build_registry(_load_user_templates())
    return _registry


def _reset_registry() -> None:
    """Clear the cached registry (for testing)."""
    global _registry  # noqa: PLW0603
    _registry = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_includes(
    includes: tuple[str, ...],
    *,
    _registry_override: dict[str, _TemplateEntry] | None = None,
) -> tuple[ArgumentDecl, ...]:
    """Resolve template names to a flat tuple of :class:`ArgumentDecl`.

    Expands nested ``includes`` recursively (depth-first).  Arguments
    from included templates appear before the including template's own
    arguments.  If a later template defines an argument with the same
    ``name`` as an earlier one, the later definition wins (last-write).

    Args:
        includes: Template names to resolve.

    Returns:
        Combined argument declarations, deduplicated by ``name``.

    Raises:
        ValueError: If a template name is not found or a circular
            include is detected.
    """
    registry = _registry_override if _registry_override is not None else _get_registry()

    # Collect arguments in order, tracking seen names for dedup.
    seen_names: dict[str, ArgumentDecl] = {}
    order: list[str] = []

    def _expand(name: str, stack: frozenset[str]) -> None:
        if name in stack:
            cycle = " -> ".join([*sorted(stack), name])
            msg = f"circular template include detected: {cycle}"
            raise ValueError(msg)
        entry = registry.get(name)
        if entry is None:
            msg = f"unknown argument template: {name!r}"
            raise ValueError(msg)

        next_stack = stack | {name}

        # Expand included templates first (depth-first).
        for child in entry.includes:
            _expand(child, next_stack)

        # Then add this template's own arguments.
        for arg in entry.own_arguments:
            if arg.name not in seen_names:
                order.append(arg.name)
            seen_names[arg.name] = arg

    for template_name in includes:
        _expand(template_name, frozenset())

    return tuple(seen_names[n] for n in order)


def list_templates() -> tuple[str, ...]:
    """Return sorted names of all available templates."""
    return tuple(sorted(_get_registry()))
