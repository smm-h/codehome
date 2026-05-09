"""Tests for codehome.serve.supabase — pure-logic and subprocess-calling functions."""

from __future__ import annotations

import json
import re
import subprocess
from unittest.mock import MagicMock, patch

from codehome.serve.supabase import (
    _DEFAULT_SUPABASE_VERSION,
    TIMEOUT_SB_MIGRATE,
    TIMEOUT_SB_START,
    TIMEOUT_SB_STATUS,
    TIMEOUT_SB_STOP,
    apply_migrations,
    extract_connection_details,
    patch_config,
    project_id_for,
    start,
    status,
    stop,
    supabase_cmd,
)

# ---------------------------------------------------------------------------
# Realistic config.toml used by patch_config tests
# ---------------------------------------------------------------------------
REALISTIC_CONFIG = """\
project_id = "original"

[api]
enabled = true
port = 54321

[db]
port = 54322
shadow_port = 54320

[db.pooler]
enabled = true
port = 54329

[studio]
port = 54323

[inbucket]
port = 54324
smtp_port = 54325
pop3_port = 54326
"""


# ── project_id_for ────────────────────────────────────────────────────────


class TestProjectIdFor:
    """Tests for deterministic project-ID generation."""

    def test_deterministic(self):
        """Same input always produces the same output."""
        assert project_id_for("bag:fix-auth") == project_id_for("bag:fix-auth")

    def test_hex_20_chars(self):
        """Output is exactly 20 hex characters."""
        pid = project_id_for("bag:fix-auth")
        assert len(pid) == 20
        assert re.fullmatch(r"[0-9a-f]{20}", pid)

    def test_different_branches_differ(self):
        """Different branch names produce different IDs."""
        assert project_id_for("bag:fix-auth") != project_id_for("bag:add-search")


# ── patch_config ──────────────────────────────────────────────────────────


def _write_config(tmp_path: object, content: str = REALISTIC_CONFIG) -> object:
    """Write a config.toml under tmp_path/supabase/ and return the path."""
    cfg_dir = tmp_path / "supabase"  # type: ignore[operator]
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.toml"
    cfg_file.write_text(content)
    return cfg_file


class TestPatchConfig:
    """Tests for section-aware config patching."""

    def test_replaces_project_id(self, tmp_path):
        cfg = _write_config(tmp_path)
        patch_config(tmp_path, {}, "newid123")
        text = cfg.read_text()
        assert 'project_id = "newid123"' in text
        assert "original" not in text

    def test_section_aware_port_replacement(self, tmp_path):
        """THE CRITICAL TEST: each section gets its own unique port value.

        A naive regex that replaces all ``port = ...`` lines with one value
        would make every section share the same port. This test catches that
        by patching each section with a distinct port and verifying them
        independently by parsing the TOML line-by-line.
        """
        cfg = _write_config(tmp_path)
        slot_ports = {
            "api_port": 10001,
            "db_port": 10002,
            "pooler_port": 10003,
            "studio_port": 10004,
            "inbucket_port": 10005,
        }
        result = patch_config(tmp_path, slot_ports, "patched")
        assert result is True

        # Parse the output to find port values per section.
        lines = cfg.read_text().splitlines()
        section_ports: dict[str, int] = {}
        current_section: str | None = None
        for line in lines:
            sec_match = re.match(r"^\[(.+)\]", line)
            if sec_match:
                current_section = sec_match.group(1)
                continue
            port_match = re.match(r"^\s*port\s*=\s*(\d+)", line)
            if port_match and current_section is not None:
                section_ports[current_section] = int(port_match.group(1))

        assert section_ports["api"] == 10001
        assert section_ports["db"] == 10002
        assert section_ports["db.pooler"] == 10003
        assert section_ports["studio"] == 10004
        assert section_ports["inbucket"] == 10005

        # All ports must be distinct (the whole point of section awareness).
        values = list(section_ports.values())
        assert len(values) == len(set(values)), f"Port values are not unique across sections: {section_ports}"

    def test_preserves_non_port_content(self, tmp_path):
        """Non-port lines must survive patching unchanged."""
        cfg = _write_config(tmp_path)
        patch_config(tmp_path, {"api_port": 10001}, "pid")
        text = cfg.read_text()
        assert "enabled = true" in text
        assert "[api]" in text
        assert "[db]" in text
        assert "[db.pooler]" in text
        assert "[studio]" in text
        assert "[inbucket]" in text

    def test_returns_false_on_missing_config(self, tmp_path):
        """Must return False when config.toml does not exist."""
        # tmp_path exists but has no supabase/ subdirectory.
        assert patch_config(tmp_path, {}, "x") is False

    def test_handles_unique_keys(self, tmp_path):
        """shadow_port, smtp_port, pop3_port must be patched correctly."""
        cfg = _write_config(tmp_path)
        slot_ports = {
            "shadow_port": 20001,
            "inbucket_smtp": 20002,
            "inbucket_pop3": 20003,
        }
        patch_config(tmp_path, slot_ports, "uid")
        text = cfg.read_text()

        # Parse section-aware values for these unique keys.
        lines = text.splitlines()
        current_section: str | None = None
        found: dict[str, int] = {}
        for line in lines:
            sec_match = re.match(r"^\[(.+)\]", line)
            if sec_match:
                current_section = sec_match.group(1)
                continue
            for key in ("shadow_port", "smtp_port", "pop3_port"):
                m = re.match(rf"^\s*{key}\s*=\s*(\d+)", line)
                if m:
                    found[f"{current_section}/{key}"] = int(m.group(1))

        assert found["db/shadow_port"] == 20001
        assert found["inbucket/smtp_port"] == 20002
        assert found["inbucket/pop3_port"] == 20003


