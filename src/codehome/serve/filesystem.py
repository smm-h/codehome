"""Filesystem operations for the worktree browser.

Lists directories, reads files, and searches via ripgrep.
All paths are guarded against traversal outside the worktree root.
"""

import base64
import json
import subprocess
from pathlib import Path
from typing import Any

# Extensions mapped to Shiki language identifiers for syntax highlighting.
_LANG_MAP: dict[str, str] = {
    ".astro": "astro",
    ".bash": "bash",
    ".bat": "bat",
    ".c": "c",
    ".cjs": "javascript",
    ".clj": "clojure",
    ".cmake": "cmake",
    ".cmd": "bat",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".cts": "typescript",
    ".csv": "csv",
    ".dart": "dart",
    ".diff": "diff",
    ".dockerfile": "dockerfile",
    ".env": "dotenv",
    ".erb": "erb",
    ".ex": "elixir",
    ".exs": "elixir",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".go": "go",
    ".gql": "graphql",
    ".graphql": "graphql",
    ".h": "c",
    ".haml": "haml",
    ".hbs": "handlebars",
    ".hpp": "cpp",
    ".hs": "haskell",
    ".html": "html",
    ".ini": "ini",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".json5": "json5",
    ".jsonc": "jsonc",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".less": "less",
    ".log": "log",
    ".lua": "lua",
    ".m": "objective-c",
    ".make": "make",
    ".md": "markdown",
    ".mdx": "mdx",
    ".mjs": "javascript",
    ".mts": "typescript",
    ".nginx": "nginx",
    ".nim": "nim",
    ".nix": "nix",
    ".patch": "diff",
    ".php": "php",
    ".pl": "perl",
    ".prisma": "prisma",
    ".proto": "proto",
    ".ps1": "powershell",
    ".pug": "pug",
    ".py": "python",
    ".r": "r",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".scss": "scss",
    ".sh": "bash",
    ".sql": "sql",
    ".styl": "stylus",
    ".svelte": "svelte",
    ".swift": "swift",
    ".tex": "latex",
    ".tf": "hcl",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsv": "csv",
    ".tsx": "typescript",
    ".vim": "viml",
    ".vue": "vue",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zig": "zig",
    ".zsh": "zsh",
}

# Binary extensions that can be previewed in the browser.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".avif"}
_FONT_EXTS = {".woff", ".woff2", ".ttf", ".otf"}
_MEDIA_EXTS = {".mp4", ".webm", ".mov", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}
_PREVIEWABLE_BINARY = _IMAGE_EXTS | _MEDIA_EXTS | _FONT_EXTS | {".pdf"}

# MIME types for previewable binary files.
_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".pdf": "application/pdf",
}

# Max binary file size for preview (10 MB).
_MAX_BINARY_SIZE = 10_485_760

# Hidden dirs to skip (but .env files are allowed).
_HIDDEN_DIR_EXCEPTIONS = {".env", ".env.local", ".env.development", ".env.production"}

# Max file size for reading (1 MB).
_MAX_FILE_SIZE = 1_048_576


def _guard_path(worktree: Path, relative: str) -> Path:
    """Resolve a relative path and ensure it stays under the worktree.

    Raises ValueError on path traversal attempts.
    """
    resolved = (worktree / relative).resolve()
    worktree_resolved = worktree.resolve()
    if not str(resolved).startswith(str(worktree_resolved)):
        msg = f"Path traversal detected: {relative}"
        raise ValueError(msg)
    return resolved


def _detect_language(path: Path) -> str:
    """Detect language from file extension. Returns empty string if unknown."""
    # Special cases: well-known filenames without extensions.
    _NAME_MAP = {"dockerfile": "dockerfile", "makefile": "make", "justfile": "just"}
    mapped = _NAME_MAP.get(path.name.lower())
    if mapped:
        return mapped
    return _LANG_MAP.get(path.suffix.lower(), "")


