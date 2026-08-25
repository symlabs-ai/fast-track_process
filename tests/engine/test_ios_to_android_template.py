from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from ft.cli.main import available_templates
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "ios-to-android"
PROCESS = TEMPLATE / "process.yml"
VALIDATOR = TEMPLATE / "scripts" / "validate_ios_to_android.py"


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _yaml(root: Path, relative: str, payload: object) -> Path:
    return _write(
        root,
        relative,
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    )


def _run(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), mode],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _write(tmp_path, "ios/App.swift", "import SwiftUI\nstruct AppRoot {}\n")
    _write(tmp_path, "docs/ios-to-android-request.md", "Portar PB-042.\n")
    _write(
        tmp_path,
        "docs/PROJECT_BACKLOG.md",
        "# PROJECT_BACKLOG\n\n"
        "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| PB-042 | Feature | P0 | planned | PRD | Android | Paridade | — | ciclo |\n",
    )
    _write(
        tmp_path,
        "docs/FEATURES.md",
        "# FEATURES\n\n| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| FEAT-001 | active | PB-001 | App | App iOS | legacy | ios/App.swift | — | — |\n",
    )
    _yaml(
        tmp_path,
        ".ft/project.yml",
        {
            "schema_version": 1,
            "project_id": "mobile-app",
            "objective": "Entregar o produto em iOS e Android",
            "target": "android-port",
            "lifecycle": {
                "phase": "maintenance",
                "owner_template": "mvp-builder",
                "delivered_revision": "a" * 40,
            },
            "definition_of_done": {
                "backlog": {
                    "path": "docs/PROJECT_BACKLOG.md",
                    "priorities": ["P0"],
                    "accepted_statuses": ["done", "accepted"],
                    "require_evidence": True,
                },
                "required_gates": [],
                "require_clean_checkout": True,
                "require_no_open_cycles": True,
            },
            "validation": {
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
                    "android": {
                        "targets": {
                            "emulator": {"required": True},
                            "physical": {"required": True},
                        }
                    },
                    "ios": {
                        "targets": {
                            "simulator": {"required": True},
                            "physical": {"required": True},
                        }
                    },
                },
            },
        },
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    return tmp_path


def _contracts(root: Path, *, implemented: bool = False) -> None:
    _yaml(
        root,
        "docs/ios-android-port-plan.yml",
        {
            "schema_version": 1,
            "clarification_status": "clear",
            "backlog_item": "PB-042",
            "ios_roots": ["ios"],
            "android_root": "android",
            "application_id": "com.example.mobile",
            "shared_strategy": "contratos de domínio estáveis",
            "shared_contracts": ["API e modelos"],
            "out_of_scope": ["publicação nas lojas"],
        },
    )
    _write(root, "docs/ios-android-questions.md", "Nenhuma pergunta pendente.\n")
    item = {
        "id": "CAP-001",
        "name": "Listar itens",
        "ios_evidence": ["ios/App.swift"],
        "parity": "exact",
        "android_strategy": "adapter de UI Android",
        "status": "implemented" if implemented else "planned",
        "acceptance_criteria": [
            {"id": "PAC-001", "assertion": "Lista renderiza dados persistidos"}
        ],
        "android_evidence": ["android/app/build.gradle.kts"] if implemented else [],
        "test_evidence": ["android/app/src/test/ListTest.kt"] if implemented else [],
    }
    _yaml(
        root,
        "docs/ios-android-capabilities.yml",
        {"schema_version": 1, "capabilities": [item]},
    )


def _android_foundation(root: Path) -> None:
    _write(root, "android/settings.gradle.kts", 'rootProject.name = "mobile"\n')
    _write(root, "android/app/build.gradle.kts", "plugins {}\n")
    _write(
        root,
        "android/app/src/test/ListTest.kt",
        "class ListTest { fun verifiesList() = check(true) }\n",
    )


def test_template_is_discoverable_pristine_and_valid() -> None:
    assert "ios-to-android" in available_templates()
    validate_template_is_pristine(TEMPLATE)
    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)
    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["execution_policy"]["project_role"] == "maintenance"
    text = PROCESS.read_text(encoding="utf-8")
    assert ".ft/process/ios-to-android/scripts/" in text
    assert "templates/ios-to-android" not in text


def test_preflight_requires_real_ios_signal_and_ft_sources(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _run(root, "preflight").returncode == 0
    (root / "ios/App.swift").unlink()
    result = _run(root, "preflight")
    assert result.returncode == 1
    assert "app iOS" in result.stderr


def test_discovery_rejects_duplicate_acceptance_ids(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _contracts(root)
    payload = yaml.safe_load((root / "docs/ios-android-capabilities.yml").read_text())
    duplicate = dict(payload["capabilities"][0])
    duplicate["id"] = "CAP-002"
    payload["capabilities"].append(duplicate)
    _yaml(root, "docs/ios-android-capabilities.yml", payload)
    result = _run(root, "discovery")
    assert result.returncode == 1
    assert "critério inválido ou duplicado" in result.stderr


def test_contract_requires_same_git_and_all_four_targets(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _contracts(root)
    _android_foundation(root)
    assert _run(root, "contract").returncode == 0

    (root / "android/.git").mkdir()
    result = _run(root, "contract")
    assert result.returncode == 1
    assert ".git aninhado" in result.stderr
    (root / "android/.git").rmdir()

    project = yaml.safe_load((root / ".ft/project.yml").read_text())
    project["validation"]["platforms"]["android"]["targets"]["physical"]["required"] = (
        False
    )
    _yaml(root, ".ft/project.yml", project)
    result = _run(root, "contract")
    assert result.returncode == 1
    assert "android/physical" in result.stderr


def test_implementation_binds_each_capability_to_code_and_test(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _contracts(root, implemented=True)
    _android_foundation(root)
    _write(
        root,
        "docs/ios-android-implementation.md",
        "# Implementação\n\n## Arquitetura\nAdapters.\n\n## Capacidades\nCAP-001.\n\n"
        "## Testes\nPAC-001.\n\n## Riscos Residuais\nNenhum conhecido.\n",
    )
    assert _run(root, "implementation").returncode == 0

    (root / "android/app/src/test/ListTest.kt").unlink()
    result = _run(root, "implementation")
    assert result.returncode == 1
    assert "path ausente" in result.stderr


def test_review_requires_exact_capability_coverage(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _contracts(root, implemented=True)
    _android_foundation(root)
    _yaml(
        root,
        "docs/ios-android-review.yml",
        {
            "schema_version": 1,
            "verdict": "APPROVED",
            "review_route": "approved",
            "summary": "Paridade comprovada",
            "capability_results": {"CAP-001": "PASS"},
            "findings": [],
        },
    )
    _write(root, "docs/ios-android-review.md", "# Review\n\nCAP-001 PASS.\n")
    assert _run(root, "review").returncode == 0

    review = yaml.safe_load((root / "docs/ios-android-review.yml").read_text())
    review["capability_results"] = {}
    _yaml(root, "docs/ios-android-review.yml", review)
    result = _run(root, "review")
    assert result.returncode == 1
    assert "cobrir exatamente" in result.stderr
