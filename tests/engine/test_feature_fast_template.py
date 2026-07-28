"""Contract tests for the opt-in feature-fast process."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import yaml

from ft.engine.graph import load_graph
from ft.engine.process_validator import validate_process
from ft.templates.catalog import TemplateCatalog


ROOT = Path(__file__).resolve().parents[2]
BASE_PROCESS = ROOT / "templates" / "feature" / "process.yml"
FAST_PROCESS = ROOT / "templates" / "feature-fast" / "process.yml"
FAST_VALIDATOR = (
    ROOT / "templates" / "feature-fast" / "scripts" / "validate_feature.py"
)


def _payload(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _node_contract(node: dict) -> dict:
    fields = (
        "id",
        "type",
        "executor",
        "sprint",
        "next",
        "reject_next",
        "branches",
        "on_fail",
        "validators",
        "outputs",
        "review_route_path",
        "llm_episode",
    )
    return {
        field: _normalize_runtime_paths(node.get(field))
        for field in fields
    }


def _normalize_runtime_paths(value):
    if isinstance(value, str):
        return value.replace(
            ".ft/process/feature-fast/",
            ".ft/process/feature/",
        )
    if isinstance(value, list):
        return [_normalize_runtime_paths(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_runtime_paths(item)
            for key, item in value.items()
        }
    return value


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_fast_validator(
    root: Path,
    mode: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FAST_VALIDATOR), "--root", str(root), mode],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _focal_fix_project(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "docs/feature-request.md",
        "PB-002: corrigir busca por telefone.\n",
    )
    _write(
        tmp_path,
        "docs/feature.md",
        "---\n"
        "type: evolution\n"
        "target_feature: FEAT-001\n"
        "backlog_item: PB-002\n"
        "priority: P1\n"
        "interface: api\n"
        "---\n\n"
        "# Busca\n\n"
        "## Objetivo\nCorrigir busca.\n\n"
        "## Comportamento Esperado\nBusca exata.\n\n"
        "## Critérios de Aceite\n- AC-01: busca correta.\n\n"
        "## Fora do Escopo\n- Busca fuzzy.\n\n"
        "## Restrições\n- Preservar contrato.\n",
    )
    _write(tmp_path, "docs/feature-plan.md", "PB-002 FEAT-001 AC-01\n")
    _write(
        tmp_path,
        "docs/feature-workset.yml",
        "schema_version: 1\n"
        "paths:\n"
        "  - project/app.py\n"
        "  - project/tests/test_app.py\n",
    )
    _write(
        tmp_path,
        "docs/PROJECT_BACKLOG.md",
        "| ID | Status |\n"
        "|---|---|\n"
        "| PB-002 | in_progress |\n",
    )
    _write(
        tmp_path,
        "docs/FEATURES.md",
        "| ID | Backlog |\n"
        "|---|---|\n"
        "| FEAT-001 | PB-001 |\n",
    )
    _write(
        tmp_path,
        "docs/feature-baseline.yml",
        "version: 2\n"
        "product_root: project\n"
        "project_backlog: []\n"
        "features: []\n"
        "documentation_sha256: {}\n",
    )
    _write(
        tmp_path,
        "docs/feature-review.md",
        "Resultado: REJECTED\n\n"
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | FAIL | F-01: valor incorreto. |\n\n"
        "| Finding | Status | Evidência |\n"
        "|---|---|---|\n"
        "| F-01 | FAIL | app retorna 1. |\n",
    )
    _write(
        tmp_path,
        "docs/feature-review.yml",
        "schema_version: 1\n"
        "verdict: REJECTED\n"
        "review_route: implementation\n"
        "summary: F-01 precisa de correção.\n",
    )
    _write(tmp_path, "project/app.py", "VALUE = 1\n")
    _write(
        tmp_path,
        "project/tests/test_app.py",
        "def test_value():\n    assert True\n",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tests"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "reviewed implementation"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_catalog_exposes_feature_fast() -> None:
    descriptor = TemplateCatalog(ROOT / "templates").get("feature-fast")

    assert descriptor.process_file == FAST_PROCESS
    assert descriptor.policy["template"] == "feature-fast"
    assert descriptor.policy["local_process_path"] == (
        ".ft/process/feature-fast/process.yml"
    )


def test_feature_fast_graph_and_session_policy_are_valid() -> None:
    graph = load_graph(FAST_PROCESS)
    report = validate_process(graph)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "feature_fast"
    assert graph.meta["version"] == "1.1.0"
    assert graph.meta["session_policy"] == {
        "mode": "sprint",
        "providers": ["claude", "codex"],
        "initial_plan": "internal",
        "parallel_strategy": "fork",
        "recovery": "rehydrate",
    }


def test_feature_fast_preserves_feature_safety_contract() -> None:
    base = _payload(BASE_PROCESS)
    fast = _payload(FAST_PROCESS)

    assert fast["close_policy"] == base["close_policy"]
    assert fast["artifact_policy"]["canonical"] == base["artifact_policy"]["canonical"]
    assert set(base["artifact_policy"]["cycle"]).issubset(
        fast["artifact_policy"]["cycle"]
    )
    assert {
        "docs/feature-fix-baseline.yml",
        "docs/feature-fix-review.md",
        "docs/feature-fix-review.yml",
    }.issubset(fast["artifact_policy"]["cycle"])

    base_nodes = {node["id"]: node for node in base["nodes"]}
    fast_nodes = {node["id"]: node for node in fast["nodes"]}
    unchanged_ids = set(base_nodes) - {"feature.review_decision"}
    assert {
        node_id: _node_contract(fast_nodes[node_id])
        for node_id in unchanged_ids
    } == {
        node_id: _node_contract(base_nodes[node_id])
        for node_id in unchanged_ids
    }

    policy = fast["correction_policy"]
    assert policy["follow_graph_after_retry"] is True
    assert policy["scope_rejection_restarts_at"] == "feature.discovery"
    assert policy["acceptance_rejection_restarts_at"] == "feature.implement"
    assert set(base["correction_policy"]["mandatory_after_implementation"]).issubset(
        policy["mandatory_after_implementation"]
    )
    assert "feature.fix_review" in policy["mandatory_after_implementation"]
    base_human_gates = sum(
        node["type"] == "human_gate" for node in base["nodes"]
    )
    assert base_human_gates == 3
    assert sum(
        node["type"] == "human_gate" for node in fast["nodes"]
    ) == base_human_gates


def test_feature_fast_session_boundaries_match_process_roles() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.get_node("feature.discovery").sprint == "feature-01-scope"
    assert graph.get_node("feature.implement").sprint == "feature-02-build"
    assert graph.get_node("feature.evidence").sprint == "feature-02-build"
    review = graph.get_node("feature.review")
    assert review.sprint == "feature-02-build"
    assert review.type == "review"
    assert review.llm_timeout_seconds == 1800
    fix = graph.get_node("feature.fix")
    assert fix.sprint == "feature-02-build"
    assert fix.llm_episode == "feature_implementation"
    assert graph.get_node("feature.fix_review").type == "review"
    assert graph.get_node("feature.fix_review").llm_timeout_seconds == 600
    assert graph.get_node("feature.reconcile").sprint == "feature-03-acceptance"


def test_feature_fast_uses_focal_fix_and_delta_review_topology() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.get_node("feature.review_decision").branches == {
        "approved": "feature.acceptance",
        "implementation": "feature.fix_prepare",
        "evidence": "feature.evidence",
        "scope": "feature.discovery",
        "_default": "feature.review",
    }
    assert graph.get_node("feature.fix_prepare").next == "feature.fix"
    assert graph.get_node("feature.fix").next == "feature.fix_validate"
    assert graph.get_node("feature.fix_validate").next == "feature.fix_review"
    assert graph.get_node("feature.fix_review").next == "feature.fix_review_route"
    assert graph.get_node("feature.fix_review_decision").branches == {
        "approved": "feature.acceptance",
        "implementation": "feature.fix",
        "evidence": "feature.evidence",
        "full_review": "feature.evidence",
        "scope": "feature.discovery",
        "_default": "feature.fix_review",
    }


def test_feature_fast_runtime_references_are_self_contained() -> None:
    process_text = FAST_PROCESS.read_text(encoding="utf-8")
    product_helper = (
        ROOT / "templates" / "feature-fast" / "scripts" / "product.sh"
    ).read_text(encoding="utf-8")
    receipt_helper = (
        ROOT / "templates" / "feature-fast" / "scripts" / "product_receipt.py"
    ).read_text(encoding="utf-8")
    serve_helper = (
        ROOT / "templates" / "feature-fast" / "scripts" / "serve.sh"
    ).read_text(encoding="utf-8")

    combined = "\n".join(
        (process_text, product_helper, receipt_helper, serve_helper)
    )
    assert ".ft/process/feature-fast/" in combined
    assert ".ft/process/feature/" not in combined


def test_feature_fast_fix_validator_anchors_and_approves_only_focal_delta(
    tmp_path: Path,
) -> None:
    root = _focal_fix_project(tmp_path)

    prepared = _run_fast_validator(root, "prepare-fix")
    assert prepared.returncode == 0, prepared.stderr
    baseline = yaml.safe_load(
        (root / "docs/feature-fix-baseline.yml").read_text(encoding="utf-8")
    )
    assert baseline["findings"] == ["F-01"]
    assert baseline["source_review"] == "docs/feature-review.yml"

    no_fix = _run_fast_validator(root, "fix-implementation")
    assert no_fix.returncode == 1
    assert "não alterou nenhum arquivo de produto" in no_fix.stderr

    _write(root, "project/app.py", "VALUE = 2\n")
    _write(
        root,
        "project/tests/test_app.py",
        "def test_value():\n    assert 2 == 2\n",
    )
    fixed = _run_fast_validator(root, "fix-implementation")
    assert fixed.returncode == 0, fixed.stderr

    fingerprint = "sha256:fixed"
    _write(
        root,
        "docs/feature-validation.json",
        json.dumps({"fingerprint": fingerprint}),
    )
    _write(
        root,
        "docs/feature-fix-review.md",
        "| Finding | Status | Evidência |\n"
        "|---|---|---|\n"
        "| F-01 | PASS | teste focal cobre VALUE=2. |\n",
    )
    _write(
        root,
        "docs/feature-fix-review.yml",
        "schema_version: 1\n"
        "verdict: APPROVED\n"
        "review_route: approved\n"
        "summary: Correção focal aprovada.\n"
        "source_review: docs/feature-review.yml\n"
        f"base_commit: {baseline['base_commit']}\n"
        f"receipt_fingerprint: {fingerprint}\n"
        "findings:\n"
        "  - id: F-01\n"
        "    status: PASS\n"
        "    evidence: teste focal cobre VALUE=2\n",
    )

    reviewed = _run_fast_validator(root, "fix-review")
    assert reviewed.returncode == 0, reviewed.stderr


def test_feature_fast_fix_validator_requires_full_review_outside_workset(
    tmp_path: Path,
) -> None:
    root = _focal_fix_project(tmp_path)
    prepared = _run_fast_validator(root, "prepare-fix")
    assert prepared.returncode == 0, prepared.stderr
    baseline = yaml.safe_load(
        (root / "docs/feature-fix-baseline.yml").read_text(encoding="utf-8")
    )
    fingerprint = "sha256:expanded"
    _write(root, "project/app.py", "VALUE = 2\n")
    _write(root, "project/outside.py", "EXPANDED = True\n")
    _write(
        root,
        "docs/feature-validation.json",
        json.dumps({"fingerprint": fingerprint}),
    )
    _write(
        root,
        "docs/feature-fix-review.md",
        "| Finding | Status | Evidência |\n"
        "|---|---|---|\n"
        "| F-01 | PASS | corrigido, mas delta expandiu. |\n",
    )
    route = (
        "schema_version: 1\n"
        "verdict: APPROVED\n"
        "review_route: approved\n"
        "summary: Correção aprovada.\n"
        "source_review: docs/feature-review.yml\n"
        f"base_commit: {baseline['base_commit']}\n"
        f"receipt_fingerprint: {fingerprint}\n"
        "findings:\n"
        "  - id: F-01\n"
        "    status: PASS\n"
        "    evidence: corrigido\n"
    )
    _write(root, "docs/feature-fix-review.yml", route)

    expanded = _run_fast_validator(root, "fix-review")
    assert expanded.returncode == 1
    assert "correção expandida exige full_review/scope" in expanded.stderr

    _write(
        root,
        "docs/feature-fix-review.yml",
        route.replace("verdict: APPROVED", "verdict: REJECTED").replace(
            "review_route: approved",
            "review_route: full_review",
        ),
    )
    fallback = _run_fast_validator(root, "fix-review")
    assert fallback.returncode == 0, fallback.stderr
