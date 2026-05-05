"""Raw HTML component scanning utilities.

Scans ``.svelte`` files under ``dashboard/src/`` and flags raw
``<button>``, ``<input>``, and ``<a href="/...">`` elements that should
use shared components.  Lines inside ``<script>`` / ``<style>`` blocks
are ignored; icon-only close buttons are whitelisted.

Relocated from ``supervisor.commands.check.raw_components``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from supervisor.paths import ROOT

if TYPE_CHECKING:
    from pathlib import Path

DASHBOARD_SRC = ROOT / "dashboard" / "src"

# Shared components that already exist; excluded from scanning so each
# component's own template definition isn't flagged.
_EXCLUDED_FILES = {"Button.svelte", "Input.svelte", "Link.svelte"}


# ---------------------------------------------------------------------------
# Inline suppression: <!-- noqa: raw-component -->
# ---------------------------------------------------------------------------

_NOQA_RE = re.compile(r"<!--\s*noqa:\s*raw-component\s*-->", re.IGNORECASE)


def _has_noqa(lines: list[str], lineno_0: int) -> bool:
    """Check if the line or the preceding line has a noqa suppression comment."""
    if _NOQA_RE.search(lines[lineno_0]):
        return True
    if lineno_0 > 0 and _NOQA_RE.search(lines[lineno_0 - 1]):
        return True
    return False


# ---------------------------------------------------------------------------
# Block-range detection: <script> and <style> regions
# ---------------------------------------------------------------------------


def _find_block_ranges(source: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character offsets for ``<script>`` / ``<style>`` blocks."""
    ranges: list[tuple[int, int]] = []
    for tag in ("script", "style"):
        pattern = re.compile(rf"<{tag}(?:\s[^>]*)?>.*?</{tag}>", re.DOTALL)
        ranges.extend((m.start(), m.end()) for m in pattern.finditer(source))
    return ranges


def _offset_for_line(source: str, lineno: int) -> int:
    """Return the character offset where the given 1-based line starts."""
    offset = 0
    for i, line in enumerate(source.splitlines(keepends=True), start=1):
        if i == lineno:
            return offset
        offset += len(line)
    return offset


