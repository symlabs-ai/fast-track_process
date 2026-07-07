"""Template repository must not contain runtime state from prior executions."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_RUNTIME_PATHS = [
    "artifacts",
    "CLAUDE.md",
    "project",
    "runs",
    "state",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/worktrees",
]


def test_template_has_no_runtime_state():
    existing = [
        path
        for rel_path in FORBIDDEN_RUNTIME_PATHS
        if (path := REPO_ROOT / rel_path).exists()
    ]

    assert existing == []
