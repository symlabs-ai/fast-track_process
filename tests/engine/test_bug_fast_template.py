"""Contracts for the two-call ``bug-fast`` template."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from ft.cli.main import available_templates
from ft.engine.context_profiles import KNOWN_CONTEXT_PROFILES
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "bug-fast"
PROCESS = TEMPLATE / "process.yml"
REGRESSION_ARGV = ["python", "-B", "tests/test_add.py"]

BACKLOG = """# PROJECT_BACKLOG

## Itens do Backlog

| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |
|---|---|---|---|---|---|---|---|---|
| PB-001 | Feature | P0 | accepted | PRD | Calculadora | Soma funciona | tests | Aceito |
| PB-002 | Bug | P1 | in_progress | feature-request | Corrigir soma | Soma deve adicionar | — | Ciclo atual |
"""

FEATURES = """# FEATURES

## Catálogo de Features

| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |
|---|---|---|---|---|---|---|---|---|
| FEAT-001 | active | PB-001 | Calculadora | Executa operações básicas. | cycle-01 | tests | — | Entrega inicial. |
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _project(tmp_path: Path) -> Path:
    local = tmp_path / ".ft" / "process" / "bug-fast"
    shutil.copytree(TEMPLATE, local)
    _write(tmp_path, ".gitignore", "__pycache__/\n*.pyc\n")
    _write(
        tmp_path,
        ".ft/manifest.yml",
        "schema_version: 3\n"
        "processes:\n"
        "  bug-fast:\n"
        "    path: .ft/process/bug-fast/process.yml\n"
        "    template: bug-fast\n"
        "    entrypoint: run\n",
    )
    _write(
        tmp_path,
        "docs/feature-request.md",
        "#BUG PB-002: add(1, 2) retorna -1; esperado 3.\n",
    )
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", BACKLOG)
    _write(tmp_path, "docs/FEATURES.md", FEATURES)
    _write(
        tmp_path,
        "CHANGELOG.md",
        "# Changelog\n\n## [Unreleased]\n\n"
        "- #BUG PB-001 / FEAT-001 — correção anterior.\n",
    )
    _write(
        tmp_path,
        "project/Makefile",
        "build:\n"
        "\t@python -m py_compile app.py tests/test_add.py\n"
        "\t@printf 'build\\n' >> ../validation-calls.log\n\n"
        "test:\n"
        "\t@python -B tests/test_add.py\n"
        "\t@printf 'test\\n' >> ../validation-calls.log\n\n"
        "run:\n\t@true\n\n"
        "url:\n\t@echo http://127.0.0.1:8021\n",
    )
    _write(
        tmp_path,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n    return left - right\n",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


def _test_source() -> str:
    return (
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from app import add\n\n"
        "actual = add(1, 2)\n"
        "assert actual == 3, f'expected total 3, got {actual}'\n"
        "print('regression passed')\n"
    )


def _subprocess_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    )
    return environment


def _run(
    root: Path,
    mode: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(root / ".ft/process/bug-fast/scripts/validate_bug.py"),
            mode,
            *arguments,
        ],
        cwd=root,
        env=_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


def _product(
    root: Path,
    phase: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(root / ".ft/process/bug-fast/scripts/product.sh"),
            phase,
            "--",
            *REGRESSION_ARGV,
        ],
        cwd=root,
        env=_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


def _report(root: Path) -> None:
    _write(
        root,
        "docs/bug-report.md",
        "---\n"
        "backlog_item: PB-002\n"
        "target_feature: FEAT-001\n"
        "severity: medium\n"
        "---\n\n"
        "Resultado: FIXED\n\n"
        "## Sintoma\nA soma subtraía o operando direito.\n\n"
        "## Comportamento esperado\nadd(1, 2) deve retornar 3.\n\n"
        "## Causa raiz\nO operador da implementação era subtração.\n\n"
        "## Regressão\nTeste focal cobre a soma de dois inteiros.\n\n"
        "Comando de regressão: python -B tests/test_add.py\n"
        "Assinatura RED: expected total 3\n\n"
        "## Correção\nSubstituído o operador por adição.\n\n"
        "## Risco\nNenhum conhecido no escopo focal.\n",
    )