def _in_blocked_range(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


# ---------------------------------------------------------------------------
# Full opening-tag extraction (handles multiline tags)
# ---------------------------------------------------------------------------


def _extract_full_tag(source: str, offset: int) -> str:
    """Extract the full opening tag starting from ``offset``.

    For multiline ``<button ...>`` or ``<input ...>`` tags, the attributes
    may span several lines.  This grabs everything from the ``<`` up to and
    including the first ``>``.  Returns up to 800 chars to avoid runaway
    scanning on malformed markup.
    """
    end = source.find(">", offset)
    if end == -1 or (end - offset) > 800:
        return source[offset : offset + 800]
    return source[offset : end + 1]


# ---------------------------------------------------------------------------
# Icon-only close-button heuristic
# ---------------------------------------------------------------------------

# Whole close-button element with a single-glyph text node. Characters
# below: U+00D7 (multiplication sign), U+2715 (multiplication X), plain
# x/X, plus HTML-entity variants -- all are standard close-button glyphs.
_CLOSE_BUTTON_RE = re.compile(
    r"<button[^>]*>[\s]*(\xd7|\u2715|x|X|&#x2715;|&#215;|&times;)[\s]*</button>",
    re.DOTALL,
)

# aria-label containing close/dismiss/hide -- handles static strings
# ("Close"), Svelte expressions ({i18n.t('action.close')}), and
# template-literal interpolations.
_CLOSE_ARIA_RE = re.compile(
    r"aria-label\s*=\s*(?:"
    r"""["\'][^"\']*(?:close|dismiss|hide)[^"\']*["\']"""  # quoted value
    r"|"
    r"""\{[^}]*(?:close|dismiss|hide)[^}]*\}"""  # Svelte expression {…}
    r")",
    re.IGNORECASE,
)

# CSS class names that signal a close/dismiss button.
_CLOSE_CLASS_RE = re.compile(
    r'class\s*=\s*"[^"]*\b(?:close|dismiss|clear)\b',
    re.IGNORECASE,
)

# title attribute containing close/dismiss -- handles both quoted and
# Svelte expression syntax.
_CLOSE_TITLE_RE = re.compile(
    r"title\s*=\s*(?:"
    r"""["\'][^"\']*(?:close|dismiss)[^"\']*["\']"""  # quoted value
    r"|"
    r"""\{[^}]*(?:close|dismiss)[^}]*\}"""  # Svelte expression {…}
    r")",
    re.IGNORECASE,
)


def _is_icon_close_button(source: str, line_offset: int) -> bool:
    """Return True if the button at ``line_offset`` looks like a close/dismiss icon button.

    Handles multiline ``<button>`` tags by extracting the full opening tag
    and a generous content slice, then checking for close-button indicators
    (aria-label, title, class, or single-glyph text content).
    """
    # Find the actual ``<button`` within the line to get a precise offset.
    line_end = source.find("\n", line_offset)
    if line_end == -1:
        line_end = len(source)
    line_text = source[line_offset:line_end]
    btn_pos = line_text.find("<button")
    if btn_pos == -1:
        return False
    tag_offset = line_offset + btn_pos

    # Grab a generous slice starting from the ``<button`` tag.
    snippet = source[tag_offset : tag_offset + 600]
    # Check for single-glyph close button (e.g., &times;).
    if _CLOSE_BUTTON_RE.match(snippet):
        return True
    # Extract the full opening tag (may be multiline) and check attributes.
    full_tag = _extract_full_tag(source, tag_offset)
    if _CLOSE_ARIA_RE.search(full_tag):
        return True
    if _CLOSE_CLASS_RE.search(full_tag):
        return True
    if _CLOSE_TITLE_RE.search(full_tag):
        return True
    return False


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Finding:
    """A single raw-element detection."""

    __slots__ = ("element", "file", "line_text", "lineno", "suggestion")

    def __init__(self, file: str, lineno: int, element: str, line_text: str, suggestion: str):
        self.file = file
        self.lineno = lineno
        self.element = element
        self.line_text = line_text
        self.suggestion = suggestion


def scan_file(path: Path) -> list[Finding]:
    """Scan a single .svelte file and return any raw-element findings."""
    findings: list[Finding] = []

    source = path.read_text(errors="replace")
    blocked = _find_block_ranges(source)
    lines = source.splitlines()

    rel = str(path.relative_to(ROOT))

    for lineno_0, line in enumerate(lines):
        lineno = lineno_0 + 1
        offset = _offset_for_line(source, lineno)

        # Skip lines inside <script> / <style> blocks.
        if _in_blocked_range(offset, blocked):
            continue

        # Skip lines with inline noqa suppression.
        if _has_noqa(lines, lineno_0):
            continue

        stripped = line.strip()

        # --- Raw <button> ---
        # Match <button followed by whitespace, >, or end-of-line (multiline tags).
        if re.search(r"<button(?:[\s>]|$)", stripped):
            # Exclude icon-only close buttons.
            if _is_icon_close_button(source, offset):
                continue
            # Find the actual tag offset within the line for full-tag extraction.
            btn_pos = line.find("<button")
            tag_offset = offset + btn_pos if btn_pos != -1 else offset
            # Exclude buttons with Svelte use: directives (can't use <Button>).
            full_tag = _extract_full_tag(source, tag_offset)
            if re.search(r"\buse:", full_tag):
                continue
            findings.append(
                Finding(
                    file=rel,
                    lineno=lineno,
                    element="<button>",
                    line_text=stripped,
                    suggestion="Use <Button> from $lib/components/Button.svelte",
                )
            )

        # --- Raw <input> ---
        # Match <input followed by whitespace, >, /, or end-of-line.
        if re.search(r"<input(?:[\s>/]|$)", stripped):
            # Find the actual tag offset within the line for full-tag extraction.
            inp_pos = line.find("<input")
            inp_offset = offset + inp_pos if inp_pos != -1 else offset
            # Exclude inputs with bind:group (radio/checkbox groups can't use <Input>).
            full_tag = _extract_full_tag(source, inp_offset)
            if re.search(r"\bbind:group\b", full_tag):
                continue
            findings.append(
                Finding(
                    file=rel,
                    lineno=lineno,
                    element="<input>",
                    line_text=stripped,
                    suggestion="Use <Input> from $lib/components/Input.svelte",
                )
            )

        # --- Raw <a href="/..."> used for internal navigation ---
        m = re.search(r'<a\s+[^>]*href\s*=\s*["\'](/[^"\']*)["\']', stripped)
        if m:
            findings.append(
                Finding(
                    file=rel,
                    lineno=lineno,
                    element="<a>",
                    line_text=stripped,
                    suggestion="Use <Link> from $lib/components/Link.svelte for internal links",
                )
            )

    return findings
