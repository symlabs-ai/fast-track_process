"""Contract tests for the opt-in feature-fast process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from ft.engine.graph import load_graph
from ft.engine.process_validator import validate_process
from ft.templates.catalog import TemplateCatalog

ROOT = Path(__file__).resolve().parents[2]
FAST_PROCESS = ROOT / "templates" / "feature-fast" / "process.yml"
FAST_VALIDATOR = ROOT / "templates" / "feature-fast" / "scripts" / "validate_feature.py"


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
    return {field: _normalize_runtime_paths(node.get(field)) for field in fields}


def _normalize_runtime_paths(value):
    if isinstance(value, str):
        return value.replace(
            ".ft/process/feature-fast/",
            ".ft/process/feature/",
        )
    if isinstance(value, list):
        return [_normalize_runtime_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_runtime_paths(item) for key, item in value.items()}
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


def _focal_fix_project(
    tmp_path: Path,
    *,
    physical_lane: bool = False,
) -> Path:
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
        "  - project/tests/test_app.py\n"
        + (
            "receipt_dependencies:\n"
            "  - id: physical_lab\n"
            "    mode: physical\n"
            "    receipt: docs/physical-lab.json\n"
            "    depends_on:\n"
            "      - project/device/**\n"
            if physical_lane
            else ""
        ),
    )
    _write(
        tmp_path,
        "docs/PROJECT_BACKLOG.md",
        "| ID | Status |\n|---|---|\n| PB-002 | in_progress |\n",
    )
    _write(
        tmp_path,
        "docs/FEATURES.md",
        "| ID | Backlog |\n|---|---|\n| FEAT-001 | PB-001 |\n",
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
    _write(tmp_path, "project/app.py", "VALUE = 0\n")
    _write(
        tmp_path,
        "project/tests/test_app.py",
        "def test_value():\n    assert 0 == 0\n",
    )
    if physical_lane:
        _write(tmp_path, "project/device/probe.kt", "const val VERSION = 1\n")
        _write(
            tmp_path,
            "docs/physical-lab.json",
            json.dumps({"result": "pass", "adapter": "lab"}),
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
    receipt_baseline = _run_fast_validator(
        tmp_path,
        "prepare-receipt-baseline",
    )
    assert receipt_baseline.returncode == 0, receipt_baseline.stderr
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "receipt baseline"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-qb", "cycle"], cwd=tmp_path, check=True)

    _write(tmp_path, "project/app.py", "VALUE = 1\n")
    _write(
        tmp_path,
        "project/tests/test_app.py",
        "def test_value():\n    assert 1 == 1\n",
    )
    impact_result = _run_fast_validator(tmp_path, "prepare-impact")
    assert impact_result.returncode == 0, impact_result.stderr
    impact = yaml.safe_load(
        (tmp_path / "docs/feature-impact.yml").read_text(encoding="utf-8")
    )
    _write(
        tmp_path,
        "docs/feature-pre-review.md",
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | PASS | teste focal presente. |\n",
    )
    _write(
        tmp_path,
        "docs/feature-pre-review.yml",
        "schema_version: 1\n"
        f"review_id: {impact['pre_review_id']}\n"
        "verdict: APPROVED\n"
        "review_route: approved\n"
        "summary: Sem falha semântica óbvia.\n",
    )
    receipt_fingerprint = "sha256:" + ("1" * 64)
    _write(
        tmp_path,
        "docs/feature-validation.json",
        json.dumps(
            {
                "fingerprint": receipt_fingerprint,
                "commands": [],
            }
        ),
    )
    _write(
        tmp_path,
        "docs/implementation-report.md",
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | PASS | project/tests/test_app.py. |\n",
    )
    _write(
        tmp_path,
        "docs/feature-evidence.yml",
        "schema_version: 1\n"
        "receipt: docs/feature-validation.json\n"
        "commands: []\n"
        "acceptance:\n"
        "  - id: AC-01\n"
        "    status: PASS\n"
        "    tests:\n"
        "      - project/tests/test_app.py\n"
        "    artifacts: []\n",
    )
    review_context_result = _run_fast_validator(tmp_path, "prepare-review")
    assert review_context_result.returncode == 0, review_context_result.stderr
    review_context = yaml.safe_load(
        (tmp_path / "docs/feature-review-context.yml").read_text(encoding="utf-8")
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
        f"review_id: {review_context['review_id']}\n"
        f"receipt_fingerprint: {receipt_fingerprint}\n"
        "verdict: REJECTED\n"
        "review_route: implementation\n"
        "summary: F-01 precisa de correção.\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
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
    assert graph.meta["version"] == "2.0.1"
    assert graph.meta["execution_policy"]["max_acceptance_criteria_per_cycle"] == 6
    assert graph.meta["session_policy"] == {
        "mode": "sprint",
        "providers": ["claude", "codex"],
        "initial_plan": "internal",
        "parallel_strategy": "fork",
        "recovery": "rehydrate",
    }


def test_feature_fast_preserves_feature_safety_contract() -> None:
    # O template feature (baseline) foi removido do catálogo; os contratos
    # de segurança que a comparação garantia viram asserções absolutas.
    fast = _payload(FAST_PROCESS)

    assert "docs/feature-fix-baseline.yml" in fast["artifact_policy"]["cycle"]
    # Os checks determinísticos provam os AC-* e sobrevivem ao `ft close`.
    assert "checks/" in fast["artifact_policy"]["canonical"]
    assert not [
        entry
        for entry in fast["artifact_policy"]["cycle"]
        if entry.startswith("checks")
    ]

    policy = fast["correction_policy"]
    assert policy["follow_graph_after_retry"] is True
    assert policy["scope_rejection_restarts_at"] == "feature.discovery"
    assert policy["acceptance_rejection_restarts_at"] == "feature.implement"
    assert policy["mandatory_after_implementation"] == [
        "feature.checks",
        "feature.verify",
        "feature.acceptance",
    ]
    assert sum(node["type"] == "human_gate" for node in fast["nodes"]) == 3


def test_feature_fast_session_boundaries_match_process_roles() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.get_node("feature.discovery").sprint == "feature-01-scope"
    assert graph.get_node("feature.implement").sprint == "feature-02-build"
    assert graph.get_node("feature.evidence_gate").sprint == "feature-02-build"
    # A cadeia de revisão é determinística: nenhum node LLM entre implement e
    # o aceite, exceto a correção focal.
    for node_id in ("feature.checks", "feature.verify"):
        node = graph.get_node(node_id)
        assert node.sprint == "feature-02-build"
        assert node.type == "gate"
        assert node.executor == "python"

    fix = graph.get_node("feature.fix")
    assert fix.sprint == "feature-02-build"
    assert fix.llm_episode == "feature_implementation"
    assert graph.get_node("feature.reconcile").sprint == "feature-03-acceptance"
    llm_nodes = sorted(
        node.id
        for node in graph.nodes.values()
        if str(node.executor).startswith("llm_")
    )
    assert llm_nodes == [
        "feature.discovery",
        "feature.fix",
        "feature.implement",
        "feature.reconcile",
    ]


def test_feature_fast_uses_focal_fix_and_delta_review_topology() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.get_node("feature.review_decision").branches == {
        "approved": "feature.acceptance",
        "implementation": "feature.fix_prepare",
        "evidence": "feature.evidence_gate",
        "scope": "feature.discovery",
        "_default": "feature.verify",
    }
    assert graph.get_node("feature.fix_prepare").next == "feature.fix"
    assert graph.get_node("feature.fix").next == "feature.fix_validate"
    assert graph.get_node("feature.fix_validate").next == "feature.fix_full_validate"
    # Depois do fix focal a atestação é refeita do zero, nunca reaproveitada.
    assert graph.get_node("feature.fix_full_validate").next == "feature.review_prepare"
    assert graph.get_node("feature.review_prepare").next == "feature.verify"
    assert graph.get_node("feature.scope_gate").next == "feature.receipt_baseline"
    assert graph.get_node("feature.implement").next == "feature.impact_prepare"
    assert graph.get_node("feature.impact_prepare").next == "feature.checks"
    assert graph.get_node("feature.checks").next == "feature.pre_review_route"
    assert graph.get_node("feature.pre_review_decision").branches == {
        "approved": "feature.product_validate",
        "implementation": "feature.implement",
        "scope": "feature.discovery",
        "_default": "feature.checks",
    }
    assert graph.get_node("feature.product_validate").next == "feature.evidence_gate"
    assert graph.get_node("feature.evidence_gate").next == "feature.review_prepare"
    # O fix focal corrige código; ele não pode reescrever a prova.
    assert "checks" not in (graph.get_node("feature.fix").write_scope or [])
    assert "checks" in (graph.get_node("feature.implement").write_scope or [])


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

    combined = "\n".join((process_text, product_helper, receipt_helper, serve_helper))
    assert ".ft/process/feature-fast/" in combined
    assert ".ft/process/feature/" not in combined


def test_feature_fast_internal_acceptance_does_not_require_make_url(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / ".ft" / "process" / "feature-fast" / "scripts"
    scripts.mkdir(parents=True)
    (tmp_path / ".ft" / "manifest.yml").write_text(
        "schema_version: 3\nprocesses: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "feature.md").write_text(
        "---\ninterface: internal\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "Makefile").write_text(
        "build:\n\t@true\n\ntest:\n\t@true\n",
        encoding="utf-8",
    )
    for name in ("product.sh", "serve.sh"):
        source = ROOT / "templates" / "feature-fast" / "scripts" / name
        target = scripts / name
        target.write_bytes(source.read_bytes())
        target.chmod(0o755)

    result = subprocess.run(
        ["bash", str(scripts / "serve.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "entrega interna" in result.stdout
    assert not (tmp_path / ".serve_url").exists()


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

    fingerprint = baseline["receipt_fingerprint"]
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
        f"source_review_id: {baseline['source_review_id']}\n"
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
    fingerprint = baseline["receipt_fingerprint"]
    _write(root, "project/app.py", "VALUE = 2\n")
    _write(root, "project/outside.py", "EXPANDED = True\n")
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
        f"source_review_id: {baseline['source_review_id']}\n"
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


def test_feature_fast_requires_large_demands_to_be_sliced_at_six_acs(
    tmp_path: Path,
) -> None:
    root = _focal_fix_project(tmp_path)
    feature = (root / "docs/feature.md").read_text(encoding="utf-8")
    feature = feature.replace(
        "- AC-01: busca correta.",
        "\n".join(f"- AC-{index:02d}: comportamento {index}." for index in range(1, 8)),
    )
    _write(root, "docs/feature.md", feature)
    _write(
        root,
        "docs/feature-plan.md",
        "PB-002 FEAT-001 " + " ".join(f"AC-{index:02d}" for index in range(1, 8)),
    )
    _write(root, "docs/feature-discovery.md", "clarification_status: clear\n")
    _write(root, "docs/feature-questions.md", "Nenhuma pergunta pendente.\n")

    result = _run_fast_validator(root, "discovery")

    assert result.returncode == 1
    assert "excede o limite de 6 ACs" in result.stderr
    assert "fatias verticais" in result.stderr


def test_feature_fast_rejects_a_stale_full_review_id(tmp_path: Path) -> None:
    root = _focal_fix_project(tmp_path)
    route_path = root / "docs/feature-review.yml"
    route = route_path.read_text(encoding="utf-8")
    route_path.write_text(
        route.replace(
            next(line for line in route.splitlines() if line.startswith("review_id:")),
            "review_id: sha256:" + "0" * 64,
        ),
        encoding="utf-8",
    )

    result = _run_fast_validator(root, "review")

    assert result.returncode == 1
    assert "review_id diverge do contexto atual" in result.stderr


def test_feature_fast_reuses_physical_receipt_only_until_dependency_changes(
    tmp_path: Path,
) -> None:
    root = _focal_fix_project(tmp_path, physical_lane=True)
    context = yaml.safe_load(
        (root / "docs/feature-review-context.yml").read_text(encoding="utf-8")
    )
    physical = next(
        lane for lane in context["receipts"] if lane["id"] == "physical_lab"
    )
    assert physical["decision"] == "reuse"

    _write(root, "project/device/probe.kt", "const val VERSION = 2\n")
    impact_result = _run_fast_validator(root, "prepare-impact")
    assert impact_result.returncode == 0, impact_result.stderr
    impact = yaml.safe_load(
        (root / "docs/feature-impact.yml").read_text(encoding="utf-8")
    )
    physical_impact = next(
        lane for lane in impact["receipt_lanes"] if lane["id"] == "physical_lab"
    )
    assert physical_impact["impacted"] is True
    assert physical_impact["reuse_allowed"] is False

    pre_route = yaml.safe_load(
        (root / "docs/feature-pre-review.yml").read_text(encoding="utf-8")
    )
    pre_route["review_id"] = impact["pre_review_id"]
    _write(
        root,
        "docs/feature-pre-review.yml",
        yaml.safe_dump(pre_route, allow_unicode=True, sort_keys=False),
    )
    product_receipt = (root / "docs/feature-validation.json").read_text(
        encoding="utf-8"
    )
    _write(root, "docs/feature-validation.json", product_receipt)

    stale = _run_fast_validator(root, "prepare-review")

    assert stale.returncode == 1
    assert "physical-lab.json está obsoleto" in stale.stderr
    assert "ensaio deve ser reexecutado" in stale.stderr


def test_feature_fast_fix_reuses_or_refreshes_physical_receipt_by_dependency(
    tmp_path: Path,
) -> None:
    root = _focal_fix_project(tmp_path, physical_lane=True)
    prepared = _run_fast_validator(root, "prepare-fix")
    assert prepared.returncode == 0, prepared.stderr

    device = root / "project/device/probe.kt"
    physical_receipt = root / "docs/physical-lab.json"
    _write(root, "project/app.py", "VALUE = 2\n")
    _write(
        root,
        "project/tests/test_app.py",
        "def test_value():\n    assert 2 == 2\n",
    )
    _write(root, "docs/feature-validation.json", '{"fingerprint":"sha256:fixed"}\n')

    unchanged = _run_fast_validator(root, "fix-receipts")
    assert unchanged.returncode == 0, unchanged.stderr

    _write(root, "project/device/probe.kt", "const val VERSION = 2\n")
    product_receipt = root / "docs/feature-validation.json"
    baseline_ns = max(
        path.stat().st_mtime_ns
        for path in (root / "project").rglob("*")
        if path.is_file()
    )
    os.utime(physical_receipt, ns=(baseline_ns, baseline_ns))
    os.utime(
        device,
        ns=(baseline_ns + 1_000_000_000, baseline_ns + 1_000_000_000),
    )
    os.utime(
        product_receipt,
        ns=(baseline_ns + 2_000_000_000, baseline_ns + 2_000_000_000),
    )

    stale = _run_fast_validator(root, "fix-receipts")
    assert stale.returncode == 1
    assert "physical-lab.json está obsoleto" in stale.stderr

    os.utime(
        physical_receipt,
        ns=(baseline_ns + 3_000_000_000, baseline_ns + 3_000_000_000),
    )
    refreshed = _run_fast_validator(root, "fix-receipts")
    assert refreshed.returncode == 0, refreshed.stderr


def test_feature_fast_semantic_impact_allows_a_new_related_test(
    tmp_path: Path,
) -> None:
    root = _focal_fix_project(tmp_path)
    prepared = _run_fast_validator(root, "prepare-fix")
    assert prepared.returncode == 0, prepared.stderr
    baseline = yaml.safe_load(
        (root / "docs/feature-fix-baseline.yml").read_text(encoding="utf-8")
    )
    assert "app" in baseline["impact_keys"]

    _write(root, "project/app.py", "VALUE = 2\n")
    _write(
        root,
        "project/tests/app_test.py",
        "def test_related_value():\n    assert 2 == 2\n",
    )
    fingerprint = baseline["receipt_fingerprint"]
    _write(
        root,
        "docs/feature-fix-review.md",
        "| Finding | Status | Evidência |\n"
        "|---|---|---|\n"
        "| F-01 | PASS | app_test.py cobre a correção. |\n",
    )
    _write(
        root,
        "docs/feature-fix-review.yml",
        "schema_version: 1\n"
        "verdict: APPROVED\n"
        "review_route: approved\n"
        "summary: Correção focal e teste relacionado.\n"
        "source_review: docs/feature-review.yml\n"
        f"source_review_id: {baseline['source_review_id']}\n"
        f"base_commit: {baseline['base_commit']}\n"
        f"receipt_fingerprint: {fingerprint}\n"
        "findings:\n"
        "  - id: F-01\n"
        "    status: PASS\n"
        "    evidence: app_test.py cobre a correção\n",
    )

    result = _run_fast_validator(root, "fix-review")

    assert result.returncode == 0, result.stderr
