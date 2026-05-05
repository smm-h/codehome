"""Utility functions: output, colors, tree rendering, JSON I/O."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NoReturn, TypeVar

if TYPE_CHECKING:
    from datetime import date

T = TypeVar("T")


# ANSI color helpers (auto-disabled when piped or --no-color).
USE_COLOR = sys.stdout.isatty()


def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def bold(text: str) -> str:
    return _ansi("1", text)


def dim(text: str) -> str:
    return _ansi("2", text)


def green(text: str) -> str:
    return _ansi("32", text)


def red(text: str) -> str:
    return _ansi("31", text)


def blue(text: str) -> str:
    return _ansi("34", text)


def cyan(text: str) -> str:
    return _ansi("36", text)


def yellow(text: str) -> str:
    return _ansi("33", text)


def magenta(text: str) -> str:
    return _ansi("35", text)


def gray(text: str) -> str:
    return _ansi("90", text)


def colorize(text: str) -> str:
    """Apply ANSI colors to known patterns in plain-text output.

    Patterns matched:
    - Lines starting with # or ## -> bold
    - `hash` (backtick-wrapped 8-char hex) -> dim
    - +N (additions) -> green
    - -N (removals) -> red
    - (N behind) -> yellow
    - -- description (in context lists) -> dim
    """
    if not USE_COLOR:
        return text

    lines = text.splitlines()
    result = []
    for line in lines:
        # Bold markdown headings.
        if re.match(r"^#{1,2} ", line):
            line = bold(line)
        else:
            # Dim backtick-wrapped hashes (8-char hex).
            # mypy can't resolve re.sub's str overload with untyped lambdas.
            line = re.sub(r"`([0-9a-f]{8})`", lambda m: dim(m.group(0)), line)  # type: ignore[arg-type]
            # Green additions, red removals (standalone +N / -N patterns).
            line = re.sub(r"(?<!\w)\+(\d+)", lambda m: green(m.group(0)), line)  # type: ignore[arg-type]
            line = re.sub(r"(?<!\w)-(\d+)", lambda m: red(m.group(0)), line)  # type: ignore[arg-type]
            # Yellow staleness tag.
            line = re.sub(r"\(\d+ behind\)", lambda m: yellow(m.group(0)), line)  # type: ignore[arg-type]
            # Dim [selected] tag.
            line = re.sub(r"\[selected\]", lambda m: dim(m.group(0)), line)  # type: ignore[arg-type]
            # Dim context descriptions after backtick-wrapped filenames.
            line = re.sub(r"(?<=`)(:\s.+)$", lambda m: dim(m.group(0)), line)  # type: ignore[arg-type]
        result.append(line)
    return "\n".join(result)


def warn(msg: str) -> None:
    """Print a warning to stderr."""
    sys.stderr.write(f"warning: {msg}\n")


def die(msg: str) -> NoReturn:
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(1)


def load_json(path: Path, default: Any = None) -> Any:
    """Read a JSON file, returning default if missing or malformed."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def atomic_json_write(path: Path, data: Any) -> None:
    """Write JSON atomically via tmpfile + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        Path(tmp).rename(path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def open_url(url: str) -> None:
    """Open a URL in the user's preferred browser (best-effort, never fatal)."""
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _detect_browser() -> str | None:
    """Detect the user's default browser binary.

    Priority: $BROWSER env var > xdg-settings default > common binary probes.
    Returns the binary path/name, or None if nothing found.
    """
    # 1. Explicit $BROWSER env var.
    env_browser = os.environ.get("BROWSER")
    if env_browser and shutil.which(env_browser):
        return env_browser

    # 2. xdg-settings: get the .desktop file, map to a binary.
    _desktop_to_binary = {
        # Traditional short names
        "google-chrome.desktop": "google-chrome",
        "google-chrome-stable.desktop": "google-chrome-stable",
        "chromium.desktop": "chromium",
        "chromium-browser.desktop": "chromium-browser",
        "firefox.desktop": "firefox",
        "firefox-esr.desktop": "firefox-esr",
        "microsoft-edge.desktop": "microsoft-edge",
        "brave-browser.desktop": "brave-browser",
        "vivaldi-stable.desktop": "vivaldi",
        # Reverse-DNS names (Flatpak, Snap, modern distros)
        "org.mozilla.firefox.desktop": "firefox",
        "org.mozilla.firefox_esr.desktop": "firefox-esr",
        "org.chromium.Chromium.desktop": "chromium",
        "com.google.Chrome.desktop": "google-chrome",
        "com.microsoft.Edge.desktop": "microsoft-edge",
        "com.brave.Browser.desktop": "brave-browser",
        "com.vivaldi.Vivaldi.desktop": "vivaldi",
    }
    try:
        result = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            desktop = result.stdout.strip()
            binary = _desktop_to_binary.get(desktop)
            if binary and shutil.which(binary):
                return binary
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 3. Probe common binaries.
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "firefox",
        "microsoft-edge",
        "brave-browser",
    ):
        if shutil.which(candidate):
            return candidate

    return None