def _implement(root: Path, source: str = "    return left + right\n") -> None:
    assert _run(root, "baseline").returncode == 0
    assert _run(root, "begin").returncode == 0
    _write(root, "project/tests/test_add.py", _test_source())
    red = _product(root, "red")
    assert red.returncode == 0, red.stderr
    _write(
        root,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n" + source,
    )
    green = _product(root, "green")
    assert green.returncode == 0, green.stderr
    _report(root)
    full = _run(root, "full")
    assert full.returncode == 0, full.stderr


def _receipt(root: Path) -> dict[str, object]:
    return json.loads((root / "docs/bug-validation.json").read_text(encoding="utf-8"))


def _baseline(root: Path) -> dict[str, object]:
    return yaml.safe_load((root / "docs/bug-baseline.yml").read_text(encoding="utf-8"))


def _approved_review(root: Path) -> None:
    _write(
        root,
        "docs/bug-review.md",
        "# Review\n\n"
        "- `project/app.py`: correção mínima.\n"
        "- `project/tests/test_add.py`: regressão congelada.\n"
        "- RED e GREEN usam o mesmo teste.\n"
        "- receipt completo válido.\n"
        "- escopo focal preservado.\n",
    )
    _write(
        root,
        "docs/bug-review.yml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "verdict": "APPROVED",
                "review_route": "approved",
                "summary": "Correção focal suficiente.",
                "base_commit": _baseline(root)["base_commit"],
                "receipt_fingerprint": _receipt(root)["fingerprint"],
                "checks": [
                    {"id": key, "status": "PASS", "evidence": f"{key} verificado"}
                    for key in ("regression", "minimal_delta", "receipt", "scope")
                ],
                "findings": [],
            },
            sort_keys=False,
        ),
    )


def _rejected_review(root: Path) -> None:
    _write(
        root,
        "docs/bug-review.md",
        "# Review\n\n"
        "- `project/app.py`: B-01 encontra delta não mínimo.\n"
        "- `project/tests/test_add.py`: regressão congelada.\n"
        "- RED e GREEN válidos; receipt válido; escopo focal.\n",
    )
    checks = [
        {"id": key, "status": "PASS", "evidence": f"{key} verificado"}
        for key in ("regression", "receipt", "scope")
    ]
    checks.append(
        {
            "id": "minimal_delta",
            "status": "FAIL",
            "evidence": "B-01: expressão contém operação redundante",
        }
    )
    _write(
        root,
        "docs/bug-review.yml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "verdict": "REJECTED",
                "review_route": "fix",
                "summary": "Remover operação redundante.",
                "base_commit": _baseline(root)["base_commit"],
                "receipt_fingerprint": _receipt(root)["fingerprint"],
                "checks": checks,
                "findings": [
                    {
                        "id": "B-01",
                        "status": "open",
                        "route": "fix",
                        "evidence": "Trocar left + right + 0 por left + right.",
                    }
                ],
            },
            sort_keys=False,
        ),
    )


def test_bug_fast_catalog_graph_and_sessions() -> None:
    validate_template_is_pristine(TEMPLATE)
    assert "bug-fast" in available_templates()
    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "bug_fast"
    assert graph.meta["session_policy"] == {
        "mode": "sprint",
        "providers": ["claude", "codex"],
        "initial_plan": "disabled",
        "parallel_strategy": "fork",
        "recovery": "rehydrate",
    }
    delegated = [
        node.id for node in graph.nodes.values() if node.executor.startswith("llm_")
    ]
    assert delegated == [
        "bug.diagnose_fix",
        "bug.review",
        "bug.fix",
        "bug.fix_review",
    ]
    happy_path_delegated = [
        "bug.diagnose_fix",
        "bug.review",
    ]
    assert len(happy_path_delegated) == 2
    assert graph.get_node("bug.diagnose_fix").llm_episode == "bug_fix"
    assert graph.get_node("bug.fix").llm_episode == "bug_fix"
    assert graph.get_node("bug.review").type == "review"
    assert graph.get_node("bug.fix_review").type == "review"
    assert graph.get_node("bug.reconcile").executor == "python"
    assert graph.get_node("bug.reconcile").type == "gate"
    assert graph.get_node("bug.acceptance").reject_next == "bug.diagnose_fix"
    assert graph.get_node("bug.review_decision").branches == {
        "approved": "bug.acceptance",
        "fix": "bug.fix_prepare",
        "scope": "bug.scope_block",
        "_default": "bug.review",
    }
    assert graph.get_node("bug.fix_review_decision").branches["approved"] == (
        "bug.fix_full_validate"
    )
    assert graph.get_node("bug.fix_review_decision").branches["full_review"] == (
        "bug.fix_full_review_validate"
    )
    assert graph.get_node("bug.fix_full_review_validate").next == "bug.review"
    assert graph.get_node("bug.fix_full_validate").next == "bug.acceptance"
    assert {
        "bug_fast.fix",
        "bug_fast.review",
        "bug_fast.fix_review",
    } <= KNOWN_CONTEXT_PROFILES


