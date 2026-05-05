"""Documentation tools: v docs regenerate (generate CLI docs).

Introspects build_parser() and generates docs/cli.md documenting
all commands, subcommands, options, and arguments. Pulls richer
descriptions from cmd_* handler docstrings when available.

Also generates per-topic docs and an aggregated dev-rules.md from
``.. feature::`` and ``.. rule::`` directives embedded in handler
docstrings.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from typing import Any

from supervisor.cli import _load_commands, build_parser
from supervisor.paths import ROOT
from supervisor.plugins import registry as plugin_registry

# ---------------------------------------------------------------------------
# Directive constants
# ---------------------------------------------------------------------------

# Regex matching ``.. feature:: <text>`` or ``.. rule:: <text>`` lines.
_DIRECTIVE_RE = re.compile(r"^\.\.\s+(feature|rule)::\s+(.+)$")

# RST double-backtick inline code -> markdown single-backtick.
_RST_CODE_RE = re.compile(r"``(.+?)``")

# ---------------------------------------------------------------------------
# Topic doc configuration
# ---------------------------------------------------------------------------
# Each entry maps a topic slug to its output file, title, and the command
# paths (dotted, matching _load_commands keys) whose directives feed it.

TOPIC_DOCS: dict[str, dict[str, Any]] = {
    "tdd": {
        "title": "TDD Verification",
        "output": "docs/tdd.md",
        # Dotted command path used by _load_commands.
        "commands": ["tests.red-green"],
    },
    "staging-workflow": {
        "title": "Staging Workflow",
        "output": "docs/staging-workflow.md",
        "commands": ["deploy.to.staging"],
    },
    "local-dev": {
        "title": "Local Development",
        "output": "docs/local-dev.md",
        "commands": ["dashboard", "server", "services"],
    },
}


def _print_docs_help(_args: argparse.Namespace) -> None:
    """Print help for the docs command group."""
    parser = build_parser()
    if parser._subparsers is None:
        return
    for action in parser._subparsers._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "docs" in choices:
            choices["docs"].print_help()
            return


def _get_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser] | None:
    """Find the subparsers action in a parser, if any."""
    if parser._subparsers is None:
        return None
    for action in parser._subparsers._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _get_help_map(
    sub_action: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, str]:
    """Extract the help= text for each subparser from the parent action.

    argparse stores help= from add_parser() on _choices_actions, not on
    the subparser itself. This maps command name -> help string.
    """
    result = {}
    for choice_action in sub_action._choices_actions:
        result[choice_action.dest] = choice_action.help or ""
    return result


def _get_arguments(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """Get positional arguments (excluding subparser selectors)."""
    return [
        a
        for a in parser._actions
        if not a.option_strings
        and not isinstance(a, argparse._SubParsersAction)
        and not isinstance(a, argparse._HelpAction)
    ]


def _get_options(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """Get optional flags (excluding --help and SUPPRESS'd args)."""
    return [
        a
        for a in parser._actions
        if a.option_strings and not isinstance(a, argparse._HelpAction) and a.help != argparse.SUPPRESS
    ]


def _capitalize(s: str) -> str:
    """Capitalize first letter, preserving the rest."""
    if not s:
        return s
    return s[0].upper() + s[1:]


# ---------------------------------------------------------------------------
# Directive extraction from docstrings
# ---------------------------------------------------------------------------


def _rst_to_md(text: str) -> str:
    """Convert RST double-backtick inline code to markdown single-backtick."""
    return _RST_CODE_RE.sub(r"`\1`", text)


def _extract_directives(docstring: str) -> tuple[list[str], list[str]]:
    """Parse ``.. feature::`` and ``.. rule::`` directives from a docstring.

    Returns (features, rules) -- each a list of the directive text,
    with RST inline code converted to markdown backticks.
    """
    features: list[str] = []
    rules: list[str] = []
    for line in docstring.splitlines():
        m = _DIRECTIVE_RE.match(line.strip())
        if m:
            kind, text = m.group(1), _rst_to_md(m.group(2).strip())
            if kind == "feature":
                features.append(text)
            else:
                rules.append(text)
    return features, rules


def _strip_directives(text: str) -> str:
    """Remove ``.. feature::`` / ``.. rule::`` lines from docstring body.

    This keeps directives out of cli.md and --help output while still
    allowing them in handler docstrings for topic doc generation.
    """
    lines = [line for line in text.splitlines() if not _DIRECTIVE_RE.match(line.strip())]
    return "\n".join(lines).strip()


def _collect_all_directives(
    handler_map: dict[str, Any],
) -> dict[str, tuple[list[str], list[str]]]:
    """Walk every handler and collect directives keyed by dotted command path.

    Returns {cmd_path: (features, rules)} for commands that have at least
    one directive.
    """
    result: dict[str, tuple[list[str], list[str]]] = {}
    for cmd_path, handler in handler_map.items():
        if not handler or not handler.__doc__:
            continue
        features, rules = _extract_directives(handler.__doc__)
        if features or rules:
            result[cmd_path] = (features, rules)
    return result


def _cmd_path_to_display(cmd_path: str) -> str:
    """Convert dotted handler-map key to the user-facing command string.

    E.g. ``tests.red-green`` -> ``tests red-green``,
         ``deploy.to.staging`` -> ``deploy to staging``.
    """
    return cmd_path.replace(".", " ")


def _format_option(action: argparse.Action) -> str:
    """Format a single option for markdown."""
    opts = ", ".join(action.option_strings)

    if isinstance(action, argparse._StoreTrueAction):
        type_str = ""
    elif action.choices:
        type_str = " " + "|".join(str(c) for c in action.choices)
    elif action.metavar:
        type_str = f" {action.metavar}"
    elif action.type and action.type is not str:
        type_str = f" {getattr(action.type, '__name__', str(action.type)).upper()}"
    else:
        type_str = ""

    help_text = _capitalize(action.help or "")
    if action.default not in (None, False, argparse.SUPPRESS) and not isinstance(action, argparse._StoreTrueAction):
        help_text += f" (default: {action.default})"

    return f"  - `{opts}{type_str}`: {help_text}"


def _format_argument(action: argparse.Action) -> str:
    """Format a positional argument for markdown."""
    raw_name = action.metavar or action.dest
    name = (raw_name if isinstance(raw_name, str) else " ".join(raw_name)).upper()
    optional = " (optional)" if action.nargs in ("?", "*") else ""
    help_text = _capitalize(action.help or "")
    sep = ": " if help_text else ""
    return f"  - `{name}`{optional}{sep}{help_text}"


def _cmd_to_anchor(full_path: str) -> str:
    """Convert command path to markdown anchor."""
    return full_path.replace(" ", "-").lower()


def _is_trivial(parser: argparse.ArgumentParser) -> bool:
    """Return True if this is a leaf subcommand with no arguments and no options."""
    if _get_subparsers_action(parser):
        return False
    return not _get_arguments(parser) and not _get_options(parser)


def _build_toc(
    parser: argparse.ArgumentParser,
    parent_path: str = "",
    plugin_names: set[str] | None = None,
) -> list[str]:
    """Build table of contents entries recursively.

    Trivial nested subcommands (no args, no options, no children) are
    skipped from the TOC since they appear as bullet items under their
    parent. Top-level commands always get a TOC entry.

    *plugin_names* is the set of top-level command names contributed by
    plugins.  When a top-level command matches, a ``[plugin]`` marker is
    appended to its TOC line.
    """
    entries: list[str] = []
    sub_action = _get_subparsers_action(parser)
    if not sub_action:
        return entries

    _plugin_names = plugin_names or set()
    help_map = _get_help_map(sub_action)

    for name, sub_parser in sub_action.choices.items():
        full_path = f"{parent_path} {name}" if parent_path else name
        # Only collapse trivial subcommands (not top-level commands).
        if parent_path and _is_trivial(sub_parser):
            continue
        depth = full_path.count(" ")
        indent = "  " * depth
        # Anchor must match the heading text (including the marker).
        is_plugin = not parent_path and name in _plugin_names
        anchor = _cmd_to_anchor(f"v {full_path}" + (" plugin" if is_plugin else ""))
        short = _capitalize(help_map.get(name, ""))
        desc = f" -- {short}" if short else ""
        marker = " [plugin]" if is_plugin else ""
        entries.append(f"{indent}- [`v {full_path}`](#{anchor}){marker}{desc}")
        entries.extend(_build_toc(sub_parser, full_path, plugin_names=_plugin_names))

    return entries


def _get_docstring(cmd_name: str, handler_map: dict[str, Any]) -> str:
    """Get the docstring from the cmd_* handler, cleaned up.

    Returns the docstring body (after the first line summary) if it
    adds information beyond the help= kwarg. Returns empty string if
    the docstring just repeats the help text or doesn't exist.

    Directive lines (``.. feature::`` / ``.. rule::``) are stripped so
    they don't leak into cli.md or --help output.
    """
    handler = handler_map.get(cmd_name)
    if not handler or not handler.__doc__:
        return ""

    doc = handler.__doc__.strip()
    lines = doc.split("\n")

    # First line is the summary (usually matches help= kwarg).
    # If there are additional lines, those are the useful body.
    if len(lines) <= 1:
        return ""

    # Return everything after the first line, dedented, with directives removed.
    body = "\n".join(lines[1:]).strip()
    return _strip_directives(body)


def _document_command(
    name: str,
    parser: argparse.ArgumentParser,
    parent_path: str = "",
    level: int = 2,
    help_text: str = "",
    handler_map: dict[str, Any] | None = None,
    plugin_names: set[str] | None = None,
) -> list[str]:
    """Document a single command and its subcommands recursively."""
    lines = []
    full_path = f"{parent_path} {name}" if parent_path else name
    heading = "#" * min(level, 6)
    # Mark top-level plugin commands with a [plugin] tag.
    is_plugin = not parent_path and plugin_names and name in plugin_names
    marker = " [plugin]" if is_plugin else ""
    lines.append(f"{heading} `v {full_path}`{marker}")
    lines.append("")

    # Help text from add_parser() help= kwarg.
    if help_text:
        lines.append(_capitalize(help_text))
        lines.append("")

    # Richer description from the cmd_* handler docstring.
    if handler_map:
        docstring_body = _get_docstring(name, handler_map)
        if docstring_body:
            lines.append(docstring_body)
            lines.append("")

    # Positional arguments.
    arguments = _get_arguments(parser)
    if arguments:
        lines.append("**Arguments:**")
        lines.append("")
        lines.extend(_format_argument(a) for a in arguments)
        lines.append("")

    # Options.
    options = _get_options(parser)
    if options:
        lines.append("**Options:**")
        lines.append("")
        lines.extend(_format_option(o) for o in options)
        lines.append("")

    # Recurse into subcommands, collapsing trivial nested ones into bullets.
    sub_action = _get_subparsers_action(parser)
    if sub_action:
        child_help_map = _get_help_map(sub_action)
        trivial = []
        nontrivial = []
        for sub_name, sub_parser in sub_action.choices.items():
            # Only collapse trivial subcommands (not top-level commands).
            if full_path and _is_trivial(sub_parser):
                trivial.append((sub_name, sub_parser))
            else:
                nontrivial.append((sub_name, sub_parser))

        if trivial:
            for sub_name, _sub_parser in trivial:
                sub_full = f"{full_path} {sub_name}"
                h = _capitalize(child_help_map.get(sub_name, ""))
                sep = " -- " if h else ""
                lines.append(f"- **`v {sub_full}`**{sep}{h}")
            lines.append("")

        for sub_name, sub_parser in nontrivial:
            lines.extend(
                _document_command(
                    sub_name,
                    sub_parser,
                    full_path,
                    level + 1,
                    help_text=child_help_map.get(sub_name, ""),
                )
            )

    return lines


def _generate_docs() -> str:
    """Generate full CLI documentation."""
    parser = build_parser()
    handler_map = _load_commands()

    # Collect plugin names so plugin-contributed commands can be annotated.
    # build_parser() already called load_all_plugins(), so the registry is
    # populated by this point.  Gracefully degrade to an empty set if no
    # plugins are loaded.
    _plugin_names: set[str] = {p.name for p in plugin_registry.list_plugins()}

    lines = [
        "# v CLI Reference",
        "",
        "Auto-generated from `build_parser()` in `src/supervisor/cli.py`. Do not edit manually.",
        "",
        f"*Generated: {datetime.now(tz=UTC).strftime('%Y-%m-%d')}*",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ]

    # Build TOC from top-level subcommands.
    lines.extend(_build_toc(parser, plugin_names=_plugin_names))
    lines.append("")
    lines.append("---")
    lines.append("")

    # Document each top-level command.
    sub_action = _get_subparsers_action(parser)
    if sub_action:
        help_map = _get_help_map(sub_action)
        for name, sub_parser in sub_action.choices.items():
            lines.extend(
                _document_command(
                    name,
                    sub_parser,
                    help_text=help_map.get(name, ""),
                    handler_map=handler_map,
                    plugin_names=_plugin_names,
                )
            )

    # Trailing newline.
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Topic doc generation
# ---------------------------------------------------------------------------


def _find_parser_for_cmd(root_parser: argparse.ArgumentParser, dotted_path: str) -> argparse.ArgumentParser | None:
    """Walk the parser tree to find the subparser for a dotted command path.

    E.g. ``deploy.to.staging`` navigates root -> deploy -> to -> staging.
    Returns None if any segment is missing.
    """
    parts = dotted_path.split(".")
    parser = root_parser
    for part in parts:
        sub_action = _get_subparsers_action(parser)
        if not sub_action or part not in sub_action.choices:
            return None
        parser = sub_action.choices[part]
    return parser


def _generate_topic_doc(
    topic: dict[str, Any],
    handler_map: dict[str, Any],
    root_parser: argparse.ArgumentParser,
) -> str:
    """Generate a single per-topic documentation file.

    Collects features, rules, and CLI reference from the commands
    listed in the topic config.
    """
    all_features: list[str] = []
    all_rules: list[str] = []
    cli_sections: list[str] = []

    for cmd_path in topic["commands"]:
        handler = handler_map.get(cmd_path)
        if handler and handler.__doc__:
            features, rules = _extract_directives(handler.__doc__)
            all_features.extend(features)
            all_rules.extend(rules)

        # Build CLI reference section for this command.
        sub_parser = _find_parser_for_cmd(root_parser, cmd_path)
        if sub_parser:
            display_path = _cmd_path_to_display(cmd_path)
            cli_sections.extend(
                _document_command(
                    display_path.split()[-1],
                    sub_parser,
                    parent_path=" ".join(display_path.split()[:-1]),
                    level=3,
                    handler_map=handler_map,
                )
            )

    lines = [
        "<!-- Auto-generated by v docs regenerate. Do not edit. -->",
        "",
        f"# {topic['title']}",
        "",
    ]

    if all_features:
        lines.append("## Features")
        lines.append("")
        lines.extend(f"- {f}" for f in all_features)
        lines.append("")

    if all_rules:
        lines.append("## Rules")
        lines.append("")
        lines.extend(f"- {r}" for r in all_rules)
        lines.append("")

    if cli_sections:
        lines.append("## CLI Reference")
        lines.append("")
        lines.extend(cli_sections)

    return "\n".join(lines) + "\n"


def _generate_dev_rules(handler_map: dict[str, Any]) -> str:
    """Generate aggregated dev-rules.md from all ``.. rule::`` directives.

    Groups rules by their command path, in the order they appear in
    the handler map.
    """
    all_directives = _collect_all_directives(handler_map)

    lines = [
        "<!-- Auto-generated by v docs regenerate. Do not edit. -->",
        "",
        "# Development Rules",
        "",
    ]

    for cmd_path, (_, rules) in all_directives.items():
        if not rules:
            continue
        display = _cmd_path_to_display(cmd_path)
        lines.append(f"## {display}")
        lines.append("")
        lines.extend(f"- {r}" for r in rules)
        lines.append("")

    return "\n".join(lines) + "\n"


def cmd_gendocs(args: argparse.Namespace) -> None:
    """Generate docs/cli.md, per-topic docs, and dev-rules.md, then commit."""
    import subprocess

    parser = build_parser()
    handler_map = _load_commands()

    # Track all generated files for the commit.
    generated: list[str] = []

    # 1. cli.md (existing behavior).
    docs = _generate_docs()
    cli_path = ROOT / "docs" / "cli.md"
    cli_path.write_text(docs)
    generated.append(str(cli_path))
    print(f"Written to {cli_path}")

    # 2. Per-topic docs.
    for slug, topic_cfg in TOPIC_DOCS.items():
        content = _generate_topic_doc(topic_cfg, handler_map, parser)
        out_path = ROOT / topic_cfg["output"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content)
        generated.append(str(out_path))
        print(f"Written to {out_path} ({slug})")

    # 3. Aggregated dev-rules.md.
    dev_rules = _generate_dev_rules(handler_map)
    dev_rules_path = ROOT / "docs" / "dev-rules.md"
    dev_rules_path.write_text(dev_rules)
    generated.append(str(dev_rules_path))
    print(f"Written to {dev_rules_path}")

    # Auto-commit only files that actually changed.
    changed: list[str] = []
    for path in generated:
        result = subprocess.run(
            ["git", "diff", "--quiet", path],
            cwd=ROOT,
        )
        if result.returncode != 0:
            changed.append(path)
        else:
            # Also check for untracked (new) files.
            status = subprocess.run(
                ["git", "status", "--porcelain", path],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if status.stdout.strip().startswith("??"):
                changed.append(path)

    if changed:
        # Stage any new untracked files individually.
        for path in changed:
            status = subprocess.run(
                ["git", "status", "--porcelain", path],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if status.stdout.strip().startswith("??"):
                subprocess.run(["git", "add", path], cwd=ROOT, check=True)

        subprocess.run(
            ["git", "commit", *changed, "-m", "Regenerate docs (cli.md, topic docs, dev-rules.md)"],
            cwd=ROOT,
            check=True,
        )
        print(f"Committed {len(changed)} file(s).")
    else:
        print("No changes.")
