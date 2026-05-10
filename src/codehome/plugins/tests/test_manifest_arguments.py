"""Tests for declarative CLI argument support in plugin manifests.

Covers:

- Parsing ArgumentDecl fields from TOML (types, nargs, actions, choices).
- Parsing nested subcommands (3 levels deep).
- Parsing the ``includes`` field on commands.
- That the ``group`` field on CommandDecl is removed (ignored in TOML).
- Round-trip: parse TOML -> inspect dataclass tree -> all values correct.
- Edge cases: no arguments, only subcommands, argument with all defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codehome.plugins.manifest import (
    ArgumentDecl,
    CommandDecl,
    parse_manifest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(plugin_dir: Path, content: str) -> None:
    """Write a ``plugin.toml`` inside *plugin_dir*."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(content)


def _header(name: str = "arg-test", version: str = "0.1.0") -> str:
    """Return a minimal manifest header."""
    return f'name = "{name}"\nversion = "{version}"\n\n'


# ===========================================================================
# 1. ArgumentDecl dataclass
# ===========================================================================


class TestArgumentDeclDataclass:
    """Tests for the ArgumentDecl frozen dataclass itself."""

    def test_minimal_argument(self) -> None:
        """An ArgumentDecl with only name uses all defaults."""
        arg = ArgumentDecl(name="--verbose")

        assert arg.name == "--verbose"
        assert arg.short == ""
        assert arg.help == ""
        assert arg.type == "str"
        assert arg.required is False
        assert arg.default is None
        assert arg.choices == ()
        assert arg.nargs == ""
        assert arg.action == ""
        assert arg.dest == ""
        assert arg.metavar == ""
        assert arg.hidden is False

    def test_fully_specified_argument(self) -> None:
        """An ArgumentDecl with every field set retains all values."""
        arg = ArgumentDecl(
            name="--output",
            short="-o",
            help="Output path",
            type="path",
            required=True,
            default="/tmp/out",
            choices=("/tmp/out", "/var/out"),
            nargs="?",
            action="",
            dest="output_path",
            metavar="PATH",
            hidden=True,
        )

        assert arg.name == "--output"
        assert arg.short == "-o"
        assert arg.help == "Output path"
        assert arg.type == "path"
        assert arg.required is True
        assert arg.default == "/tmp/out"
        assert arg.choices == ("/tmp/out", "/var/out")
        assert arg.nargs == "?"
        assert arg.dest == "output_path"
        assert arg.metavar == "PATH"
        assert arg.hidden is True

    def test_frozen(self) -> None:
        """ArgumentDecl is frozen -- assignment raises."""
        arg = ArgumentDecl(name="pos")
        with pytest.raises(AttributeError):
            arg.name = "changed"  # type: ignore[misc]


# ===========================================================================
# 2. CommandDecl with arguments
# ===========================================================================


class TestCommandDeclWithArguments:
    """Tests for CommandDecl's new fields: arguments, subcommands, includes."""

    def test_command_with_no_arguments(self) -> None:
        """CommandDecl defaults: empty arguments, subcommands, includes."""
        cmd = CommandDecl(name="simple", handler="handle_simple")

        assert cmd.arguments == ()
        assert cmd.subcommands == ()
        assert cmd.includes == ()

    def test_command_with_arguments(self) -> None:
        """CommandDecl holds ArgumentDecl instances."""
        arg = ArgumentDecl(name="--force", short="-f", help="Force it")
        cmd = CommandDecl(
            name="deploy",
            handler="handle_deploy",
            arguments=(arg,),
        )

        assert len(cmd.arguments) == 1
        assert cmd.arguments[0].name == "--force"
        assert cmd.arguments[0].short == "-f"

    def test_command_with_subcommands(self) -> None:
        """CommandDecl can nest subcommands."""
        child = CommandDecl(name="sub", handler="handle_sub")
        parent = CommandDecl(
            name="parent",
            handler="handle_parent",
            subcommands=(child,),
        )

        assert len(parent.subcommands) == 1
        assert parent.subcommands[0].name == "sub"

    def test_command_with_includes(self) -> None:
        """CommandDecl stores template includes."""
        cmd = CommandDecl(
            name="build",
            handler="handle_build",
            includes=("branch", "dry-run"),
        )

        assert cmd.includes == ("branch", "dry-run")

    def test_group_not_on_command_decl(self) -> None:
        """CommandDecl no longer has a 'group' attribute."""
        cmd = CommandDecl(name="x", handler="h")
        assert not hasattr(cmd, "group")


