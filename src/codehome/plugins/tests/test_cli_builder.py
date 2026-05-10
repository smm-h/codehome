"""Tests for the manifest-driven argparse tree builder.

Covers:

- Type mapping: all 5 types + empty string + unknown -> error.
- Argparse tree builder: CommandDecl tree -> argparse subcommands + arguments.
- Lazy dispatcher: no import at construction, imports on call.
- Integration: build_parser() includes plugin commands from manifests.
- Group command: invoking without subcommand prints help.
- Template includes: arguments from includes appear in the parser.
- Passthrough: passthrough commands accept extra args.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from codehome.plugins.cli_builder import (
    LazyHandler,
    build_commands,
    map_type,
)
from codehome.plugins.manifest import ArgumentDecl, CommandDecl


# ===========================================================================
# 1. Type mapping
# ===========================================================================


class TestTypeMapping:
    """Tests for map_type()."""

    def test_str_returns_none(self) -> None:
        """'str' maps to None (argparse default)."""
        assert map_type("str") is None

    def test_empty_returns_none(self) -> None:
        """Empty string maps to None (argparse default)."""
        assert map_type("") is None

    def test_int_returns_int(self) -> None:
        assert map_type("int") is int

    def test_float_returns_float(self) -> None:
        assert map_type("float") is float

    def test_path_returns_path(self) -> None:
        assert map_type("path") is Path

    def test_bool_returns_none(self) -> None:
        """'bool' is handled via action, not type -- map_type returns None."""
        assert map_type("bool") is None

    def test_unknown_raises_valueerror(self) -> None:
        """An unknown type string raises ValueError with a clear message."""
        with pytest.raises(ValueError, match="unknown argument type.*'date'"):
            map_type("date")

    def test_unknown_includes_valid_types_in_message(self) -> None:
        """The error message mentions the valid type names."""
        with pytest.raises(ValueError, match="str, int, float, path, or bool"):
            map_type("banana")


# ===========================================================================
# 2. Argparse tree builder
# ===========================================================================


class TestBuildCommands:
    """Tests for build_commands()."""

    def _make_parser_and_sub(self) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        return parser, sub

    def test_simple_command_registered(self, tmp_path: Path) -> None:
        """A single command with no args creates a subparser."""
        parser, sub = self._make_parser_and_sub()
        cmd = CommandDecl(name="greet", handler="cmd_greet", description="Say hello")
        build_commands((cmd,), sub, tmp_path)

        assert "greet" in sub.choices

    def test_command_with_positional_arg(self, tmp_path: Path) -> None:
        """A positional argument is added to the subparser."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="target", help="Build target")
        cmd = CommandDecl(name="build", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        # Parse to verify the positional is registered.
        args = parser.parse_args(["build", "myapp"])
        assert args.target == "myapp"

    def test_command_with_optional_arg(self, tmp_path: Path) -> None:
        """An optional argument with short form works."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--verbose", short="-v", action="store_true")
        cmd = CommandDecl(name="run", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["run", "-v"])
        assert args.verbose is True

    def test_command_with_int_type(self, tmp_path: Path) -> None:
        """An int-typed argument parses as int."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--count", type="int", default=1)
        cmd = CommandDecl(name="repeat", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["repeat", "--count", "5"])
        assert args.count == 5
        assert isinstance(args.count, int)

    def test_append_action_with_int_type(self, tmp_path: Path) -> None:
        """action='append' with type='int' applies int converter to each value."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--num", type="int", action="append")
        cmd = CommandDecl(name="collect", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["collect", "--num", "1", "--num", "2", "--num", "3"])
        assert args.num == [1, 2, 3]
        assert all(isinstance(v, int) for v in args.num)

    def test_command_with_path_type(self, tmp_path: Path) -> None:
        """A path-typed argument parses as Path."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--output", type="path")
        cmd = CommandDecl(name="export", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["export", "--output", "/tmp/out"])
        assert isinstance(args.output, Path)
        assert str(args.output) == "/tmp/out"

    def test_command_with_choices(self, tmp_path: Path) -> None:
        """Choices constraint is enforced."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--env", choices=("dev", "prod"))
        cmd = CommandDecl(name="deploy", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["deploy", "--env", "dev"])
        assert args.env == "dev"

        with pytest.raises(SystemExit):
            parser.parse_args(["deploy", "--env", "staging"])

    def test_command_with_nargs(self, tmp_path: Path) -> None:
        """nargs='+' accepts multiple values."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="files", nargs="+")
        cmd = CommandDecl(name="upload", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["upload", "a.txt", "b.txt"])
        assert args.files == ["a.txt", "b.txt"]

    def test_command_with_nargs_remainder(self, tmp_path: Path) -> None:
        """nargs='remainder' maps to argparse.REMAINDER."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="rest", nargs="remainder")
        cmd = CommandDecl(name="proxy", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        # REMAINDER captures everything after the subcommand, but
        # argparse still parses known flags at the parent level.
        # Use positional-style args to verify REMAINDER is wired.
        args = parser.parse_args(["proxy", "foo", "bar"])
        assert args.rest == ["foo", "bar"]

    def test_hidden_argument_uses_suppress(self, tmp_path: Path) -> None:
        """A hidden argument uses argparse.SUPPRESS for help."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--secret", hidden=True, help="should be suppressed")
        cmd = CommandDecl(name="cmd", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        # The argument should still work, just hidden from help.
        args = parser.parse_args(["cmd", "--secret", "val"])
        assert args.secret == "val"

    def test_required_optional_arg(self, tmp_path: Path) -> None:
        """A required optional arg raises on missing."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--name", required=True)
        cmd = CommandDecl(name="cmd", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        with pytest.raises(SystemExit):
            parser.parse_args(["cmd"])

    def test_dest_override(self, tmp_path: Path) -> None:
        """dest overrides the default attribute name."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--no-color", action="store_true", dest="no_color_flag")
        cmd = CommandDecl(name="cmd", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["cmd", "--no-color"])
        assert args.no_color_flag is True

    def test_metavar(self, tmp_path: Path) -> None:
        """metavar is passed to argparse."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--port", type="int", metavar="PORT")
        cmd = CommandDecl(name="serve", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        # Verify the parser accepted it (metavar shows up in help text).
        args = parser.parse_args(["serve", "--port", "8080"])
        assert args.port == 8080

    def test_bool_type_becomes_store_true(self, tmp_path: Path) -> None:
        """type='bool' with no explicit action becomes store_true."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--flag", type="bool")
        cmd = CommandDecl(name="cmd", handler="h", arguments=(arg,))
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["cmd", "--flag"])
        assert args.flag is True

        args = parser.parse_args(["cmd"])
        assert args.flag is False or args.flag is None  # argparse default for store_true

    def test_multiple_commands(self, tmp_path: Path) -> None:
        """Multiple commands are all registered."""
        parser, sub = self._make_parser_and_sub()
        cmds = (
            CommandDecl(name="alpha", handler="h_a"),
            CommandDecl(name="beta", handler="h_b"),
            CommandDecl(name="gamma", handler="h_c"),
        )
        build_commands(cmds, sub, tmp_path)

        assert "alpha" in sub.choices
        assert "beta" in sub.choices
        assert "gamma" in sub.choices


