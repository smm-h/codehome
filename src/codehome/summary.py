"""Diff engine: section renderers, numstat parsing, branch summaries."""

import fnmatch
import re
from pathlib import Path
from typing import Any

from codehome.git import exclude_args, git, pipe_through_delta
from codehome.linear_shared import load_issue_link
from codehome.paths import (
    NOISE_PATTERNS,
    ROOT,
    area_map,
    base_ref,
    branch_dir,
    docs_path,
    tests_file,
    worktree_path,
)
from codehome.utils import (
    colorize,
    dim,
    load_json,
    render_box_table,
    render_file_tree,
    render_unified_file_table,
)

# ---------------------------------------------------------------------------
# Pure data helpers
# ---------------------------------------------------------------------------


def classify_path(path: str, repo: str) -> str:
    """Map a file path to an area label. First match wins."""
    for prefix, label in area_map(repo):
        if path.startswith(prefix):
            return label
    return "Other"


def partition_files_by_area(
    file_entries: list[tuple[str, str]],
    repo: str,
) -> dict[str, list[tuple[str, str]]]:
    """Group (path, suffix) tuples by area label."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for path, suffix in file_entries:
        area = classify_path(path, repo)
        groups.setdefault(area, []).append((path, suffix))
    return groups


def parse_numstat(wt: Path, excl: list[str], repo: str, branch: str) -> tuple[list[tuple[str, str]], int, int]:
    """Parse git numstat for base_ref...HEAD.

    Returns (file_entries, total_added, total_removed) where file_entries
    is a list of (path, " +N -M") tuples.
    """
    raw = git(wt, "diff", f"{base_ref(repo, branch)}...HEAD", "--numstat", "--", ".", *excl)
    file_entries: list[tuple[str, str]] = []
    total_added = 0
    total_removed = 0
    for line in raw.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            is_binary = parts[0] == "-" and parts[1] == "-"
            a = int(parts[0]) if parts[0] != "-" else 0
            r = int(parts[1]) if parts[1] != "-" else 0
            total_added += a
            total_removed += r
            if is_binary:
                file_entries.append((parts[2], " (binary)"))
            else:
                a_str = f"+{a}" if a else ""
                r_str = f"-{r}" if r else ""
                file_entries.append((parts[2], f" {a_str} {r_str}".rstrip()))
    return file_entries, total_added, total_removed


def parse_commits(wt: Path, repo: str, branch: str, *, recent_first: bool = False) -> list[dict[str, Any]]:
    """Parse commit log for base_ref..HEAD.

    Returns list of dicts with keys: hash, message, date, files, added, removed.
    Chronological (oldest first) by default; newest first when recent_first=True.
    """
    order_args = [] if recent_first else ["--reverse"]
    raw = git(wt, "log", f"{base_ref(repo, branch)}..HEAD", *order_args, "--format=%x00%h%x09%s%x09%ar", "--shortstat")
    commits = []
    for chunk in raw.split("\0"):
        chunk = chunk.strip()
        if not chunk:
            continue
        chunk_lines = [l for l in chunk.splitlines() if l.strip()]
        if not chunk_lines:
            continue
        header_parts = chunk_lines[0].split("\t", 2)
        if len(header_parts) < 3:
            continue
        c_hash, c_msg, c_date = header_parts
        c_files, c_added, c_removed = 0, 0, 0
        if len(chunk_lines) > 1:
            stat_line = chunk_lines[1]
            m_files = re.search(r"(\d+) file", stat_line)
            m_ins = re.search(r"(\d+) insertion", stat_line)
            m_del = re.search(r"(\d+) deletion", stat_line)
            if m_files:
                c_files = int(m_files.group(1))
            if m_ins:
                c_added = int(m_ins.group(1))
            if m_del:
                c_removed = int(m_del.group(1))
        commits.append(
            {
                "hash": c_hash,
                "message": c_msg,
                "date": c_date,
                "files": c_files,
                "added": c_added,
                "removed": c_removed,
            }
        )
    return commits


def _area_sort_key(area: str, repo: str) -> int:
    """Sort key for area labels: area map order, Other last."""
    area_order = [label for _, label in area_map(repo)]
    if area in area_order:
        return area_order.index(area)
    return len(area_order)


def _area_stats(entries: list[tuple[str, str]]) -> tuple[int, int]:
    """Sum added/removed from file entry suffixes."""
    added = 0
    removed = 0
    for _, suffix in entries:
        m_add = re.search(r"\+(\d+)", suffix)
        m_rem = re.search(r"-(\d+)", suffix)
        if m_add:
            added += int(m_add.group(1))
        if m_rem:
            removed += int(m_rem.group(1))
    return added, removed


# ---------------------------------------------------------------------------
# Section generators (each returns a markdown string)
# ---------------------------------------------------------------------------


def _read_issue_identifier(repo: str, branch: str) -> str | None:
    """Read the Linear issue identifier from issue.json, if it exists."""
    link = load_issue_link(repo, branch)
    return link.get("identifier") if link else None


def _push_status(wt: Path, branch: str) -> str | None:
    """Return '(N unpushed)' if branch has unpushed commits, else None."""
    remote_ref = git(wt, "rev-parse", "--verify", f"origin/{branch}", check=False)
    if not remote_ref:
        return "not pushed"
    unpushed = int(git(wt, "rev-list", f"origin/{branch}..HEAD", "--count"))
    if unpushed:
        return f"{unpushed} unpushed"
    return None


def _plural(n: int, word: str) -> str:
    """Return 'N word' or 'N words'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _fmt_lines(added: int, removed: int) -> str:
    """Format added/removed counts, omitting zeros."""
    parts = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"-{removed}")
    return " ".join(parts) if parts else "0"


