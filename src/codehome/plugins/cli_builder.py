"""Build argparse trees from declarative plugin manifests.

Converts :class:`CommandDecl` trees into argparse subparsers without
importing any plugin Python.  Handler functions are loaded lazily via
:class:`LazyHandler` -- only the invoked command's module is imported.

Type mapping
~~~~~~~~~~~~

The ``type`` field on :class:`ArgumentDecl` maps to Python callables:

- ``"str"`` or ``""`` -> ``None`` (argparse default, i.e. str)
- ``"int"`` -> ``int``
- ``"float"`` -> ``float``
- ``"path"`` -> ``pathlib.Path``
- ``"bool"`` -> not used as a type; means ``action="store_true"``
- anything else -> ``ValueError``
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codehome.plugins.manifest import ArgumentDecl, CommandDecl

# Actions that are incompatible with the ``type`` kwarg in argparse.
_NO_TYPE_ACTIONS = frozenset({"store_true", "store_false", "append", "count"})


def map_type(type_str: str) -> type | None:
    """Map a manifest type string to a Python callable for argparse.

    Returns ``None`` when argparse should use its default (str).
    Raises ``ValueError`` for unknown type strings.

    The special value ``"bool"`` is not handled here -- callers must
    check for it and use ``action="store_true"`` instead.
    """
    if type_str in ("str", ""):
        return None
    if type_str == "int":
        return int
    if type_str == "float":
        return float
    if type_str == "path":
        return Path
    if type_str == "bool":
        # "bool" is not a type -- callers should set action="store_true".
        # Return None so it doesn't break if a caller accidentally passes
        # a bool-typed arg through here; the action handles it.
        return None
    msg = f"unknown argument type: {type_str!r} (expected str, int, float, path, or bool)"
    raise ValueError(msg)


class LazyHandler:
    """Callable that lazily imports a plugin handler on first invocation.

    Constructed at parser-build time with only string metadata.  No
    Python module is imported until ``__call__`` is invoked (i.e. when
    the user actually runs the command).

    Args:
        plugin_dir: Absolute path to the plugin directory on disk.
        handler_ref: Handler reference string.  Plain name (e.g.
            ``"cmd_foo"``) looks up in ``handlers.py``.  Colon-separated
            (e.g. ``"screen:cmd_screen"``) looks up ``cmd_screen`` in
            ``screen.py``.
    """

    def __init__(self, plugin_dir: Path, handler_ref: str) -> None:
        self._plugin_dir = plugin_dir
        self._handler_ref = handler_ref
        # Resolved lazily on first call.
        self._resolved: object | None = None

    def __repr__(self) -> str:
        return f"LazyHandler({self._plugin_dir.name!r}, {self._handler_ref!r})"

    def __call__(self, args: argparse.Namespace) -> None:
        if self._resolved is None:
            self._resolved = self._import()
        self._resolved(args)  # type: ignore[operator]

    def _import(self) -> object:
        """Import the handler module and resolve the function."""
        if ":" in self._handler_ref:
            module_name, func_name = self._handler_ref.split(":", 1)
        else:
            module_name = "handlers"
            func_name = self._handler_ref

        module_file = self._plugin_dir / f"{module_name}.py"
        if not module_file.is_file():
            msg = f"handler module not found: {module_file}"
            raise ImportError(msg)

        # Use a unique spec name to avoid collisions between plugins.
        spec_name = f"codehome_plugin_{self._plugin_dir.name}_{module_name}"
        spec = importlib.util.spec_from_file_location(spec_name, module_file)
        if spec is None or spec.loader is None:
            msg = f"cannot create import spec for {module_file}"
            raise ImportError(msg)

        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = mod
        spec.loader.exec_module(mod)

        func = getattr(mod, func_name, None)
        if func is None:
            msg = f"handler function {func_name!r} not found in {module_file}"
            raise AttributeError(msg)
        if not callable(func):
            msg = f"handler {func_name!r} in {module_file} is not callable"
            raise TypeError(msg)

        return func


def _add_argument(parser: argparse.ArgumentParser, arg: ArgumentDecl) -> None:
    """Add a single ArgumentDecl to an argparse parser."""
    kwargs: dict[str, object] = {}

    # Help text.
    if arg.hidden:
        kwargs["help"] = argparse.SUPPRESS
    elif arg.help:
        kwargs["help"] = arg.help

    # Action -- must be set before type decision.
    effective_action = arg.action
    if not effective_action and arg.type == "bool":
        effective_action = "store_true"

    if effective_action:
        kwargs["action"] = effective_action

    # Type -- skip for actions that don't accept it.
    if effective_action not in _NO_TYPE_ACTIONS:
        mapped_type = map_type(arg.type)
        if mapped_type is not None:
            kwargs["type"] = mapped_type

    # Choices.
    if arg.choices:
        kwargs["choices"] = list(arg.choices)

    # Nargs.
    if arg.nargs:
        if arg.nargs == "remainder":
            kwargs["nargs"] = argparse.REMAINDER
        else:
            kwargs["nargs"] = arg.nargs

    # Default.
    if arg.default is not None:
        kwargs["default"] = arg.default

    # Dest.
    if arg.dest:
        kwargs["dest"] = arg.dest

    # Metavar.
    if arg.metavar:
        kwargs["metavar"] = arg.metavar

    # Positional vs optional.
    is_optional = arg.name.startswith("-")

    if is_optional:
        # Required (only for optional args).
        if arg.required:
            kwargs["required"] = True
        # Name args: --name and optional short form.
        name_args = [arg.name]
        if arg.short:
            name_args.append(arg.short)
        parser.add_argument(*name_args, **kwargs)
    else:
        # Positional argument.
        parser.add_argument(arg.name, **kwargs)


def _print_help_handler(parser: argparse.ArgumentParser) -> object:
    """Return a callable that prints help for a group command."""

    def _handler(args: argparse.Namespace) -> None:
        parser.print_help()

    return _handler


def build_commands(
    commands: tuple[CommandDecl, ...],
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    plugin_dir: Path,
) -> None:
    """Recursively build argparse subparsers from CommandDecl trees.

    Args:
        commands: Top-level commands declared by the plugin.
        sub: The parent subparsers action to add commands to.
        plugin_dir: Plugin directory (used by LazyHandler for imports).
    """
    from codehome.plugins.templates import resolve_includes

    for cmd in commands:
        parser = sub.add_parser(cmd.name, help=cmd.description or None)

        # Collect arguments: template includes first, then command-specific.
        # Command-specific args override template args on name collision.
        seen_names: dict[str, ArgumentDecl] = {}
        arg_order: list[str] = []

        # 1. Resolve template includes.
        if cmd.includes:
            try:
                template_args = resolve_includes(cmd.includes)
            except ValueError:
                # Template resolution failure is non-fatal at build time.
                template_args = ()
            for targ in template_args:
                if targ.name not in seen_names:
                    arg_order.append(targ.name)
                seen_names[targ.name] = targ

        # 2. Command-specific arguments (override templates on collision).
        for arg in cmd.arguments:
            if arg.name not in seen_names:
                arg_order.append(arg.name)
            seen_names[arg.name] = arg

        # 3. Add all arguments to the parser.
        for name in arg_order:
            _add_argument(parser, seen_names[name])

        # 4. Handle subcommands or set handler.
        if cmd.subcommands:
            child_sub = parser.add_subparsers(dest=f"{cmd.name}_cmd")
            build_commands(cmd.subcommands, child_sub, plugin_dir)

        # Set _cmd default.
        if cmd.handler:
            parser.set_defaults(_cmd=LazyHandler(plugin_dir, cmd.handler))
        else:
            # Group command with no handler: print help.
            parser.set_defaults(_cmd=_print_help_handler(parser))
