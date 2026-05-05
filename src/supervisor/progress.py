"""Progress tracking: append-only session headers in progress.md (branch root)."""

import re
from datetime import UTC, datetime

from supervisor.paths import progress_file


def append_session_header(repo: str, branch: str) -> int:
    """Append a ## Session N header to progress.md. Returns N.

    Creates the file and parent dir if they don't exist.
    """
    progress = progress_file(repo, branch)
    progress.parent.mkdir(parents=True, exist_ok=True)

    # Count existing session headers to determine N.
    existing = ""
    if progress.exists():
        existing = progress.read_text()
    count = len(re.findall(r"^## Session \d+", existing, re.MULTILINE))
    n = count + 1

    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    header = f"\n## Session {n} -- {ts}\n\n"
    with progress.open("a") as f:
        f.write(header)

    return n


def latest_section_is_empty(repo: str, branch: str) -> bool:
    """Return True if the last ## Session section has no content below the header."""
    progress = progress_file(repo, branch)
    if not progress.exists():
        return True

    text = progress.read_text()
    # Find the last session header.
    matches = list(re.finditer(r"^## Session \d+.*$", text, re.MULTILINE))
    if not matches:
        return True

    last_header = matches[-1]
    after_header = text[last_header.end() :]
    # Check for non-whitespace content after the header.
    return not after_header.strip()