# ===========================================================================
# 3. TOML parsing -- arguments
# ===========================================================================


class TestParseArguments:
    """Tests for parsing [[commands.arguments]] from TOML."""

    def test_parse_argument_all_fields(self, tmp_path: Path) -> None:
        """A command with a fully-specified argument parses all fields."""
        plugin_dir = tmp_path / "full-arg"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "deploy"\n'
            'handler = "handle_deploy"\n'
            'description = "Deploy the app"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--env"\n'
            'short = "-e"\n'
            'help = "Target environment"\n'
            'type = "str"\n'
            'required = true\n'
            'default = "staging"\n'
            'choices = ["staging", "production"]\n'
            'nargs = "?"\n'
            'action = ""\n'
            'dest = "environment"\n'
            'metavar = "ENV"\n'
            'hidden = true\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        assert len(m.commands) == 1
        cmd = m.commands[0]
        assert len(cmd.arguments) == 1
        arg = cmd.arguments[0]

        assert arg.name == "--env"
        assert arg.short == "-e"
        assert arg.help == "Target environment"
        assert arg.type == "str"
        assert arg.required is True
        assert arg.default == "staging"
        assert arg.choices == ("staging", "production")
        assert arg.nargs == "?"
        assert arg.action == ""
        assert arg.dest == "environment"
        assert arg.metavar == "ENV"
        assert arg.hidden is True

    def test_parse_argument_defaults(self, tmp_path: Path) -> None:
        """An argument with only name gets all defaults."""
        plugin_dir = tmp_path / "defaults"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "positional"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.name == "positional"
        assert arg.short == ""
        assert arg.help == ""
        assert arg.type == "str"
        assert arg.required is False
        assert arg.default is None
        assert arg.choices == ()
        assert arg.nargs == ""
        assert arg.action == ""
        assert arg.dest == ""
        assert arg.metavar == ""
        assert arg.hidden is False

    def test_parse_multiple_arguments(self, tmp_path: Path) -> None:
        """A command with multiple arguments parses them in order."""
        plugin_dir = tmp_path / "multi"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "run"\n'
            'handler = "handle_run"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "target"\n'
            'help = "Build target"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--verbose"\n'
            'short = "-v"\n'
            'action = "store_true"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--count"\n'
            'type = "int"\n'
            'default = 1\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        cmd = m.commands[0]

        assert len(cmd.arguments) == 3
        assert cmd.arguments[0].name == "target"
        assert cmd.arguments[0].help == "Build target"
        assert cmd.arguments[1].name == "--verbose"
        assert cmd.arguments[1].short == "-v"
        assert cmd.arguments[1].action == "store_true"
        assert cmd.arguments[2].name == "--count"
        assert cmd.arguments[2].type == "int"
        assert cmd.arguments[2].default == 1

    def test_parse_argument_with_int_default(self, tmp_path: Path) -> None:
        """TOML int default is preserved as int."""
        plugin_dir = tmp_path / "int-default"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--port"\n'
            'type = "int"\n'
            'default = 8080\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.default == 8080
        assert isinstance(arg.default, int)

    def test_parse_argument_with_float_default(self, tmp_path: Path) -> None:
        """TOML float default is preserved as float."""
        plugin_dir = tmp_path / "float-default"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--rate"\n'
            'type = "float"\n'
            'default = 0.5\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.default == 0.5
        assert isinstance(arg.default, float)

    def test_parse_argument_with_bool_default(self, tmp_path: Path) -> None:
        """TOML bool default is preserved as bool."""
        plugin_dir = tmp_path / "bool-default"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--flag"\n'
            'type = "bool"\n'
            'default = false\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.default is False
        assert isinstance(arg.default, bool)

    def test_parse_argument_nargs_variants(self, tmp_path: Path) -> None:
        """Different nargs values parse correctly."""
        plugin_dir = tmp_path / "nargs"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--files"\n'
            'nargs = "+"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--extras"\n'
            'nargs = "*"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--opt"\n'
            'nargs = "?"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--rest"\n'
            'nargs = "remainder"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        args = m.commands[0].arguments

        assert args[0].nargs == "+"
        assert args[1].nargs == "*"
        assert args[2].nargs == "?"
        assert args[3].nargs == "remainder"

    def test_parse_argument_actions(self, tmp_path: Path) -> None:
        """Different action values parse correctly."""
        plugin_dir = tmp_path / "actions"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--verbose"\n'
            'action = "store_true"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--no-color"\n'
            'action = "store_false"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--include"\n'
            'action = "append"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "-v"\n'
            'action = "count"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        args = m.commands[0].arguments

        assert args[0].action == "store_true"
        assert args[1].action == "store_false"
        assert args[2].action == "append"
        assert args[3].action == "count"

    def test_parse_argument_missing_name_raises(self, tmp_path: Path) -> None:
        """An argument without a name raises ValueError."""
        plugin_dir = tmp_path / "no-name"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'help = "Missing name"\n'
        )
        _write_toml(plugin_dir, toml)

        with pytest.raises(ValueError, match="name"):
            parse_manifest(plugin_dir)


