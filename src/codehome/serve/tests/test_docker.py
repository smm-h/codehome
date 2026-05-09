"""Tests for Docker Compose helpers: project naming, env building, and subprocess calls."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from codehome.serve.docker import (
    _compose_file,
    _compose_tdd_file,
    TIMEOUT_COMPOSE_PS,
    TIMEOUT_COMPOSE_RM,
    TIMEOUT_COMPOSE_STOP,
    TIMEOUT_COMPOSE_UP,
    build_functions_env,
    build_vite_env,
    compose_down,
    compose_project_name,
    compose_ps,
    compose_up,
    docker_inspect_compose_containers,
)

# ---------------------------------------------------------------------------
# 1. compose_project_name
# ---------------------------------------------------------------------------


class TestComposeProjectName:
    def test_colon_replaced(self):
        assert compose_project_name("bag:navchat") == "bag-navchat"

    def test_slash_replaced(self):
        assert compose_project_name("bag:fix/auth") == "bag-fix-auth"

    def test_uppercase_lowered(self):
        assert compose_project_name("Bag:FIX") == "bag-fix"


# ---------------------------------------------------------------------------
# 2-3-4. build_vite_env
# ---------------------------------------------------------------------------


class TestBuildViteEnv:
    """Verify env dicts for the three vite compose services."""

    BRANCH = "bag:feat"
    WORKTREE = Path("/fake/worktree")
    APP_DIR = "apps/bag"
    VITE_PORT = 5173
    SUPA_PORT = 54321
    ANON_KEY = "test-anon-key"

    def _call(self, compose_service="vite"):
        return build_vite_env(
            branch=self.BRANCH,
            worktree=self.WORKTREE,
            app_dir=self.APP_DIR,
            vite_port=self.VITE_PORT,
            supabase_api_port=self.SUPA_PORT,
            supabase_anon_key=self.ANON_KEY,
            compose_service=compose_service,
        )

    # -- base vite service --

    def test_base_has_vite_port(self):
        env = self._call("vite")
        assert env["VITE_PORT"] == str(self.VITE_PORT)

    def test_base_has_app_src(self):
        env = self._call("vite")
        assert env["APP_SRC"] == str(self.WORKTREE / self.APP_DIR)

    def test_base_no_tdd_keys(self):
        env = self._call("vite")
        assert "TDD_BAG_PORT" not in env
        assert "PROD_BAG_SRC" not in env
        assert "TDD_ORDERS_PORT" not in env
        assert "PROD_ORDERS_SRC" not in env

    # -- tdd-vite-bag --

    def test_tdd_bag_has_tdd_port(self):
        env = self._call("tdd-vite-bag")
        assert env["TDD_BAG_PORT"] == str(self.VITE_PORT)

    def test_tdd_bag_has_prod_src(self):
        env = self._call("tdd-vite-bag")
        assert env["PROD_BAG_SRC"] == str(self.WORKTREE / self.APP_DIR)

    def test_tdd_bag_no_base_keys(self):
        env = self._call("tdd-vite-bag")
        assert "VITE_PORT" not in env
        assert "APP_SRC" not in env

    # -- tdd-vite-orders --

    def test_tdd_orders_has_tdd_port(self):
        env = self._call("tdd-vite-orders")
        assert env["TDD_ORDERS_PORT"] == str(self.VITE_PORT)

    def test_tdd_orders_has_prod_src(self):
        env = self._call("tdd-vite-orders")
        assert env["PROD_ORDERS_SRC"] == str(self.WORKTREE / self.APP_DIR)

    # -- .env.local fallback --

    def test_env_local_fallback_uses_placeholder(self, tmp_path):
        """When .env.local does not exist, ENV_LOCAL_PATH falls back to .env.placeholder."""
        worktree = tmp_path / "wt"
        app = worktree / "apps" / "bag"
        app.mkdir(parents=True)
        # .env.local intentionally NOT created.
        env = build_vite_env(
            branch="b:x",
            worktree=worktree,
            app_dir="apps/bag",
            vite_port=5173,
            supabase_api_port=54321,
            supabase_anon_key="k",
        )
        assert env["ENV_LOCAL_PATH"].endswith(".env.placeholder")

    def test_env_local_used_when_present(self, tmp_path):
        """When .env.local exists, it is used directly."""
        worktree = tmp_path / "wt"
        app = worktree / "apps" / "bag"
        app.mkdir(parents=True)
        (app / ".env.local").write_text("LOCAL=1")
        env = build_vite_env(
            branch="b:x",
            worktree=worktree,
            app_dir="apps/bag",
            vite_port=5173,
            supabase_api_port=54321,
            supabase_anon_key="k",
        )
        assert env["ENV_LOCAL_PATH"] == str(app / ".env.local")


# ---------------------------------------------------------------------------
# 5-6. build_functions_env
# ---------------------------------------------------------------------------


class TestBuildFunctionsEnv:
    """Verify env dicts for the two functions compose services."""

    BRANCH = "bag:feat"
    WORKTREE = Path("/fake/worktree")
    SUPA_PORT = 54321
    ANON_KEY = "test-anon-key"
    SERVICE_KEY = "test-service-role-key"

    def _call(self, compose_service="functions"):
        return build_functions_env(
            branch=self.BRANCH,
            worktree=self.WORKTREE,
            supabase_api_port=self.SUPA_PORT,
            supabase_anon_key=self.ANON_KEY,
            supabase_service_role_key=self.SERVICE_KEY,
            compose_service=compose_service,
        )

    # -- base functions service --

    def test_base_has_functions_src(self):
        env = self._call("functions")
        assert env["FUNCTIONS_SRC"] == str(self.WORKTREE / "supabase" / "functions")

    def test_base_no_prod_key(self):
        env = self._call("functions")
        assert "PROD_FUNCTIONS_SRC" not in env

    # -- tdd-functions --

    def test_tdd_has_prod_src(self):
        env = self._call("tdd-functions")
        assert env["PROD_FUNCTIONS_SRC"] == str(self.WORKTREE / "supabase" / "functions")

    def test_tdd_no_base_key(self):
        env = self._call("tdd-functions")
        assert "FUNCTIONS_SRC" not in env


# ---------------------------------------------------------------------------
# 7. compose_up
# ---------------------------------------------------------------------------


class TestComposeUp:
    """Verify subprocess invocation for compose_up."""

    BRANCH = "bag:feat"
    SERVICE = "vite"
    ENV = {"VITE_PORT": "5173", "APP_SRC": "/fake/src"}

    def _expected_cmd(self, tdd=False):
        cmd = ["docker", "compose", "-p", "bag-feat", "-f", str(_compose_file())]
        if tdd:
            cmd += ["-f", str(_compose_tdd_file())]
        cmd += ["up", "-d", "--build", self.SERVICE]
        return cmd

    @patch("subprocess.run")
    def test_success(self, mock_run):
        """Successful compose_up returns (True, started message)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msg = compose_up(self.BRANCH, self.SERVICE, self.ENV)
        assert ok is True
        assert "Started" in msg
        assert self.SERVICE in msg
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == self._expected_cmd()
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == TIMEOUT_COMPOSE_UP
        # Env should include both os.environ and the custom env.
        passed_env = kwargs["env"]
        for k, v in self.ENV.items():
            assert passed_env[k] == v

    @patch("subprocess.run")
    def test_success_with_tdd(self, mock_run):
        """When tdd=True, the TDD compose file is included."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, _msg = compose_up(self.BRANCH, self.SERVICE, self.ENV, tdd=True)
        assert ok is True
        args, _ = mock_run.call_args
        assert args[0] == self._expected_cmd(tdd=True)

    @patch("subprocess.run")
    def test_failure_nonzero_returncode(self, mock_run):
        """Non-zero returncode returns (False, stderr message)."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Error: image not found", stdout="")
        ok, msg = compose_up(self.BRANCH, self.SERVICE, self.ENV)
        assert ok is False
        assert "image not found" in msg

    @patch("subprocess.run")
    def test_failure_uses_stdout_when_stderr_empty(self, mock_run):
        """When stderr is empty, stdout is used for the error message."""
        mock_run.return_value = MagicMock(returncode=1, stderr="", stdout="stdout error")
        ok, msg = compose_up(self.BRANCH, self.SERVICE, self.ENV)
        assert ok is False
        assert "stdout error" in msg

    @patch("subprocess.run")
    def test_failure_fallback_exit_code(self, mock_run):
        """When both stderr and stdout are empty, the exit code is reported."""
        mock_run.return_value = MagicMock(returncode=42, stderr="", stdout="")
        ok, msg = compose_up(self.BRANCH, self.SERVICE, self.ENV)
        assert ok is False
        assert "exit 42" in msg

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=300))
    def test_timeout(self, mock_run):
        """TimeoutExpired returns (False, timed out message)."""
        ok, msg = compose_up(self.BRANCH, self.SERVICE, self.ENV)
        assert ok is False
        assert "timed out" in msg
        assert str(TIMEOUT_COMPOSE_UP) in msg