def list_files(worktree: Path, path: str = "") -> list[dict[str, Any]]:
    """List directory contents under the worktree.

    Each entry: {name, type: "file"|"dir", size, path}.
    Skips hidden directories (names starting with .) except .env files.
    Sorts directories first, then files, both alphabetically.
    """
    target = _guard_path(worktree, path)
    if not target.is_dir():
        msg = f"Not a directory: {path}"
        raise FileNotFoundError(msg)

    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    try:
        entries = sorted(target.iterdir(), key=lambda e: e.name.lower())
    except PermissionError:
        return []

    for entry in entries:
        name = entry.name

        # Skip hidden dirs, but allow .env files.
        if name.startswith("."):
            if entry.is_dir():
                continue
            if name not in _HIDDEN_DIR_EXCEPTIONS:
                continue

        # Build the relative path from worktree root.
        rel = str(entry.relative_to(worktree.resolve()))

        if entry.is_dir():
            dirs.append(
                {
                    "name": name,
                    "type": "dir",
                    "size": 0,
                    "path": rel,
                },
            )
        elif entry.is_file():
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            files.append(
                {
                    "name": name,
                    "type": "file",
                    "size": size,
                    "path": rel,
                },
            )

    return dirs + files


def read_file(worktree: Path, path: str) -> dict[str, Any]:
    """Read file content from the worktree.

    Returns {path, content, size, language}.
    Guards against path traversal and refuses files over 1 MB.
    """
    target = _guard_path(worktree, path)

    if not target.is_file():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    size = target.stat().st_size
    if size > _MAX_FILE_SIZE:
        msg = f"File too large: {size} bytes (max {_MAX_FILE_SIZE})"
        raise ValueError(msg)

    ext = target.suffix.lower()

    # Previewable binary files: return base64-encoded content with MIME type.
    if ext in _PREVIEWABLE_BINARY:
        if size > _MAX_BINARY_SIZE:
            msg = f"File too large: {size} bytes (max {_MAX_BINARY_SIZE})"
            raise ValueError(msg)
        raw = target.read_bytes()
        return {
            "path": path,
            "content": base64.b64encode(raw).decode("ascii"),
            "size": size,
            "language": "",
            "binary": True,
            "mime": _MIME_MAP.get(ext, "application/octet-stream"),
        }

    # Try to read as text; if it fails, it's a non-previewable binary.
    try:
        content = target.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        msg = "Binary file"
        raise ValueError(msg) from None

    return {
        "path": path,
        "content": content,
        "size": size,
        "language": _detect_language(target),
        "binary": False,
    }


def search_files(
    worktree: Path,
    pattern: str,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Search file contents using ripgrep (rg).

    Returns [{path, line, content, context_before, context_after}].
    Timeout: 10 seconds. Returns empty list on error.
    """
    if not pattern:
        return []

    try:
        result = subprocess.run(
            [
                "rg",
                "--json",
                "--max-count",
                "5",  # max matches per file
                "--context",
                "2",  # 2 lines of context
                "--max-filesize",
                "1M",
                "--no-heading",
                pattern,
            ],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        # rg not installed
        return []

    results: list[dict[str, Any]] = []
    # Accumulate context lines per match.
    context_before: list[str] = []
    context_after: list[str] = []
    current_match: dict[str, Any] | None = None

    for line in result.stdout.splitlines():
        if len(results) >= max_results:
            break

        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type")

        if msg_type == "match":
            # Flush previous match if any.
            if current_match is not None:
                current_match["context_after"] = context_after
                results.append(current_match)
                if len(results) >= max_results:
                    break

            data = entry.get("data", {})
            path_data = data.get("path", {})
            file_path = path_data.get("text", "") if isinstance(path_data, dict) else str(path_data)
            lines_data = data.get("lines", {})
            line_text = lines_data.get("text", "").rstrip("\n") if isinstance(lines_data, dict) else str(lines_data)

            current_match = {
                "path": file_path,
                "line": data.get("line_number", 0),
                "content": line_text,
                "context_before": list(context_before),
                "context_after": [],
            }
            context_before = []
            context_after = []

        elif msg_type == "context":
            data = entry.get("data", {})
            lines_data = data.get("lines", {})
            ctx_text = lines_data.get("text", "").rstrip("\n") if isinstance(lines_data, dict) else str(lines_data)

            if current_match is not None:
                context_after.append(ctx_text)
            else:
                context_before.append(ctx_text)
                # Keep only last 2 context lines.
                if len(context_before) > 2:
                    context_before.pop(0)

        elif msg_type in ("begin", "end"):
            # File boundary: flush context.
            if msg_type == "end" and current_match is not None:
                current_match["context_after"] = context_after
                results.append(current_match)
                current_match = None
                context_before = []
                context_after = []
            elif msg_type == "begin":
                context_before = []
                context_after = []

    # Flush last match.
    if current_match is not None and len(results) < max_results:
        current_match["context_after"] = context_after
        results.append(current_match)

    return results