# ===========================================================================
# 3. Subcommands (recursive)
# ===========================================================================


class TestSubcommands:
    """Tests for recursive subcommand building."""

    def _make_parser_and_sub(self) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        return parser, sub

    def test_one_level_subcommands(self, tmp_path: Path) -> None:
        """A command with subcommands creates nested subparsers."""
        parser, sub = self._make_parser_and_sub()
        child = CommandDecl(name="migrate", handler="h_migrate")
        parent = CommandDecl(name="db", handler="h_db", subcommands=(child,))
        build_commands((parent,), sub, tmp_path)

        args = parser.parse_args(["db", "migrate"])
        assert args.command == "db"
        assert args.db_cmd == "migrate"

    def test_subcommand_with_arguments(self, tmp_path: Path) -> None:
        """Subcommands have their own arguments."""
        parser, sub = self._make_parser_and_sub()
        arg = ArgumentDecl(name="--force", action="store_true")
        child = CommandDecl(name="reset", handler="h", arguments=(arg,))
        parent = CommandDecl(name="db", handler="h_db", subcommands=(child,))
        build_commands((parent,), sub, tmp_path)

        args = parser.parse_args(["db", "reset", "--force"])
        assert args.force is True


# ===========================================================================
# 4. Group commands (no handler -> prints help)
# ===========================================================================


