from pathlib import Path
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch
import subprocess

import pytest
import yaml

from ft.engine.process_improvements import (
    ProcessImprovementError,
    load_process_improvement_review,
    process_improvement_close_readiness,
    resolve_global_process_candidate,
)
from ft.engine.runner import VALIDATOR_REGISTRY
from ft.engine.validators.artifacts import process_improvements_classified
from ft.cli import main as cli_main


def _criteria(**overrides):
    values = {
        "domain_independent": True,
        "no_product_identifiers": True,
        "configurable": True,
        "verified_in_cycle": True,
        "backward_compatible": True,
    }
    values.update(overrides)
    return values


def _global_candidate():
    return {
        "id": "PI-001",
        "title": "Preservar ownership de processos externos",
        "classification": "global_candidate",
        "rationale": "A regra vale para qualquer projeto executado pelo engine.",
        "evidence": [
            {
                "source": "cycle_log",
                "detail": "O smoke encontrou uma porta ocupada por outro checkout.",
            }
        ],
        "criteria": _criteria(),
        "change": {
            "applied_locally": True,
            "summary": "O hook passou a encerrar somente o PID iniciado por ele.",
            "paths": [".ft/process/process.yml"],
        },
        "global": {
            "target": "process_template",
            "summary": "Aplicar ownership explícito aos hooks do template.",
            "test_plan": ["Simular conflito de porta e preservar o listener externo."],
            "resolution": {"status": "pending", "reason": "", "reference": ""},
        },
    }


def _local_improvement():
    return {
        "id": "PI-002",
        "title": "Validar rota exclusiva do produto",
        "classification": "local",
        "rationale": "A rota existe somente neste produto.",
        "evidence": [{"source": "validator", "detail": "A rota falhou no smoke."}],
        "criteria": _criteria(
            domain_independent=False,
            no_product_identifiers=False,
        ),
        "change": {
            "applied_locally": True,
            "summary": "Adicionado validator da rota no fork local.",
            "paths": [".ft/process/process.yml"],
        },
    }