# ---------------------------------------------------------------------------
# 8. compose_down
# ---------------------------------------------------------------------------


class TestComposeDown:
    """Verify subprocess invocation for compose_down."""

    BRANCH = "bag:feat"

    def _base_cmd(self, tdd=False):
        cmd = ["docker", "compose", "-p", "bag-feat", "-f", str(_compose_file())]
        if tdd:
            cmd += ["-f", str(_compose_tdd_file())]
        return cmd

    # -- full down (no service) --

    @patch("subprocess.run")
    def test_full_down_success(self, mock_run):
        """Full down with no service calls 'docker compose down'."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msg = compose_down(self.BRANCH)
        assert ok is True
        assert msg == "Stopped."
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [*self._base_cmd(), "down"]
        assert kwargs["timeout"] == TIMEOUT_COMPOSE_STOP

    @patch("subprocess.run")
    def test_full_down_with_remove_volumes(self, mock_run):
        """remove_volumes=True appends -v and --remove-orphans."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, _msg = compose_down(self.BRANCH, remove_volumes=True)
        assert ok is True
        args, _ = mock_run.call_args
        assert args[0] == [*self._base_cmd(), "down", "-v", "--remove-orphans"]

    @patch("subprocess.run")
    def test_full_down_failure(self, mock_run):
        """Non-zero returncode on full down returns (False, error)."""
        mock_run.return_value = MagicMock(returncode=1, stderr="network busy", stdout="")
        ok, msg = compose_down(self.BRANCH)
        assert ok is False
        assert "network busy" in msg

    # -- single service stop+rm --

    @patch("subprocess.run")
    def test_single_service_issues_stop_then_rm(self, mock_run):
        """Stopping a single service calls stop then rm -f."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msg = compose_down(self.BRANCH, service="vite")
        assert ok is True
        assert msg == "Stopped."
        assert mock_run.call_count == 2
        stop_args = mock_run.call_args_list[0][0][0]
        rm_args = mock_run.call_args_list[1][0][0]
        assert stop_args == [*self._base_cmd(), "stop", "vite"]
        assert rm_args == [*self._base_cmd(), "rm", "-f", "vite"]
        # Verify timeouts for each call.
        assert mock_run.call_args_list[0][1]["timeout"] == TIMEOUT_COMPOSE_STOP
        assert mock_run.call_args_list[1][1]["timeout"] == TIMEOUT_COMPOSE_RM

    @patch("subprocess.run")
    def test_single_service_stop_failure_short_circuits(self, mock_run):
        """If stop fails, rm is never called."""
        mock_run.return_value = MagicMock(returncode=1, stderr="stop failed", stdout="")
        ok, msg = compose_down(self.BRANCH, service="vite")
        assert ok is False
        assert "stop failed" in msg
        # Only stop was called, not rm.
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_single_service_rm_failure(self, mock_run):
        """If stop succeeds but rm fails, returns failure."""
        stop_result = MagicMock(returncode=0, stdout="", stderr="")
        rm_result = MagicMock(returncode=1, stderr="rm failed", stdout="")
        mock_run.side_effect = [stop_result, rm_result]
        ok, msg = compose_down(self.BRANCH, service="vite")
        assert ok is False
        assert "rm failed" in msg

    @patch("subprocess.run")
    def test_single_service_with_tdd(self, mock_run):
        """tdd=True includes TDD compose file in stop+rm commands."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, _msg = compose_down(self.BRANCH, service="vite", tdd=True)
        assert ok is True
        stop_args = mock_run.call_args_list[0][0][0]
        rm_args = mock_run.call_args_list[1][0][0]
        assert stop_args == [*self._base_cmd(tdd=True), "stop", "vite"]
        assert rm_args == [*self._base_cmd(tdd=True), "rm", "-f", "vite"]

    # -- timeout --

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=60))
    def test_timeout(self, mock_run):
        """TimeoutExpired returns (False, timed out message)."""
        ok, msg = compose_down(self.BRANCH)
        assert ok is False
        assert "timed out" in msg
        assert str(TIMEOUT_COMPOSE_STOP) in msg

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=60))
    def test_timeout_single_service(self, mock_run):
        """TimeoutExpired during single-service stop returns (False, timed out)."""
        ok, msg = compose_down(self.BRANCH, service="vite")
        assert ok is False
        assert "timed out" in msg


