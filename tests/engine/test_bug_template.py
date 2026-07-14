"""End-to-end contracts for the lightweight ``bug`` feature template."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

from ft.cli.main import available_templates
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY, run_validators


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "bug"
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

RECONCILED_BACKLOG = BACKLOG.replace(
    "| PB-002 | Bug | P1 | in_progress |",
    "| PB-002 | Bug | P1 | accepted |",
).replace("| — | Ciclo atual |", "| RED→GREEN | Corrigido |")

RECONCILED_FEATURES = FEATURES.replace(
    "| FEAT-001 | active | PB-001 |",
    "| FEAT-001 | active | PB-001, PB-002 |",
).replace("| — | Entrega inicial. |", "| PB-002 | Bug de soma corrigido. |")


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
    scripts = tmp_path / ".ft" / "process" / "bug" / "scripts"
    shutil.copytree(TEMPLATE / "scripts", scripts)
    shutil.copy2(PROCESS, scripts.parent / "process.yml")
    shutil.copy2(TEMPLATE / "environment.yml", scripts.parent / "environment.yml")
    _write(tmp_path, ".gitignore", "__pycache__/\n*.pyc\n")
    _write(
        tmp_path,
        ".ft/manifest.yml",
        "schema_version: 2\n"
        "default_process: bug\n"
        "processes:\n"
        "  bug:\n"
        "    path: .ft/process/bug/process.yml\n"
        "    template: bug\n"
        "    entrypoint: feature\n",
    )
    _write(
        tmp_path,
        "docs/feature-request.md",
        "#BUG PB-002: add(1, 2) retorna -1; o comportamento esperado é 3.\n",
    )
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", BACKLOG)
    _write(tmp_path, "docs/FEATURES.md", FEATURES)
    _write(
        tmp_path,
        "CHANGELOG.md",
        "# Changelog\n\n- #BUG PB-001 / FEAT-001 — correção anterior.\n",
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
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


def _test_source(expected: int) -> str:
    return (
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from app import add\n\n"
        "actual = add(1, 2)\n"
        f"assert actual == {expected}, f'expected total {expected}, got {{actual}}'\n"
        "print('regression passed')\n"
    )


def _subprocess_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = (
        str(Path(sys.executable).parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    return environment


def _run_validator(
    root: Path, mode: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(root / ".ft/process/bug/scripts/validate_bug.py"),
            mode,
            *arguments,
        ],
        cwd=root,
        env=_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )


def _run_product(
    root: Path, phase: str, *argv: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(root / ".ft/process/bug/scripts/product.sh"),
            phase,
            "--",
            *argv,
        ],
        cwd=root,
        env=_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )


def _baseline(root: Path) -> None:
    result = _run_validator(root, "baseline")
    assert result.returncode == 0, result.stderr
    result = _run_validator(root, "begin")
    assert result.returncode == 0, result.stderr


def _red(root: Path) -> None:
    _baseline(root)
    _write(root, "project/tests/test_add.py", _test_source(3))
    result = _run_product(root, "red", *REGRESSION_ARGV)
    assert result.returncode == 0, result.stderr
    assert "bug RED PASS" in result.stdout


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


def _green(root: Path) -> None:
    _red(root)
    _write(
        root,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n",
    )
    result = _run_product(root, "green", *REGRESSION_ARGV)
    assert result.returncode == 0, result.stderr
    assert "mesmo comando/teste" in result.stdout
    _report(root)


def _full(root: Path) -> None:
    _green(root)
    result = _run_validator(root, "full")
    assert result.returncode == 0, result.stderr


def test_bug_catalog_entrypoint_and_exact_graph_contract() -> None:
    validate_template_is_pristine(TEMPLATE)
    assert available_templates(entrypoint="feature") == ["bug", "feature", "tweak"]
    assert "bug" not in available_templates(entrypoint="init")

    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)

    assert report.passed, [issue.message for issue in report.errors]
    assert list(graph.nodes) == [
        "bug.preflight",
        "bug.diagnose_fix",
        "bug.acceptance",
        "bug.reconcile",
        "bug.final_gate",
        "bug.end",
    ]
    delegated = [
        node for node in graph.nodes.values() if node.executor.startswith("llm_")
    ]
    assert [node.id for node in delegated] == [
        "bug.diagnose_fix",
        "bug.reconcile",
    ]
    assert [node.id for node in graph.nodes.values() if node.type == "human_gate"] == [
        "bug.acceptance"
    ]
    assert graph.meta["execution_policy"]["entrypoint"] == "feature"
    assert graph.meta["execution_policy"]["template"] == "bug"
    assert graph.meta["parallel_policy"] == {
        "planner_timeout_seconds": 120,
        "rate_limit_respawns": 0,
    }
    assert graph.nodes["bug.acceptance"].reject_next == "bug.diagnose_fix"
    implementation_validator = graph.nodes["bug.diagnose_fix"].validators[-1][
        "command_succeeds"
    ]
    assert "validate_bug.py full" in implementation_validator["command"]
    assert "validate_bug.py implementation" not in implementation_validator["command"]
    assert "validate_bug.py verify" in implementation_validator["resume_command"]
    assert len(graph.nodes["bug.final_gate"].validators) == 1
    assert "#BUG" in graph.nodes["bug.reconcile"].prompt
    assert "Nunca crie" in graph.nodes["bug.reconcile"].prompt


def test_bug_preflight_records_baseline_without_running_product(tmp_path: Path) -> None:
    root = _project(tmp_path)
    graph = load_graph(PROCESS)

    result = run_validators(
        graph.nodes["bug.preflight"],
        str(root),
        work_dir=str(root),
    )

    assert result.passed, result.feedback
    baseline = yaml.safe_load(
        (root / "docs/bug-baseline.yml").read_text(encoding="utf-8")
    )
    assert baseline["kind"] == "ft.bug.baseline"
    assert baseline["base_commit"] == _git(root, "rev-parse", "HEAD")
    assert baseline["product_root"] == "project"
    assert baseline["limits"] == {"max_files": 8, "max_changed_lines": 500}
    assert baseline["project_backlog"][1]["id"] == "PB-002"
    assert baseline["features"][0]["id"] == "FEAT-001"
    assert not (root / "validation-calls.log").exists()


def test_bug_red_requires_failure_and_freezes_regression_test(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _baseline(root)
    test_path = "project/tests/test_add.py"
    _write(root, test_path, _test_source(-1))

    passing = _run_product(root, "red", *REGRESSION_ARGV)

    assert passing.returncode == 1
    assert "RED deve falhar" in passing.stderr
    assert not (root / "state/bug-red.json").exists()

    _write(root, test_path, _test_source(3))
    red = _run_product(root, "red", *REGRESSION_ARGV)

    assert red.returncode == 0, red.stderr
    receipt = json.loads((root / "state/bug-red.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "ft.bug.red"
    assert receipt["exit_code"] == 1
    assert receipt["argv"] == REGRESSION_ARGV
    assert "expected total 3" in receipt["output"]
    assert list(receipt["test_hashes"]) == [test_path]

    _write(root, test_path, _test_source(3) + "# changed after RED\n")
    frozen = _run_product(root, "red", *REGRESSION_ARGV)

    assert frozen.returncode == 1
    assert "mudou depois do RED" in frozen.stderr


def test_bug_red_rejects_infrastructure_failure_and_test_side_effect(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _baseline(root)
    test_path = "project/tests/test_add.py"
    _write(root, test_path, "raise ModuleNotFoundError('No module named missing')\n")

    infrastructure = _run_product(root, "red", *REGRESSION_ARGV)

    assert infrastructure.returncode == 1
    assert "infraestrutura/coleta" in infrastructure.stderr
    assert not (root / "state/bug-red.json").exists()

    _write(
        root,
        test_path,
        "from pathlib import Path\n"
        "Path('app.py').write_text('CORRUPTED = True\\n')\n"
        "raise AssertionError('expected total 3')\n",
    )
    side_effect = _run_product(root, "red", *REGRESSION_ARGV)

    assert side_effect.returncode == 1
    assert "alterou arquivos versionáveis" in side_effect.stderr
    assert not (root / "state/bug-red.json").exists()


def test_bug_green_requires_same_argv(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _red(root)
    _write(
        root,
        "project/app.py",
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n",
    )

    different = _run_product(root, "green", *REGRESSION_ARGV, "--verbose")

    assert different.returncode == 1
    assert "mesmo argv" in different.stderr
    green = _run_product(root, "green", *REGRESSION_ARGV)
    assert green.returncode == 0, green.stderr
    red_receipt = json.loads(
        (root / "state/bug-red.json").read_text(encoding="utf-8")
    )
    green_receipt = json.loads(
        (root / "state/bug-green.json").read_text(encoding="utf-8")
    )
    assert green_receipt["kind"] == "ft.bug.green"
    assert green_receipt["exit_code"] == 0
    assert green_receipt["argv"] == red_receipt["argv"] == REGRESSION_ARGV
    assert green_receipt["test_hashes"] == red_receipt["test_hashes"]


def test_bug_implementation_full_and_verify_share_one_receipt(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _green(root)

    implementation = _run_validator(root, "implementation")
    assert implementation.returncode == 0, implementation.stderr
    assert not (root / "validation-calls.log").exists()
    assert not (root / "docs/bug-validation.json").exists()

    full = _run_validator(root, "full")
    assert full.returncode == 0, full.stderr
    receipt = json.loads(
        (root / "docs/bug-validation.json").read_text(encoding="utf-8")
    )
    assert receipt["kind"] == "ft.bug.validation"
    assert receipt["result"] == "pass"
    assert receipt["regression_argv"] == REGRESSION_ARGV
    assert receipt["commands"] == [
        ["make", "-C", "project", "build"],
        ["make", "-C", "project", "test"],
    ]
    assert (root / "validation-calls.log").read_text(encoding="utf-8") == (
        "build\ntest\n"
    )

    verify = _run_validator(root, "verify")
    assert verify.returncode == 0, verify.stderr
    assert (root / "validation-calls.log").read_text(encoding="utf-8") == (
        "build\ntest\n"
    )


def test_bug_reconcile_requires_bug_entry_and_forbids_new_feature(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _full(root)
    _write(root, "docs/PROJECT_BACKLOG.md", RECONCILED_BACKLOG)
    _write(root, "docs/FEATURES.md", RECONCILED_FEATURES)
    _write(
        root,
        "docs/bug-result.md",
        "# Resultado\n\nPB-002 atualiza FEAT-001 com evidência RED → GREEN.\n",
    )
    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n"
        "- #BUG PB-001 / FEAT-001 — correção anterior.\n"
        "- #FEAT PB-002 / FEAT-001 — tag incorreta para bug.\n",
    )

    wrong_tag = _run_validator(root, "reconcile")

    assert wrong_tag.returncode == 1
    assert "#BUG" in wrong_tag.stderr

    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n"
        "- #BUG PB-001 / FEAT-001 — correção anterior.\n"
        "- #BUG PB-002 / FEAT-001 — corrige a operação de soma.\n",
    )
    reconciled = _run_validator(root, "reconcile")

    assert reconciled.returncode == 0, reconciled.stderr
    final_gate = run_validators(
        load_graph(PROCESS).nodes["bug.final_gate"],
        str(root),
        work_dir=str(root),
    )
    assert final_gate.passed, final_gate.feedback

    _write(
        root,
        "docs/FEATURES.md",
        RECONCILED_FEATURES
        + "| FEAT-002 | active | PB-002 | Duplicada | Não permitida. | cycle-02 | tests | PB-002 | Indevida. |\n",
    )
    new_feature = _run_validator(root, "reconcile")

    assert new_feature.returncode == 1
    assert "FEAT" in new_feature.stderr
