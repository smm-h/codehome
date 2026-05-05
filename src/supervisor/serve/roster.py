"""Team roster loader: reads team.json and provides fast identity lookup.

Builds in-memory lookup dicts on first access for resolving team members
by handle, email, git committer name, or GitHub username.

Usage::

    from supervisor.serve.roster import resolve, by_handle, all_members

    member = resolve("m.hosseini@veliu.com")
    if member:
        print(member.handle, member.name)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from supervisor.paths import ROOT
from supervisor.serve.logging_config import get_logger

logger = get_logger(component="roster")

TEAM_FILE = ROOT / "team.json"


@dataclass
class TeamMember:
    handle: str
    name: str
    emails: list[str] = field(default_factory=list)
    git_names: list[str] = field(default_factory=list)
    github: list[str] = field(default_factory=list)
    linear: list[str] = field(default_factory=list)
    slack: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal state -- populated lazily on first access
# ---------------------------------------------------------------------------

_members: list[TeamMember] = []
_by_handle: dict[str, TeamMember] = {}
_by_email: dict[str, TeamMember] = {}
_by_git_name: dict[str, TeamMember] = {}
_by_github: dict[str, TeamMember] = {}
_loaded: bool = False


def _parse_member(raw: dict[str, Any]) -> TeamMember:
    """Build a TeamMember from a raw JSON dict, tolerating missing keys."""
    return TeamMember(
        handle=str(raw.get("handle", "")),
        name=str(raw.get("name", "")),
        emails=list(raw.get("emails", [])),
        git_names=list(raw.get("git_names", [])),
        github=list(raw.get("github", [])),
        linear=list(raw.get("linear", [])),
        slack=list(raw.get("slack", [])),
        repos=list(raw.get("repos", [])),
    )


def _ensure_loaded() -> None:
    """Load team.json if not already loaded. Safe to call repeatedly."""
    if _loaded:
        return
    _load()


def _load() -> None:
    """Read team.json from disk and rebuild all lookup dicts."""
    global _members, _by_handle, _by_email, _by_git_name, _by_github, _loaded

    _members = []
    _by_handle = {}
    _by_email = {}
    _by_git_name = {}
    _by_github = {}
    _loaded = True

    if not TEAM_FILE.is_file():
        logger.warning("team_file_missing", path=str(TEAM_FILE))
        return

    try:
        raw_list = json.loads(TEAM_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("team_file_read_error", path=str(TEAM_FILE), error=str(exc))
        return

    if not isinstance(raw_list, list):
        logger.warning("team_file_bad_shape", path=str(TEAM_FILE))
        return

    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        member = _parse_member(raw)
        _members.append(member)

        if member.handle:
            _by_handle[member.handle] = member

        # Case-insensitive keys for email and git_name lookups
        for email in member.emails:
            _by_email[email.lower()] = member

        for gn in member.git_names:
            _by_git_name[gn.lower()] = member

        for gh in member.github:
            _by_github[gh] = member

    logger.info("roster_loaded", count=len(_members))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(identifier: str) -> TeamMember | None:
    """Resolve any identifier (handle, email, git name, github) to a TeamMember.

    Tries each lookup dict in order: handle, email, git_name, github.
    """
    _ensure_loaded()
    result = _by_handle.get(identifier)
    if result:
        return result
    result = _by_email.get(identifier.lower())
    if result:
        return result
    result = _by_git_name.get(identifier.lower())
    if result:
        return result
    return _by_github.get(identifier)


def by_handle(handle: str) -> TeamMember | None:
    """Look up by handle."""
    _ensure_loaded()
    return _by_handle.get(handle)


def by_email(email: str) -> TeamMember | None:  # noqa: dead-code
    """Look up by email address (case-insensitive)."""
    _ensure_loaded()
    return _by_email.get(email.lower())


def by_git_name(name: str) -> TeamMember | None:  # noqa: dead-code
    """Look up by git committer name (case-insensitive)."""
    _ensure_loaded()
    return _by_git_name.get(name.lower())


def all_members() -> list[TeamMember]:
    """Return all team members."""
    _ensure_loaded()
    return list(_members)


def members_for_repo(repo: str) -> list[TeamMember]:  # noqa: dead-code
    """Return members who work on a specific repo."""
    _ensure_loaded()
    return [m for m in _members if repo in m.repos]


def reload() -> None:
    """Re-read team.json from disk (for when the file is edited)."""
    global _loaded
    _loaded = False
    _load()
