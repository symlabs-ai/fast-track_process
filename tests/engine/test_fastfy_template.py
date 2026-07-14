from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from ft.cli.main import available_templates
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "fastfy"
PROCESS = TEMPLATE / "process.yml"
VALIDATOR = TEMPLATE / "scripts" / "validate_fastfy.py"
PRODUCT_HELPER = TEMPLATE / "scripts" / "product.sh"
SERVE = TEMPLATE / "scripts" / "serve.sh"


SURVEY = """---
stack: Python + Flask
product_root: .
interface: {interface}
has_tests: {has_tests}
build_command: "true"
test_command: pytest -q
run_command: python app.py
clarification_status: {clarification}
---

# Levantamento

Produto legado de exemplo. Histórico Git: v1.0 inicial.
"""

PLAN = """# Plano de Adoção

- FEAT-001: cadastro (app.py)
- PB-001 done (adoption); PB-002 dívida deferred com decisão.
- Harness: Makefile na raiz com build/test/run/url.
- Smoke test mínimo em tests/test_smoke.py (repositório sem testes).
"""

BACKLOG = """# PROJECT_BACKLOG

| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |
|---|---|---|---|---|---|---|---|---|
| PB-001 | Feature | P0 | done | adoption | Cadastro | Cadastro funciona | app.py | Capacidade legada |
| PB-002 | Dívida | P2 | planned | adoption | Criar suíte completa | Suíte cobre fluxos P0 | — | Registrada na adoção |
"""

FEATURES = """# FEATURES

| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |
|---|---|---|---|---|---|---|---|---|
| FEAT-001 | active | PB-001 | Cadastro | Cadastro de clientes legado. | legado | app.py | — | Adoção |
"""

