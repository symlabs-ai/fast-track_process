from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

from ft.project import bootstrap_project
from ft.project.lifecycle import (
    ProjectLifecycleError,
    assert_template_allowed,
    close_project_contract,
    evaluate_project_readiness,
    read_project_contract,
    reopen_project_contract,
    write_project_contract,
)
from ft.engine.validation_profiles import (
    MOCKUP_WATERMARK_CHECK,
    write_validation_matrix,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit_all(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(root, "rev-parse", "HEAD")


def _backlog_row(
    *,
    status: str = "done",
    evidence: str = "tests/report.md",
    decision: str = "-",
) -> str:
    return (
        "# Project backlog\n\n"
        "| ID | Prioridade | Status | Evidência | Decisão |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| PB-001 | P0 | {status} | {evidence} | {decision} |\n"
    )


def _project(tmp_path: Path, *, status: str = "done") -> Path:
    root = tmp_path / "product"
    bootstrap_project(root)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "PRD.md").write_text(
        "# PRD\n\nEntregar o produto de teste.\n",
        encoding="utf-8",
    )
    (root / "docs" / "PROJECT_BACKLOG.md").write_text(
        _backlog_row(status=status),
        encoding="utf-8",
    )
    _commit_all(root, "define product")
    return root


def _builder_policy() -> dict[str, object]:
    return {
        "project_role": "builder",
        "allowed_project_phases": ["building"],
    }


def _maintenance_policy() -> dict[str, object]:
    return {
        "project_role": "maintenance",
        "allowed_project_phases": ["maintenance"],
    }


def test_blocked_or_decided_backlog_is_not_project_done(tmp_path):
    root = _project(tmp_path, status="blocked")
    backlog = root / "docs" / "PROJECT_BACKLOG.md"
    backlog.write_text(
        _backlog_row(
            status="blocked",
            evidence="docs/decision.md",
            decision="Fornecedor ainda não liberou o acesso.",
        ),
        encoding="utf-8",
    )
    _commit_all(root, "record blocker decision")

    readiness = evaluate_project_readiness(root)

    assert readiness.status == "BLOCKED"
    assert not readiness.ready
    backlog_blocker = next(
        blocker
        for blocker in readiness.blockers
        if blocker.code == "project.backlog_unfinished"
    )
    assert backlog_blocker.references == ("PB-001",)
    assert not (root / ".ft" / "project-readiness.yml").exists()


def test_project_close_is_explicit_and_enables_maintenance(tmp_path):
    root = _project(tmp_path)

    before_close = evaluate_project_readiness(root)
    assert before_close.status == "READY_TO_CLOSE"
    assert read_project_contract(root)["lifecycle"]["phase"] == "building"

    with pytest.raises(ProjectLifecycleError, match="não em maintenance"):
        assert_template_allowed(
            root,
            template_name="feature-fast",
            execution_policy=_maintenance_policy(),
        )

    closed = close_project_contract(root)

    assert closed.ready
    contract = read_project_contract(root)
    assert contract["lifecycle"]["phase"] == "maintenance"
    assert contract["lifecycle"]["delivered_revision"] == closed.evaluated_revision
    receipt = yaml.safe_load(
        (root / ".ft" / "project-readiness.yml").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "READY"
    assert receipt["blocking_count"] == 0

    allowed = assert_template_allowed(
        root,
        template_name="feature-fast",
        execution_policy=_maintenance_policy(),
    )
    assert allowed is not None
    assert evaluate_project_readiness(root).status == "MAINTENANCE"


def test_required_structured_gate_must_be_green(tmp_path):
    root = _project(tmp_path)
    gate_path = root / "reports" / "release.json"
    gate_path.parent.mkdir()
    gate_path.write_text('{"decision": {"status": "NO-GO"}}\n', encoding="utf-8")
    contract = read_project_contract(root)
    contract["definition_of_done"]["required_gates"] = [
        {
            "id": "release-decision",
            "path": "reports/release.json",
            "field": "decision.status",
            "equals": "GO",
        }
    ]
    write_project_contract(root, contract)
    _commit_all(root, "define release gate")

    blocked = evaluate_project_readiness(root)
    assert blocked.status == "BLOCKED"
    assert any(blocker.code == "project.gate_blocked" for blocker in blocked.blockers)

    gate_path.write_text('{"decision": {"status": "GO"}}\n', encoding="utf-8")
    _commit_all(root, "approve release")
    assert evaluate_project_readiness(root).status == "READY_TO_CLOSE"


def test_active_platform_matrix_is_a_project_level_definition_of_done_gate(tmp_path):
    root = _project(tmp_path)
    contract = read_project_contract(root)
    contract["validation"] = {
        "schema_version": 1,
        "mode": "explicit",
        "matrix_path": "docs/validation-matrix.yml",
        "report_path": "docs/platform-validation-report.yml",
        "evidence_root": "docs/evidence/platform-validation",
        "test_identity": {
            "policy": "optional",
            "path": "docs/test-identity.json",
        },
        "platforms": {
            "web": {
                "targets": {
                    "desktop_browser": {"required": True},
                }
            }
        },
    }
    write_project_contract(root, contract)
    _commit_all(root, "require web validation")

    blocked = evaluate_project_readiness(root)
    assert any(
        blocker.code == "project.platform_validation" for blocker in blocked.blockers
    )

    matrix_path, matrix = write_validation_matrix(root, contract)
    evidence = root / matrix["evidence_root"] / "web-desktop.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("browser evidence\n", encoding="utf-8")
    screenshot = root / matrix["evidence_root"] / "web-desktop-S01.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)
    target = matrix["profiles"][0]["targets"][0]
    checks = []
    for check in target["checks"]:
        if check == MOCKUP_WATERMARK_CHECK:
            screenshot_path = screenshot.relative_to(root).as_posix()
            checks.append(
                {
                    "id": check,
                    "result": "PASS",
                    "evidence": [screenshot_path],
                    "inventory_complete": True,
                    "discovered_screen_count": 1,
                    "unmapped_screens": [],
                    "screens": [
                        {
                            "id": "home",
                            "mockup_ref": "S01",
                            "watermark_text": "S01",
                            "result": "PASS",
                            "evidence": [screenshot_path],
                        }
                    ],
                }
            )
            continue
        checks.append(
            {
                "id": check,
                "result": "PASS",
                "evidence": [evidence.relative_to(root).as_posix()],
            }
        )
    report = {
        "schema_version": 1,
        "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
        "verdict": "APPROVED",
        "candidate_ref": "candidate-ready",
        "profiles": [
            {
                "id": "web",
                "targets": [
                    {
                        "id": "desktop_browser",
                        "required": True,
                        "result": "PASS",
                        "observed_candidate_ref": "candidate-ready",
                        "environment": {
                            "kind": "browser",
                            "execution_surface": "web_desktop_browser",
                            "os_name": "linux",
                            "os_version": "test",
                        },
                        "checks": checks,
                    }
                ],
            }
        ],
        "findings": [],
    }
    (root / matrix["report_path"]).write_text(
        yaml.safe_dump(report, sort_keys=False),
        encoding="utf-8",
    )
    _commit_all(root, "prove web validation")

    ready = evaluate_project_readiness(root)
    assert ready.status == "READY_TO_CLOSE"
    assert any(
        check.id == "platform-validation" and check.status == "PASS"
        for check in ready.checks
    )


def test_changing_validation_profiles_invalidates_closed_project_receipt(tmp_path):
    root = _project(tmp_path)
    closed = close_project_contract(root)
    assert closed.ready

    contract = read_project_contract(root)
    contract["validation"]["mode"] = "disabled"
    contract["validation"]["reason"] = "produto sem superfície executável"
    write_project_contract(root, contract)

    readiness = evaluate_project_readiness(root)
    assert readiness.status == "INVALID_MAINTENANCE"
    assert any(
        blocker.code == "project.closure_receipt" for blocker in readiness.blockers
    )


def test_dirty_checkout_and_open_cycle_are_project_blockers(tmp_path, monkeypatch):
    root = _project(tmp_path)
    (root / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    dirty = evaluate_project_readiness(root)
    assert any(blocker.code == "project.dirty_checkout" for blocker in dirty.blockers)

    (root / "uncommitted.txt").unlink()
    ft_home = tmp_path / "ft-home"
    monkeypatch.setenv("FT_HOME", str(ft_home))
    worktree = ft_home / "worktrees" / root.name / "cycle-01-mvp-builder"
    (worktree / "state").mkdir(parents=True)
    (worktree / "state" / "engine_state.yml").write_text(
        "node_status: ready\n",
        encoding="utf-8",
    )

    active = evaluate_project_readiness(root)
    blocker = next(
        blocker
        for blocker in active.blockers
        if blocker.code == "project.active_cycles"
    )
    assert blocker.references == ("cycle-01-mvp-builder",)

    archived = root / ".ft/cycles/cycle-01-mvp-builder"
    archived.mkdir(parents=True)
    (archived / "cycle.yml").write_text(
        "schema_version: 3\n"
        "id: cycle-01-mvp-builder\n"
        "status: done\n"
        "closed_at: '2026-07-29T12:00:00+00:00'\n",
        encoding="utf-8",
    )
    _commit_all(root, "archive retained worktree")
    assert evaluate_project_readiness(root).status == "READY_TO_CLOSE"


def test_builder_owns_building_goal_and_reopen_clears_owner(tmp_path):
    root = _project(tmp_path)

    claimed = assert_template_allowed(
        root,
        template_name="mvp-builder-fast",
        execution_policy=_builder_policy(),
    )
    assert claimed is not None
    assert claimed["lifecycle"]["owner_template"] == "mvp-builder-fast"

    with pytest.raises(ProjectLifecycleError, match="pertence ao construtor"):
        assert_template_allowed(
            root,
            template_name="mvp-builder",
            execution_policy=_builder_policy(),
        )

    _commit_all(root, "claim builder")
    close_project_contract(root)
    _commit_all(root, "close project")
    reopened = reopen_project_contract(root, reason="Novo objetivo maior")

    assert reopened["lifecycle"] == {
        "phase": "building",
        "owner_template": None,
        "delivered_revision": None,
    }
    with pytest.raises(ProjectLifecycleError, match="não em maintenance"):
        assert_template_allowed(
            root,
            template_name="bug-fast",
            execution_policy=_maintenance_policy(),
        )


def test_tampered_closure_receipt_fails_closed(tmp_path):
    root = _project(tmp_path)
    close_project_contract(root)
    receipt_path = root / ".ft" / "project-readiness.yml"
    receipt = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
    receipt["definition_of_done_digest"] = "sha256:" + ("0" * 64)
    receipt_path.write_text(
        yaml.safe_dump(receipt, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ProjectLifecycleError, match="não corresponde"):
        assert_template_allowed(
            root,
            template_name="tweak",
            execution_policy=_maintenance_policy(),
        )