def test_bug_fast_runtime_references_are_self_contained() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROCESS,
            TEMPLATE / "scripts/product.sh",
            TEMPLATE / "scripts/serve.sh",
            TEMPLATE / "scripts/validate_bug.py",
        )
    )
    assert ".ft/process/bug-fast/" in combined
    assert ".ft/process/bug/" not in combined


def test_bug_fast_happy_path_uses_one_suite_and_deterministic_reconcile(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _implement(root)
    _approved_review(root)

    reviewed = _run(root, "review")
    assert reviewed.returncode == 0, reviewed.stderr
    reconciled = _run(root, "reconcile-apply")
    assert reconciled.returncode == 0, reconciled.stderr
    final = _run(root, "final")
    assert final.returncode == 0, final.stderr

    assert (root / "validation-calls.log").read_text(encoding="utf-8") == (
        "build\ntest\n"
    )
    assert "| PB-002 | Bug | P1 | accepted |" in (
        root / "docs/PROJECT_BACKLOG.md"
    ).read_text(encoding="utf-8")
    assert "PB-001, PB-002" in (root / "docs/FEATURES.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.count("#BUG PB-002 / FEAT-001") == 1
    result = (root / "docs/bug-result.md").read_text(encoding="utf-8")
    assert all(token in result for token in ("PB-002", "FEAT-001", "RED", "GREEN"))

    repeated = _run(root, "reconcile-apply")
    assert repeated.returncode == 0, repeated.stderr
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == changelog
    assert (root / "validation-calls.log").read_text(encoding="utf-8") == (
        "build\ntest\n"
    )


def test_bug_fast_rejected_review_anchors_and_audits_only_fix(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _implement(root, "    return left + right + 0\n")
    assert (root / "validation-calls.log").read_text(encoding="utf-8") == (
        "build\ntest\n"
    )
    (root / "validation-calls.log").unlink()
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial bug correction")
    _rejected_review(root)
    assert _run(root, "review").returncode == 0

    prepared = _run(root, "prepare-fix")
    assert prepared.returncode == 0, prepared.stderr
    anchor = yaml.safe_load(
        (root / "docs/bug-fix-baseline.yml").read_text(encoding="utf-8")
    )
    assert anchor["findings"] == ["B-01"]
    assert anchor["initial_product_paths"] == [
        "project/app.py",
        "project/tests/test_add.py",
    ]

    assert _run(root, "begin-fix").returncode == 0
    _write(
        root,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n    return left + right\n",
    )
    green = _product(root, "green")
    assert green.returncode == 0, green.stderr
    fixed = _run(root, "fix-implementation")
    assert fixed.returncode == 0, fixed.stderr

    _write(
        root,
        "docs/bug-fix-review.md",
        "# Auditoria do fix\n\n- `project/app.py`: B-01 PASS; redundância removida.\n",
    )
    _write(
        root,
        "docs/bug-fix-review.yml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "verdict": "APPROVED",
                "review_route": "approved",
                "summary": "Fix focal aprovado.",
                "source_review": "docs/bug-review.yml",
                "base_commit": anchor["base_commit"],
                "receipt_fingerprint": anchor["receipt_fingerprint"],
                "findings": [
                    {
                        "id": "B-01",
                        "status": "PASS",
                        "evidence": "Expressão agora é left + right.",
                    }
                ],
            },
            sort_keys=False,
        ),
    )
    audited = _run(root, "fix-review")
    assert audited.returncode == 0, audited.stderr
    assert not (root / "validation-calls.log").exists()

    full = _run(root, "full")
    assert full.returncode == 0, full.stderr
    reconciled = _run(root, "reconcile-apply")
    assert reconciled.returncode == 0, reconciled.stderr

    assert (root / "validation-calls.log").read_text(encoding="utf-8") == (
        "build\ntest\n"
    )


def test_bug_fast_refreshes_fix_anchor_after_a_new_full_review(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _implement(root, "    return left + right + 0\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial bug correction")
    _rejected_review(root)
    assert _run(root, "review").returncode == 0
    assert _run(root, "prepare-fix").returncode == 0
    first = yaml.safe_load(
        (root / "docs/bug-fix-baseline.yml").read_text(encoding="utf-8")
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first rejected review")
    new_base = _git(root, "rev-parse", "HEAD")

    review = yaml.safe_load((root / "docs/bug-review.yml").read_text(encoding="utf-8"))
    review["summary"] = "Review completa renovada após expansão focal."
    _write(
        root,
        "docs/bug-review.yml",
        yaml.safe_dump(review, sort_keys=False),
    )
    assert _run(root, "review").returncode == 0

    refreshed = _run(root, "prepare-fix")

    assert refreshed.returncode == 0, refreshed.stderr
    assert "REFRESHED" in refreshed.stdout
    second = yaml.safe_load(
        (root / "docs/bug-fix-baseline.yml").read_text(encoding="utf-8")
    )
    assert second["base_commit"] == new_base
    assert second["source_review_sha256"] != first["source_review_sha256"]


def test_bug_fast_tracks_shared_src_changes_with_project_product(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    assert _run(root, "baseline").returncode == 0
    assert _run(root, "begin").returncode == 0
    _write(root, "project/tests/test_add.py", _test_source())
    red = _product(root, "red")
    assert red.returncode == 0, red.stderr
    _write(
        root,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n    return left + right\n",
    )
    _write(root, "src/shared_contract.py", "SNAPSHOT_VERSION = 1\n")
    green = _product(root, "green")
    assert green.returncode == 0, green.stderr
    _report(root)

    implemented = _run(root, "implementation")

    assert implemented.returncode == 0, implemented.stderr
    assert "3 arquivo(s)" in implemented.stdout


def test_bug_fast_counts_reconciled_receipts_as_derived_artifacts(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    assert _run(root, "baseline").returncode == 0
    assert _run(root, "begin").returncode == 0
    _write(root, "project/tests/test_add.py", _test_source())
    red = _product(root, "red")
    assert red.returncode == 0, red.stderr
    _write(
        root,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n    return left + right\n",
    )
    for index in range(6):
        _write(
            root,
            f"project/gate/receipts/lane-{index}.json",
            '{"status": "BLOCKED"}\n',
        )
    _write(root, "project/gate/pb001-package.json", '{"decision": "NO-GO"}\n')
    _write(root, "project/gate/pb001-result.json", '{"status": "BLOCKED"}\n')
    green = _product(root, "green")
    assert green.returncode == 0, green.stderr
    _report(root)

    implemented = _run(root, "implementation")

    assert implemented.returncode == 0, implemented.stderr
    assert "10 arquivo(s)" in implemented.stdout
    assert "2 primário(s) e 8 derivado(s)" in implemented.stdout


def test_bug_fast_internal_acceptance_does_not_require_make_url(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _write(
        root,
        "project/Makefile",
        "build:\n\t@true\n\ntest:\n\t@true\n\nrun:\n\t@true\n",
    )

    result = subprocess.run(
        ["bash", str(root / ".ft/process/bug-fast/scripts/serve.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "produto interno sem target url" in result.stdout
    assert not (root / ".serve_url").exists()


def test_bug_fast_scope_route_blocks_with_feature_fast_instruction(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    blocked = _run(root, "scope-block")

    assert blocked.returncode == 1
    assert "feature-fast" in blocked.stderr