# ---------------------------------------------------------------------------
# 9. compose_ps
# ---------------------------------------------------------------------------


class TestComposePs:
    """Verify subprocess invocation and JSON parsing for compose_ps."""

    BRANCH = "bag:feat"

    def _expected_cmd(self, tdd=False):
        cmd = ["docker", "compose", "-p", "bag-feat", "-f", str(_compose_file())]
        if tdd:
            cmd += ["-f", str(_compose_tdd_file())]
        cmd += ["ps", "--format", "json"]
        return cmd

    @patch("subprocess.run")
    def test_parses_json_output(self, mock_run):
        """Parses one-JSON-object-per-line output correctly."""
        json_lines = '{"Name": "vite-1", "State": "running"}\n{"Name": "db-1", "State": "running"}\n'
        mock_run.return_value = MagicMock(returncode=0, stdout=json_lines, stderr="")
        result = compose_ps(self.BRANCH)
        assert len(result) == 2
        assert result[0]["Name"] == "vite-1"
        assert result[1]["Name"] == "db-1"
        args, kwargs = mock_run.call_args
        assert args[0] == self._expected_cmd()
        assert kwargs["timeout"] == TIMEOUT_COMPOSE_PS

    @patch("subprocess.run")
    def test_parses_single_container(self, mock_run):
        """Handles single-line JSON output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"Name": "vite-1", "State": "running"}\n',
            stderr="",
        )
        result = compose_ps(self.BRANCH)
        assert len(result) == 1
        assert result[0]["State"] == "running"

    @patch("subprocess.run")
    def test_with_tdd(self, mock_run):
        """tdd=True includes TDD compose file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        compose_ps(self.BRANCH, tdd=True)
        args, _ = mock_run.call_args
        assert args[0] == self._expected_cmd(tdd=True)

    @patch("subprocess.run")
    def test_empty_output_returns_empty_list(self, mock_run):
        """Empty stdout returns an empty list."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = compose_ps(self.BRANCH)
        assert result == []

    @patch("subprocess.run")
    def test_whitespace_only_output_returns_empty_list(self, mock_run):
        """Whitespace-only stdout returns an empty list."""
        mock_run.return_value = MagicMock(returncode=0, stdout="  \n  \n", stderr="")
        result = compose_ps(self.BRANCH)
        assert result == []

    @patch("subprocess.run")
    def test_nonzero_returncode_returns_empty_list(self, mock_run):
        """Non-zero returncode returns an empty list."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = compose_ps(self.BRANCH)
        assert result == []

    @patch("subprocess.run")
    def test_invalid_json_returns_empty_list(self, mock_run):
        """Malformed JSON returns an empty list."""
        mock_run.return_value = MagicMock(returncode=0, stdout="NOT VALID JSON", stderr="")
        result = compose_ps(self.BRANCH)
        assert result == []

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30))
    def test_timeout_returns_empty_list(self, mock_run):
        """TimeoutExpired returns an empty list."""
        result = compose_ps(self.BRANCH)
        assert result == []