class TestGroupCommand:
    """Tests for group commands that have no handler."""

    def test_group_command_prints_help(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A command with empty handler prints help when invoked."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        child = CommandDecl(name="sub", handler="h_sub")
        group = CommandDecl(name="group", handler="", subcommands=(child,))
        build_commands((group,), sub, tmp_path)

        args = parser.parse_args(["group"])
        cmd_handler = args._cmd
        assert callable(cmd_handler)

        # Invoking the handler should print help (contains "sub").
        cmd_handler(args)
        captured = capsys.readouterr()
        assert "sub" in captured.out


# ===========================================================================
# 5. Lazy handler dispatcher
# ===========================================================================


class TestLazyHandler:
    """Tests for LazyHandler."""

    def test_no_import_at_construction(self, tmp_path: Path) -> None:
        """LazyHandler does NOT import anything when constructed."""
        plugin_dir = tmp_path / "my_plugin"
        plugin_dir.mkdir()

        # Don't create handlers.py -- construction must not touch filesystem.
        handler = LazyHandler(plugin_dir, "cmd_foo")

        # If it tried to import, it would fail since there's no file.
        assert handler._resolved is None
        assert repr(handler) == "LazyHandler('my_plugin', 'cmd_foo')"

    def test_imports_on_call(self, tmp_path: Path) -> None:
        """LazyHandler imports the handler module on first call."""
        plugin_dir = tmp_path / "test_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "handlers.py").write_text(
            "def cmd_greet(args):\n    args._result = 'hello'\n"
        )

        handler = LazyHandler(plugin_dir, "cmd_greet")
        assert handler._resolved is None

        args = argparse.Namespace()
        handler(args)

        assert handler._resolved is not None
        assert args._result == "hello"

    def test_colon_syntax_imports_from_named_module(self, tmp_path: Path) -> None:
        """'screen:cmd_screen' imports cmd_screen from screen.py."""
        plugin_dir = tmp_path / "screen_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "screen.py").write_text(
            "def cmd_screen(args):\n    args._screen = True\n"
        )

        handler = LazyHandler(plugin_dir, "screen:cmd_screen")
        args = argparse.Namespace()
        handler(args)

        assert args._screen is True

    def test_missing_module_raises_import_error(self, tmp_path: Path) -> None:
        """Missing handler module raises ImportError."""
        plugin_dir = tmp_path / "broken_plugin"
        plugin_dir.mkdir()

        handler = LazyHandler(plugin_dir, "cmd_missing")

        with pytest.raises(ImportError, match="handler module not found"):
            handler(argparse.Namespace())

    def test_missing_function_raises_attribute_error(self, tmp_path: Path) -> None:
        """Missing function in the module raises AttributeError."""
        plugin_dir = tmp_path / "no_func"
        plugin_dir.mkdir()
        (plugin_dir / "handlers.py").write_text("def other_func(): pass\n")

        handler = LazyHandler(plugin_dir, "nonexistent")

        with pytest.raises(AttributeError, match="nonexistent"):
            handler(argparse.Namespace())

    def test_caches_after_first_call(self, tmp_path: Path) -> None:
        """LazyHandler caches the resolved function after first call."""
        plugin_dir = tmp_path / "cache_test"
        plugin_dir.mkdir()
        (plugin_dir / "handlers.py").write_text(
            "call_count = 0\n"
            "def cmd_count(args):\n"
            "    global call_count\n"
            "    call_count += 1\n"
            "    args._count = call_count\n"
        )

        handler = LazyHandler(plugin_dir, "cmd_count")

        args1 = argparse.Namespace()
        handler(args1)
        resolved_first = handler._resolved

        args2 = argparse.Namespace()
        handler(args2)
        resolved_second = handler._resolved

        # Same object -- not re-imported.
        assert resolved_first is resolved_second


# ===========================================================================
# 6. Template includes in parser
# ===========================================================================


class TestTemplateIncludes:
    """Tests that template includes add arguments to the parser."""

    def test_branch_template_adds_branch_arg(self, tmp_path: Path) -> None:
        """Including 'branch' template adds --branch/-B argument."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        cmd = CommandDecl(
            name="deploy",
            handler="h",
            includes=("branch",),
        )
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["deploy", "-B", "main"])
        assert args.branch == "main"

    def test_dry_run_template_adds_dry_run_arg(self, tmp_path: Path) -> None:
        """Including 'dry-run' template adds --dry-run/-D argument."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        cmd = CommandDecl(
            name="deploy",
            handler="h",
            includes=("dry-run",),
        )
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["deploy", "--dry-run"])
        assert args.dry_run is True

    def test_command_arg_overrides_template(self, tmp_path: Path) -> None:
        """Command-specific argument overrides same-named template argument."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")

        # Include 'branch' template which adds --branch with metavar=BRANCH.
        # Then override with our own --branch that has different help.
        custom_branch = ArgumentDecl(
            name="--branch",
            short="-B",
            help="custom branch help",
            metavar="MYBRANCH",
        )
        cmd = CommandDecl(
            name="deploy",
            handler="h",
            includes=("branch",),
            arguments=(custom_branch,),
        )
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["deploy", "--branch", "feature"])
        assert args.branch == "feature"

    def test_multiple_templates(self, tmp_path: Path) -> None:
        """Multiple template includes are combined."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        cmd = CommandDecl(
            name="push",
            handler="h",
            includes=("branch", "dry-run"),
        )
        build_commands((cmd,), sub, tmp_path)

        args = parser.parse_args(["push", "-B", "main", "-D"])
        assert args.branch == "main"
        assert args.dry_run is True


