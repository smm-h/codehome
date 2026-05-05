"""Per-repo alias management and worktree validation."""

import sys
from pathlib import Path

from codehome.paths import repo_aliases_file, worktree_path
from codehome.utils import die, dim


def load_aliases(repo: str) -> dict[str, str]:
    """Parse repos/<repo>/aliases.txt, return {old: new} dict."""
    path = repo_aliases_file(repo)
    if not path.exists():
        return {}
    aliases = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#")[0].strip()
        if not line:
            continue
        if "=" not in line:
            continue
        old, new = line.split("=", 1)
        old, new = old.strip(), new.strip()
        if old and new:
            aliases[old] = new
    return aliases


def save_aliases(repo: str, aliases: dict[str, str]) -> None:
    """Write alias dict back to repos/<repo>/aliases.txt."""
    path = repo_aliases_file(repo)
    lines = [f"{old}={new}" for old, new in sorted(aliases.items())]
    path.write_text("\n".join(lines) + "\n" if lines else "")


def reverse_aliases(repo: str) -> dict[str, list[str]]:
    """Build {branch: [alias1, alias2, ...]} from aliases."""
    aliases = load_aliases(repo)
    rev: dict[str, list[str]] = {}
    for old, new in aliases.items():
        rev.setdefault(new, []).append(old)
    return rev


def resolve_alias(repo: str, name: str) -> str:
    """Follow alias chain transitively (A->B->C). Print hint to stderr."""
    aliases = load_aliases(repo)
    if name not in aliases:
        return name
    original = name
    seen = {name}
    while name in aliases:
        name = aliases[name]
        if name in seen:
            break  # cycle guard
        seen.add(name)
    sys.stderr.write(f"  {dim(f'({original} -> {name})')}\n")
    return name


def require_worktree(repo: str, name: str) -> Path:
    """Validate that a worktree exists and return its path."""
    wt = worktree_path(repo, name)
    if not wt.exists():
        die(f"worktree '{repo}:{name}' does not exist")
    if not (wt / ".git").exists():
        die(f"'{repo}:{name}' exists but is not a git worktree")
    return wt