def _annotate_branch_file(p: Path, rel: str) -> str:
    """Generate annotation suffix for a branch metadata file."""
    if rel == "issue.json":
        data = load_json(p)
        if data:
            ident = data.get("identifier")
            return f" ({ident})" if ident else ""
    elif rel == "review.md":
        try:
            content = p.read_text()
            if not content.strip():
                return " (empty)"
            count = sum(1 for l in content.splitlines() if l.startswith("## "))
            return f" ({_plural(count, 'comment')})" if count else ""
        except OSError:
            pass
    elif rel == "prs.json":
        data = load_json(p)
        if data:
            bases = [pr.get("base", "?") for pr in data]
            return f" ({', '.join(bases)})"
    elif rel == "tests.json":
        data = load_json(p)
        if data:
            tests = data.get("tests", [])
            if tests:
                skipped = sum(1 for t in tests if t.get("skip"))
                skip_info = f", {skipped} skipped" if skipped else ""
                return f" ({_plural(len(tests), 'test')}{skip_info})"
    return ""


def _branch_metadata_tree(repo: str, branch: str) -> str:
    """Render branch directory contents as a tree, excluding worktree/."""
    bd = branch_dir(repo, branch)
    if not bd.is_dir():
        return ""

    entries: list[tuple[str, str]] = []
    for p in sorted(bd.rglob("*")):
        if p.is_dir():
            continue
        rel = str(p.relative_to(bd))
        if rel.startswith(("worktree/", "worktree\\", ".git/", ".git\\")):
            continue
        if rel == ".gitignore":
            continue
        if p.name == ".gitkeep":
            continue
        suffix = _annotate_branch_file(p, rel)
        entries.append((rel, suffix))

    if not entries:
        return ""
    return render_file_tree(entries)