# ===========================================================================
# 4. TOML parsing -- subcommands
# ===========================================================================


class TestParseSubcommands:
    """Tests for nested subcommand parsing."""

    def test_one_level_subcommands(self, tmp_path: Path) -> None:
        """A command with one level of subcommands."""
        plugin_dir = tmp_path / "sub1"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "db"\n'
            'handler = "handle_db"\n'
            'description = "Database commands"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "migrate"\n'
            'handler = "handle_migrate"\n'
            'description = "Run migrations"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "seed"\n'
            'handler = "handle_seed"\n'
            'description = "Seed data"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        assert len(m.commands) == 1
        db = m.commands[0]
        assert db.name == "db"
        assert len(db.subcommands) == 2
        assert db.subcommands[0].name == "migrate"
        assert db.subcommands[0].handler == "handle_migrate"
        assert db.subcommands[1].name == "seed"

    def test_three_levels_deep(self, tmp_path: Path) -> None:
        """Subcommands nested 3 levels deep parse correctly."""
        plugin_dir = tmp_path / "deep"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "level1"\n'
            'handler = "h1"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "level2"\n'
            'handler = "h2"\n'
            '\n'
            '[[commands.subcommands.subcommands]]\n'
            'name = "level3"\n'
            'handler = "h3"\n'
            'description = "Deepest"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        l1 = m.commands[0]
        assert l1.name == "level1"
        assert len(l1.subcommands) == 1

        l2 = l1.subcommands[0]
        assert l2.name == "level2"
        assert len(l2.subcommands) == 1

        l3 = l2.subcommands[0]
        assert l3.name == "level3"
        assert l3.handler == "h3"
        assert l3.description == "Deepest"
        assert l3.subcommands == ()

    def test_subcommand_with_arguments(self, tmp_path: Path) -> None:
        """A subcommand can have its own arguments."""
        plugin_dir = tmp_path / "sub-args"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "remote"\n'
            'handler = "handle_remote"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "add"\n'
            'handler = "handle_remote_add"\n'
            '\n'
            '[[commands.subcommands.arguments]]\n'
            'name = "url"\n'
            'help = "Remote URL"\n'
            'required = true\n'
            '\n'
            '[[commands.subcommands.arguments]]\n'
            'name = "--name"\n'
            'short = "-n"\n'
            'default = "origin"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        add = m.commands[0].subcommands[0]

        assert add.name == "add"
        assert len(add.arguments) == 2
        assert add.arguments[0].name == "url"
        assert add.arguments[0].required is True
        assert add.arguments[1].name == "--name"
        assert add.arguments[1].short == "-n"
        assert add.arguments[1].default == "origin"

    def test_command_only_subcommands_no_args(self, tmp_path: Path) -> None:
        """A command with only subcommands and no direct arguments."""
        plugin_dir = tmp_path / "no-args"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "config"\n'
            'handler = "handle_config"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "get"\n'
            'handler = "handle_config_get"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "set"\n'
            'handler = "handle_config_set"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        config = m.commands[0]

        assert config.arguments == ()
        assert len(config.subcommands) == 2


# ===========================================================================
# 5. TOML parsing -- includes
# ===========================================================================


