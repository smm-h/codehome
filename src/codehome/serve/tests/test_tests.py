"""Tests for test suite discovery and _load_json helper."""

import json
from pathlib import Path

import pytest

import codehome.serve.test_runner as test_runner_mod

discover_suites = test_runner_mod.discover_suites
_load_json = test_runner_mod._load_json


@pytest.fixture()
def _register_layout(_register_minimal_layout):
    """Adapter: exposes (repo_root, branches_dir) from the shared MinimalLayout.

    The conftest autouse fixture already registers core.layout; this just
    provides the directories that test_runner discovery tests populate.
    """
    layout = _register_minimal_layout
    repo_root = layout.repo_dir("bag")
    branches_dir = layout.repo_branches("bag")
    yield repo_root, branches_dir


# ---------------------------------------------------------------------------
# 1. discover_suites -- finds repo-level *.tests.json
# ---------------------------------------------------------------------------


class TestDiscoverRepoSuites:
    def test_finds_repo_level_tests_json(self, _register_layout):
        repo_root, _branches_dir = _register_layout
        # Set up: repos/<repo>/tests/ with *.tests.json files.
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        suite_file = tests_dir / "smoke.tests.json"
        suite_file.write_text(json.dumps({"tests": ["a", "b"]}))

        suites = discover_suites("bag")
        assert len(suites) == 1
        assert suites[0]["suite"] == "smoke"
        assert suites[0]["test_count"] == 2
        assert suites[0]["type"] == "repo"


# ---------------------------------------------------------------------------
# 2. discover_suites -- finds per-branch tests.json
# ---------------------------------------------------------------------------


class TestDiscoverBranchSuites:
    def test_finds_branch_tests_json(self, _register_layout):
        _repo_root, branches_dir = _register_layout
        branch = branches_dir / "feat-login"
        branch.mkdir()
        (branch / "tests.json").write_text(
            json.dumps(
                {
                    "tests": ["test1", "test2", "test3"],
                },
            ),
        )

        suites = discover_suites("bag")
        assert len(suites) == 1
        assert suites[0]["suite"] == "feat-login"
        assert suites[0]["test_count"] == 3
        assert suites[0]["type"] == "branch"


# ---------------------------------------------------------------------------
# 3. discover_suites -- excludes empty branch tests
# ---------------------------------------------------------------------------


class TestDiscoverExcludesEmpty:
    def test_empty_tests_array_excluded(self, _register_layout):
        _repo_root, branches_dir = _register_layout
        branch = branches_dir / "empty-branch"
        branch.mkdir()
        # tests.json with an empty tests array should be excluded.
        (branch / "tests.json").write_text(json.dumps({"tests": []}))

        suites = discover_suites("bag")
        assert len(suites) == 0


# ---------------------------------------------------------------------------
# 4. discover_suites -- handles malformed JSON
# ---------------------------------------------------------------------------


class TestDiscoverMalformedJSON:
    def test_malformed_json_yields_zero_test_count(self, _register_layout):
        repo_root, _branches_dir = _register_layout
        # Repo-level malformed file.
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        bad_file = tests_dir / "broken.tests.json"
        bad_file.write_text("{not valid json!!!")

        suites = discover_suites("bag")
        # The file is still discovered, but with test_count=0.
        assert len(suites) == 1
        assert suites[0]["test_count"] == 0
        assert suites[0]["suite"] == "broken"


# ---------------------------------------------------------------------------
# 5. _load_json -- missing file
# ---------------------------------------------------------------------------


class TestLoadJsonMissing:
    def test_missing_file_returns_none(self, tmp_path):
        result = _load_json(tmp_path / "nonexistent.json")
        assert result is None


# ---------------------------------------------------------------------------
# 6. _load_json -- bad JSON
# ---------------------------------------------------------------------------


class TestLoadJsonBadJSON:
    def test_bad_json_returns_none(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{{{{not json")
        result = _load_json(bad)
        assert result is None


# ---------------------------------------------------------------------------
# 7. _load_json -- valid JSON
# ---------------------------------------------------------------------------


class TestLoadJsonValid:
    def test_valid_json_returns_dict(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"key": "value", "count": 42}))
        result = _load_json(good)
        assert result == {"key": "value", "count": 42}