def section_header(
    repo: str,
    branch: str,
    wt: Path,
    staleness: str,
    file_entries: list[tuple[str, str]],
    total_added: int,
    total_removed: int,
    wt_uncommitted: tuple[int, int, int, int] | None = None,
) -> str:
    """Render the branch header with stats and metadata."""
    # Key-value summary.
    issue_id = _read_issue_identifier(repo, branch)
    lines = [f"- BRANCH   {branch}"]

    if issue_id:
        lines.append(f"- ISSUE    {issue_id}")

    rel_path = str(worktree_path(repo, branch).relative_to(ROOT))
    lines.append(f"- PATH     {rel_path}")

    stale_suffix = f"  {staleness}" if staleness else ""
    if stale_suffix:
        lines.append(f"- STALE   {stale_suffix.strip()}")

    # Uncommitted files warning (if any).
    if wt_uncommitted:
        mod, uc_add, uc_rm, untracked = wt_uncommitted
        uc_total = mod + untracked
        if uc_total:
            parts = []
            if mod:
                parts.append(f"{mod} modified")
            if untracked:
                parts.append(f"{untracked} new")
            lines.append(f"- DIRTY    {uc_total} uncommitted: {', '.join(parts)}")

    # Branch directory tree (skip worktree/).
    tree = _branch_metadata_tree(repo, branch)
    if tree:
        lines.append("")
        lines.append(tree)

    lines.append("")
    return "\n".join(lines)


def section_header_diff(
    repo: str,
    branch: str,
    wt: Path,
    staleness: str,
    file_entries: list[tuple[str, str]],
    total_added: int,
    total_removed: int,
    wt_uncommitted: tuple[int, int, int, int] | None = None,
) -> str:
    """Render the branch header for `v git diff`: key-value summary only, no context tree.

    Mirrors section_header() but omits the branch metadata tree so the diff
    output stays focused on code changes rather than context-level files.
    """
    # Key-value summary.
    issue_id = _read_issue_identifier(repo, branch)
    lines = [f"- BRANCH   {branch}"]

    if issue_id:
        lines.append(f"- ISSUE    {issue_id}")

    rel_path = str(worktree_path(repo, branch).relative_to(ROOT))
    lines.append(f"- PATH     {rel_path}")

    stale_suffix = f"  {staleness}" if staleness else ""
    if stale_suffix:
        lines.append(f"- STALE   {stale_suffix.strip()}")

    # Uncommitted files warning (if any).
    if wt_uncommitted:
        mod, uc_add, uc_rm, untracked = wt_uncommitted
        uc_total = mod + untracked
        if uc_total:
            parts = []
            if mod:
                parts.append(f"{mod} modified")
            if untracked:
                parts.append(f"{untracked} new")
            lines.append(f"- DIRTY    {uc_total} uncommitted: {', '.join(parts)}")

    lines.append("")
    return "\n".join(lines)


def section_commits_chronological(commits: list[dict[str, Any]]) -> str:
    """Render commits as a markdown table in chronological order."""
    lines = []
    if commits:
        rows = []
        for c in commits:
            rows.append(
                [
                    c["hash"],
                    c["message"],
                    c["date"],
                    c["files"],
                    _fmt_lines(c["added"], c["removed"]),
                ]
            )
        str_rows = [[str(c) for c in r] for r in rows]
        lines.append(
            render_box_table(
                ["Hash", "Message", "Date", "Files", "Lines"],
                str_rows,
                aligns=["l", "l", "l", "r", "r"],
            )
        )
    else:
        lines.append("No commits ahead of production.")
    lines.append("")
    return "\n".join(lines)


def _build_table_groups(
    areas: dict[str, list[tuple[str, str]]],
    repo: str,
    statuses: dict[str, str] | None = None,
    uncommitted: dict[str, str] | None = None,
) -> list[tuple[str, str, str, list[tuple[str, str, str]]]]:
    """Build unified table groups from area-partitioned file entries.

    Returns [(area_label, area_lines, area_uc, [(display_path, lines, uc)])].
    The uc (uncommitted) field is "" when there's no uncommitted data.
    """
    has_uc = bool(uncommitted)
    groups = []
    for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
        entries = areas[area]
        n_files = len(entries)
        area_added, area_removed = _area_stats(entries)
        f_word = "file" if n_files == 1 else "files"
        area_label = f"{area} ({n_files} {f_word})"
        area_lines = _fmt_lines(area_added, area_removed)
        area_prefix = next((p for p, l in area_map(repo) if l == area), "")

        # Aggregate uncommitted stats for area header.
        area_uc = ""
        if has_uc:
            uc_added = uc_removed = 0
            for path, _ in entries:
                uc_str = (uncommitted or {}).get(path, "")
                if uc_str and uc_str != "(binary)":
                    for part in uc_str.replace("+", " +").replace("-", " -").split():
                        if part.startswith("+"):
                            uc_added += int(part[1:] or "0")
                        elif part.startswith("-"):
                            uc_removed += int(part[1:] or "0")
            if uc_added or uc_removed:
                area_uc = _fmt_lines(uc_added, uc_removed)

        rows: list[tuple[str, str, str]] = []
        for path, suffix in entries:
            display = path[len(area_prefix) :] if area_prefix and path.startswith(area_prefix) else path
            lines_str = suffix.strip()
            uc_str = ""
            if has_uc:
                raw = (uncommitted or {}).get(path, "")
                if raw and raw != "(binary)":
                    uc_str = raw
            if statuses is not None:
                status_code = statuses.get(path, "M")
                char = STATUS_CHARS.get(status_code, status_code)
                prefix = dim(char) if char in ("+", "-") else char
                display = f"{prefix} {display}"
            rows.append((display, lines_str, uc_str))
        groups.append((area_label, area_lines, area_uc, rows))
    return groups