def _write_review(tmp_path: Path, improvements, *, no_findings_reason=""):
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    ids = [item["id"] for item in improvements]
    (docs / "process-improvements.md").write_text(
        "# Melhorias\n\n" + "\n".join(f"## {item}" for item in ids),
        encoding="utf-8",
    )
    (docs / "process-improvements.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "no_findings_reason": no_findings_reason,
                "improvements": improvements,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_accepts_no_findings_with_explicit_reason(tmp_path):
    _write_review(
        tmp_path,
        [],
        no_findings_reason=(
            "Logs, retries e gates foram revisados sem encontrar desvio reproduzível."
        ),
    )

    review = load_process_improvement_review(tmp_path)

    assert review.improvements == ()
    assert review.global_candidates == ()
    assert process_improvement_close_readiness(tmp_path)[0]


def test_validates_local_and_pending_global_candidates(tmp_path):
    _write_review(tmp_path, [_global_candidate(), _local_improvement()])

    passed, detail = process_improvements_classified(project_root=str(tmp_path))
    close_ok, close_detail = process_improvement_close_readiness(tmp_path)

    assert passed, detail
    assert "1 local" in detail
    assert not close_ok
    assert "PI-001" in close_detail


def test_global_candidate_requires_every_global_criterion(tmp_path):
    item = _global_candidate()
    item["criteria"]["configurable"] = False
    _write_review(tmp_path, [item])

    with pytest.raises(ProcessImprovementError, match="configurable"):
        load_process_improvement_review(tmp_path)


def test_local_classification_cannot_hide_global_candidate(tmp_path):
    item = _global_candidate()
    item["classification"] = "local"
    item.pop("global")
    _write_review(tmp_path, [item])

    with pytest.raises(
        ProcessImprovementError, match="nao pode ser classificado local"
    ):
        load_process_improvement_review(tmp_path)


def test_report_must_reference_every_improvement_id(tmp_path):
    _write_review(tmp_path, [_local_improvement()])
    (tmp_path / "docs/process-improvements.md").write_text(
        "# Relatorio sem identificador\n",
        encoding="utf-8",
    )

    with pytest.raises(ProcessImprovementError, match="PI-002 nao aparece"):
        load_process_improvement_review(tmp_path)


def test_promoted_candidate_requires_reference_and_unblocks_close(tmp_path):
    _write_review(tmp_path, [_global_candidate()])

    with pytest.raises(ProcessImprovementError, match="reference obrigatoria"):
        resolve_global_process_candidate(
            tmp_path,
            "PI-001",
            status="promoted",
            reason="Aplicado no template e validado.",
        )

    review = resolve_global_process_candidate(
        tmp_path,
        "PI-001",
        status="promoted",
        reason="Aplicado no template e validado pela suíte do engine.",
        reference="commit abc123 templates/fast-track-v3/process.yml",
    )

    assert review.global_candidates[0].status == "promoted"
    assert process_improvement_close_readiness(tmp_path)[0]

    passed, detail = process_improvements_classified(project_root=str(tmp_path))
    assert not passed
    assert "nao pode resolver sua propria promocao" in detail


def test_legacy_cycle_without_structured_review_can_close(tmp_path):
    ok, detail = process_improvement_close_readiness(tmp_path)

    assert ok
    assert "ciclo legado" in detail


def test_close_does_not_merge_pending_global_candidate(tmp_path):
    _write_review(tmp_path, [_global_candidate()])

    class _StateManager:
        @staticmethod
        def load():
            return SimpleNamespace(node_status="done", current_node=None)

    class _Runner:
        project_root = tmp_path
        state_mgr = _StateManager()
        merge_called = False

        def merge_on_close(self, *_args, **_kwargs):
            self.merge_called = True
            return True

    runner = _Runner()
    args = Namespace(
        process=None,
        force=False,
        merge="full",
        merge_paths=None,
        keep_worktree=False,
        claude=None,
        codex=None,
        gemini=None,
        opencode=None,
        verbose=False,
    )

    with patch("ft.cli.main.get_runner", return_value=runner):
        cli_main.cmd_close(args)

    assert runner.merge_called is False


def test_process_candidates_reads_active_worktree_from_project_root(tmp_path, capsys):
    project = tmp_path / "project"
    worktree = tmp_path / "worktree"
    project.mkdir()
    _write_review(worktree, [_global_candidate()])
    args = Namespace(
        process=None,
        candidate_id=None,
        status=None,
        reason=None,
        reference=None,
        verbose=False,
    )

    with (
        patch("ft.cli.main.find_project_root", return_value=project),
        patch(
            "ft.cli.main.get_runner",
            return_value=SimpleNamespace(project_root=worktree),
        ),
    ):
        cli_main.cmd_process_candidates(args)

    output = capsys.readouterr().out
    assert "PI-001" in output
    assert "pending" in output
    assert str(worktree / "docs/process-improvements.yml") in output


def test_global_template_declares_structured_process_governance():
    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load(
        (root / "templates/fast-track-v3/process.yml").read_text(encoding="utf-8")
    )
    node = next(
        item for item in data["nodes"] if item["id"] == "ft.handoff.05.process_evolve"
    )

    assert data["version"] == "1.2.0"
    assert "docs/process-improvements.yml" in data["artifact_policy"]["cycle"]
    assert "docs/process-improvements.yml" in node["outputs"]
    assert "docs/process-improvements.yml" in node["write_scope"]
    assert any("process_improvements_classified" in item for item in node["validators"])
    assert "process_improvements_classified" in VALIDATOR_REGISTRY

    by_id = {item["id"]: item for item in data["nodes"]}
    red = by_id["ft.tdd.01.red"]
    assert any(
        "result.returncode == 1" in item.get("command_succeeds", "")
        for item in red["validators"]
    )

    smoke = by_id["ft.smoke.01.run"]
    smoke_yaml = yaml.safe_dump(smoke, sort_keys=False)
    assert ".smoke_started_process" in smoke_yaml
    assert "fuser -k" not in smoke_yaml
    assert "PORT=" in smoke_yaml
    for script in [*smoke["env_setup"], *smoke["env_teardown"]]:
        result = subprocess.run(
            ["sh", "-n", "-c", script],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    acceptance = by_id["ft.acceptance.01.cli"]
    assert "p0_blockers" in yaml.safe_dump(acceptance, sort_keys=False)

    visual_gate = by_id["gate.visual_check"]
    assert any("visual_p0_acceptance" in item for item in visual_gate["validators"])
    assert "visual_p0_acceptance" in VALIDATOR_REGISTRY