CHANGELOG = """# Changelog

## v1.0 — histórico legado

- Cadastro de clientes.

## Adoção Fast Track

- #FEAT Adoção Fast Track (PB-001): documentação canônica e harness criados.
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _legacy_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "app.py", "VALUE = 1\n")
    _write(tmp_path, "README.md", "# Legado\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "commit", "-qm", "legado",
    )
    return tmp_path


def _survey(
    root: Path,
    *,
    interface: str = "internal",
    has_tests: str = "true",
    clarification: str = "clear",
) -> None:
    _write(
        root,
        "docs/adoption-survey.md",
        SURVEY.format(
            interface=interface, has_tests=has_tests, clarification=clarification
        ),
    )


def _run_validator(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), mode],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _run_product_helper(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    scripts = root / ".ft" / "process" / "fastfy" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    _write(root, ".ft/manifest.yml", "schema_version: 3\nprocesses: {}\n")
    target = scripts / "product.sh"
    if not target.exists():
        target.write_bytes(PRODUCT_HELPER.read_bytes())
    return subprocess.run(
        ["bash", str(target), *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


def test_fastfy_template_is_discoverable_and_pristine():
    assert "fastfy" in available_templates()
    validate_template_is_pristine(TEMPLATE)


def test_fastfy_process_is_valid_and_uses_local_runtime_paths():
    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "fastfy"
    policy = graph.meta["execution_policy"]
    assert policy["entrypoint"] == "run"
    assert policy["template"] == "fastfy"
    assert policy["requires_worktree"] is True

    text = PROCESS.read_text(encoding="utf-8")
    assert ".ft/process/fastfy/scripts/" in text
    assert "templates/fastfy" not in text


def test_fastfy_preflight_requires_git_head(tmp_path):
    _write(tmp_path, "app.py", "VALUE = 1\n")
    result = _run_validator(tmp_path, "preflight")
    assert result.returncode == 1
    assert "Git" in result.stderr


def test_fastfy_preflight_rejects_already_adopted_project(tmp_path):
    root = _legacy_repo(tmp_path)
    _write(root, "docs/PRD.md", "# PRD\n")
    _write(root, "docs/FEATURES.md", FEATURES)
    result = _run_validator(root, "preflight")
    assert result.returncode == 1
    assert "template feature" in result.stderr


def test_fastfy_preflight_passes_on_legacy_repository(tmp_path):
    root = _legacy_repo(tmp_path)
    assert _run_validator(root, "preflight").returncode == 0


def test_fastfy_survey_requires_questions_when_clarification_required(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root, clarification="required")
    _write(root, "docs/adoption-questions.md", "Nenhuma pergunta.\n")
    _write(root, "docs/adoption-plan.md", PLAN)
    result = _run_validator(root, "survey")
    assert result.returncode == 1
    assert "perguntas" in result.stderr

    _write(root, "docs/adoption-questions.md", "1. O módulo X ainda é usado?\n")
    assert _run_validator(root, "survey").returncode == 0


def test_fastfy_survey_clear_requires_harness_plan_and_valid_frontmatter(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root)
    _write(root, "docs/adoption-questions.md", "Nenhuma pergunta pendente.\n")
    _write(root, "docs/adoption-plan.md", "# Plano vago\n\nsem harness definido\n")
    result = _run_validator(root, "survey")
    assert result.returncode == 1
    assert "Makefile" in result.stderr

    _write(root, "docs/adoption-plan.md", PLAN)
    assert _run_validator(root, "survey").returncode == 0

    bad = SURVEY.format(
        interface="internal", has_tests="true", clarification="clear"
    ).replace("product_root: .", "product_root: legacy")
    _write(root, "docs/adoption-survey.md", bad)
    result = _run_validator(root, "survey")
    assert result.returncode == 1
    assert "product_root" in result.stderr


def test_fastfy_survey_without_tests_requires_smoke_in_plan(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root, has_tests="false")
    _write(root, "docs/adoption-questions.md", "Nenhuma pergunta pendente.\n")
    _write(root, "docs/adoption-plan.md", "# Plano\n\nHarness: Makefile na raiz.\n")
    result = _run_validator(root, "survey")
    assert result.returncode == 1
    assert "smoke" in result.stderr.lower()

    _write(root, "docs/adoption-plan.md", PLAN)
    assert _run_validator(root, "survey").returncode == 0


def _canonical_docs(root: Path) -> None:
    _write(root, "docs/PRD.md", "# PRD\n\n## Propósito\n\nCadastro legado.\n")
    _write(root, "docs/TECH_STACK.md", "# Tech Stack\n\nPython.\n")
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG)
    _write(root, "docs/FEATURES.md", FEATURES)
    _write(root, "CHANGELOG.md", CHANGELOG)


def test_fastfy_docs_pass_with_rebuilt_changelog(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root)
    _canonical_docs(root)
    assert _run_validator(root, "docs").returncode == 0


def test_fastfy_docs_require_feat_adoption_entry_with_backlog_reference(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root)
    _canonical_docs(root)

    _write(root, "CHANGELOG.md", "# Changelog\n\n## v1.0\n\n- Cadastro.\n")
    result = _run_validator(root, "docs")
    assert result.returncode == 1
    assert "#FEAT" in result.stderr

    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n## Adoção\n\n- #FEAT Adoção Fast Track: sem referência.\n",
    )
    result = _run_validator(root, "docs")
    assert result.returncode == 1
    assert "PB-" in result.stderr


def test_fastfy_docs_require_interface_documents(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root, interface="ui")
    _canonical_docs(root)
    result = _run_validator(root, "docs")
    assert result.returncode == 1
    assert "ui_criteria" in result.stderr

    _write(root, "docs/ui_criteria.md", "# UI\n\n- C01: tela inicial carrega.\n")
    assert _run_validator(root, "docs").returncode == 0


def test_fastfy_harness_requires_targets_by_interface(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root)
    _write(root, "Makefile", "build:\n\t@true\n\ntest:\n\t@true\n")
    assert _run_validator(root, "harness").returncode == 0

    _survey(root, interface="api")
    result = _run_validator(root, "harness")
    assert result.returncode == 1
    assert "run" in result.stderr and "url" in result.stderr

    _write(
        root,
        "Makefile",
        "build:\n\t@true\n\ntest:\n\t@true\n\nrun:\n\t@true\n\n"
        "url:\n\t@echo http://127.0.0.1:8021\n",
    )
    assert _run_validator(root, "harness").returncode == 0


def test_fastfy_harness_without_tests_requires_smoke_file(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root, has_tests="false")
    _write(root, "Makefile", "build:\n\t@true\n\ntest:\n\t@true\n")
    result = _run_validator(root, "harness")
    assert result.returncode == 1
    assert "smoke" in result.stderr.lower()

    _write(root, "tests/test_smoke.py", "def test_import():\n    import app\n")
    assert _run_validator(root, "harness").returncode == 0


def test_fastfy_review_requires_exactly_one_result_line(tmp_path):
    root = _legacy_repo(tmp_path)
    _write(root, "docs/adoption-review.md", "# Revisão\n\nSem resultado.\n")
    assert _run_validator(root, "review").returncode == 1

    _write(
        root,
        "docs/adoption-review.md",
        "# Revisão\n\nResultado: APPROVED\n\n| FEAT-001 | PASS |\n",
    )
    assert _run_validator(root, "review").returncode == 0

    _write(
        root,
        "docs/adoption-review.md",
        "Resultado: APPROVED\nResultado: REJECTED\n",
    )
    assert _run_validator(root, "review").returncode == 1


def test_fastfy_product_helper_resolves_root_and_nested_makefiles(tmp_path):
    root = _legacy_repo(tmp_path)
    _write(root, "Makefile", "build:\n\t@true\n\ntest:\n\t@echo ran-tests\n")
    result = _run_product_helper(root, "path")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "."

    tests_run = _run_product_helper(root, "test")
    assert tests_run.returncode == 0
    assert "ran-tests" in tests_run.stdout

    _write(root, "project/Makefile", "build:\n\t@true\n\ntest:\n\t@true\n")
    nested = _run_product_helper(root, "path")
    assert nested.returncode == 0
    assert nested.stdout.strip() == "project"


def test_fastfy_serve_is_noop_for_internal_interface(tmp_path):
    root = _legacy_repo(tmp_path)
    _survey(root, interface="internal")
    scripts = root / ".ft" / "process" / "fastfy" / "scripts"
    scripts.mkdir(parents=True)
    _write(root, ".ft/manifest.yml", "schema_version: 3\nprocesses: {}\n")
    (scripts / "serve.sh").write_bytes(SERVE.read_bytes())

    result = subprocess.run(
        ["bash", str(scripts / "serve.sh")],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (root / ".serve.pid").exists()
