from __future__ import annotations

from functools import lru_cache
from pathlib import Path

CONTEXT_DIR = Path(__file__).parent / "context"

# Fixed ordering so the prompt is assembled in a logical sequence.
_SECTION_ORDER = [
    "identity.md",
    "autonomy.md",
    "workflow.md",
    "roles.md",
    "patterns.md",
    "architecture.md",
]


@lru_cache(maxsize=1)
def _read_context_files() -> str:
    parts: list[str] = []
    for filename in _SECTION_ORDER:
        path = CONTEXT_DIR / filename
        if path.exists():
            parts.append(path.read_text().strip())
    return "\n\n---\n\n".join(parts)


def _build_team_section() -> str:
    """Generate a Team section from team.json for agent identity awareness.

    Returns an empty string if the roster is unavailable or empty, so the
    caller can skip it without special-casing.
    """
    # Import lazily to avoid circular imports and keep the module lightweight
    # when the roster is not needed.
    from codehome.serve.roster import TeamMember, all_members

    members: list[TeamMember] = all_members()
    if not members:
        return ""

    lines = [
        "## Team",
        "",
        "The following people work on this codebase. Use their names (not raw git emails) when referring to them.",
        "",
        "| Handle | Name | GitHub | Repos |",
        "|--------|------|--------|-------|",
    ]

    for m in members:
        github = ", ".join(m.github) if m.github else ""
        repos = ", ".join(m.repos) if m.repos else ""
        lines.append(f"| {m.handle} | {m.name} | {github} | {repos} |")

    # Email alias mapping so agents can resolve git commit authors.
    aliases: list[str] = []
    for m in members:
        aliases.extend(f"- {email} -> {m.name} ({m.handle})" for email in m.emails)

    if aliases:
        lines.append("")
        lines.append("Email aliases (for resolving git commits):")
        lines.extend(aliases)

    return "\n".join(lines)


def build_conductor_prompt(autonomy_level: int = 2, extra_context: str = "") -> str:
    """Assemble the Conductor's system prompt from context markdown files."""
    sections = [f"AUTONOMY_LEVEL: {autonomy_level}", _read_context_files()]

    team_section = _build_team_section()
    if team_section:
        sections.append(team_section)

    if extra_context:
        sections.append(extra_context)
    return "\n\n".join(sections)
