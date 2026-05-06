"""Dev server lifecycle: v server, v server stop, v server status, v server check."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from codehome.config import load_server_config, server_config_exists
from codehome.http_client import _resolve_token_file
from codehome.paths import ROOT, SUPERVISOR_DIR, resolve_global
from codehome.serve import DEFAULT_PORT, PORT_FILE, read_server_info, read_server_info_raw, read_server_url
from codehome.utils import die, green, red, yellow


def _can_bind(port: int) -> bool:
    """Return True if we can bind a fresh listener on the port right now.

    Authoritative test: if bind() succeeds, no process is holding the port
    with an active listening socket. We set SO_REUSEADDR to mirror what
    the ASGI server does when starting -- otherwise our probe would reject
    ports that are actually free but in kernel TIME_WAIT cleanup (false
    negative that caused spurious "failed to free port" errors during
    graceful shutdowns).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    """Return True if the pid refers to an existing process.

    Uses signal 0 (no-op) to probe liveness without side effects.
    PermissionError means the pid exists but is owned by another user;
    we treat that as "alive" because the process IS there (we just can't
    signal it -- which will be surfaced later when our SIGTERM fails).
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _descendant_pids(pid: int) -> list[int]:
    """Return the full set of descendant pids (children, grandchildren, ...).

    Uses /proc/PID/task/PID/children which is Linux-specific. On non-Linux
    platforms this returns an empty list and the caller must fall back to
    single-pid signalling (acceptable: the zombie bug we're fixing is Linux
    dev environment specific).
    """
    descendants: list[int] = []
    queue: list[int] = [pid]
    seen: set[int] = set()
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        seen.add(cur)
        try:
            raw = Path(f"/proc/{cur}/task/{cur}/children").read_text().strip()
        except (OSError, FileNotFoundError):
            continue
        for tok in raw.split():
            try:
                child = int(tok)
            except ValueError:
                continue
            if child != pid:
                descendants.append(child)
            queue.append(child)
    return descendants


def _kill_tree(pid: int, sig: int) -> None:
    """Send `sig` to pid and all its descendants. Ignores already-dead pids."""
    # Signal descendants first so the parent doesn't respawn them mid-shutdown.
    for child in _descendant_pids(pid):
        try:
            os.kill(child, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _wait_port_free(port: int, timeout: float) -> bool:
    """Poll until bind() on `port` succeeds, up to `timeout` seconds.

    bind() is the authoritative test: if it succeeds, no process is holding
    the port. Returns True if the port freed, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _can_bind(port):
            return True
        time.sleep(0.05)
    return False


