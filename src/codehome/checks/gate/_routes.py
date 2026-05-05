"""Route reachability scanning utilities.

Parses the route table from ``dashboard/src/lib/router/routes.ts`` (custom
client-side router, NOT SvelteKit file-based routing), collects navigation
targets from ``tabs.ts`` and from ``href=`` / ``goto()`` /
``window.location =`` / ``branchUrl()`` usages in .svelte files, then checks
whether every route has at least one path pointing to it.

Relocated from ``codehome.commands.check.routes`` and adapted from the
original SvelteKit-specific implementation to work with the plain-Svelte
custom router.
"""

from __future__ import annotations

import re
from pathlib import Path

from codehome.paths import ROOT

_DASHBOARD = ROOT / "dashboard"
_ROUTES_FILE = _DASHBOARD / "src" / "lib" / "router" / "routes.ts"
_TABS_FILE = _DASHBOARD / "src" / "lib" / "tabs.ts"
_SRC_DIR = _DASHBOARD / "src"


# ---------------------------------------------------------------------------
# 1. Discover routes from routes.ts
# ---------------------------------------------------------------------------


def _discover_routes() -> list[str]:
    """Parse path strings from the routeTable array in routes.ts.

    Looks for ``path: '/...'`` entries in the exported route table.
    Returns sorted, deduplicated URL patterns using the custom router
    syntax (``:param`` for named params, ``*rest`` for catch-all).
    """
    if not _ROUTES_FILE.exists():
        return []

    text = _ROUTES_FILE.read_text()
    # Match path: '...' or path: "..." inside the routeTable definition.
    path_re = re.compile(r"""path:\s*['"](/[^'"]*?)['"]""")
    routes = sorted(set(m.group(1) for m in path_re.finditer(text)))
    return routes


# ---------------------------------------------------------------------------
# 2. Discover navigation targets
# ---------------------------------------------------------------------------


def _parse_tabs() -> set[str]:
    """Extract tab keys from tabs.ts and map them to route patterns.

    Branch-scoped tabs map to ``/branch/:repo/:branch/<key>``.
    ``home`` maps to ``/``. ``overview`` maps to ``/branches``.
    Other global tabs map to ``/<key>``.
    """
    targets: set[str] = set()
    if not _TABS_FILE.exists():
        return targets

    text = _TABS_FILE.read_text()
    # Match entries like: { key: 'chat', ..., scope: 'branch' }
    # The fields may appear in any order within the object literal.
    for m in re.finditer(
        r"""\{\s*key:\s*['"](\w+)['"].*?scope:\s*['"](\w+)['"]""",
        text,
        re.DOTALL,
    ):
        key, scope = m.group(1), m.group(2)
        if key == "home":
            targets.add("/")
        elif key == "overview":
            targets.add("/branches")
        elif scope == "branch":
            targets.add(f"/branch/:repo/:branch/{key}")
        else:
            targets.add(f"/{key}")
    return targets


def _grep_hrefs(svelte_files: list[Path]) -> set[str]:
    """Extract ``href="..."`` values from .svelte files (internal paths only)."""
    targets: set[str] = set()
    href_re = re.compile(r'href=["\'](/[^"\']*)["\']')
    # Also catch template-literal hrefs like href={`/branch/...`}.
    href_tmpl_re = re.compile(r"href=\{`(/[^`]*)`\}")
    for f in svelte_files:
        text = f.read_text(errors="replace")
        for pattern in (href_re, href_tmpl_re):
            for m in pattern.finditer(text):
                targets.add(m.group(1))
    return targets


def _grep_gotos(svelte_files: list[Path]) -> set[str]:
    """Extract ``goto('...')`` / ``goto(`...`)`` targets."""
    targets: set[str] = set()
    goto_str_re = re.compile(r"goto\(['\"](/[^'\"]*)['\"]")
    goto_tmpl_re = re.compile(r"goto\(`(/[^`]*)`")
    for f in svelte_files:
        text = f.read_text(errors="replace")
        for pattern in (goto_str_re, goto_tmpl_re):
            for m in pattern.finditer(text):
                targets.add(m.group(1))
    return targets