def _is_chromium_based(binary: str) -> bool:
    """Return True if the browser binary is Chromium-based (not Firefox)."""
    chromium_names = {
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "brave-browser",
        "vivaldi",
    }
    # Check the basename in case it's a full path.
    name = Path(binary).name
    return any(name.startswith(c) for c in chromium_names)


def _find_free_cdp_port() -> int | None:
    """Return 9222 or 9223 if available, None if both are occupied."""
    import socket

    for port in (9222, 9223):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
            # Port is in use, try next.
        except (ConnectionRefusedError, OSError):
            return port
    return None


def open_url_with_cdp(url: str) -> int | None:
    """Open a URL with CDP (Chrome DevTools Protocol) debugging enabled.

    Detects the browser, launches with --remote-debugging-port if possible.
    Returns the CDP port used, or None if falling back to plain xdg-open.
    """
    browser = _detect_browser()
    if not browser:
        open_url(url)
        return None

    cdp_port = _find_free_cdp_port()
    if cdp_port is None:
        # Both 9222 and 9223 are occupied; fall back to plain open.
        open_url(url)
        return None

    if not _is_chromium_based(browser):
        # CDP client uses Chrome DevTools Protocol; non-Chromium browsers
        # accept --remote-debugging-port but speak a different protocol.
        open_url(url)
        return None

    try:
        subprocess.Popen(
            [browser, f"--remote-debugging-port={cdp_port}", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return cdp_port
    except Exception:
        # If launching with CDP flag fails, fall back to xdg-open.
        open_url(url)
        return None


def claude_prompt(prompt: str, stdin_text: str) -> subprocess.CompletedProcess[str]:
    """Run claude -p for a one-shot prompt.

    Sets CLAUDECODE=0 so the nested claude process doesn't think it's
    inside another session (claude sets this in its own env).
    """
    env = {**os.environ, "CLAUDECODE": "0"}
    return subprocess.run(
        ["claude", "-p", prompt],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
    )


def _terminal_width() -> int:
    """Get real terminal width, even inside Claude Code's pty.

    shutil.get_terminal_size() queries the local pty, which may report
    a wrong size (e.g. fixed 80 inside Claude Code). Walk up the
    process tree via /proc/<ppid>/fd/1 and return the first ancestor
    whose stdout is a terminal device. Subtract CLAUDECODE_PADDING
    when running inside Claude Code to account for UI chrome.
    """
    claudecode = bool(os.environ.get("CLAUDECODE"))
    padding = 8 if claudecode else 0

    # Fast path: if stdout IS a terminal, trust it.
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns - padding
    except (OSError, ValueError):
        pass

    # stdout isn't a terminal (piped/redirected). Walk parent processes.
    pid = os.getpid()
    for _ in range(20):
        try:
            ppid = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
        except (OSError, ValueError):
            break
        if ppid <= 1:
            break
        try:
            fd = os.open(f"/proc/{ppid}/fd/1", os.O_RDONLY)
            try:
                w = os.get_terminal_size(fd).columns
            finally:
                os.close(fd)
            return w - padding
        except OSError:
            pass
        pid = ppid

    # Fallback: shutil (checks COLUMNS env, then defaults).
    return shutil.get_terminal_size((80, 24)).columns - padding


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(s: str) -> int:
    """Length of string excluding ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s))


def _truncate_ansi(text: str, max_visible: int) -> str:
    """Truncate text to max_visible visible chars, preserving ANSI codes."""
    if max_visible < 1:
        return "\u2026"
    visible = 0
    i = 0
    result: list[str] = []
    target = max_visible - 1  # reserve 1 char for ellipsis
    while i < len(text) and visible < target:
        if text[i] == "\033":
            j = i + 1
            while j < len(text) and text[j] != "m":
                j += 1
            result.append(text[i : j + 1])
            i = j + 1
        else:
            result.append(text[i])
            visible += 1
            i += 1
    result.append("\u2026")
    return "".join(result)


def render_box_table(
    headers: list[str],
    rows: list[list[str]],
    aligns: list[str] | None = None,
    separators: set[int] | None = None,
) -> str:
    """Render a table with box-drawing characters (dimmed borders).

    aligns: per-column "l" or "r" (default "l").
    separators: row indices before which to insert a ├─┼─┤ line.
    """
    n_cols = len(headers)
    aligns = aligns or ["l"] * n_cols
    separators = separators or set()
    col_widths = [len(h) for h in headers]
    for r in rows:
        for j, cell in enumerate(r):
            col_widths[j] = max(col_widths[j], _visible_len(cell))

    # Shrink the widest column to fit the terminal (if needed).
    term_width = _terminal_width()
    overhead = 1 + 3 * n_cols  # │ + (space + content + space) per column
    total = sum(col_widths) + overhead
    shrink_col = -1
    if total > term_width:
        shrink_col = col_widths.index(max(col_widths))
        min_width = max(len(headers[shrink_col]), 10)
        col_widths[shrink_col] = max(col_widths[shrink_col] - (total - term_width), min_width)

    def _pad(text: str, width: int, align: str) -> str:
        pad = width - _visible_len(text)
        return (" " * pad) + text if align == "r" else text + (" " * pad)

    def _rule(left: str, mid: str, right: str) -> str:
        return dim(left + mid.join("─" * (w + 2) for w in col_widths) + right)

    def _data(cells: list[str]) -> str:
        d = dim("│")
        parts = []
        for j in range(n_cols):
            cell = cells[j]
            if j == shrink_col and _visible_len(cell) > col_widths[j]:
                cell = _truncate_ansi(cell, col_widths[j])
            parts.append(" " + _pad(cell, col_widths[j], aligns[j]) + " ")
        return d + d.join(parts) + d

    out = [_rule("┌", "┬", "┐"), _data(headers), _rule("├", "┼", "┤")]
    for i, r in enumerate(rows):
        if i > 0 and i in separators:
            out.append(_rule("├", "┼", "┤"))
        out.append(_data(r))
    out.append(_rule("└", "┴", "┘"))
    return "\n".join(out)


def render_unified_file_table(
    groups: list[tuple[str, str, str, list[tuple[str, str, str]]]],
) -> str:
    """Render grouped files as a single table with area separator rows.

    groups: [(area_label, area_lines, area_uc, [(file, lines, uc)])].
    The Uncommitted column is hidden when all uc values are empty.
    """
    has_uc = any(area_uc or any(uc for _, _, uc in rows) for _, _, area_uc, rows in groups)

    all_rows: list[list[str]] = []
    seps: set[int] = set()
    for area_label, area_lines, area_uc, rows in groups:
        seps.add(len(all_rows))
        row = [area_label, area_lines]
        if has_uc:
            row.append(area_uc)
        all_rows.append(row)
        seps.add(len(all_rows))
        for file, lines, uc in rows:
            row = [file, lines]
            if has_uc:
                row.append(uc)
            all_rows.append(row)

    headers = ["File changed", "Lines"]
    aligns = ["l", "r"]
    if has_uc:
        headers.append("Uncommitted")
        aligns.append("r")

    return render_box_table(headers, all_rows, aligns=aligns, separators=seps)


def render_file_tree(entries: list[tuple[str, str]]) -> str:
    """Render file paths as an ASCII tree (like the tree command).

    entries: list of (path, suffix) tuples. suffix is appended after the
    filename (e.g. stats or descriptions).

    Single-child directories are collapsed: supabase/functions/ instead
    of nested supabase/ > functions/.
    """
    # Build nested dict tree. Leaves store their suffix.
    tree: dict[str, Any] = {}
    for path, suffix in entries:
        parts = path.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        # Leaf: store suffix under a special key.
        node[parts[-1]] = {"__leaf__": suffix}

    lines: list[str] = []

    def _render(node: dict[str, Any], prefix: str = "") -> None:
        keys = sorted(node.keys())
        for i, key in enumerate(keys):
            is_last = i == len(keys) - 1
            connector = "└── " if is_last else "├── "
            child = node[key]
            if "__leaf__" in child and len(child) == 1:
                # Leaf file.
                suffix = child["__leaf__"]
                lines.append(f"{prefix}{connector}{key}{suffix}")
            else:
                # Directory node. Collapse single-child chains.
                collapsed = key
                inner = child
                while len(inner) == 1 and "__leaf__" not in inner:
                    only_key = next(iter(inner))
                    only_child = inner[only_key]
                    if "__leaf__" in only_child and len(only_child) == 1:
                        break
                    collapsed = f"{collapsed}/{only_key}"
                    inner = only_child
                lines.append(f"{prefix}{connector}{collapsed}/")
                extension = "    " if is_last else "│   "
                _render(inner, prefix + extension)

    _render(tree)
    return "\n".join(lines)


def _fmt_date(d: date) -> str:
    """Format date without year: 'Mar 13'."""
    return d.strftime("%b %d").replace(" 0", " ")


def group_by_date(items: list[T], date_fn: Callable[[T], date]) -> list[tuple[date, list[T]]]:
    """Group pre-sorted items by date. Items must already be sorted by date descending."""
    groups: list[tuple[date, list[T]]] = []
    for item in items:
        d = date_fn(item)
        if groups and groups[-1][0] == d:
            groups[-1][1].append(item)
        else:
            groups.append((d, [item]))
    return groups


def render_date_tree(
    groups: list[tuple[date, list[T]]],
    indent: str,
    name_fn: Callable[[T], str],
    is_last_sibling: Callable[[int], bool] | None = None,
) -> list[str]:
    """Render date-grouped items as tree nodes.

    Collapses single-item groups inline: '|- Mar 13/ name'.
    name_fn(item) returns the display string for an item.
    is_last_sibling(group_index) returns True if this is the last
    top-level sibling. Defaults to checking against len(groups).
    """
    if is_last_sibling is None:
        total = len(groups)

        def is_last_sibling(gi: int) -> bool:
            return gi == total - 1

    lines = []
    for gi, (d, group_items) in enumerate(groups):
        is_last = is_last_sibling(gi)
        gc = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        if len(group_items) == 1:
            lines.append(f"{indent}{gc}{dim(_fmt_date(d) + '/')} {name_fn(group_items[0])}")
        else:
            lines.append(f"{indent}{gc}{dim(_fmt_date(d))}")
            prefix = indent + ("    " if is_last else "\u2502   ")
            for fi, item in enumerate(group_items):
                is_last_file = fi == len(group_items) - 1
                fc = "\u2514\u2500\u2500 " if is_last_file else "\u251c\u2500\u2500 "
                lines.append(f"{prefix}{fc}{name_fn(item)}")
    return lines