def _find_port_holder_pid(port: int) -> int | None:
    """Best-effort lookup of the pid holding `port` on 127.0.0.1.

    Returns None if we can't determine it. Purely diagnostic -- used to
    improve error messages when bind() fails. Uses `ss` since it doesn't
    require root like `lsof -i` sometimes does.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # Output lines contain: users:(("name",pid=12345,fd=4),...)
    match = re.search(r"pid=(\d+)", out)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _maybe_rebuild() -> None:
    """Rebuild the dashboard frontend if any source file is newer than the build output.

    Compares mtimes of files under dashboard/src/ against the built
    static/index.html. If the build output is missing or stale, runs
    `npm run build` in the dashboard directory. Build failures are
    logged but do not prevent the server from starting.
    """
    import subprocess

    from codehome.plugins import registry as plugin_registry

    dashboard_dir = ROOT / "dashboard"
    src_dir = dashboard_dir / "src"

    # Discover the static output dir from the dashboard plugin if loaded;
    # fall back to the legacy path adjacent to the server module.
    _plugin = plugin_registry.get("dashboard")
    if _plugin is not None:
        _plugin_static = Path(_plugin.plugin_dir) / "static"
        build_output = _plugin_static / "index.html"
    else:
        build_output = ROOT / "src" / "codehome" / "serve" / "static" / "index.html"

    if not src_dir.is_dir():
        print(f"Warning: dashboard source dir not found: {src_dir}")
        return

    needs_rebuild = False
    trigger_file: str | None = None

    if not build_output.exists():
        needs_rebuild = True
        trigger_file = "(build output missing)"
    else:
        build_mtime = build_output.stat().st_mtime
        for path in src_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.stat().st_mtime > build_mtime:
                needs_rebuild = True
                trigger_file = str(path.relative_to(dashboard_dir))
                break

    if not needs_rebuild:
        return

    print(f"Rebuilding dashboard frontend (triggered by: {trigger_file})")
    try:
        subprocess.run(["npm", "run", "build"], cwd=dashboard_dir, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Warning: dashboard build failed (exit {e.returncode}), continuing anyway")
    except FileNotFoundError:
        print("Warning: npm not found, skipping dashboard rebuild")


def _ensure_tls_certs() -> tuple[Path, Path]:
    """Generate localhost TLS certs via mkcert.

    Certs are stored in ~/.codehome/certs/ (or .supervisor/certs/ for legacy
    installs) and reused across restarts. Requires mkcert to be installed.
    """
    import shutil

    from codehome.paths import codehome_home

    # Dual-read: resolve_global checks ~/.codehome/ first, falls back to .supervisor/.
    cert_dir = resolve_global("certs")
    cert = cert_dir / "localhost.pem"
    key = cert_dir / "localhost-key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    if shutil.which("mkcert") is None:
        die(
            "mkcert is required for the server (HTTPS/HTTP2).\n"
            "  Install:  go install filippo.io/mkcert@latest\n"
            "  Then run: mkcert -install\n"
            "  (one-time setup, needs sudo for system trust store)"
        )
    # New certs are always written to ~/.codehome/certs/.
    cert_dir = codehome_home() / "certs"
    cert = cert_dir / "localhost.pem"
    key = cert_dir / "localhost-key.pem"
    cert_dir.mkdir(parents=True, exist_ok=True)
    # Ensure the local CA is installed in the system/browser trust store.
    subprocess.run(["mkcert", "-install"], capture_output=True)
    result = subprocess.run(
        ["mkcert", "-cert-file", str(cert), "-key-file", str(key), "localhost", "127.0.0.1", "::1"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"Failed to generate TLS certs: {result.stderr.strip()}")
    return cert, key


# CDP port file lives alongside the server port file so `v dashboard debug`
# can auto-detect without scanning. Imported here because cmd_server cleans
# it up on exit.
_CDP_PORT_FILE = PORT_FILE.parent / "dashboard.cdp-port"


def cmd_server(args: argparse.Namespace) -> None:
    """Start the dev server (or stop/restart it).

    .. feature:: Branch-aware local dev server (FastAPI + Svelte 5) at http://127.0.0.1:9100
    .. feature:: Register and start Supabase + Vite + Edge Functions per branch with dynamic port allocation
    .. feature:: Supports multiple branches running simultaneously
    .. feature:: Dev mode: Granian auto-reloads on Python changes, Vite HMR for instant Svelte updates
    .. feature:: Test accounts with password123 and phone OTP bypass configured in config.toml
    .. feature:: Seed data covers bag and orders apps (drops and backoffice have no seed data)

    .. rule:: Requires ``v auth setup`` before first use
    .. rule:: Never run ``npx supabase`` directly -- always use the pinned ``npx supabase@2.67.0``
    .. rule:: Never run ``npm run dev`` from inside frontend directories -- use ``v server`` or ``make`` targets
    .. rule:: Vite HMR proxy must forward ``Sec-WebSocket-Protocol`` headers in both directions
    .. rule:: Force Vite subprocess to ``--host 127.0.0.1`` (Node.js 17+ resolves localhost to IPv6)
    .. rule:: Source changes require editable install (``uv tool install --reinstall --editable``)
    """
    # Guard: require server config (created by `v auth setup`).
    if not server_config_exists():
        die("Server not configured. Run 'v auth setup' first.")

    subcmd = getattr(args, "server_command", None)
    if subcmd == "stop":
        _server_stop(
            cleanup=getattr(args, "cleanup", False),
            port_override=getattr(args, "port", None),
        )
        return
    if subcmd == "status":
        _server_status()
        return
    if subcmd == "check":
        _server_check()
        return
    if subcmd == "restart":
        _server_stop(port_override=getattr(args, "port", None))
        # Fall through to start the server.

    # Use port from config as default; --port flag overrides.
    cfg = load_server_config()
    port = args.port if args.port != DEFAULT_PORT else (cfg.port if cfg else args.port)

    # Check if already running via port file + liveness ping.
    existing_url = read_server_url()
    if existing_url:
        print(f"v server is already running on {existing_url}")
        return

    # Port file exists but server not responding -- stale.  Delete it so we
    # can start fresh.  (Happens when the server is force-killed.)
    if PORT_FILE.exists():
        PORT_FILE.unlink()

    # Authoritative "port is free" test: bind() must succeed.  connect()
    # can be fooled by sockets held by a zombie reloader parent whose
    # accept loop is stopped -- that's the exact failure mode this test
    # catches.  Fail loudly (with the PID if we can find it) so the user
    # knows what to kill.
    if not _can_bind(port):
        holder = _find_port_holder_pid(port)
        hint = f" (held by pid {holder})" if holder else ""
        die(f"port {port} is already in use{hint}")

    dev = getattr(args, "dev", False)
    cert_path, key_path = _ensure_tls_certs()
    url = f"https://127.0.0.1:{port}"

    # Acquire an exclusive lock on the port file to prevent two concurrent
    # launches from racing.  The lock is released as soon as the port is
    # written -- we don't need to hold it during the server's lifetime because
    # a second launcher would fail via _can_bind() above once the server is
    # up.  Keeping the lock across Granian.serve() is also undesirable in dev
    # mode: reload spawns worker subprocesses, and holding kernel locks
    # across that boundary invites subtle hangs.
    #
    # We record our own pid here.  In reload mode, this process is the
    # Granian reloader that owns the listening socket; in stable mode,
    # this process becomes the Granian server itself.  Either way,
    # signalling this pid is the correct way for an external CLI to stop
    # the server cleanly.
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(PORT_FILE, "w")  # noqa: SIM115, PTH123 -- need fd for flock
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("another v server instance is starting up")
        lock_fd.write(json.dumps({"port": port, "pid": os.getpid(), "scheme": "https"}))
        lock_fd.flush()
    finally:
        # Closing the fd releases the flock.  The port file itself stays on
        # disk so `v server status` / `read_server_url()` can find the server.
        lock_fd.close()

    # In dev mode (opt-in via --dev), set env var so the server knows to
    # proxy to Vite instead of serving static files.
    if dev:
        os.environ["V_SERVER_DEV"] = "1"

    # Rebuild frontend if source files are newer than the build output.
    # Skipped in dev mode (Vite HMR serves sources directly) and when
    # --skip-rebuild is passed.
    skip_rebuild = getattr(args, "skip_rebuild", False)
    if not dev and not skip_rebuild:
        _maybe_rebuild()

    mode_label = "dev (Granian reload + Vite HMR, HTTPS)" if dev else "stable (HTTPS)"
    print(f"v server: {url}  [{mode_label}]")

    try:
        from granian import Granian
        from granian.constants import HTTPModes, Interfaces
        from granian.log import LogLevels

        reload = dev
        reload_paths = [Path(__file__).resolve().parent.parent / "serve"] if reload else None

        server = Granian(
            "codehome.serve.server:app",
            address="127.0.0.1",
            port=port,
            interface=Interfaces.ASGI,
            http=HTTPModes.auto,
            ssl_cert=cert_path,
            ssl_key=key_path,
            log_level=LogLevels.warning,
            reload=reload,
            reload_paths=reload_paths,
        )
        server.serve()
    finally:
        # Clean up port file and CDP port file when Granian exits.
        for f in (PORT_FILE, _CDP_PORT_FILE):
            try:
                f.unlink()
            except FileNotFoundError:
                pass


def _server_status() -> None:
    """Show status of the running server instance."""
    existing_url = read_server_url()
    if not existing_url:
        print("v server is not running.")
        return

    from codehome import http_client

    try:
        info = http_client.get("/api/server/info", timeout=3)
        uptime = int(info.get("uptime", 0))
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"

        services_resp: Any = http_client.get("/api/services", timeout=3)
        services: list[dict[str, Any]] = services_resp if isinstance(services_resp, list) else []
        running = sum(1 for s in services if s.get("state") == "running")

        print(f"URL:      {existing_url}")
        print(f"Uptime:   {uptime_str}")
        print(f"Clients:  {info.get('clients', '?')}")
        print(f"Services: {len(services)} ({running} running)")
    except SystemExit:
        # http_client.die() calls sys.exit -- translate to a friendlier message
        print(f"v server appears to be running at {existing_url} but is not responding.")


def _server_check() -> None:
    """Run diagnostic checks against the running server instance."""
    base_url = read_server_url()
    if not base_url:
        die("v server is not running.")

    tf = _resolve_token_file()
    token = tf.read_text().strip() if tf.exists() else None

    # Discover the static dir from the dashboard plugin (same pattern as
    # _maybe_rebuild and static_files.py); fall back to legacy path.
    from codehome.plugins import registry as plugin_registry

    _plugin = plugin_registry.get("dashboard")
    if _plugin is not None:
        static_dir = Path(_plugin.plugin_dir) / "static"
    else:
        static_dir = Path(__file__).resolve().parent.parent / "serve" / "static"

    results: list[tuple[str, str, str]] = []  # (name, status, detail)

    # -- Check 1: Server alive -------------------------------------------------
    results.append(_check_ping(base_url))

    # -- Check 2: Chunk integrity ----------------------------------------------
    results.append(_check_chunks(base_url))

    # -- Check 3: Frontend errors (auth required) ------------------------------
    results.append(_check_frontend_errors(base_url, token))

    # -- Check 4: Build freshness ----------------------------------------------
    results.append(_check_build_freshness(base_url, token, static_dir))

    # -- Check 5: System health (auth required) --------------------------------
    results.append(_check_system_health(base_url, token))

    # -- Print table -----------------------------------------------------------
    _print_results(results)

    # Exit non-zero if any check failed.
    if any(status == "FAIL" for _, status, _ in results):
        sys.exit(1)


def _server_stop(*, cleanup: bool = False, port_override: int | None = None) -> None:
    """Stop a running server instance.

    Authoritative (not best-effort): on return, the port is guaranteed
    free or an error was raised. This is what makes `v server restart`
    safe -- the subsequent start can trust the port state.

    Stop sequence:
      1. If --cleanup, POST /api/shutdown/cleanup to run async service
         teardown inside the server process. The endpoint no longer
         self-kills; the CLI handles process lifecycle.
      2. Send SIGTERM to the recorded pid (the Granian reloader in dev
         mode, or the server process in stable mode). This triggers a
         clean shutdown (lifespan teardown, Vite cleanup, etc.).
      3. Poll bind() on the port until it frees.
      4. If it doesn't free within the grace period, SIGKILL the process
         tree (pid + descendants) and poll again.
      5. If still stuck, die with a clear diagnostic.
    """
    # Prefer the port file (records both port + pid from the process that
    # started the server -- the only reliable way to kill the right pid
    # under reload mode). Liveness is irrelevant: if the port file is
    # there we want to tear down whatever it points at, responsive or
    # not (zombie reloader parent case).
    info = read_server_info_raw()
    port: int | None = None
    pid: int | None = None
    if info is not None:
        port, pid = info
    elif port_override is not None:
        port = port_override
        # Try to locate who's holding the port so we can kill it even when
        # the port file is missing/stale (e.g. previous run crashed).
        pid = _find_port_holder_pid(port)

    if port is None:
        if PORT_FILE.exists():
            PORT_FILE.unlink()
            print("Removed stale port file (server was not running).")
        else:
            print("v server is not running.")
        return

    existing_url = f"http://127.0.0.1:{port}"  # best-effort display; stop works regardless of scheme

    # Optional: run async service cleanup inside the server before we signal
    # it. Only attempt the HTTP call if the server is actually responsive
    # (liveness-checked); a zombie won't answer and we'd hang. The endpoint
    # itself only performs cleanup -- it no longer self-kills, since
    # self-SIGTERM in reload mode hits the worker and leaves the parent
    # zombie.
    cleaned_count: int | None = None
    if cleanup and read_server_info() is not None:
        from codehome import http_client

        try:
            data = http_client.post("/api/shutdown/cleanup", timeout=30)
            cleaned_count = data.get("cleaned", 0)
        except SystemExit:
            # http_client.die() calls sys.exit -- we still proceed to
            # signal-kill; cleanup is best-effort once the server is
            # unreachable.
            print(f"Warning: cleanup endpoint unreachable at {existing_url}; proceeding with shutdown")

    # Signal the recorded process directly. If we don't know the pid (stale
    # state), fall back to whatever ss tells us is holding the port.
    target_pid = pid if pid is not None else _find_port_holder_pid(port)

    if target_pid is None:
        # Nothing to signal -- check if the port is actually occupied.
        if _can_bind(port):
            # Port is free already; just clean up the port file.
            if PORT_FILE.exists():
                PORT_FILE.unlink()
            print(f"Server at {existing_url} was not running.")
            return
        die(f"port {port} is in use but owner could not be determined; kill it manually")

    if _pid_alive(target_pid):
        _kill_tree(target_pid, signal.SIGTERM)

    # Wait for the kernel to release the port. 5s is generous for a clean
    # shutdown; Vite teardown is the slowest step and takes <2s.
    if not _wait_port_free(port, timeout=5.0):
        # Graceful shutdown didn't free the port. Escalate.
        if _pid_alive(target_pid):
            _kill_tree(target_pid, signal.SIGKILL)
        if not _wait_port_free(port, timeout=3.0):
            # Something else (orphaned child, different process) still
            # holds it. Surface this clearly rather than silently failing.
            holder = _find_port_holder_pid(port)
            hint = f" (now held by pid {holder})" if holder else ""
            die(f"failed to free port {port} within timeout{hint}")

    # Remove the port file -- server is definitively down.
    if PORT_FILE.exists():
        try:
            PORT_FILE.unlink()
        except FileNotFoundError:
            pass

    if cleaned_count is not None:
        print(f"Stopped {cleaned_count} service(s) and shut down server at {existing_url}")
    else:
        print(f"Stopped server at {existing_url}")


# -- Check helpers ------------------------------------------------------------


def _http_get(url: str, token: str | None = None, timeout: int = 5) -> tuple[int, dict[str, str], bytes]:
    """Low-level GET returning (status, headers_dict, body_bytes).

    Does not call die() -- callers handle errors themselves.
    """
    import ssl

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    # Use unverified SSL for localhost HTTPS (mkcert self-signed certs).
    ssl_ctx: ssl.SSLContext | None = None
    if url.startswith("https://"):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, hdrs, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        return e.code, hdrs, body
    except (urllib.error.URLError, OSError) as e:
        raise ConnectionError(str(e)) from e


def _check_ping(base_url: str) -> tuple[str, str, str]:
    try:
        status, _, body = _http_get(f"{base_url}/api/ping")
        data = json.loads(body)
        if status == 200 and data.get("ok"):
            return ("server alive", "PASS", "200 OK")
        return ("server alive", "FAIL", f"unexpected response: {status} {body[:80].decode('utf-8', errors='replace')}")
    except ConnectionError as e:
        return ("server alive", "FAIL", f"connection refused: {e}")
    except (json.JSONDecodeError, KeyError):
        return ("server alive", "FAIL", "bad response from server (not JSON)")


def _check_chunks(base_url: str) -> tuple[str, str, str]:
    """Fetch index.html, find JS chunk refs, verify each returns JS content."""
    try:
        status, _, body = _http_get(f"{base_url}/")
        if status != 200:
            return ("chunk integrity", "FAIL", f"index.html returned {status}")
    except ConnectionError as e:
        return ("chunk integrity", "FAIL", f"cannot reach server: {e}")

    html = body.decode("utf-8", errors="replace")
    # Match all /_app/immutable/ JS references: href/src attributes and
    # import() calls in inline scripts.  Mirrors check-build-integrity.
    chunk_refs = re.findall(r'(?:src|href)="(/_app/immutable/[^"]*\.js)"', html)
    chunk_refs += re.findall(r'import\("(/_app/immutable/[^"]*\.js)"\)', html)
    # Deduplicate: entry chunks appear in both modulepreload links and import() calls.
    chunk_refs = list(dict.fromkeys(chunk_refs))
    if not chunk_refs:
        return ("chunk integrity", "WARN", "no JS chunks found in index.html")

    bad: list[str] = []
    for ref in chunk_refs:
        try:
            st, hdrs, _ = _http_get(f"{base_url}{ref}")
            content_type = hdrs.get("content-type", "")
            if st != 200:
                bad.append(f"{ref} -> {st}")
            elif "javascript" not in content_type:
                # SPA fallback returns text/html for missing files.
                bad.append(f"{ref} -> {content_type}")
        except ConnectionError:
            bad.append(f"{ref} -> unreachable")

    total = len(chunk_refs)
    if bad:
        return ("chunk integrity", "FAIL", f"{len(bad)}/{total} bad: {', '.join(bad[:3])}")
    return ("chunk integrity", "PASS", f"{total}/{total} chunks valid")


def _check_frontend_errors(base_url: str, token: str | None) -> tuple[str, str, str]:
    if not token:
        return ("frontend errors", "SKIP", "login required")
    try:
        status, _, body = _http_get(f"{base_url}/api/diagnostics/errors", token=token)
        if status == 401:
            return ("frontend errors", "SKIP", "login required")
        data = json.loads(body)
        errors = data.get("errors", [])
        count = len(errors)
        if count == 0:
            return ("frontend errors", "PASS", "0 errors captured")
        # Show first error message as preview.
        first = errors[0].get("message", "unknown")[:60]
        return ("frontend errors", "FAIL", f"{count} error(s): {first}")
    except ConnectionError as e:
        return ("frontend errors", "FAIL", f"cannot reach server: {e}")
    except (json.JSONDecodeError, KeyError):
        return ("frontend errors", "FAIL", "bad response from server")


def _check_build_freshness(
    base_url: str,
    token: str | None,
    static_dir: Path,
) -> tuple[str, str, str]:
    index_html = static_dir / "index.html"
    if not index_html.exists():
        return ("build freshness", "WARN", "static/index.html not found on disk")

    build_mtime = index_html.stat().st_mtime

    if not token:
        return ("build freshness", "SKIP", "login required")
    try:
        status, _, body = _http_get(f"{base_url}/api/server/info", token=token)
        if status == 401:
            return ("build freshness", "SKIP", "login required")
        data = json.loads(body)
        uptime = data.get("uptime", 0)
        # Server started at = now - uptime.
        server_started = time.time() - uptime
        if build_mtime > server_started:
            return ("build freshness", "WARN", "dashboard rebuilt after server started -- run: v server restart")
        return ("build freshness", "PASS", "server started after latest build")
    except ConnectionError as e:
        return ("build freshness", "FAIL", f"cannot reach server: {e}")
    except (json.JSONDecodeError, KeyError):
        return ("build freshness", "FAIL", "bad response from server")


def _check_system_health(base_url: str, token: str | None) -> tuple[str, str, str]:
    if not token:
        return ("system health", "SKIP", "login required")
    try:
        status, _, body = _http_get(f"{base_url}/api/system/health", token=token)
        if status == 401:
            return ("system health", "SKIP", "login required")
        checks = json.loads(body)
        # Summarize: count pass/warn/fail.
        counts = {"pass": 0, "warn": 0, "fail": 0}
        for c in checks:
            s = c.get("status", "fail")
            counts[s] = counts.get(s, 0) + 1
        total = len(checks)
        if counts["fail"] > 0:
            failed = [c["name"] for c in checks if c.get("status") == "fail"]
            return ("system health", "FAIL", f"{counts['fail']}/{total} failed: {', '.join(failed[:3])}")
        if counts["warn"] > 0:
            warned = [c["name"] for c in checks if c.get("status") == "warn"]
            return ("system health", "WARN", f"{counts['warn']}/{total} warnings: {', '.join(warned[:3])}")
        return ("system health", "PASS", f"{total}/{total} checks passed")
    except ConnectionError as e:
        return ("system health", "FAIL", f"cannot reach server: {e}")
    except (json.JSONDecodeError, KeyError):
        return ("system health", "FAIL", "bad response from server")


def _print_results(results: list[tuple[str, str, str]]) -> None:
    """Print check results as an aligned, color-coded table."""
    name_w = max(len(name) for name, _, _ in results)
    status_w = max(len(status) for _, status, _ in results)

    color_fn = {"PASS": green, "WARN": yellow, "FAIL": red, "SKIP": yellow}

    for name, status, detail in results:
        colored_status = color_fn.get(status, str)(status)
        # Pad the raw status text, then replace with colored version.
        padded = f"{status:<{status_w}}"
        colored_padded = padded.replace(status, colored_status, 1)
        print(f"  {name:<{name_w}}  {colored_padded}  {detail}")