def section_files_topical(file_entries: list[tuple[str, str]], repo: str, tree: bool = False) -> str:
    """Render files grouped by area. Unified table by default, tree with --tree."""
    areas = partition_files_by_area(file_entries, repo)
    if not areas:
        return "(no changes)\n"

    if tree:
        lines = []
        for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
            entries = areas[area]
            n_files = len(entries)
            area_added, area_removed = _area_stats(entries)
            f_word = "file" if n_files == 1 else "files"
            lines.append(f"### {area} ({n_files} {f_word}, {_fmt_lines(area_added, area_removed)})")
            lines.append("")
            lines.append(render_file_tree(entries))
            lines.append("")
        return "\n".join(lines)

    groups = _build_table_groups(areas, repo)
    return "\n".join([render_unified_file_table(groups), ""])


STATUS_CHARS = {"A": "+", "M": "m", "D": "-", "R": "r", "C": "m", "U": "u"}
STATUS_LABELS = {
    "A": "new",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
}


def _parse_file_statuses(wt: Path, excl: list[str], repo: str, branch: str) -> dict[str, str]:
    """Parse git diff --name-status to get per-file status (A/M/D/R/etc)."""
    raw = git(wt, "diff", f"{base_ref(repo, branch)}...HEAD", "--name-status", "--", ".", *excl)
    statuses: dict[str, str] = {}
    for line in raw.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            status_code = parts[0][0]
            path = parts[2] if len(parts) == 3 else parts[1]
            statuses[path] = status_code
    return statuses


def section_files_annotated(
    file_entries: list[tuple[str, str]],
    statuses: dict[str, str],
    repo: str,
    tree: bool = False,
    uncommitted: dict[str, str] | None = None,
) -> str:
    """Render files grouped by area with status annotations (new/modified/deleted).

    uncommitted: optional {path: lines_str} for uncommitted changes per file.
    When present, a separate "Uncommitted" column appears in the table.
    """
    areas = partition_files_by_area(file_entries, repo)
    if not areas:
        return "(no changes)\n"

    if tree:
        lines = []
        for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
            entries = areas[area]
            n_files = len(entries)
            area_added, area_removed = _area_stats(entries)
            f_word = "file" if n_files == 1 else "files"
            lines.append(f"### {area} ({n_files} {f_word}, {_fmt_lines(area_added, area_removed)})")
            lines.append("")
            annotated = []
            for path, suffix in entries:
                status_code = statuses.get(path, "M")
                label = STATUS_LABELS.get(status_code, status_code)
                if suffix.strip() == "(binary)":
                    annotated.append((path, f"  ({label}, binary)"))
                else:
                    annotated.append((path, f"{suffix}  ({label})"))
            lines.append(render_file_tree(annotated))
            lines.append("")
        return "\n".join(lines)

    groups = _build_table_groups(areas, repo, statuses, uncommitted)
    return "\n".join([render_unified_file_table(groups), ""])


def _is_noise(path: str) -> bool:
    """True if path matches any NOISE_PATTERNS entry."""
    return any(fnmatch.fnmatch(path, p) for p in NOISE_PATTERNS)


