from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ft.cli.main import available_templates
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "material_design_pwa"
PROCESS = TEMPLATE / "process.yml"
VALIDATOR = TEMPLATE / "scripts" / "validate_mdpwa.py"
GUIDELINES = TEMPLATE / "guidelines" / "material_design_pwa.md"


AUDIT = """---
framework: vanilla
ui_root: src
has_manifest: false
has_service_worker: false
clarification_status: {clarification}
---

# Auditoria M3/PWA

Hex soltos em src/styles.css; sem manifesto nem service worker.
"""

PLAN = """---
backlog_item: PB-010
theme_file: src/styles/tokens.css
manifest_path: public/manifest.webmanifest
sw_source: src/sw.js
offline_fallback: {offline}
---

# Plano de Transformação

Fase 1 tokens, fase 2 shell, fase 3 PWA.
"""

BACKLOG = """# PROJECT_BACKLOG

| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |
|---|---|---|---|---|---|---|---|---|
| PB-001 | Feature | P0 | done | PRD | Cadastro | Cadastro funciona | tests | Aceito |
| PB-010 | Melhoria | P1 | {status} | mdpwa-request | UI Material + PWA | Checklist QA PASS | — | Ciclo atual |
"""

THEME = """:root {
  --md-sys-color-primary: #0057d8;
  --md-sys-color-on-primary: #ffffff;
  --md-sys-color-surface: #fcf8f8;
  --md-sys-color-on-surface: #1b1b1f;
  --md-sys-color-outline: #74777f;
  --md-sys-typescale-body-large-font: 400 1rem/1.5rem system-ui;
  --md-sys-shape-corner-medium: 12px;
  --space-4: 16px;
  --motion-medium: 220ms;
}

@media (prefers-color-scheme: dark) {
  :root {
    --md-sys-color-surface: #121316;
  }
}

:where(button, [href], input):focus-visible {
  outline: 3px solid var(--md-sys-color-primary);
}
"""

MANIFEST = {
    "name": "Minha PWA Material",
    "short_name": "MinhaPWA",
    "start_url": "/",
    "display": "standalone",
    "theme_color": "#0057d8",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}

