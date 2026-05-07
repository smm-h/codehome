"""PTY session management for embedded terminals.

Manages pseudo-terminal sessions that back the dashboard's Terminal tab.
Each session spawns a bash process via pty.fork(), streams I/O over
WebSockets, and maintains a scrollback buffer for reconnection.

Supports shared sessions: when sharing is enabled on a session, multiple
WebSocket clients can connect simultaneously for pair programming.
A single reader task per session reads PTY output and broadcasts to all
connected WebSockets, avoiding fd read races.
"""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
import time
from collections import deque
from dataclasses import dataclass, field

from starlette.websockets import WebSocket

# Lines of output to buffer so a reconnecting client gets recent history.
SCROLLBACK_SIZE = 5000


@dataclass
class PTYConnection:
    """A single WebSocket connection to a PTY session."""

    websocket: WebSocket
    user: str
    connected_at: float = field(default_factory=time.time)


@dataclass
class PTYSession:
    session_id: str
    pid: int
    fd: int  # master file descriptor
    user: str  # owner
    branch: str
    cwd: str
    shared: bool = False
    # Set to True when destroy() is called so handlers know to exit.
    destroyed: bool = False
    scrollback: deque[bytes] = field(default_factory=lambda: deque(maxlen=SCROLLBACK_SIZE))
    # All active WebSocket connections to this session.
    connections: list[PTYConnection] = field(default_factory=list)
    # Protects concurrent writes to the connections list.
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Single reader task that broadcasts PTY output to all connections.
    # Managed by PTYManager.ensure_reader / PTYManager.stop_reader_if_empty.
    _reader_task: asyncio.Task[None] | None = field(default=None, repr=False)


