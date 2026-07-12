from __future__ import annotations

from pathlib import Path

import yaml

from ft.engine.runner import StepRunner, VALIDATOR_REGISTRY
from ft.engine.validators.artifacts import (
    features_catalog_valid,
    implemented_backlog_covered_by_features,
)


def test_mvp_builder_maintains_features_before_planning_and_after_delivery():
    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load(
        (root / "templates/mvp-builder/process.yml").read_text(encoding="utf-8")
    )
    by_id = {node["id"]: node for node in data["nodes"]}

    assert data["version"] == "1.2.0"
    assert "docs/FEATURES.md" in data["artifact_policy"]["canonical"]
    assert by_id["ft.start.backlog.route"]["branches"]["true"] == "ft.start.features.route"
    assert by_id["ft.plan.00.project_backlog"]["next"] == "ft.start.features.route"
    assert by_id["ft.start.features.route"]["branches"] == {
        "true": "ft.plan.01.task_list",
        "false": "ft.plan.00.features_catalog",
    }

    create = by_id["ft.plan.00.features_catalog"]
    update = by_id["ft.handoff.02b.features_update"]
    assert by_id["ft.handoff.02.backlog_update"]["next"] == update["id"]
    assert update["next"] == "ft.handoff.02.prd_rewrite"
    for node in (create, update):
        assert "docs/FEATURES.md" in node["outputs"]
        validator_names = {name for spec in node["validators"] for name in spec}
        assert {
            "features_catalog_valid",
            "implemented_backlog_covered_by_features",
        } <= validator_names

    assert "features_catalog_valid" in VALIDATOR_REGISTRY
    assert "implemented_backlog_covered_by_features" in VALIDATOR_REGISTRY


def test_opencode_features_fallback_derives_capabilities_but_not_debt(tmp_path):
    process = tmp_path / "process.yml"
    process.write_text(
        """id: test
version: '1.0'
title: Test
nodes:
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "PROJECT_BACKLOG.md").write_text(
        "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| PB-001 | US | P0 | done | PRD | Cadastro | Criar clientes | docs/e2e.md | Entregue |\n"
        "| PB-002 | Debt | P1 | done | Retro | Refatoração interna | Simplificar código | docs/tests.md | Entregue |\n",
        encoding="utf-8",
    )

    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state" / "engine_state.yml",
        project_root=tmp_path,
    )
    runner._write_opencode_features_catalog_artifact()

    catalog = (docs / "FEATURES.md").read_text(encoding="utf-8")
    assert "FEAT-001" in catalog
    assert "PB-001" in catalog
    assert "PB-002" not in catalog
    assert features_catalog_valid(project_root=str(tmp_path))[0]
    assert implemented_backlog_covered_by_features(project_root=str(tmp_path))[0]