class TestParseIncludes:
    """Tests for the ``includes`` field on commands."""

    def test_includes_on_command(self, tmp_path: Path) -> None:
        """A command with includes parses template names."""
        plugin_dir = tmp_path / "inc"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "deploy"\n'
            'handler = "handle_deploy"\n'
            'includes = ["branch", "dry-run", "verbose"]\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        assert m.commands[0].includes == ("branch", "dry-run", "verbose")

    def test_includes_empty(self, tmp_path: Path) -> None:
        """A command without includes defaults to empty tuple."""
        plugin_dir = tmp_path / "no-inc"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        assert m.commands[0].includes == ()

    def test_includes_on_subcommand(self, tmp_path: Path) -> None:
        """Subcommands can also have includes."""
        plugin_dir = tmp_path / "sub-inc"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "parent"\n'
            'handler = "h_parent"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "child"\n'
            'handler = "h_child"\n'
            'includes = ["branch"]\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        assert m.commands[0].subcommands[0].includes == ("branch",)


# ===========================================================================
# 6. Group field removal
# ===========================================================================


class TestGroupFieldRemoval:
    """Tests that the group field no longer exists on CommandDecl."""

    def test_group_not_on_dataclass(self) -> None:
        """CommandDecl has no 'group' field."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(CommandDecl)}
        assert "group" not in field_names

    def test_group_in_toml_ignored(self, tmp_path: Path) -> None:
        """A 'group' key in the TOML command entry is silently ignored."""
        plugin_dir = tmp_path / "old-style"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            'group = "ops"\n'
        )
        _write_toml(plugin_dir, toml)

        # Should parse without error (group is just an unknown key, ignored).
        m = parse_manifest(plugin_dir)
        assert m.commands[0].name == "cmd"
        assert not hasattr(m.commands[0], "group")


# ===========================================================================
# 7. Round-trip / integration
# ===========================================================================


class TestRoundTrip:
    """Full round-trip: parse a rich TOML -> verify the entire dataclass tree."""

    def test_full_roundtrip(self, tmp_path: Path) -> None:
        """Parse a TOML with commands, arguments, subcommands, includes."""
        plugin_dir = tmp_path / "roundtrip"
        toml = (
            _header("roundtrip-plugin", "1.2.3")
            + '[[commands]]\n'
            'name = "build"\n'
            'handler = "handle_build"\n'
            'description = "Build the project"\n'
            'includes = ["verbose"]\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "target"\n'
            'help = "Build target name"\n'
            'type = "str"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--jobs"\n'
            'short = "-j"\n'
            'help = "Parallel jobs"\n'
            'type = "int"\n'
            'default = 4\n'
            'metavar = "N"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "release"\n'
            'handler = "handle_build_release"\n'
            'description = "Build for release"\n'
            '\n'
            '[[commands.subcommands.arguments]]\n'
            'name = "--sign"\n'
            'action = "store_true"\n'
            'help = "Sign the build"\n'
            '\n'
            '[[commands.subcommands.arguments]]\n'
            'name = "--profile"\n'
            'choices = ["small", "fast", "balanced"]\n'
            'default = "balanced"\n'
            '\n'
            '[[commands]]\n'
            'name = "clean"\n'
            'handler = "handle_clean"\n'
            'description = "Clean build artifacts"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        # Top-level: two commands.
        assert m.name == "roundtrip-plugin"
        assert m.version == "1.2.3"
        assert len(m.commands) == 2

        # First command: build.
        build = m.commands[0]
        assert build.name == "build"
        assert build.handler == "handle_build"
        assert build.description == "Build the project"
        assert build.includes == ("verbose",)

        # Build arguments.
        assert len(build.arguments) == 2
        target = build.arguments[0]
        assert target.name == "target"
        assert target.help == "Build target name"
        assert target.type == "str"

        jobs = build.arguments[1]
        assert jobs.name == "--jobs"
        assert jobs.short == "-j"
        assert jobs.type == "int"
        assert jobs.default == 4
        assert jobs.metavar == "N"

        # Build subcommands.
        assert len(build.subcommands) == 1
        release = build.subcommands[0]
        assert release.name == "release"
        assert release.handler == "handle_build_release"
        assert release.description == "Build for release"

        # Release arguments.
        assert len(release.arguments) == 2
        sign = release.arguments[0]
        assert sign.name == "--sign"
        assert sign.action == "store_true"
        assert sign.help == "Sign the build"

        profile = release.arguments[1]
        assert profile.name == "--profile"
        assert profile.choices == ("small", "fast", "balanced")
        assert profile.default == "balanced"

        # Second command: clean (no args, no subcommands).
        clean = m.commands[1]
        assert clean.name == "clean"
        assert clean.handler == "handle_clean"
        assert clean.arguments == ()
        assert clean.subcommands == ()
        assert clean.includes == ()


# ===========================================================================
# 8. Edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge cases for argument and subcommand parsing."""

    def test_command_no_arguments_no_subcommands(self, tmp_path: Path) -> None:
        """A command with neither arguments nor subcommands parses cleanly."""
        plugin_dir = tmp_path / "bare"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "noop"\n'
            'handler = "handle_noop"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        cmd = m.commands[0]

        assert cmd.arguments == ()
        assert cmd.subcommands == ()
        assert cmd.includes == ()

    def test_multiple_commands_with_different_structures(self, tmp_path: Path) -> None:
        """Multiple commands: one with args, one with subcommands, one bare."""
        plugin_dir = tmp_path / "mixed"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "with-args"\n'
            'handler = "h1"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--flag"\n'
            'action = "store_true"\n'
            '\n'
            '[[commands]]\n'
            'name = "with-subs"\n'
            'handler = "h2"\n'
            '\n'
            '[[commands.subcommands]]\n'
            'name = "sub"\n'
            'handler = "h3"\n'
            '\n'
            '[[commands]]\n'
            'name = "bare"\n'
            'handler = "h4"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)

        assert len(m.commands) == 3
        assert len(m.commands[0].arguments) == 1
        assert m.commands[0].subcommands == ()
        assert m.commands[1].arguments == ()
        assert len(m.commands[1].subcommands) == 1
        assert m.commands[2].arguments == ()
        assert m.commands[2].subcommands == ()

    def test_argument_hidden_suppresses(self, tmp_path: Path) -> None:
        """A hidden argument is parsed with hidden=True."""
        plugin_dir = tmp_path / "hidden"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--secret"\n'
            'hidden = true\n'
            'help = "This will be suppressed"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.hidden is True
        assert arg.help == "This will be suppressed"

    def test_positional_argument(self, tmp_path: Path) -> None:
        """A positional argument (no -- prefix) parses correctly."""
        plugin_dir = tmp_path / "positional"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "filename"\n'
            'help = "Input file"\n'
            'type = "path"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.name == "filename"
        assert not arg.name.startswith("-")
        assert arg.type == "path"