# ── extract_connection_details ────────────────────────────────────────────


class TestExtractConnectionDetails:
    """Tests for status dict -> connection details mapping."""

    def test_maps_keys_correctly(self):
        status_dict = {
            "API_URL": "http://127.0.0.1:54321",
            "DB_URL": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
            "STUDIO_URL": "http://127.0.0.1:54323",
            "GRAPHQL_URL": "http://127.0.0.1:54321/graphql/v1",
            "ANON_KEY": "eyJ0eXAi.anon",
            "SERVICE_ROLE_KEY": "eyJ0eXAi.service",
        }
        result = extract_connection_details(status_dict)
        assert result == {
            "api_url": "http://127.0.0.1:54321",
            "db_url": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
            "studio_url": "http://127.0.0.1:54323",
            "graphql_url": "http://127.0.0.1:54321/graphql/v1",
            "anon_key": "eyJ0eXAi.anon",
            "service_role_key": "eyJ0eXAi.service",
        }

    def test_handles_missing_keys(self):
        """Missing keys in the status dict must default to empty strings."""
        result = extract_connection_details({})
        assert result == {
            "api_url": "",
            "db_url": "",
            "studio_url": "",
            "graphql_url": "",
            "anon_key": "",
            "service_role_key": "",
        }

    def test_handles_partial_keys(self):
        """Only the provided keys should have values; rest default to empty."""
        result = extract_connection_details({"API_URL": "http://localhost:5000"})
        assert result["api_url"] == "http://localhost:5000"
        assert result["db_url"] == ""
        assert result["anon_key"] == ""


# ── supabase_cmd ──────────────────────────────────────────────────────────


class TestSupabaseCmd:
    """Tests for supabase command resolution."""

    def test_returns_local_binary_when_exists(self, tmp_path):
        """Prefer the local node_modules binary when it exists."""
        local_bin = tmp_path / "node_modules" / ".bin" / "supabase"
        local_bin.parent.mkdir(parents=True)
        local_bin.touch()
        result = supabase_cmd(tmp_path)
        assert result == [str(local_bin)]

    def test_falls_back_to_npx(self, tmp_path):
        """Fall back to npx with pinned version when local binary is absent."""
        result = supabase_cmd(tmp_path)
        assert result == ["npx", f"supabase@{_DEFAULT_SUPABASE_VERSION}"]


# ── status ────────────────────────────────────────────────────────────────