REVIEW = """# Revisão

Resultado: APPROVED

| Item | Status |
|---|---|
| Tokens | PASS |
| Tipografia | PASS |
| Responsividade | PASS |
| Navegação | PASS |
| Acessibilidade | PASS |
| Instalação | PASS |
| Offline | PASS |
| Atualização | PASS |
| Payload | PASS |
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_validator(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), mode],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _base_project(root: Path, *, offline: str = "public/offline.html") -> Path:
    _write(root, "docs/mdpwa-audit.md", AUDIT.format(clarification="clear"))
    _write(root, "docs/mdpwa-questions.md", "Nenhuma pergunta pendente.\n")
    _write(root, "docs/mdpwa-plan.md", PLAN.format(offline=offline))
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.format(status="in_progress"))
    return root


def test_template_is_discoverable_pristine_and_ships_guidelines():
    assert "material_design_pwa" in available_templates()
    validate_template_is_pristine(TEMPLATE)
    guidelines = GUIDELINES.read_text(encoding="utf-8")
    assert "Material Design 3" in guidelines
    assert "citeturn" not in guidelines


def test_process_is_valid_and_references_guidelines():
    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "material_design_pwa"
    policy = graph.meta["execution_policy"]
    assert policy["entrypoint"] == "run"
    assert policy["template"] == "material_design_pwa"

    text = PROCESS.read_text(encoding="utf-8")
    assert ".ft/process/material_design_pwa/guidelines/material_design_pwa.md" in text
    assert ".ft/process/material_design_pwa/scripts/" in text


def test_preflight_requires_ui_harness(tmp_path):
    result = _run_validator(tmp_path, "preflight")
    assert result.returncode == 1
    assert "fastfy" in result.stderr

    _write(tmp_path, "Makefile", "build:\n\t@true\n\ntest:\n\t@true\n")
    result = _run_validator(tmp_path, "preflight")
    assert result.returncode == 1
    assert "run" in result.stderr and "url" in result.stderr

    _write(
        tmp_path,
        "Makefile",
        "build:\n\t@true\n\ntest:\n\t@true\n\nrun:\n\t@true\n\n"
        "url:\n\t@echo http://127.0.0.1:8021\n",
    )
    assert _run_validator(tmp_path, "preflight").returncode == 0


def test_audit_requires_questions_or_complete_plan(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "docs/mdpwa-audit.md", AUDIT.format(clarification="required"))
    result = _run_validator(root, "audit")
    assert result.returncode == 1
    assert "perguntas" in result.stderr

    _write(root, "docs/mdpwa-questions.md", "1. Qual a cor da marca?\n")
    assert _run_validator(root, "audit").returncode == 0

    _write(root, "docs/mdpwa-audit.md", AUDIT.format(clarification="clear"))
    assert _run_validator(root, "audit").returncode == 0

    _write(root, "docs/mdpwa-plan.md", PLAN.format(offline="public/offline.html").replace("PB-010", "sem-pb"))
    result = _run_validator(root, "audit")
    assert result.returncode == 1
    assert "backlog_item" in result.stderr


def test_audit_requires_backlog_item_registered(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.format(status="in_progress").replace("PB-010", "PB-099"))
    result = _run_validator(root, "audit")
    assert result.returncode == 1
    assert "PB-010" in result.stderr


def test_theme_requires_m3_token_contract(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "src/styles/tokens.css", ":root { --brand: #123456; }\n")
    result = _run_validator(root, "theme")
    assert result.returncode == 1
    assert "--md-sys-color-primary" in result.stderr
    assert "prefers-color-scheme" in result.stderr

    _write(root, "src/styles/tokens.css", THEME)
    assert _run_validator(root, "theme").returncode == 0


def test_pwa_requires_manifest_sw_and_offline_fallback(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "src/sw.js", "self.addEventListener('fetch', () => {});\n")
    _write(root, "public/offline.html", "<h1>Offline</h1>\n")

    incomplete = {key: value for key, value in MANIFEST.items() if key != "short_name"}
    _write(root, "public/manifest.webmanifest", json.dumps(incomplete))
    result = _run_validator(root, "pwa")
    assert result.returncode == 1
    assert "short_name" in result.stderr

    small_icons = dict(MANIFEST)
    small_icons["icons"] = [MANIFEST["icons"][0]]
    _write(root, "public/manifest.webmanifest", json.dumps(small_icons))
    result = _run_validator(root, "pwa")
    assert result.returncode == 1
    assert "512x512" in result.stderr

    _write(root, "public/manifest.webmanifest", json.dumps(MANIFEST))
    assert _run_validator(root, "pwa").returncode == 0

    (root / "public/offline.html").unlink()
    result = _run_validator(root, "pwa")
    assert result.returncode == 1
    assert "offline.html" in result.stderr


def test_pwa_accepts_generated_offline_fallback(tmp_path):
    root = _base_project(tmp_path, offline="generated")
    _write(root, "src/sw.js", "// vite-plugin-pwa config\n")
    _write(root, "public/manifest.webmanifest", json.dumps(MANIFEST))
    assert _run_validator(root, "pwa").returncode == 0


def test_review_requires_full_checklist_and_consistent_result(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "docs/mdpwa-review.md", REVIEW)
    assert _run_validator(root, "review").returncode == 0

    missing = REVIEW.replace("| Payload | PASS |\n", "")
    _write(root, "docs/mdpwa-review.md", missing)
    result = _run_validator(root, "review")
    assert result.returncode == 1
    assert "payload" in result.stderr

    failed = REVIEW.replace("| Offline | PASS |", "| Offline | FAIL |")
    _write(root, "docs/mdpwa-review.md", failed)
    result = _run_validator(root, "review")
    assert result.returncode == 1
    assert "APPROVED" in result.stderr

    rejected = failed.replace("Resultado: APPROVED", "Resultado: REJECTED")
    _write(root, "docs/mdpwa-review.md", rejected)
    assert _run_validator(root, "review").returncode == 0


def test_reconcile_requires_done_backlog_and_feat_changelog(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.format(status="done"))
    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n## Ciclo atual\n\n- #FEAT UI Material + PWA (PB-010): tema M3 e app instalável.\n",
    )
    _write(
        root,
        "docs/mdpwa-result.md",
        "# Resultado\n\nPB-010 entregue.\n\n## Documentação atualizada\n\n"
        "- CHANGELOG.md\n- docs/PROJECT_BACKLOG.md\n- docs/TECH_STACK.md\n",
    )
    assert _run_validator(root, "reconcile").returncode == 0

    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.format(status="in_progress"))
    result = _run_validator(root, "reconcile")
    assert result.returncode == 1
    assert "done/accepted" in result.stderr

    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.format(status="done"))
    _write(root, "CHANGELOG.md", "# Changelog\n\n- UI Material sem tag.\n")
    result = _run_validator(root, "reconcile")
    assert result.returncode == 1
    assert "#FEAT" in result.stderr
