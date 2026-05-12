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
_NO_TYPE_ACTIONS = frozenset({"store_true", "store_false", "count"})


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

        # Set up the plugin's _sdk.py so ``from _sdk import ...``
        # works inside the handler module, mirroring the pattern in
        # service_registry._resolve_deferred().
        sdk_path = self._plugin_dir / "_sdk.py"
        prev_sdk = sys.modules.get("_sdk")
        _sdk_installed = False
        if sdk_path.is_file():
            sdk_spec_name = f"_plugin_{self._plugin_dir.name}__sdk"
            sdk_mod = sys.modules.get(sdk_spec_name)
            if sdk_mod is None:
                sdk_spec = importlib.util.spec_from_file_location(
                    sdk_spec_name, sdk_path
                )
                if sdk_spec and sdk_spec.loader:
                    sdk_mod = importlib.util.module_from_spec(sdk_spec)
                    sys.modules[sdk_spec_name] = sdk_mod
                    try:
                        sdk_spec.loader.exec_module(sdk_mod)
                    except Exception:
                        sys.modules.pop(sdk_spec_name, None)
                        sdk_mod = None
            if sdk_mod is not None:
                sys.modules["_sdk"] = sdk_mod
                _sdk_installed = True

        try:
            spec.loader.exec_module(mod)
        finally:
            # Restore previous _sdk state.
            if _sdk_installed:
                if prev_sdk is not None:
                    sys.modules["_sdk"] = prev_sdk
                else:
                    sys.modules.pop("_sdk", None)

        func = getattr(mod, func_name, None)
        if func is None:
            msg = f"handler function {func_name!r} not found in {module_file}"
            raise AttributeError(msg)
        if not callable(func):
            msg = f"handler {func_name!r} in {module_file} is not callable"
            raise TypeError(msg)

        return func


def _build_arg_kwargs(arg: ArgumentDecl) -> tuple[list[str], dict[str, object]]:
    """Build the name-args list and kwargs dict for ``add_argument``.

    Returns a ``(name_args, kwargs)`` tuple ready to be unpacked into
    ``parser.add_argument(*name_args, **kwargs)`` (or the equivalent
    call on a mutually exclusive group).
    """
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
    else:
        name_args = [arg.name]

    return name_args, kwargs


def _add_argument(parser: argparse.ArgumentParser, arg: ArgumentDecl) -> None:
    """Add a single ArgumentDecl to an argparse parser."""
    name_args, kwargs = _build_arg_kwargs(arg)
    parser.add_argument(*name_args, **kwargs)


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

        # 3. Add all arguments to the parser, respecting mutex groups.
        #    Collect mutex group names in insertion order, then create one
        #    argparse mutually exclusive group per unique name.
        mutex_groups: dict[str, argparse._MutuallyExclusiveGroup] = {}
        for name in arg_order:
            arg = seen_names[name]
            if arg.mutex_group:
                if arg.mutex_group not in mutex_groups:
                    mutex_groups[arg.mutex_group] = parser.add_mutually_exclusive_group()
                group = mutex_groups[arg.mutex_group]
                name_args, kwargs = _build_arg_kwargs(arg)
                group.add_argument(*name_args, **kwargs)
            else:
                _add_argument(parser, arg)

        # 4. Handle subcommands or set handler.
        if cmd.subcommands:
            child_sub = parser.add_subparsers(dest=f"{cmd.name}_cmd")
            build_commands(cmd.subcommands, child_sub, plugin_dir)

        # Set _cmd default.
        if cmd.handler:
            parser.set_defaults(_cmd=LazyHandler(plugin_dir, cmd.handler))
        elif cmd.subcommands:
            # Group command with subcommands but no handler: print help
            # when invoked without a subcommand.
            parser.set_defaults(_cmd=_print_help_handler(parser))
        # else: leaf subcommand with no handler -- do NOT set _cmd here.
        # The parent command's handler dispatches based on the subcommand
        # name.  Setting _cmd would override the parent's _cmd in
        # argparse's namespace and prevent the parent handler from running.