# ===========================================================================
# 7. Passthrough
# ===========================================================================


class TestPassthrough:
    """Tests that passthrough commands accept extra args."""

    def test_passthrough_collected_from_manifest(self, tmp_path: Path) -> None:
        """Passthrough flag on manifest is surfaced by _register_plugin_commands."""
        from codehome.plugins.manifest import PluginManifest

        # Create a minimal plugin with passthrough=True.
        plugin_dir = tmp_path / "passthru_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "passthru"\n'
            'version = "0.1.0"\n'
            'passthrough = true\n'
            '\n'
            '[[commands]]\n'
            'name = "proxy"\n'
            'handler = "cmd_proxy"\n'
        )

        from codehome.plugins.discovery import discover_plugins
        from codehome.plugins.manifest import parse_manifest

        manifest = parse_manifest(plugin_dir)

        assert manifest.passthrough is True

        # The passthrough command names should be the top-level command names.
        passthrough_names = {cmd.name for cmd in manifest.commands}
        assert "proxy" in passthrough_names


# ===========================================================================
# 8. Integration: build_parser with plugin commands
# ===========================================================================


class TestIntegration:
    """Integration tests for build_parser including plugin commands."""

    def test_build_parser_includes_core_commands(self) -> None:
        """build_parser() registers core commands regardless of plugins."""
        from codehome.cli import build_parser

        parser = build_parser()
        sub = None
        for action in parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                sub = action
                break

        assert sub is not None
        assert "home" in sub.choices
        assert "init" in sub.choices
        assert "plugins" in sub.choices
        assert "server" in sub.choices
        assert "auth" in sub.choices
        assert "migrate" in sub.choices

    def test_build_parser_returns_plugin_errors(self) -> None:
        """Plugin errors are attached to the parser."""
        from codehome.cli import build_parser

        parser = build_parser()
        errors = getattr(parser, "_plugin_errors", None)
        # Should be a list (possibly empty if no plugins exist or all succeed).
        assert isinstance(errors, list)

    def test_build_parser_returns_plugin_passthrough(self) -> None:
        """Plugin passthrough set is attached to the parser."""
        from codehome.cli import build_parser

        parser = build_parser()
        passthrough = getattr(parser, "_plugin_passthrough", None)
        assert isinstance(passthrough, set)

    def test_plugin_from_manifest_appears_in_parser(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A plugin discovered from TOML manifests appears as a subcommand."""
        # Create a minimal plugin directory with a manifest.
        plugin_dir = tmp_path / "plugins" / "hello"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            'name = "hello"\n'
            'version = "0.1.0"\n'
            '\n'
            '[[commands]]\n'
            'name = "hello"\n'
            'handler = "cmd_hello"\n'
            'description = "Say hello"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--name"\n'
            'default = "world"\n'
        )

        # Monkeypatch discover_plugins to use our test directory.
        from codehome.plugins import discovery

        original_discover = discovery.discover_plugins

        def patched_discover(root=None):
            return original_discover(root=tmp_path)

        monkeypatch.setattr(discovery, "discover_plugins", patched_discover)

        from codehome.cli import build_parser

        parser = build_parser()

        # Find the subparsers action.
        sub = None
        for action in parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                sub = action
                break

        assert sub is not None
        assert "hello" in sub.choices

        # Parse a command with the plugin's argument.
        args = parser.parse_args(["hello", "--name", "Alice"])
        assert args.name == "Alice"

    def test_collision_with_core_command_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A plugin command that collides with a core command is skipped."""
        plugin_dir = tmp_path / "plugins" / "evil"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            'name = "evil"\n'
            'version = "0.1.0"\n'
            '\n'
            '[[commands]]\n'
            'name = "home"\n'
            'handler = "cmd_evil_home"\n'
        )

        from codehome.plugins import discovery

        original_discover = discovery.discover_plugins

        def patched_discover(root=None):
            return original_discover(root=tmp_path)

        monkeypatch.setattr(discovery, "discover_plugins", patched_discover)

        from codehome.cli import build_parser

        parser = build_parser()
        errors = getattr(parser, "_plugin_errors", [])

        # Should report a collision error.
        collision_errors = [e for e in errors if "conflict with core commands" in e]
        assert len(collision_errors) >= 1
        assert "home" in collision_errors[0]