class PTYManager:
    """Create, track, resize, and destroy PTY sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, PTYSession] = {}

    def create(self, session_id: str, cwd: str, user: str, branch: str) -> PTYSession:
        """Spawn a new bash process in a PTY.

        Uses pty.fork() which handles setsid/dup2 on the child side.
        Returns (pid, fd) where pid=0 in the child process.
        """
        pid, fd = pty.fork()

        if pid == 0:
            # -- Child process --
            # Change to the requested working directory, then exec bash.
            try:
                os.chdir(cwd)
            except OSError:
                # Fall back to home if the directory doesn't exist.
                os.chdir(os.path.expanduser("~"))

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            os.execvpe("bash", ["bash", "--login"], env)
            # execvpe never returns; if it fails the child exits.

        # -- Parent process --
        # Set non-blocking would complicate the read loop; we use
        # run_in_executor with blocking reads instead (simpler, safer).
        session = PTYSession(
            session_id=session_id,
            pid=pid,
            fd=fd,
            user=user,
            branch=branch,
            cwd=cwd,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> PTYSession | None:
        """Look up a session by ID."""
        return self._sessions.get(session_id)

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        """Send a TIOCSWINSZ ioctl to resize the PTY."""
        session = self._sessions.get(session_id)
        if session:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(session.fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    async def destroy(self, session_id: str) -> None:
        """Kill the child process, close all connections, and close the master fd."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        # Signal handlers to exit their loops.
        session.destroyed = True

        # Cancel the reader task if running.
        if session._reader_task and not session._reader_task.done():
            session._reader_task.cancel()

        # Close all WebSocket connections so handlers exit cleanly.
        # Snapshot the list to avoid mutation during iteration.
        conns = list(session.connections)
        session.connections.clear()
        for conn in conns:
            try:
                await conn.websocket.close(code=1001)
            except Exception:
                pass

        try:
            os.kill(session.pid, signal.SIGTERM)
        except OSError:
            pass

        # Reap the child to avoid zombies.
        try:
            os.waitpid(session.pid, os.WNOHANG)  # noqa: ASYNC222 -- WNOHANG is non-blocking
        except (OSError, ChildProcessError):
            pass

        try:
            os.close(session.fd)
        except OSError:
            pass

    def list_sessions(self, user: str | None = None) -> list[dict[str, object]]:
        """List active sessions, optionally filtered by user."""
        sessions_list: list[PTYSession] = list(self._sessions.values())
        if user is not None:
            sessions_list = [s for s in sessions_list if s.user == user]
        return [
            {
                "session_id": s.session_id,
                "branch": s.branch,
                "cwd": s.cwd,
                "shared": s.shared,
            }
            for s in sessions_list
        ]

    def set_shared(self, session_id: str, shared: bool) -> bool:
        """Enable or disable sharing on a session. Returns True if session exists."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.shared = shared
        return True

    async def add_connection(self, session: PTYSession, ws: WebSocket, user: str) -> PTYConnection:
        """Register a WebSocket connection to a session."""
        conn = PTYConnection(websocket=ws, user=user)
        async with session._lock:
            session.connections.append(conn)
        return conn

    async def remove_connection(self, session: PTYSession, conn: PTYConnection) -> None:
        """Unregister a WebSocket connection from a session."""
        async with session._lock:
            try:
                session.connections.remove(conn)
            except ValueError:
                pass

    async def disconnect_non_owners(self, session: PTYSession) -> list[str]:
        """Close all connections from non-owner users. Returns list of disconnected usernames.

        Collects connections to disconnect under the lock, then closes them
        OUTSIDE the lock to avoid deadlock with remove_connection (which also
        acquires session._lock from the WebSocket handler's finally block).
        """
        to_disconnect: list[PTYConnection] = []
        async with session._lock:
            remaining: list[PTYConnection] = []
            for conn in session.connections:
                if conn.user != session.user:
                    to_disconnect.append(conn)
                else:
                    remaining.append(conn)
            session.connections = remaining

        # Close OUTSIDE the lock to avoid deadlock.
        disconnected: list[str] = []
        for conn in to_disconnect:
            disconnected.append(conn.user)
            try:
                await conn.websocket.close(code=1000)
            except Exception:
                pass
        return disconnected

    def get_connections(self, session_id: str) -> list[dict[str, object]]:
        """Return info about who is connected to a session."""
        session = self._sessions.get(session_id)
        if not session:
            return []
        return [{"user": c.user, "connected_at": c.connected_at} for c in session.connections]

    async def broadcast_to_session(self, session: PTYSession, data: bytes) -> None:
        """Send PTY output bytes to all connected WebSockets."""
        async with session._lock:
            dead: list[PTYConnection] = []
            for conn in session.connections:
                try:
                    await conn.websocket.send_bytes(data)
                except Exception:
                    dead.append(conn)
            # Remove dead connections outside the iteration.
            for conn in dead:
                try:
                    session.connections.remove(conn)
                except ValueError:
                    pass

    def ensure_reader(self, session: PTYSession) -> None:
        """Start the per-session PTY reader task if not already running.

        The reader runs in a background asyncio task, reading from the PTY fd
        and broadcasting to all connected WebSockets. Only one reader per
        session exists at a time to avoid fd read races.
        """
        if session._reader_task and not session._reader_task.done():
            return
        session._reader_task = asyncio.create_task(self._read_loop(session))

    async def _read_loop(self, session: PTYSession) -> None:
        """Continuous PTY reader: reads output and broadcasts to all connections."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    data = await loop.run_in_executor(None, os.read, session.fd, 4096)
                    if not data:
                        break
                except OSError:
                    break

                # deque(maxlen=SCROLLBACK_SIZE) auto-trims old entries on append.
                session.scrollback.append(data)

                await self.broadcast_to_session(session, data)
        except asyncio.CancelledError:
            pass

    def stop_reader_if_empty(self, session: PTYSession) -> None:
        """Cancel the reader task if no connections remain."""
        if not session.connections and session._reader_task and not session._reader_task.done():
            session._reader_task.cancel()
            session._reader_task = None

    async def cleanup_all(self) -> None:
        """Destroy every active session. Called on server shutdown."""
        for sid in list(self._sessions):
            await self.destroy(sid)


# Module-level singleton -- shared by the server endpoints and WebSocket handler.
pty_manager = PTYManager()