# ---------------------------------------------------------------------------
# 10. docker_inspect_compose_containers
# ---------------------------------------------------------------------------


class TestDockerInspectComposeContainers:
    """Verify the read-only Compose container scan used by discover_docker."""

    # Canonical fixture modeled on real `docker inspect` output for
    # `bag-lisa-vite-1`.  Keeps the discovery tests honest about the exact
    # shape they're consuming.
    BAG_LISA_VITE_INSPECT = {
        "Name": "/bag-lisa-vite-1",
        "Config": {
            "Labels": {
                "com.docker.compose.project": "bag-lisa",
                "com.docker.compose.service": "vite",
                "com.docker.compose.container-number": "1",
                "com.veliu.managed": "true",
            },
        },
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Ports": {
                "5173/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "59899"},  # noqa: S104
                    {"HostIp": "::", "HostPort": "59899"},
                ],
            },
        },
    }

    def _make_subprocess_side_effect(self, ps_ids, inspect_lines):
        """Return a side_effect that maps ps->ids, inspect->lines."""
        import json as _json

        def _run(cmd, **_kwargs):
            if cmd[:3] == ["docker", "ps", "-q"]:
                return MagicMock(returncode=0, stdout="\n".join(ps_ids) + "\n", stderr="")
            if cmd[:2] == ["docker", "inspect"]:
                body = "\n".join(line if isinstance(line, str) else _json.dumps(line) for line in inspect_lines)
                return MagicMock(returncode=0, stdout=body + "\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="unexpected cmd")

        return _run

    @patch("subprocess.run")
    def test_extracts_branch_service_and_host_port(self, mock_run):
        """Container labels + IPv4 port binding are parsed correctly."""
        mock_run.side_effect = self._make_subprocess_side_effect(
            ps_ids=["3c3b3ff8a0c6"],
            inspect_lines=[self.BAG_LISA_VITE_INSPECT],
        )
        out = docker_inspect_compose_containers()
        assert len(out) == 1
        c = out[0]
        assert c["name"] == "bag-lisa-vite-1"
        assert c["project"] == "bag-lisa"
        assert c["service"] == "vite"
        assert c["container_number"] == 1
        assert c["state"] == "running"
        assert c["host_ports"] == {5173: 59899}

    @patch("subprocess.run")
    def test_skips_stopped_containers(self, mock_run):
        """State != running is dropped; discovery never registers stopped services."""
        stopped = {
            **self.BAG_LISA_VITE_INSPECT,
            "State": {"Status": "exited"},
        }
        mock_run.side_effect = self._make_subprocess_side_effect(
            ps_ids=["abc"],
            inspect_lines=[stopped],
        )
        assert docker_inspect_compose_containers() == []

    @patch("subprocess.run")
    def test_empty_ps_short_circuits(self, mock_run):
        """When no container IDs come back, inspect is never called."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        out = docker_inspect_compose_containers()
        assert out == []
        # Only ps should have run, not inspect.
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_handles_missing_labels_defensively(self, mock_run):
        """Containers without the compose labels are skipped."""
        bad = {
            "Name": "/random-container",
            "Config": {"Labels": {}},
            "State": {"Status": "running"},
            "NetworkSettings": {"Ports": {}},
        }
        mock_run.side_effect = self._make_subprocess_side_effect(
            ps_ids=["xxx"],
            inspect_lines=[bad],
        )
        assert docker_inspect_compose_containers() == []

    @patch("subprocess.run")
    def test_prefers_ipv4_host_binding_over_ipv6(self, mock_run):
        """When both IPv4 and IPv6 bindings are present, IPv4 wins."""
        ipv6_first = {
            **self.BAG_LISA_VITE_INSPECT,
            "NetworkSettings": {
                "Ports": {
                    "5173/tcp": [
                        {"HostIp": "::", "HostPort": "60000"},
                        {"HostIp": "0.0.0.0", "HostPort": "59899"},  # noqa: S104
                    ],
                },
            },
        }
        mock_run.side_effect = self._make_subprocess_side_effect(
            ps_ids=["id"],
            inspect_lines=[ipv6_first],
        )
        out = docker_inspect_compose_containers()
        assert out[0]["host_ports"] == {5173: 59899}

    @patch("subprocess.run")
    def test_unbound_ports_omitted(self, mock_run):
        """Container ports without a host binding are not included."""
        partial = {
            **self.BAG_LISA_VITE_INSPECT,
            "NetworkSettings": {
                "Ports": {
                    "5173/tcp": [{"HostIp": "0.0.0.0", "HostPort": "59899"}],  # noqa: S104
                    "24678/tcp": None,
                },
            },
        }
        mock_run.side_effect = self._make_subprocess_side_effect(
            ps_ids=["id"],
            inspect_lines=[partial],
        )
        out = docker_inspect_compose_containers()
        assert out[0]["host_ports"] == {5173: 59899}

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30))
    def test_timeout_returns_empty(self, _mock_run):
        """TimeoutExpired from docker ps yields an empty list (never raises)."""
        assert docker_inspect_compose_containers() == []

    @patch("subprocess.run", side_effect=FileNotFoundError("no docker"))
    def test_missing_docker_cli_returns_empty(self, _mock_run):
        """When docker isn't on PATH, discovery degrades to no-op."""
        assert docker_inspect_compose_containers() == []
