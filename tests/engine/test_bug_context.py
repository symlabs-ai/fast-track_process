"""Contracts for the bounded contexts used by the lightweight bug process."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ft.engine.context_profiles import (
    BUG_DIRECT_PROFILE,
    BUG_PROFILES,
    BUG_RECONCILE_PROFILE,
    KNOWN_CONTEXT_PROFILES,
    compose_context_profile,
)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _receipt() -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "kind": "ft.bug.product-validation",
            "result": "PASS",
            "fingerprint": "sha256:bug-validation",
            "recorded_at": "2026-07-14T12:00:00Z",
            "product_root": "src",
            "commands": [["make", "-C", "src", "test"]],
            "files": [
                {"path": "src/app.py", "sha256": "SECRET_PER_FILE_HASH"},
                {"path": "tests/test_app.py", "sha256": "ANOTHER_SECRET_HASH"},
            ],
        }
    )


def _init_repository(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return _git(root, "rev-parse", "HEAD")


def test_bug_profiles_are_registered_with_explicit_caps() -> None:
    assert {BUG_DIRECT_PROFILE, BUG_RECONCILE_PROFILE} <= KNOWN_CONTEXT_PROFILES
    assert {
        name: profile.max_chars for name, profile in BUG_PROFILES.items()
    } == {
        BUG_DIRECT_PROFILE: 40_000,
        BUG_RECONCILE_PROFILE: 40_000,
    }


def test_bug_direct_injects_bounded_diagnostic_context_and_retry_delta(
    tmp_path: Path,
) -> None:
    documents = {
        "docs/feature-request.md": "Ao salvar vazio, a tela encerra com erro.",
        "docs/bug-baseline.yml": "version: 1\nproduct_root: src\n",
        "docs/bug-report.md": "BUG REPORT: falha reproduzida no formulário.",
        "docs/bug-validation.json": _receipt(),
        "docs/TECH_STACK.md": "Python + browser UI",
        "docs/ui_criteria.md": "UI CONTRACT FOR FORM",
        "docs/api_contract.md": "API CONTRACT FOR SAVE",
        "docs/PROJECT_BACKLOG.md": "| PB-042 | in_progress | Corrigir save |",
        "docs/FEATURES.md": "| FEAT-007 | Formulário | PB-042 |",
        "src/app.py": "VALUE = 1\n",
        "tests/test_app.py": "def test_save():\n    assert True\n",
        ".ft/cycles/cycle-99/bug-report.md": "ARCHIVED SECRET",
        "state/engine_state.yml": "STATE SECRET",
        "logs/provider.log": "LOG SECRET",
    }
    for relative, content in documents.items():
        _write(tmp_path, relative, content)
    base = _init_repository(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 2\n")

    result = compose_context_profile(
        BUG_DIRECT_PROFILE,
        tmp_path,
        "DIAGNOSE AND FIX",
        base_commit=base,
        last_approval_message="preserve o contrato atual",
    )

    assert len(result.context) <= 40_000
    for marker in (
        "Ao salvar vazio, a tela encerra com erro.",
        "product_root: src",
        "BUG REPORT: falha reproduzida",
        "Python + browser UI",
        "UI CONTRACT FOR FORM",
        "API CONTRACT FOR SAVE",
        "PB-042",
        "FEAT-007",
        "sha256:bug-validation",
        '"file_count":2',
    ):
        assert marker in result.context
    assert "SECRET_PER_FILE_HASH" not in result.context
    assert "ANOTHER_SECRET_HASH" not in result.context
    assert "ARCHIVED SECRET" not in result.context
    assert "STATE SECRET" not in result.context
    assert "LOG SECRET" not in result.context
    assert "git:bug.changed-files" in result.context
    assert "git:bug.diff" in result.context
    assert "+VALUE = 2" in result.context
    assert "current:src/app.py" in result.context
    assert "git:bug.product-manifest" in result.context
    assert result.context.index("git:bug.diff") < result.context.index(
        "git:bug.product-manifest"
    )
    assert (
        'CURRENT_FEEDBACK={"state":"present","text":"preserve o contrato atual"}'
        in result.context
    )
    assert result.prompt.endswith("DIAGNOSE AND FIX")
    assert "docs/**" in result.deny_read_paths
    assert ".ft/cycles/**" in result.deny_read_paths
    assert "docs/bug-validation.json" in result.deny_read_paths


def test_bug_reconcile_is_document_focused_and_excludes_product_delta(
    tmp_path: Path,
) -> None:
    documents = {
        "docs/feature-request.md": "REQUEST MUST STAY OUT",
        "docs/bug-baseline.yml": "BASELINE MUST STAY OUT",
        "docs/TECH_STACK.md": "STACK MUST STAY OUT",
        "docs/bug-report.md": "FIXED ROOT CAUSE",
        "docs/bug-validation.json": _receipt(),
        "docs/stakeholder-feedback.md": "STAKEHOLDER APPROVED",
        "docs/bug-result.md": "RESULT DRAFT SURVIVES RETRY",
        "docs/PROJECT_BACKLOG.md": "PB-042 in_progress\n",
        "docs/FEATURES.md": "FEAT-007 old evidence\n",
        "CHANGELOG.md": "# Changelog\n",
        "src/app.py": "VALUE = 1\n",
    }
    for relative, content in documents.items():
        _write(tmp_path, relative, content)
    base = _init_repository(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 2\n")
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", "PB-042 accepted\n")
    _write(tmp_path, "CHANGELOG.md", "# Changelog\n- #BUG PB-042 / FEAT-007\n")

    result = compose_context_profile(
        BUG_RECONCILE_PROFILE,
        tmp_path,
        "RECONCILE",
        base_commit=base,
    )

    assert len(result.context) <= 40_000
    for marker in (
        "FIXED ROOT CAUSE",
        "STAKEHOLDER APPROVED",
        "RESULT DRAFT SURVIVES RETRY",
        "PB-042 accepted",
        "FEAT-007 old evidence",
        "#BUG PB-042 / FEAT-007",
        "sha256:bug-validation",
    ):
        assert marker in result.context
    assert "REQUEST MUST STAY OUT" not in result.context
    assert "BASELINE MUST STAY OUT" not in result.context
    assert "STACK MUST STAY OUT" not in result.context
    assert "SECRET_PER_FILE_HASH" not in result.context
    assert "git:bug-reconcile.changed-files" not in result.context
    assert "git:bug-reconcile.diff" not in result.context
    assert "+VALUE = 2" not in result.context
    assert "VALUE = 2" not in result.context
    assert "git:bug-reconcile.product-manifest" not in result.context
    assert "docs/**" in result.deny_read_paths
    assert "docs/bug-baseline.yml" not in result.deny_read_paths