def _grep_window_location(svelte_files: list[Path]) -> set[str]:
    """Extract ``window.location = '...'`` / ``window.location.href = '...'`` targets."""
    targets: set[str] = set()
    loc_re = re.compile(r"window\.location(?:\.href)?\s*=\s*['\"](/[^'\"]*)['\"]")
    for f in svelte_files:
        text = f.read_text(errors="replace")
        for m in loc_re.finditer(text):
            targets.add(m.group(1))
    return targets


def _grep_branch_url_calls(svelte_files: list[Path]) -> set[str]:
    """Detect ``branchUrl()`` / ``localBranchUrl()`` calls and infer target routes.

    - ``branchUrl(qualified)``         -> ``/branch/:repo/:branch``
    - ``branchUrl(qualified, 'tab')``  -> ``/branch/:repo/:branch/tab``
    - ``branchUrl(qualified, key)``    -> ``/branch/:repo/:branch/:tab`` (dynamic)
    """
    targets: set[str] = set()
    call_re = re.compile(r"(?:local)?branchUrl\(([^)]*)\)")
    for f in svelte_files:
        text = f.read_text(errors="replace")
        for m in call_re.finditer(text):
            args_str = m.group(1).strip()
            args = [a.strip() for a in args_str.split(",")]
            if len(args) >= 2:
                tab_arg = args[1].strip().strip("'\"")
                if args[1].strip().startswith(("'", '"')):
                    # String-literal tab -- concrete target.
                    targets.add(f"/branch/${{repo}}/${{branch}}/{tab_arg}")
                else:
                    # Dynamic tab variable -- covers any branch tab.
                    targets.add("/branch/${repo}/${branch}/${tab}")
            else:
                # No tab arg -- branch index route.
                targets.add("/branch/${repo}/${branch}")
    return targets


# ---------------------------------------------------------------------------
# 3. Matching logic
# ---------------------------------------------------------------------------


def _normalize_target(target: str) -> str:
    """Strip query strings and trailing slashes for comparison."""
    target = target.split("?", 1)[0]
    if len(target) > 1 and target.endswith("/"):
        target = target.rstrip("/")
    return target


def _route_to_regex(route: str) -> re.Pattern[str]:
    """Convert a route pattern to a regex that matches concrete URLs.

    ``:param`` matches exactly one path segment; ``*rest`` matches
    one or more segments.  This matches the custom router syntax used
    in ``dashboard/src/lib/router/router.svelte.ts``.
    """
    parts = route.strip("/").split("/")
    regex_parts: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("*"):
            # Rest/catch-all param.
            regex_parts.append(r"[^/]+(?:/[^/]+)*")
        elif part.startswith(":"):
            # Named param.
            regex_parts.append(r"[^/]+")
        else:
            regex_parts.append(re.escape(part))
    pattern = "/" + "/".join(regex_parts) if regex_parts else "/"
    return re.compile(f"^{pattern}$")


def _target_to_regex(target: str) -> re.Pattern[str]:
    """Convert a concrete nav target to a regex.

    Template-literal interpolations (``${...}``) and bare ``{...}`` are
    treated as single wildcard segments.  ``:param`` segments are also
    treated as wildcards (from tab-generated targets).
    """
    target = _normalize_target(target)
    parts = target.strip("/").split("/")
    regex_parts: list[str] = []
    for part in parts:
        if not part:
            continue
        if "${" in part or "{" in part or part.startswith(":"):
            regex_parts.append(r"[^/]+")
        else:
            regex_parts.append(re.escape(part))
    pattern = "/" + "/".join(regex_parts) if regex_parts else "/"
    return re.compile(f"^{pattern}$")


def _is_route_reachable(route: str, targets: set[str]) -> bool:
    """Return True if any navigation target resolves to the given route."""
    route_re = _route_to_regex(route)

    for t in targets:
        norm = _normalize_target(t)

        # Direct static-route match.
        if norm == route:
            return True

        # Target has interpolations -- test both regex directions.
        t_re = _target_to_regex(t)

        # Generate a sample URL from the route by replacing params with a
        # placeholder, then see if the target regex matches it.
        sample = re.sub(r"\*\w+", "PLACEHOLDER", route)
        sample = re.sub(r":\w+", "PLACEHOLDER", sample)
        if t_re.match(sample):
            return True

        # Also check: does the route regex match the concrete target?
        if route_re.match(norm):
            return True

    return False