def _untracked_files(wt: Path) -> list[str]:
    """List untracked files in the worktree, excluding noise patterns."""
    raw = git(wt, "ls-files", "--others", "--exclude-standard")
    if not raw.strip():
        return []
    return [f for f in raw.strip().splitlines() if not _is_noise(f)]


def _count_lines(wt: Path, path: str) -> int:
    """Count lines in a file. Returns 0 for binary/missing."""
    try:
        return len((wt / path).read_text().splitlines())
    except (OSError, UnicodeDecodeError):
        return 0


def _uncommitted_file_data(wt: Path) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Get uncommitted file entries and statuses (tracked modifications + untracked).

    Returns (entries, statuses) where entries are (path, suffix) tuples
    and statuses maps path -> git status code ('A'/'M'/'D'/etc).
    """
    excl = exclude_args()
    raw_numstat = git(wt, "diff", "HEAD", "--numstat", "--", ".", *excl)
    raw_status = git(wt, "diff", "HEAD", "--name-status", "--", ".", *excl)

    entries: list[tuple[str, str]] = []
    statuses: dict[str, str] = {}

    for line in raw_numstat.strip().splitlines() if raw_numstat.strip() else []:
        parts = line.split("\t", 2)
        if len(parts) == 3:
            is_binary = parts[0] == "-" and parts[1] == "-"
            a = int(parts[0]) if parts[0] != "-" else 0
            r = int(parts[1]) if parts[1] != "-" else 0
            if is_binary:
                entries.append((parts[2], " (binary)"))
            else:
                a_str = f"+{a}" if a else ""
                r_str = f"-{r}" if r else ""
                entries.append((parts[2], f" {a_str} {r_str}".rstrip()))

    for line in raw_status.strip().splitlines() if raw_status.strip() else []:
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            status_code = parts[0][0]
            path = parts[2] if len(parts) == 3 else parts[1]
            statuses[path] = status_code

    for f in _untracked_files(wt):
        lc = _count_lines(wt, f)
        if lc:
            entries.append((f, f" +{lc}"))
        else:
            entries.append((f, " (binary)"))
        statuses[f] = "A"

    return entries, statuses


def section_full_diff(
    wt: Path, file_entries: list[tuple[str, str]], repo: str, branch: str, delta: bool = False
) -> str:
    """Render full diff per area. When delta=True, pipe through delta and print directly."""
    areas = partition_files_by_area(file_entries, repo)
    if not areas:
        return ""

    excl = exclude_args()

    if delta:
        print("## Full diff\n")
        for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
            entries = areas[area]
            paths = [path for path, _ in entries]
            diff = git(wt, "diff", f"{base_ref(repo, branch)}...HEAD", "--", *paths, *excl)
            if not diff.strip():
                continue
            print(f"### {area}\n")
            pipe_through_delta(diff)
            print()
        return ""

    lines = ["## Full diff", ""]
    for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
        entries = areas[area]
        paths = [path for path, _ in entries]
        diff = git(wt, "diff", f"{base_ref(repo, branch)}...HEAD", "--", *paths, *excl)
        if not diff.strip():
            continue
        lines.append(f"### {area}")
        lines.append("")
        lines.append("```diff")
        lines.append(diff)
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def uncommitted_stats(wt: Path) -> tuple[int, int, int, int]:
    """Count uncommitted tracked modifications and untracked files.

    Returns (modified_files, added_lines, removed_lines, untracked_count).
    """
    excl = exclude_args()
    raw = git(wt, "diff", "HEAD", "--numstat", "--", ".", *excl)
    mod_files = added = removed = 0
    for line in raw.strip().splitlines() if raw.strip() else []:
        parts = line.split("\t", 2)
        if len(parts) == 3:
            mod_files += 1
            added += int(parts[0]) if parts[0] != "-" else 0
            removed += int(parts[1]) if parts[1] != "-" else 0
    untracked = len(_untracked_files(wt))
    return mod_files, added, removed, untracked


def section_uncommitted(wt: Path, repo: str, delta: bool = False) -> str:
    """Render uncommitted changes (staged + unstaged + untracked)."""
    excl = exclude_args()

    raw = git(wt, "diff", "HEAD", "--numstat", "--", ".", *excl)
    mod_entries: list[tuple[str, str]] = []
    total_added = 0
    total_removed = 0
    for line in raw.strip().splitlines() if raw.strip() else []:
        parts = line.split("\t", 2)
        if len(parts) == 3:
            a = int(parts[0]) if parts[0] != "-" else 0
            r = int(parts[1]) if parts[1] != "-" else 0
            total_added += a
            total_removed += r
            a_str = f"+{a}" if a else ""
            r_str = f"-{r}" if r else ""
            mod_entries.append((parts[2], f" {a_str} {r_str}".rstrip()))

    untracked = _untracked_files(wt)
    untracked_entries: list[tuple[str, str]] = []
    untracked_lines = 0
    for f in untracked:
        lc = _count_lines(wt, f)
        untracked_lines += lc
        untracked_entries.append((f, f" +{lc}" if lc else " (binary)"))

    if not mod_entries and not untracked_entries:
        return ""

    total_files = len(mod_entries) + len(untracked_entries)
    total_new_lines = total_added + untracked_lines
    parts_label = []
    if mod_entries:
        parts_label.append(f"{len(mod_entries)} modified")
    if untracked_entries:
        parts_label.append(f"{len(untracked_entries)} new")
    header = (
        f"## Uncommitted changes  ({total_files} files: {', '.join(parts_label)};  +{total_new_lines} -{total_removed})"
    )

    if delta:
        print(f"{header}\n")
        if mod_entries:
            areas = partition_files_by_area(mod_entries, repo)
            for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
                entries = areas[area]
                paths = [path for path, _ in entries]
                diff = git(wt, "diff", "HEAD", "--", *paths, *excl)
                if not diff.strip():
                    continue
                print(f"### {area}\n")
                pipe_through_delta(diff)
                print()
        if untracked_entries:
            areas = partition_files_by_area(untracked_entries, repo)
            for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
                entries = areas[area]
                print(f"### {area} (new)\n")
                for path, _ in entries:
                    diff = git(wt, "diff", "--no-index", "--", "/dev/null", path, check=False)
                    if diff.strip():
                        pipe_through_delta(diff)
                print()
        return ""

    lines = [header, ""]
    if mod_entries:
        areas = partition_files_by_area(mod_entries, repo)
        for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
            entries = areas[area]
            paths = [path for path, _ in entries]
            diff = git(wt, "diff", "HEAD", "--", *paths, *excl)
            if not diff.strip():
                continue
            lines.append(f"### {area}")
            lines.append("")
            lines.append("```diff")
            lines.append(diff)
            lines.append("```")
            lines.append("")
    if untracked_entries:
        areas = partition_files_by_area(untracked_entries, repo)
        for area in sorted(areas, key=lambda a: _area_sort_key(a, repo)):
            entries = areas[area]
            lines.append(f"### {area} (new)")
            lines.append("")
            lines.append("```diff")
            for path, _ in entries:
                diff = git(wt, "diff", "--no-index", "--", "/dev/null", path, check=False, errors="replace")
                if diff.strip():
                    lines.append(diff)
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template composers
# ---------------------------------------------------------------------------


def branch_summary(wt: Path, branch: str, repo: str, staleness: str, delta: bool = False, tree: bool = False) -> str:
    """Assemble colorized branch summary vs production (commits, files, diff)."""
    excl = exclude_args()
    file_entries, total_added, total_removed = parse_numstat(wt, excl, repo, branch)
    commits = parse_commits(wt, repo, branch)
    uc_stats = uncommitted_stats(wt)
    has_uncommitted = uc_stats[0] + uc_stats[3] > 0

    if delta:
        preamble = "\n".join(
            [
                section_header_diff(
                    repo,
                    branch,
                    wt,
                    staleness,
                    file_entries,
                    total_added,
                    total_removed,
                    wt_uncommitted=uc_stats if has_uncommitted else None,
                ),
                section_commits_chronological(commits),
                section_files_topical(file_entries, repo, tree=tree),
            ]
        )
        print(colorize(preamble))
        section_full_diff(wt, file_entries, repo, branch, delta=True)
        if has_uncommitted:
            section_uncommitted(wt, repo, delta=True)
        return ""

    parts = [
        section_header_diff(
            repo,
            branch,
            wt,
            staleness,
            file_entries,
            total_added,
            total_removed,
            wt_uncommitted=uc_stats if has_uncommitted else None,
        ),
        section_commits_chronological(commits),
        section_files_topical(file_entries, repo, tree=tree),
        section_full_diff(wt, file_entries, repo, branch),
    ]
    uc_section = section_uncommitted(wt, repo)
    if uc_section:
        parts.append(uc_section)
    return colorize("\n".join(parts))


# ---------------------------------------------------------------------------
# Status logic (used by v branch list -M)
# ---------------------------------------------------------------------------


def _parse_shortstat(shortstat: str) -> tuple[int, int, int]:
    """Extract (files_changed, insertions, deletions) from --shortstat output."""
    files = ins = dels = 0
    m = re.search(r"(\d+) file", shortstat)
    if m:
        files = int(m.group(1))
    m = re.search(r"(\d+) insertion", shortstat)
    if m:
        ins = int(m.group(1))
    m = re.search(r"(\d+) deletion", shortstat)
    if m:
        dels = int(m.group(1))
    return files, ins, dels


def _format_tests(repo: str, name: str) -> list[str]:
    """Read branch tests.json, return formatted lines for nested ulist."""
    data = load_json(tests_file(repo, name))
    if not data:
        return []
    tests = data.get("tests", [])
    if not tests:
        return []
    lines = ["- Tests:"]
    for t in tests:
        desc = t.get("description", "untitled")
        steps = t.get("steps", [])
        test_type = t.get("type", "api")
        lines.append(f"  - {desc} ({len(steps)} steps, {test_type})")
    return lines


def worktree_info(
    repo: str, name: str, wt_path: Path, commit_hash: str, label: str | None, aka: list[str] | None = None
) -> str:
    """Build the full info block for a single non-production worktree."""
    from codehome.git import ignorable_patterns, staleness_tag

    lines = []
    header = f"## {name}"
    if aka:
        header += f" (aka: {', '.join(sorted(aka))})"
    if label:
        header += f" [{label}]"
    tag = staleness_tag(name, repo)
    if tag:
        header += f" {tag}"
    lines.append(header)

    lines.append(f"- Path: {wt_path}")
    lines.append(f"- Commit: {commit_hash}")

    ahead = git(wt_path, "rev-list", f"{base_ref(repo, name)}..HEAD", "--count")
    push_status = _push_status(wt_path, name) or "pushed"
    lines.append(f"- {ahead} commits, {push_status}")

    excl = exclude_args()
    shortstat = git(wt_path, "diff", f"{base_ref(repo, name)}...HEAD", "--shortstat", "--", ".", *excl)
    if shortstat:
        files, ins, dels = _parse_shortstat(shortstat)
        lines.append(f"- {files} files, +{ins} -{dels}")

    dirty_output = git(wt_path, "status", "--porcelain")
    if dirty_output:
        patterns = ignorable_patterns()
        noise_files = []
        real_files = []
        for line in dirty_output.splitlines():
            filepath = line[3:].split(" -> ")[0]
            if any(fnmatch.fnmatch(filepath, p) for p in patterns):
                noise_files.append(filepath)
            else:
                real_files.append(filepath)
        if real_files:
            lines.append(f"- Uncommitted: {', '.join(real_files)}")
        if noise_files:
            lines.append(f"- Uncommitted (noise): {', '.join(noise_files)}")

    test_lines = _format_tests(repo, name)
    if test_lines:
        lines.extend(test_lines)

    dd = docs_path(repo, name)
    if dd.is_dir():
        count = sum(1 for p in dd.rglob("*") if p.is_file() and p.name not in (".gitkeep", ".manifest.json"))
        if count:
            lines.append(f"- Docs: {count} files")

    return "\n".join(lines)
