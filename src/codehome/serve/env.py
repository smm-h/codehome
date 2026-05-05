"""Environment variable resolution for Docker containers."""

from __future__ import annotations

from codehome.serve.services import State, services


def resolve_supabase_env(branch: str) -> dict[str, int | str] | None:
    """Get Supabase connection details for a branch from the running instance.

    Returns {api_port, anon_key, service_role_key} or None if not available.
    """
    supabase_key = f"{branch}/supabase"
    svc = services.get(supabase_key)
    if not svc or svc.state != State.RUNNING:
        return None
    conn = svc.metadata.get("connection", {})
    if not conn:
        return None
    # Extract port from API URL (e.g. "http://127.0.0.1:54321" -> 54321).
    api_url = conn.get("api_url", "")
    try:
        api_port = int(api_url.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        api_port = 54321
    return {
        "api_port": api_port,
        "anon_key": conn.get("anon_key", ""),
        "service_role_key": conn.get("service_role_key", ""),
    }
