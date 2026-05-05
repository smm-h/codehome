"""Role definitions for agent tool provisioning.

Each role maps to a set of allowed tools and an access level hint.
The server reads SA_ROLE from environment and registers only the
tools listed for that role.
"""

from typing import Any

ROLES: dict[str, dict[str, Any]] = {
    "implementor": {
        "level": "read-write",
        "tools": [
            "read_file",
            "write_file",
            "edit_file",
            "list_files",
            "search_files",
            "git_status",
            "git_diff",
            "git_commit",
            "git_log",
            "run_tests",
            "ask_user",
        ],
    },
    "auditor": {
        "level": "read-only",
        "tools": [
            "read_file",
            "list_files",
            "search_files",
            "git_status",
            "git_diff",
            "git_log",
            "run_tests",
            "ask_user",
        ],
    },
    "reviewer": {
        "level": "read-only",
        "tools": [
            "read_file",
            "list_files",
            "search_files",
            "git_diff",
            "git_log",
            "post_review_comment",
            "ask_user",
        ],
    },
    "deployer": {
        "level": "everything",
        "tools": [
            "read_file",
            "list_files",
            "search_files",
            "git_status",
            "git_diff",
            "git_log",
            "stage_branch",
            "create_prod_pr",
            "check_pipeline",
            "ask_user",
        ],
    },
}

DEFAULT_ROLE = "auditor"
