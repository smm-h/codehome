"""Unified dispatch helper for commands that have a server route.

A handful of `v` CLI subcommands have two code paths:

1. **Server route** -- if `v server` is running, forward the request over HTTP
   to the server, which runs the work in its own long-lived process (reusing
   SSH transport, central service registry, SSE event fan-out, etc.).
2. **Local route** -- if `v server` is not running, execute the work in-process.

Historically each such command reimplemented the "is the server running?"
probe and called one of two internal helpers. That duplication caused real bugs
(stale modules in the server's memory diverging from fresh source on disk)
and made each new dual-path command copy-paste the same plumbing.

This module exposes a single `dispatched()` helper that encapsulates the
routing decision. Callers provide two callables -- one for each path -- and the
helper decides which to invoke based on server liveness.

Two shapes are supported because the commands don't all have the same shape:

- Simple request-response commands (e.g. `v telemac screen start`, `v services delete-volumes`)
  have a trivial server call: POST/GET, parse JSON, return. Both paths can
  be trivially wrapped as callables and the helper picks one.
- Streaming / long-running commands (e.g. `v tests red-green`) have a server call that
  is intrinsically more complex than the local one (it streams SSE events from
  a shared orchestrator). The same helper still works: the server callable
  contains the SSE streaming logic, the local callable contains the in-process
  runner. The helper just decides which to run.

The helper is intentionally a thin shim -- it does not transform payloads or
interpret results. Each call site still writes exactly the network and local
logic it needs; the helper only centralises the "which path?" decision. This
makes it easy to add transparency logging (see TODO in the body of this file)
in one place later without touching callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


def server_running() -> bool:
    """Return True if `v server` is running and reachable.

    Thin wrapper around `codehome.serve.read_server_url()` that only returns
    a boolean. Callers that also need the URL should use `server_url()`.
    """
    return server_url() is not None


def server_url() -> str | None:
    """Return the running server's base URL, or None if it isn't running.

    Performs a liveness check via /api/ping -- a stale port file on disk will
    not falsely report a running server.
    """
    # Imported lazily so importing this module does not trigger the HTTP probe
    # in `read_server_url()` during test collection or `--help` runs.
    from codehome.serve import read_server_url

    return read_server_url()


def dispatched(
    *,
    server_impl: Callable[[], T],
    local_impl: Callable[[], T],
) -> T:
    """Run `server_impl` if the server is up, else run `local_impl`.

    Both callables must have the same return shape -- whatever the CLI command
    wants to return -- because the caller cannot tell from the return value
    which path ran (by design: that's the whole point).

    Raises and exit codes propagate unchanged from whichever impl ran.

    Usage:
        return dispatched(
            server_impl=lambda: api_start_screen(),
            local_impl=lambda: start_screen_local(args),
        )

    Note: transparency logging for the server path lives in
    `codehome.http_client._emit_routing_banner()` -- the single choke point
    every server-routed HTTP call passes through. Dispatch sits above
    that; putting the banner here would miss direct http_client consumers
    (e.g. `v server stop --cleanup`).
    """
    if server_running():
        return server_impl()
    return local_impl()
