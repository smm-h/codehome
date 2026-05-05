"""Install-mode check: detect editable vs frozen supervisor installs.

A frozen (non-editable) install silently swallows source changes, which
is the root cause of many "why isn't my change being picked up" debugging
spirals.  This check catches that early so agents can fix it before
wasting time.

Detection strategy:
- If a dist-info sibling exists with a direct_url.json marked editable,
  it is an editable install (PEP 660).
- Else, if the supervisor package __file__ lives under the source repo
  (ROOT/src/supervisor), it is editable-by-path (legacy `pip install -e .`
  or direct `python -m` from checkout).
- Else, it is frozen.
"""

from __future__ import annotations

import importlib.metadata
import json
import shlex
from pathlib import Path
from urllib.parse import unquote, urlparse

import supervisor
from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.paths import ROOT


def _detect_install_mode() -> tuple[str, str, Path]:
    """Return (mode, reason, source_path).

    mode is one of: "editable", "frozen", "unknown".
    """
    pkg_file = Path(supervisor.__file__).resolve()

    # Primary: importlib.metadata locates the dist-info no matter where it
    # lives relative to the package's source files (uv editable installs
    # redirect package imports via a .pth while keeping dist-info in the
    # tool's site-packages dir, so walking from pkg_file's parents misses
    # it).
    try:
        dist = importlib.metadata.distribution("supervisor")
    except importlib.metadata.PackageNotFoundError:
        dist = None

    if dist is not None:
        try:
            direct_url_text = dist.read_text("direct_url.json")
        except (OSError, FileNotFoundError):
            direct_url_text = None
        if direct_url_text:
            try:
                data = json.loads(direct_url_text)
            except json.JSONDecodeError:
                data = {}
            if data.get("dir_info", {}).get("editable"):
                return ("editable", "dist-info direct_url marks editable", pkg_file)
            return ("frozen", "dist-info direct_url marks non-editable", pkg_file)

    # Fallback: path-based heuristic.  If the package lives under our
    # source checkout, consider it editable.  Covers `python -m` from
    # source with no install at all.
    src_supervisor = ROOT / "src" / "supervisor"
    try:
        pkg_file.relative_to(src_supervisor)
        return ("editable", f"package path is under source tree ({src_supervisor})", pkg_file)
    except ValueError:
        pass

    return ("unknown", "no dist-info marker and package is outside source tree", pkg_file)


def _direct_url_source_path() -> Path | None:
    """Return the `url` path from dist-info direct_url.json as a local Path.

    Per PEP 610, `direct_url.json` records where the distribution was
    installed from.  For file:// URLs this points at the user's source
    checkout.  Returns None for remote URLs or missing data.
    """
    try:
        dist = importlib.metadata.distribution("supervisor")
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        text = dist.read_text("direct_url.json")
    except (OSError, FileNotFoundError):
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    url = data.get("url")
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    parsed = urlparse(url)
    path = Path(unquote(parsed.path))
    return path if path.is_absolute() else None


@register_check("install-mode", group="precommit", timeout=5)
async def check_install_mode(ctx: CheckContext) -> CheckResult:
    """Detect if supervisor is installed in editable mode.

    A frozen install means source edits are silently ignored until
    reinstalled editable.
    """
    mode, reason, _source_path = _detect_install_mode()

    if mode == "editable":
        return CheckResult(
            name="install-mode",
            outcome="pass",
            duration_ms=0,
            message=f"editable ({reason})",
        )

    if mode == "frozen":
        # Build fix command using the install-time source URL if available,
        # otherwise fall back to a generic placeholder.
        install_src = _direct_url_source_path()
        if install_src is not None:
            fix_cmd = f"uv tool install --reinstall --editable {shlex.quote(str(install_src))}"
        else:
            fix_cmd = "uv tool install --reinstall --editable /path/to/super"
        return CheckResult(
            name="install-mode",
            outcome="fail",
            duration_ms=0,
            message=f"frozen install -- source edits are ignored ({reason})",
            fix=fix_cmd,
        )

    # Unknown: no dist-info and package is outside source tree.
    return CheckResult(
        name="install-mode",
        outcome="fail",
        duration_ms=0,
        message=f"unable to determine install mode ({reason})",
        fix="uv tool install --reinstall --editable /path/to/super",
    )