class TestStatus:
    """Tests for supabase status wrapper."""

    @patch("codehome.serve.supabase.subprocess.run")
    def test_success_returns_parsed_json(self, mock_run, tmp_path):
        """Successful status call returns parsed JSON dict."""
        status_data = {"API_URL": "http://127.0.0.1:54321", "DB_URL": "pg://..."}
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(status_data),
        )
        result = status(tmp_path)
        assert result == status_data
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["timeout"] == TIMEOUT_SB_STATUS
        assert call_kwargs.kwargs["capture_output"] is True
        assert call_kwargs.kwargs["text"] is True
        # Verify command includes status -o json --workdir.
        cmd = call_kwargs.args[0]
        assert "status" in cmd
        assert "-o" in cmd
        assert "json" in cmd
        assert "--workdir" in cmd
        assert str(tmp_path) in cmd

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure_returns_none(self, mock_run, tmp_path):
        """Non-zero return code returns None."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = status(tmp_path)
        assert result is None

    @patch("codehome.serve.supabase.subprocess.run")
    def test_timeout_returns_none(self, mock_run, tmp_path):
        """TimeoutExpired returns None."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="supabase", timeout=30)
        result = status(tmp_path)
        assert result is None

    @patch("codehome.serve.supabase.subprocess.run")
    def test_invalid_json_returns_none(self, mock_run, tmp_path):
        """Stdout with invalid JSON returns None."""
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json{{{")
        result = status(tmp_path)
        assert result is None


# ── start ─────────────────────────────────────────────────────────────────


class TestStart:
    """Tests for supabase start wrapper."""

    @patch("codehome.serve.supabase.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        """Successful start returns (True, success message)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Started")
        ok, msg = start(tmp_path)
        assert ok is True
        assert msg == "Supabase started."
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["timeout"] == TIMEOUT_SB_START
        # Verify excluded services appear in the command.
        cmd = call_kwargs.args[0]
        assert "start" in cmd
        assert "-x" in cmd

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure_with_stderr(self, mock_run, tmp_path):
        """Failed start returns (False, stderr message)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="port already in use",
            stdout="",
        )
        ok, msg = start(tmp_path)
        assert ok is False
        assert "port already in use" in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure_with_stdout_fallback(self, mock_run, tmp_path):
        """When stderr is empty, stdout is used for the error message."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="",
            stdout="something went wrong",
        )
        ok, msg = start(tmp_path)
        assert ok is False
        assert "something went wrong" in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure_exit_code_fallback(self, mock_run, tmp_path):
        """When both stderr and stdout are empty, exit code is reported."""
        mock_run.return_value = MagicMock(
            returncode=42,
            stderr="",
            stdout="",
        )
        ok, msg = start(tmp_path)
        assert ok is False
        assert "exit 42" in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        """TimeoutExpired returns (False, timeout message)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="supabase", timeout=300)
        ok, msg = start(tmp_path)
        assert ok is False
        assert str(TIMEOUT_SB_START) in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_extra_excludes_appended(self, mock_run, tmp_path):
        """Additional exclude services are appended to ALWAYS_EXCLUDE."""
        mock_run.return_value = MagicMock(returncode=0)
        start(tmp_path, exclude=["storage"])
        cmd = mock_run.call_args.args[0]
        # Find the argument after -x and verify it contains both always-excluded
        # and the extra service.
        x_idx = cmd.index("-x")
        excludes_str = cmd[x_idx + 1]
        assert "storage" in excludes_str
        assert "mailpit" in excludes_str  # from ALWAYS_EXCLUDE


# ── stop ──────────────────────────────────────────────────────────────────


class TestStop:
    """Tests for supabase stop wrapper."""

    @patch("codehome.serve.supabase.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        """Successful stop returns (True, success message)."""
        mock_run.return_value = MagicMock(returncode=0)
        ok, msg = stop(tmp_path)
        assert ok is True
        assert msg == "Supabase stopped."
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["timeout"] == TIMEOUT_SB_STOP
        cmd = call_kwargs.args[0]
        assert "stop" in cmd
        assert "--workdir" in cmd
        assert str(tmp_path) in cmd

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure(self, mock_run, tmp_path):
        """Failed stop returns (False, error message)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="containers still running",
            stdout="",
        )
        ok, msg = stop(tmp_path)
        assert ok is False
        assert "containers still running" in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        """TimeoutExpired returns (False, timeout message)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="supabase", timeout=60)
        ok, msg = stop(tmp_path)
        assert ok is False
        assert str(TIMEOUT_SB_STOP) in msg


# ── apply_migrations ──────────────────────────────────────────────────────


class TestApplyMigrations:
    """Tests for supabase migration up wrapper."""

    @patch("codehome.serve.supabase.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        """Successful migration returns (True, success message)."""
        mock_run.return_value = MagicMock(returncode=0)
        ok, msg = apply_migrations(tmp_path)
        assert ok is True
        assert msg == "Migrations applied."
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["timeout"] == TIMEOUT_SB_MIGRATE
        cmd = call_kwargs.args[0]
        assert "migration" in cmd
        assert "up" in cmd
        assert "--include-all" in cmd
        assert "--workdir" in cmd
        assert str(tmp_path) in cmd

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure(self, mock_run, tmp_path):
        """Failed migration returns (False, error message with prefix)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="relation already exists",
            stdout="",
        )
        ok, msg = apply_migrations(tmp_path)
        assert ok is False
        assert "Migration failed" in msg
        assert "relation already exists" in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_failure_no_output(self, mock_run, tmp_path):
        """When stderr and stdout are empty, '(no output)' is used."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="",
            stdout="",
        )
        ok, msg = apply_migrations(tmp_path)
        assert ok is False
        assert "(no output)" in msg

    @patch("codehome.serve.supabase.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        """TimeoutExpired returns (False, timeout message)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="supabase", timeout=120)
        ok, msg = apply_migrations(tmp_path)
        assert ok is False
        assert str(TIMEOUT_SB_MIGRATE) in msg