# ===========================================================================
# 9. Mutex group parsing
# ===========================================================================


class TestParseMutexGroup:
    """Tests for parsing mutex_group from TOML argument entries."""

    def test_mutex_group_parsed(self, tmp_path: Path) -> None:
        """Arguments with mutex_group parse the field correctly."""
        plugin_dir = tmp_path / "mutex"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--poll"\n'
            'action = "store_true"\n'
            'dest = "poll"\n'
            'mutex_group = "poll-mode"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--no-poll"\n'
            'action = "store_true"\n'
            'dest = "poll"\n'
            'mutex_group = "poll-mode"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        args = m.commands[0].arguments

        assert len(args) == 2
        assert args[0].mutex_group == "poll-mode"
        assert args[1].mutex_group == "poll-mode"

    def test_mutex_group_default_empty(self, tmp_path: Path) -> None:
        """An argument without mutex_group defaults to empty string."""
        plugin_dir = tmp_path / "no-mutex"
        toml = (
            _header()
            + '[[commands]]\n'
            'name = "cmd"\n'
            'handler = "h"\n'
            '\n'
            '[[commands.arguments]]\n'
            'name = "--flag"\n'
            'action = "store_true"\n'
        )
        _write_toml(plugin_dir, toml)

        m = parse_manifest(plugin_dir)
        arg = m.commands[0].arguments[0]

        assert arg.mutex_group == ""

    def test_mutex_group_on_dataclass(self) -> None:
        """ArgumentDecl accepts mutex_group as a keyword argument."""
        arg = ArgumentDecl(name="--poll", mutex_group="poll-mode")
        assert arg.mutex_group == "poll-mode"

    def test_mutex_group_default_on_dataclass(self) -> None:
        """ArgumentDecl defaults mutex_group to empty string."""
        arg = ArgumentDecl(name="--flag")
        assert arg.mutex_group == ""
