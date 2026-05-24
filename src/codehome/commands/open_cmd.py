"""Open the codehome dashboard in a native desktop window or browser.

Starts the server if not already running, then opens the dashboard.
The native window uses pywebview; --browser falls back to the default
browser. When the native window is closed, the server is stopped
(unless it was already running before `open` was invoked).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import webbrowser

from codehome.config import server_config_exists
from codehome.serve import PORT_FILE, read_server_url
from codehome.utils import die


def _wait_for_server(timeout: float = 30.0) -> str:
    """Block until the server is reachable. Returns the server URL."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = read_server_url()
        if url:
            return url
        time.sleep(0.3)
    die(f"Server did not start within {timeout:.0f}s")
    return ""  # unreachable, but keeps mypy happy


def _start_server_subprocess() -> subprocess.Popen[bytes]:
    """Spawn `v server` as a background subprocess."""
    return subprocess.Popen(
        [sys.executable, "-m", "codehome", "server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cmd_open(args: argparse.Namespace) -> None:
    """Open the codehome dashboard.

    .. feature:: Open dashboard in a native desktop window (pywebview)
    .. feature:: --browser flag opens in default browser instead
    .. feature:: Auto-starts the server if not already running
    .. feature:: Server shutdown on native window close (if started by this command)
    """
    if not server_config_exists():
        die("Server not configured. Run 'v auth setup' first.")

    use_browser = getattr(args, "browser", False)

    # Check if server is already running.
    existing_url = read_server_url()
    server_was_running = existing_url is not None
    server_proc: subprocess.Popen[bytes] | None = None

    if existing_url:
        url = existing_url
    else:
        # Start the server as a subprocess.
        print("Starting server...")
        server_proc = _start_server_subprocess()
        url = _wait_for_server()
        print(f"Server running at {url}")

    if use_browser:
        print(f"Opening {url} in browser...")
        webbrowser.open(url)
        if server_proc is not None:
            print("Server is running in the background. Stop it with: v server stop")
        return

    # Native desktop window via pywebview.
    try:
        import webview
    except ImportError:
        die(
            "pywebview is required for native desktop mode.\n"
            "  Install: pip install pywebview\n"
            "  Or use: v open --browser"
        )
        return  # unreachable

    print(f"Opening dashboard at {url}")
    webview.create_window("codehome", url, width=1400, height=900)
    webview.start()

    # Window was closed. Stop the server if we started it.
    if server_proc is not None and not server_was_running:
        print("Window closed. Stopping server...")
        # Use the CLI stop command for clean shutdown.
        try:
            subprocess.run(
                [sys.executable, "-m", "codehome", "server", "stop"],
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            # Force kill if stop hangs.
            if server_proc.poll() is None:
                server_proc.kill()
        # Clean up port file.
        try:
            PORT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        print("Server stopped.")
