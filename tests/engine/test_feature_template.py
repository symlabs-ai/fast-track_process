from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys

import yaml

from ft.cli.main import available_templates
from ft.engine.graph import load_graph
from ft.engine.layout import validate_template_is_pristine
from ft.engine.process_validator import validate_process
from ft.engine.runner import VALIDATOR_REGISTRY, run_validators

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "feature"
PROCESS = TEMPLATE / "process.yml"
VALIDATOR = TEMPLATE / "scripts" / "validate_feature.py"
PRODUCT_HELPER = TEMPLATE / "scripts" / "product.sh"
PRODUCT_RECEIPT = TEMPLATE / "scripts" / "product_receipt.py"


BACKLOG = """# PROJECT_BACKLOG

## Itens do Backlog

| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |
|---|---|---|---|---|---|---|---|---|
| PB-001 | Feature | P0 | accepted | PRD | Cadastro | Cadastro funciona | tests | Aceito |
| PB-002 | Feature | P1 | in_progress | feature-request | Busca | AC-01; AC-02 | — | Ciclo atual |
"""

FEATURES = """# FEATURES

## Catálogo de Features

| ID | Status | Backlog | Título | Descrição | Entregue em | Evidência | Última evolução | Notas |
|---|---|---|---|---|---|---|---|---|
| FEAT-001 | active | PB-001 | Cadastro | Permite cadastrar clientes. | cycle-01 | tests | — | Entrega inicial. |
"""

FEATURE = """---
type: evolution
target_feature: FEAT-001
backlog_item: PB-002
priority: P1
interface: ui
---

# Busca de clientes

## Objetivo
Encontrar clientes por telefone.

## Comportamento Esperado
A busca aceita telefone com ou sem máscara.

## Critérios de Aceite
- AC-01: Busca por telefone completo.
- AC-02: Busca funciona sem formatação.

## Fora do Escopo
- Busca fuzzy.

## Restrições
- Preservar a API atual.
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_project(tmp_path: Path, product_dir: str = "project") -> Path:
    _write(
        tmp_path, "CHANGELOG.md", "# Changelog\n\n## Histórico\n\n- PB-001 entregue.\n"
    )
    _write(
        tmp_path,
        "docs/feature-request.md",
        "PB-002: adicionar busca por telefone.\n",
    )
    _write(tmp_path, "docs/PRD.md", "# PRD\n\nProduto existente.\n")
    _write(tmp_path, "docs/PROJECT_BACKLOG.md", BACKLOG)
    _write(tmp_path, "docs/FEATURES.md", FEATURES)
    _write(
        tmp_path,
        f"{product_dir}/Makefile",
        "test:\n\t@true\n\nbuild:\n\t@true\n\nrun:\n\t@true\n\nurl:\n\t@echo http://127.0.0.1:8021\n",
    )
    return tmp_path


def _clear_discovery(root: Path) -> None:
    _write(root, "docs/feature.md", FEATURE)
    _write(
        root,
        "docs/feature-plan.md",
        "# Plano\n\nPB-002 evolui FEAT-001.\n\nAC-01\nAC-02\n\n## Testes\nmake test\n",
    )
    _write(root, "docs/feature-discovery.md", "clarification_status: clear\n")
    _write(root, "docs/feature-questions.md", "Nenhuma pergunta pendente.\n")
    _write(root, "docs/feature-workset.yml", "schema_version: 1\npaths: [project/]\n")
    _write(
        root,
        "docs/feature-id-reservation.yml",
        "schema_version: 1\n"
        "backlog_item: PB-002\n"
        "target_feature: FEAT-001\n"
        "final_feature_id: FEAT-001\n"
        "reservation_owner: test\n",
    )


def _write_review_route(
    root: Path,
    *,
    route: str = "approved",
    verdict: str = "APPROVED",
) -> None:
    _write(
        root,
        "docs/feature-review.yml",
        "schema_version: 1\n"
        f"verdict: {verdict}\n"
        f"review_route: {route}\n"
        "summary: Revisão estruturada.\n",
    )


def _run_validator(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), mode],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _snapshot_baseline(root: Path) -> None:
    result = _run_validator(root, "baseline")
    assert result.returncode == 0, result.stderr
    assert (root / "docs" / "feature-baseline.yml").is_file()


def _prepare_reconcile_artifacts(root: Path) -> None:
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.replace("in_progress", "accepted"))
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace("| PB-001 |", "| PB-001, PB-002 |"),
    )
    _write(
        root,
        "docs/feature-result.md",
        "# Resultado PB-002\n\n| AC-01 | PASS |\n| AC-02 | PASS |\n\n"
        "## Documentação atualizada\n\n"
        "- CHANGELOG.md\n- docs/PROJECT_BACKLOG.md\n- docs/FEATURES.md\n",
    )


def _receipt_project(tmp_path: Path) -> Path:
    root = _base_project(tmp_path)
    scripts = root / ".ft" / "process" / "feature" / "scripts"
    scripts.mkdir(parents=True)
    _write(root, ".ft/manifest.yml", "schema_version: 2\ndefault_process: feature\n")
    shutil.copy2(PRODUCT_HELPER, scripts / "product.sh")
    shutil.copy2(PRODUCT_RECEIPT, scripts / "product_receipt.py")
    _write(root, "project/app.py", "VALUE = 1\n")
    _write(root, "requirements.txt", "example==1\n")
    _write(
        root,
        "project/Makefile",
        "test:\n\t@printf 'test\\n' >> ../validation-calls.log\n\n"
        "build:\n\t@printf 'build\\n' >> ../validation-calls.log\n\n"
        "run:\n\t@true\n\n"
        "url:\n\t@echo http://127.0.0.1:8021\n",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=root,
        check=True,
    )
    return root


def _run_product_helper(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / ".ft/process/feature/scripts/product.sh"), *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
        env=env,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_owned_process(token: str) -> None:
    mode, raw_pid = token.strip().split(":", 1)
    pid = int(raw_pid)
    try:
        if mode == "group":
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def test_feature_template_is_discoverable_and_pristine():
    assert {"bug", "feature", "tweak"} <= set(available_templates())
    assert "feature" in available_templates()
    validate_template_is_pristine(TEMPLATE)


def test_feature_process_is_valid_and_uses_local_runtime_paths():
    graph = load_graph(PROCESS)
    report = validate_process(graph, VALIDATOR_REGISTRY)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "feature"
    assert graph.meta["version"] == "1.3.0"
    assert graph.meta["execution_policy"] == {
        "entrypoint": "run",
        "template": "feature",
        "materialization": "copy_once",
        "runtime_source": "local_only",
        "requires_initialized_project": True,
        "requires_worktree": True,
        "local_process_path": ".ft/process/feature/process.yml",
        "merge_command": "ft close --merge full",
    }
    assert graph.meta["correction_policy"] == {
        "follow_graph_after_retry": True,
        "scope_rejection_restarts_at": "feature.discovery",
        "acceptance_rejection_restarts_at": "feature.implement",
        "mandatory_after_implementation": ["feature.review", "feature.acceptance"],
    }
    assert graph.meta["close_policy"]["backlog"] == {
        "mode": "referenced",
        "references_path": "docs/feature.md",
        "reference_field": "backlog_item",
        "required_count": 1,
        "accepted_statuses": ["done", "accepted"],
    }

    nodes = graph.nodes
    assert nodes["feature.discovery"].next == "feature.discovery_gate"
    assert "docs/PROJECT_BACKLOG.md" not in nodes["feature.discovery"].outputs
    assert "docs/PROJECT_BACKLOG.md" not in nodes["feature.discovery"].write_scope
    assert nodes["feature.discovery_gate"].next == "feature.clarity"
    assert nodes["feature.discovery_gate"].validators == [
        {
            "read_artifact": {
                "path": "docs/feature-discovery.md",
                "key": "clarification_status",
                "pattern": r"clarification_status:\s*(required|clear)",
            }
        }
    ]
    assert nodes["feature.clarity"].branches == {
        "required": "feature.questions",
        "clear": "feature.reserve_ids",
        "_default": "feature.questions",
    }
    assert nodes["feature.questions"].next == "feature.discovery"
    assert nodes["feature.scope_gate"].reject_next == "feature.discovery"
    assert nodes["feature.acceptance"].reject_next == "feature.implement"
    assert nodes["feature.acceptance"].env_teardown == [
        "bash .ft/process/feature/scripts/serve.sh stop"
    ]
    assert nodes["feature.reserve_ids"].next == "feature.scope_gate"
    assert nodes["feature.implement"].next == "feature.product_validate"
    assert nodes["feature.product_validate"].next == "feature.evidence"
    assert nodes["feature.evidence_gate"].next == "feature.review"
    assert nodes["feature.review"].next == "feature.review_route"
    assert nodes["feature.review_decision"].branches == {
        "approved": "feature.acceptance",
        "implementation": "feature.implement",
        "evidence": "feature.evidence",
        "scope": "feature.discovery",
        "_default": "feature.review",
    }
    assert nodes["feature.end"].type == "end"
    assert "CHANGELOG.md" not in nodes["feature.reconcile"].outputs
    assert "CHANGELOG.md" not in nodes["feature.reconcile"].write_scope
    assert "docs/feature-reconciliation.yml" in nodes["feature.reconcile"].write_scope

    raw = PROCESS.read_text(encoding="utf-8")
    assert "começar com `#FEAT` como primeiro token" in raw
    process_payload = yaml.safe_load(raw)
    raw_nodes = {node["id"]: node for node in process_payload["nodes"]}
    assert "templates/feature" not in raw
    assert ".ft/process/process.yml" not in raw
    assert ".ft/process/feature/scripts/" in raw
    assert "hyper_mode_" not in raw
    assert "Leia obrigatoriamente" not in raw
    assert "Releia feature-plan" not in raw
    assert "batch" not in raw.lower()
    assert "wave" not in raw.lower()
    assert "planner" not in raw.lower()
    assert {
        node_id: raw_nodes[node_id]["context_profile"]
        for node_id in (
            "feature.discovery",
            "feature.implement",
            "feature.evidence",
            "feature.review",
            "feature.reconcile",
        )
    } == {
        "feature.discovery": "feature_delta.discovery",
        "feature.implement": "feature_delta.implement",
        "feature.evidence": "feature_delta.evidence",
        "feature.review": "feature_delta.review",
        "feature.reconcile": "feature_delta.reconcile",
    }
    assert "product.sh ensure-baseline" in raw
    assert "product.sh ensure --record" in raw
    assert "cd project" not in raw
    assert nodes["feature.implement"].write_scope[:2] == ["project", "src"]
    assert nodes["feature.implement"].validators == []
    assert nodes["feature.implement"].llm_timeout_seconds == 900
    assert nodes["feature.implement"].llm_episode_budget_seconds == 1800
    assert {
        node_id: nodes[node_id].llm_timeout_seconds
        for node_id in (
            "feature.discovery",
            "feature.evidence",
            "feature.review",
            "feature.reconcile",
        )
    } == {
        "feature.discovery": 600,
        "feature.evidence": 480,
        "feature.review": 600,
        "feature.reconcile": 480,
    }
    assert nodes["feature.product_validate"].validators == [
        {
            "command_succeeds": {
                "command": (
                    "python .ft/process/feature/scripts/validate_feature.py "
                    "implementation"
                ),
                "stop_on_failure": True,
            }
        },
        {
            "command_succeeds": {
                "command": (
                    "bash .ft/process/feature/scripts/product.sh ensure --record "
                    "docs/feature-validation.json"
                ),
                "resume_command": (
                    "bash .ft/process/feature/scripts/product.sh verify "
                    "docs/feature-validation.json"
                ),
                "timeout": 300,
            }
        },
    ]
    assert nodes["feature.review"].validators == [
        {"file_exists": "docs/feature-review.md"},
        {"file_exists": "docs/feature-review.yml"},
        {
            "command_succeeds": {
                "command": (
                    "bash .ft/process/feature/scripts/product.sh verify "
                    "docs/feature-validation.json"
                ),
                "stop_on_failure": True,
            }
        },
        {
            "command_succeeds": "python .ft/process/feature/scripts/validate_feature.py review"
        },
    ]
    assert nodes["feature.final_gate"].validators == [
        {
            "command_succeeds": "bash .ft/process/feature/scripts/product.sh verify docs/feature-validation.json"
        },
        {
            "command_succeeds": "python .ft/process/feature/scripts/validate_feature.py reconcile"
        },
    ]
    assert nodes["feature.preflight"].validators[-2:] == [
        {
            "command_succeeds": {
                "command": "python .ft/process/feature/scripts/validate_feature.py baseline",
                "stop_on_failure": True,
            }
        },
        {
            "command_succeeds": {
                "command": (
                    "bash .ft/process/feature/scripts/product.sh ensure-baseline --record "
                    "docs/feature-baseline-attestation.json"
                ),
                "resume_command": (
                    "bash .ft/process/feature/scripts/product.sh verify-baseline "
                    "docs/feature-baseline-attestation.json"
                ),
                "timeout": 300,
            }
        },
    ]
    reconcile_commands = [
        validator["command_succeeds"]
        for validator in nodes["feature.reconcile"].validators
        if "command_succeeds" in validator
    ]
    assert reconcile_commands == [
        {
            "command": "python .ft/process/feature/scripts/validate_feature.py proposal",
            "stop_on_failure": True,
        },
        {
            "command": "python .ft/process/feature/scripts/validate_feature.py apply-reconcile",
            "stop_on_failure": True,
        },
        "python .ft/process/feature/scripts/validate_feature.py reconcile",
    ]

    policy = graph.meta["artifact_policy"]
    assert "CHANGELOG.md" in policy["canonical"]
    assert "docs/feature-validation.json" in policy["cycle"]
    assert not (set(policy["canonical"]) & set(policy["cycle"]))
    environment = yaml.safe_load((TEMPLATE / "environment.yml").read_text())
    assert environment == {
        "run_mode": "isolated",
        "max_node_retries": 1,
        "max_gate_retries": 0,
        "max_auto_fix": 0,
    }


def test_feature_validator_baseline_and_clear_discovery_pass(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)

    baseline = _run_validator(root, "baseline")
    discovery = _run_validator(root, "discovery")

    assert baseline.returncode == 0, baseline.stderr
    assert discovery.returncode == 0, discovery.stderr


def test_feature_baseline_requires_an_open_preexisting_pb(tmp_path):
    root = _base_project(tmp_path)
    _write(root, "docs/feature-request.md", "PB-001: refazer entrega aceita.\n")

    result = _run_validator(root, "baseline")

    assert result.returncode == 1
    assert "não está aberto" in result.stderr


def test_feature_workset_is_a_nonexistent_but_safe_hint(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(
        root,
        "docs/feature-workset.yml",
        "schema_version: 1\npaths: [project/path-that-does-not-exist.py]\n",
    )
    assert _run_validator(root, "discovery").returncode == 0

    _write(
        root,
        "docs/feature-workset.yml",
        "schema_version: 1\npaths: [../outside]\n",
    )
    invalid = _run_validator(root, "discovery")
    assert invalid.returncode == 1
    assert "paths inválidos" in invalid.stderr


def test_feature_product_full_records_and_verify_reuses_exact_receipt(tmp_path):
    root = _receipt_project(tmp_path)
    receipt_path = root / "docs" / "feature-validation.json"

    full = _run_product_helper(root, "full", "--record", "docs/feature-validation.json")

    assert full.returncode == 0, full.stderr
    assert (root / "validation-calls.log").read_text(
        encoding="utf-8"
    ) == "build\ntest\n"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["kind"] == "ft.feature.product-validation"
    assert payload["validation_kind"] == "implementation"
    assert payload["result"] == "pass"
    assert payload["fingerprint"].startswith("sha256:")
    assert payload["product_root"] == "project"
    assert isinstance(payload["recorded_at"], str)
    assert payload["file_count"] > 0
    assert set(payload) == {
        "schema_version",
        "kind",
        "validation_kind",
        "product_root",
        "project_identity",
        "commands",
        "file_count",
        "fingerprint",
        "result",
        "recorded_at",
    }
    assert payload["commands"] == [
        [
            "env", "-u", "MAKEFLAGS", "-u", "MFLAGS", "-u",
            "GNUMAKEFLAGS", "make", "-C", "project", "build",
        ],
        [
            "env", "-u", "MAKEFLAGS", "-u", "MFLAGS", "-u",
            "GNUMAKEFLAGS", "make", "-C", "project", "test",
        ],
    ]

    verified = _run_product_helper(root, "verify", "docs/feature-validation.json")

    assert verified.returncode == 0, verified.stderr
    assert "VERIFIED" in verified.stdout
    assert (root / "validation-calls.log").read_text(
        encoding="utf-8"
    ) == "build\ntest\n"


def test_feature_baseline_and_implementation_receipts_are_separate_and_ensure_reuses(
    tmp_path,
):
    root = _receipt_project(tmp_path)

    first_baseline = _run_product_helper(
        root,
        "ensure-baseline",
        "--record",
        "docs/feature-baseline-attestation.json",
    )
    second_baseline = _run_product_helper(
        root,
        "ensure-baseline",
        "--record",
        "docs/feature-baseline-attestation.json",
    )

    assert first_baseline.returncode == 0, first_baseline.stderr
    assert second_baseline.returncode == 0, second_baseline.stderr
    assert "REUSED" in second_baseline.stdout
    baseline = json.loads(
        (root / "docs/feature-baseline-attestation.json").read_text()
    )
    assert baseline["validation_kind"] == "baseline"
    assert baseline["kind"] == "ft.feature.baseline-attestation"
    assert not (root / "docs/feature-validation.json").exists()
    assert (root / "validation-calls.log").read_text() == "build\ntest\n"

    _write(root, "project/app.py", "VALUE = 2\n")
    implementation = _run_product_helper(
        root, "ensure", "--record", "docs/feature-validation.json"
    )
    implementation_again = _run_product_helper(
        root, "ensure", "--record", "docs/feature-validation.json"
    )

    assert implementation.returncode == 0, implementation.stderr
    assert implementation_again.returncode == 0, implementation_again.stderr
    assert "REUSED" in implementation_again.stdout
    receipt = json.loads((root / "docs/feature-validation.json").read_text())
    assert receipt["validation_kind"] == "implementation"
    assert (root / "validation-calls.log").read_text() == (
        "build\ntest\nbuild\ntest\n"
    )


def test_feature_receipt_detects_external_ft_toolchain_change(tmp_path):
    root = _receipt_project(tmp_path / "project-root")
    receipt_path = root / "docs/feature-validation.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_ft = bin_dir / "ft"
    fake_ft.write_text("#!/bin/sh\nprintf 'ft test 1\\n'\n", encoding="utf-8")
    fake_ft.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    full = _run_product_helper(
        root,
        "full",
        "--record",
        "docs/feature-validation.json",
        env=env,
    )
    assert full.returncode == 0, full.stderr

    fake_ft.write_text("#!/bin/sh\nprintf 'ft test 2\\n'\n", encoding="utf-8")
    fake_ft.chmod(0o755)
    verified = _run_product_helper(
        root,
        "verify",
        "docs/feature-validation.json",
        env=env,
    )

    assert verified.returncode == 1
    assert "ferramentas" in verified.stderr
    assert receipt_path.is_file()


def test_feature_shared_cache_is_opt_in_hermetic_and_single_keyed(tmp_path):
    root = _receipt_project(tmp_path / "project-root")
    env = dict(os.environ)
    env.update(
        {
            "FT_HOME": str(tmp_path / "ft-home"),
            "FT_FEATURE_SHARED_CACHE": "1",
            "FT_FEATURE_VALIDATION_HERMETIC": "1",
            "FT_FEATURE_EXTERNAL_DEPENDENCIES": "none",
        }
    )

    first = _run_product_helper(
        root,
        "ensure",
        "--record",
        "docs/feature-validation.json",
        env=env,
    )
    assert first.returncode == 0, first.stderr
    assert (root / "validation-calls.log").read_text() == "build\ntest\n"
    (root / "docs/feature-validation.json").unlink()
    (root / "validation-calls.log").unlink()

    cached = _run_product_helper(
        root,
        "ensure",
        "--record",
        "docs/feature-validation.json",
        env=env,
    )

    assert cached.returncode == 0, cached.stderr
    assert "SHARED-CACHE" in cached.stdout
    assert not (root / "validation-calls.log").exists()
    cache_files = list(
        (tmp_path / "ft-home/cache/feature-validation").glob("implementation-*.json")
    )
    assert len(cache_files) == 1


def test_feature_product_full_resource_is_opt_in_and_shared_by_git_worktrees(
    tmp_path,
):
    root = _receipt_project(tmp_path / "project-root")
    lock_dir = root / ".git" / "ft-resource-locks"
    lock_dir.mkdir()
    lock_path = lock_dir / "product_full.lock"
    lock_handle = lock_path.open("w")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    env = dict(os.environ)
    env["FT_FEATURE_PRODUCT_FULL_RESOURCE"] = "1"
    process = subprocess.Popen(
        [
            "bash",
            str(root / ".ft/process/feature/scripts/product.sh"),
            "full",
            "--record",
            "docs/feature-validation.json",
        ],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == (
            "product_full resource WAITING: implementation"
        )
        assert process.poll() is None
        assert not (root / "validation-calls.log").exists()
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        lock_handle.close()

    assert process.returncode == 0, stderr
    assert "product_full resource ACQUIRED: implementation" in stdout
    assert (root / "validation-calls.log").read_text() == "build\ntest\n"


def test_feature_implementation_validation_fails_before_full_suite(tmp_path):
    root = _receipt_project(tmp_path)
    _clear_discovery(root)
    _snapshot_baseline(root)
    # Sem mudança de produto, o check estático deve parar antes de make build/test.
    node = load_graph(PROCESS).nodes["feature.product_validate"]

    result = run_validators(node, str(root), work_dir=str(root))

    assert not result.passed
    assert not (root / "validation-calls.log").exists()
    assert not (root / "docs" / "feature-validation.json").exists()


def test_feature_product_verify_rejects_source_lockfile_and_receipt_tampering(tmp_path):
    root = _receipt_project(tmp_path)
    receipt = "docs/feature-validation.json"
    product_script = root / ".ft/process/feature/scripts/product.sh"
    original_product_script = product_script.read_text(encoding="utf-8")
    full = _run_product_helper(root, "full", "--record", receipt)
    assert full.returncode == 0, full.stderr

    _write(root, "project/app.py", "VALUE = 2\n")
    changed_source = _run_product_helper(root, "verify", receipt)
    assert changed_source.returncode == 1
    assert "inputs executáveis" in changed_source.stderr

    _write(root, "project/app.py", "VALUE = 1\n")
    _write(root, "requirements.txt", "example==2\n")
    changed_lockfile = _run_product_helper(root, "verify", receipt)
    assert changed_lockfile.returncode == 1
    assert "inputs executáveis" in changed_lockfile.stderr

    _write(root, "requirements.txt", "example==1\n")
    product_script.write_text(
        original_product_script + "\n# changed\n", encoding="utf-8"
    )
    changed_script = _run_product_helper(root, "verify", receipt)
    assert changed_script.returncode == 1
    assert "inputs executáveis" in changed_script.stderr

    product_script.write_text(original_product_script, encoding="utf-8")
    receipt_path = root / receipt
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["commands"][0][-1] = "test-fast"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    tampered = _run_product_helper(root, "verify", receipt)
    assert tampered.returncode == 1
    assert "fingerprint interno" in tampered.stderr


def test_feature_product_failed_full_invalidates_old_receipt(tmp_path):
    root = _receipt_project(tmp_path)
    receipt_path = root / "docs/feature-validation.json"
    passed = _run_product_helper(
        root, "full", "--record", "docs/feature-validation.json"
    )
    assert passed.returncode == 0, passed.stderr
    assert receipt_path.is_file()
    _write(
        root,
        "project/Makefile",
        "test:\n\t@false\n\nbuild:\n\t@true\n\nrun:\n\t@true\n\nurl:\n\t@echo http://127.0.0.1:8021\n",
    )

    failed = _run_product_helper(
        root, "full", "--record", "docs/feature-validation.json"
    )

    assert failed.returncode != 0
    assert "receipt não foi gravado" in failed.stderr
    assert not receipt_path.exists()


def test_feature_product_receipt_symlink_never_deletes_target(tmp_path):
    root = _receipt_project(tmp_path)
    target = root / "CHANGELOG.md"
    expected = target.read_text(encoding="utf-8")
    receipt_path = root / "docs" / "feature-validation.json"
    receipt_path.symlink_to("../CHANGELOG.md")

    result = _run_product_helper(
        root, "full", "--record", "docs/feature-validation.json"
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert target.read_text(encoding="utf-8") == expected
    assert receipt_path.is_symlink()


def test_feature_product_root_symlink_outside_project_is_rejected(tmp_path):
    root = _receipt_project(tmp_path / "root")
    external = tmp_path / "external-product"
    external.mkdir()
    _write(external, "Makefile", "test:\n\t@true\n\nbuild:\n\t@true\n")
    shutil.rmtree(root / "project")
    (root / "project").symlink_to(external, target_is_directory=True)

    result = _run_product_helper(
        root, "full", "--record", "docs/feature-validation.json"
    )

    assert result.returncode != 0
    assert "product_root não pode conter symlink" in result.stderr
    assert not (root / "docs" / "feature-validation.json").exists()


def test_feature_product_verify_rejects_non_compact_or_mistyped_receipt(tmp_path):
    root = _receipt_project(tmp_path)
    receipt = root / "docs" / "feature-validation.json"
    full = _run_product_helper(root, "full", "--record", str(receipt.relative_to(root)))
    assert full.returncode == 0, full.stderr
    original = json.loads(receipt.read_text(encoding="utf-8"))

    for mutate, expected in (
        (lambda payload: payload.update({"files": []}), "campos ausentes ou não permitidos"),
        (lambda payload: payload.update({"file_count": True}), "file_count"),
        (lambda payload: payload.update({"recorded_at": None}), "recorded_at"),
        (lambda payload: payload.update({"fingerprint": "sha256:not-a-digest"}), "fingerprint"),
    ):
        payload = dict(original)
        mutate(payload)
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        verified = _run_product_helper(root, "verify", str(receipt.relative_to(root)))
        assert verified.returncode != 0
        assert expected in verified.stderr


def test_feature_product_full_neutralizes_makeflags(tmp_path, monkeypatch):
    root = _receipt_project(tmp_path)
    monkeypatch.setenv("MAKEFLAGS", "-n -i")
    monkeypatch.setenv("MFLAGS", "-n")
    monkeypatch.setenv("GNUMAKEFLAGS", "-n")

    result = _run_product_helper(
        root, "full", "--record", "docs/feature-validation.json"
    )

    assert result.returncode == 0, result.stderr
    assert (root / "validation-calls.log").read_text(
        encoding="utf-8"
    ) == "build\ntest\n"


def test_feature_product_build_and_test_neutralize_makeflags(tmp_path, monkeypatch):
    root = _receipt_project(tmp_path)
    monkeypatch.setenv("MAKEFLAGS", "-n -i")
    monkeypatch.setenv("MFLAGS", "-n")
    monkeypatch.setenv("GNUMAKEFLAGS", "-n")

    build = _run_product_helper(root, "build")
    test = _run_product_helper(root, "test")

    assert build.returncode == 0, build.stderr
    assert test.returncode == 0, test.stderr
    assert (root / "validation-calls.log").read_text(
        encoding="utf-8"
    ) == "build\ntest\n"


def test_feature_product_tracks_root_inputs_but_not_reconcile_docs(tmp_path):
    root = _receipt_project(tmp_path)
    receipt = "docs/feature-validation.json"
    _write(root, "shared.mk", "VALUE := one\n")
    full = _run_product_helper(root, "full", "--record", receipt)
    assert full.returncode == 0, full.stderr

    _write(root, "docs/PRD.md", "# PRD\n\nReconciliado.\n")
    docs_only = _run_product_helper(root, "verify", receipt)
    assert docs_only.returncode == 0, docs_only.stderr

    _write(root, "shared.mk", "VALUE := two\n")
    changed_input = _run_product_helper(root, "verify", receipt)
    assert changed_input.returncode == 1
    assert "inputs executáveis" in changed_input.stderr


def test_feature_product_focal_executes_argv_directly_from_product_root(tmp_path):
    root = _receipt_project(tmp_path)
    script = (
        "from pathlib import Path; Path('../focal.out').write_text('ok;not-a-shell')"
    )

    focal = _run_product_helper(root, "focal", "--", sys.executable, "-c", script)

    assert focal.returncode == 0, focal.stderr
    assert (root / "focal.out").read_text(encoding="utf-8") == "ok;not-a-shell"
    assert not (root / "validation-calls.log").exists()


def test_feature_validator_detects_src_product_root(tmp_path):
    root = _base_project(tmp_path, product_dir="src")
    _clear_discovery(root)

    baseline = _run_validator(root, "baseline")

    assert baseline.returncode == 0, baseline.stderr
    payload = yaml.safe_load(
        (root / "docs" / "feature-baseline.yml").read_text(encoding="utf-8")
    )
    assert payload["product_root"] == "src"


def test_feature_validator_detects_root_product_root(tmp_path):
    root = _base_project(tmp_path, product_dir=".")
    _clear_discovery(root)

    baseline = _run_validator(root, "baseline")

    assert baseline.returncode == 0, baseline.stderr
    payload = yaml.safe_load(
        (root / "docs" / "feature-baseline.yml").read_text(encoding="utf-8")
    )
    assert payload["product_root"] == "."


def test_feature_validator_prefers_nested_makefile_over_root(tmp_path):
    root = _base_project(tmp_path, product_dir="project")
    _write(root, "Makefile", "test:\n\t@true\n\nbuild:\n\t@true\n")
    _clear_discovery(root)

    baseline = _run_validator(root, "baseline")

    assert baseline.returncode == 0, baseline.stderr
    payload = yaml.safe_load(
        (root / "docs" / "feature-baseline.yml").read_text(encoding="utf-8")
    )
    assert payload["product_root"] == "project"


def test_feature_validator_accepts_implementation_at_repo_root(tmp_path):
    root = _base_project(tmp_path, product_dir=".")
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "app.py", "VALUE = 1\n")
    _write(root, "tests/test_app.py", "def test_value():\n    assert True\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=root,
        check=True,
    )
    _write(root, "app.py", "VALUE = 2\n")
    _write(root, "tests/test_app.py", "def test_value():\n    assert 2 == 2\n")
    _write(
        root, "docs/implementation-report.md", "| AC-01 | PASS |\n| AC-02 | PASS |\n"
    )

    implementation = _run_validator(root, "implementation")

    assert implementation.returncode == 0, implementation.stderr

    # Mudança apenas em docs/ não conta como implementação do produto na raiz.
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "implemented",
        ],
        cwd=root,
        check=True,
    )
    _write(root, "docs/feature-result.md", "draft\n")

    docs_only = _run_validator(root, "implementation")

    assert docs_only.returncode == 1
    assert "não alterou nenhum arquivo" in docs_only.stderr


def test_feature_validator_rejects_ambiguous_product_roots(tmp_path):
    root = _base_project(tmp_path, product_dir="project")
    _write(root, "src/Makefile", "test:\n\t@true\n\nbuild:\n\t@true\n")

    baseline = _run_validator(root, "baseline")

    assert baseline.returncode == 1
    assert "mais de um diretório" in baseline.stderr


def test_feature_discovery_gate_exports_clarification_status(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    node = load_graph(PROCESS).get_node("feature.discovery_gate")

    validation = run_validators(
        node,
        project_root=str(root),
        work_dir=str(root),
    )

    assert validation.passed
    assert validation.artifacts == {"clarification_status": "clear"}


def test_feature_validator_requires_real_questions_when_clarification_is_required(
    tmp_path,
):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(root, "docs/feature-discovery.md", "clarification_status: required\n")

    result = _run_validator(root, "discovery")

    assert result.returncode == 1
    assert "exige perguntas" in result.stderr


def test_feature_validator_rejects_unknown_target_feature(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(root, "docs/feature.md", FEATURE.replace("FEAT-001", "FEAT-999"))
    _write(
        root,
        "docs/feature-plan.md",
        "# Plano\n\nPB-002 evolui FEAT-999.\nAC-01\nAC-02\n",
    )

    result = _run_validator(root, "discovery")

    assert result.returncode == 1
    assert "FEATURES não contém" in result.stderr


def test_feature_validator_implementation_review_and_reconcile_pass(tmp_path):
    root = _base_project(tmp_path)
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "project/app.py", "VALUE = 1\n")
    _write(root, "project/tests/test_app.py", "def test_value():\n    assert True\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=root,
        check=True,
    )
    _write(root, "project/app.py", "VALUE = 2\n")
    _write(root, "project/tests/test_app.py", "def test_value():\n    assert 2 == 2\n")
    _write(
        root, "docs/implementation-report.md", "| AC-01 | PASS |\n| AC-02 | PASS |\n"
    )
    _write(
        root,
        "docs/feature-review.md",
        "Resultado: APPROVED\n\n| AC-01 | PASS |\n| AC-02 | PASS |\n",
    )
    _write_review_route(root)

    implementation = _run_validator(root, "implementation")
    review = _run_validator(root, "review")
    assert implementation.returncode == 0, implementation.stderr
    assert review.returncode == 0, review.stderr

    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.replace("in_progress", "accepted"))
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace("| PB-001 |", "| PB-001, PB-002 |"),
    )
    _write(
        root,
        "docs/feature-result.md",
        "# Resultado PB-002\n\n| AC-01 | PASS |\n| AC-02 | PASS |\n\n"
        "## Documentação atualizada\n\n"
        "- CHANGELOG.md\n- docs/PROJECT_BACKLOG.md\n- docs/FEATURES.md\n",
    )
    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n## Não lançado\n\n- #FEAT PB-002: busca de clientes entregue.\n",
    )

    reconcile = _run_validator(root, "reconcile")
    assert reconcile.returncode == 0, reconcile.stderr


def test_feature_evidence_gate_checks_references_without_claiming_semantics(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(root, "project/tests/test_search.py", "def test_search():\n    assert True\n")
    commands = [["make", "-C", "project", "build"], ["make", "-C", "project", "test"]]
    _write(
        root,
        "docs/feature-validation.json",
        json.dumps({"commands": commands}),
    )
    _write(
        root,
        "docs/implementation-report.md",
        "| AC-01 | PASS | project/tests/test_search.py |\n"
        "| AC-02 | PASS | project/tests/test_search.py |\n",
    )
    _write(
        root,
        "docs/feature-evidence.yml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "receipt": "docs/feature-validation.json",
                "commands": commands,
                "acceptance": [
                    {
                        "id": "AC-01",
                        "status": "PASS",
                        "tests": ["project/tests/test_search.py"],
                        "artifacts": [],
                    },
                    {
                        "id": "AC-02",
                        "status": "PASS",
                        "tests": ["project/tests/test_search.py"],
                        "artifacts": [],
                    },
                ],
            },
            sort_keys=False,
        ),
    )

    valid = _run_validator(root, "evidence")
    assert valid.returncode == 0, valid.stderr

    evidence = yaml.safe_load((root / "docs/feature-evidence.yml").read_text())
    evidence["acceptance"][1]["tests"] = ["project/tests/missing.py"]
    _write(root, "docs/feature-evidence.yml", yaml.safe_dump(evidence))
    invalid = _run_validator(root, "evidence")
    assert invalid.returncode == 1
    assert "path ausente" in invalid.stderr


def test_feature_review_route_is_exported_for_all_semantic_destinations(tmp_path):
    graph = load_graph(PROCESS)
    for route, destination in {
        "approved": "feature.acceptance",
        "implementation": "feature.implement",
        "evidence": "feature.evidence",
        "scope": "feature.discovery",
    }.items():
        assert graph.resolve_next(
            "feature.review_decision", {"review_route": route}
        ) == destination


def test_feature_id_reservation_blocks_same_pb_in_live_parallel_worktrees(tmp_path):
    root = _base_project(tmp_path / "repo")
    _clear_discovery(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    first = tmp_path / "cycle-one"
    second = tmp_path / "cycle-two"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "cycle-one", str(first), "HEAD"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "cycle-two", str(second), "HEAD"],
        cwd=root,
        check=True,
    )

    reserved = _run_validator(first, "reserve")
    duplicate = _run_validator(second, "reserve")

    assert reserved.returncode == 0, reserved.stderr
    payload = yaml.safe_load((first / "docs/feature-id-reservation.yml").read_text())
    assert payload["backlog_item"] == "PB-002"
    assert payload["final_feature_id"] == "FEAT-001"
    assert duplicate.returncode == 1
    assert "ciclos paralelos devem usar PBs distintos" in duplicate.stderr


def test_feature_id_reservation_allocates_distinct_new_feat_ids_under_one_lock(
    tmp_path,
):
    root = _base_project(tmp_path / "repo")
    _clear_discovery(root)
    _write(
        root,
        "docs/PROJECT_BACKLOG.md",
        BACKLOG
        + "| PB-003 | Feature | P1 | planned | feature-request | Exportação | AC-01 | — | Planejado |\n",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)

    worktrees = [tmp_path / "cycle-new-one", tmp_path / "cycle-new-two"]
    for index, worktree in enumerate(worktrees, start=1):
        subprocess.run(
            [
                "git", "worktree", "add", "-q", "-b", f"cycle-new-{index}",
                str(worktree), "HEAD",
            ],
            cwd=root,
            check=True,
        )
        backlog = "PB-002" if index == 1 else "PB-003"
        feature = FEATURE.replace("type: evolution", "type: new").replace(
            "target_feature: FEAT-001", "target_feature: new"
        ).replace("backlog_item: PB-002", f"backlog_item: {backlog}")
        _write(worktree, "docs/feature.md", feature)
        _write(worktree, "docs/feature-request.md", f"{backlog}: capacidade nova.\n")
        _write(
            worktree,
            "docs/feature-plan.md",
            f"# Plano\n\n{backlog}\nAC-01\nAC-02\n",
        )

    results = [_run_validator(worktree, "reserve") for worktree in worktrees]

    assert all(result.returncode == 0 for result in results)
    reservations = [
        yaml.safe_load((worktree / "docs/feature-id-reservation.yml").read_text())
        for worktree in worktrees
    ]
    assert [item["final_feature_id"] for item in reservations] == [
        "FEAT-002",
        "FEAT-003",
    ]


def test_feature_reconciliation_proposal_is_validated_then_applied_atomically(tmp_path):
    root = _base_project(tmp_path)
    _snapshot_baseline(root)
    _clear_discovery(root)
    accepted_backlog = BACKLOG.replace("in_progress", "accepted")
    evolved_features = FEATURES.replace("| PB-001 |", "| PB-001, PB-002 |")
    backlog_row = next(
        line for line in accepted_backlog.splitlines() if line.startswith("| PB-002 |")
    )
    feature_row = next(
        line for line in evolved_features.splitlines() if line.startswith("| FEAT-001 |")
    )
    proposal = {
        "schema_version": 1,
        "backlog_item": "PB-002",
        "target_feature": "FEAT-001",
        "final_feature_id": "FEAT-001",
        "backlog_row": backlog_row,
        "feature_row": feature_row,
        "changelog_entry": "- #FEAT PB-002 / FEAT-001: busca entregue.",
        "documentation": {},
    }
    _write(
        root,
        "docs/feature-reconciliation.yml",
        yaml.safe_dump(proposal, allow_unicode=True, sort_keys=False),
    )
    _write(
        root,
        "docs/feature-result.md",
        "# Resultado PB-002\n\nAC-01 PASS\nAC-02 PASS\n\n"
        "## Documentação atualizada\n\n"
        "- CHANGELOG.md\n- docs/PROJECT_BACKLOG.md\n- docs/FEATURES.md\n",
    )

    proposed = _run_validator(root, "proposal")
    applied = _run_validator(root, "apply-reconcile")

    assert proposed.returncode == 0, proposed.stderr
    assert applied.returncode == 0, applied.stderr
    merged_changelog = (root / "CHANGELOG.md").read_text()
    assert "- #FEAT PB-002 / FEAT-001: busca entregue." in merged_changelog
    assert "PB-001 entregue" in merged_changelog
    assert "| PB-002 | Feature | P1 | accepted" in (
        root / "docs/PROJECT_BACKLOG.md"
    ).read_text()

    proposal["documentation"]["docs/UNRELATED.md"] = "not allowed\n"
    _write(root, "docs/feature-reconciliation.yml", yaml.safe_dump(proposal))
    rejected = _run_validator(root, "proposal")
    assert rejected.returncode == 1
    assert "path canônico não autorizado" in rejected.stderr


def test_feature_validator_reconcile_requires_updated_changelog(tmp_path):
    root = _base_project(tmp_path)
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.replace("in_progress", "accepted"))
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace("| PB-001 |", "| PB-001, PB-002 |"),
    )
    _write(
        root,
        "docs/feature-result.md",
        "PB-002\nAC-01 PASS\nAC-02 PASS\n"
        "CHANGELOG.md\ndocs/PROJECT_BACKLOG.md\ndocs/FEATURES.md\n",
    )

    result = _run_validator(root, "reconcile")

    assert result.returncode == 1
    assert "CHANGELOG.md não foi atualizado" in result.stderr


def test_feature_validator_reconcile_requires_changelog_backlog_reference(tmp_path):
    root = _base_project(tmp_path)
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.replace("in_progress", "accepted"))
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace("| PB-001 |", "| PB-001, PB-002 |"),
    )
    _write(
        root,
        "docs/feature-result.md",
        "PB-002\nAC-01 PASS\nAC-02 PASS\n"
        "CHANGELOG.md\ndocs/PROJECT_BACKLOG.md\ndocs/FEATURES.md\n",
    )
    _write(root, "CHANGELOG.md", "# Changelog\n\n- Busca entregue sem referência.\n")

    result = _run_validator(root, "reconcile")

    assert result.returncode == 1
    assert "CHANGELOG.md não referencia PB-002" in result.stderr


def test_feature_validator_reconcile_requires_feat_as_first_entry_token(tmp_path):
    root = _base_project(tmp_path)
    _prepare_reconcile_artifacts(root)
    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n## Não lançado\n\n- PB-002: busca entregue com #FEAT.\n",
    )

    result = _run_validator(root, "reconcile")

    assert result.returncode == 1
    assert "`#FEAT` como primeiro token" in result.stderr


def test_feature_validator_reconcile_accepts_feat_tag_without_bullet(tmp_path):
    root = _base_project(tmp_path)
    _prepare_reconcile_artifacts(root)
    _write(
        root,
        "CHANGELOG.md",
        "# Changelog\n\n## Não lançado\n\n#FEAT PB-002: busca entregue.\n",
    )

    result = _run_validator(root, "reconcile")

    assert result.returncode == 0, result.stderr


def test_feature_validator_reconcile_requires_documentation_section(tmp_path):
    root = _base_project(tmp_path)
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.replace("in_progress", "accepted"))
    _write(
        root,
        "docs/FEATURES.md",
        FEATURES.replace("| PB-001 |", "| PB-001, PB-002 |"),
    )
    _write(root, "docs/feature-result.md", "PB-002\nAC-01 PASS\nAC-02 PASS\n")
    _write(root, "CHANGELOG.md", "# Changelog\n\n- #FEAT PB-002: busca entregue.\n")

    result = _run_validator(root, "reconcile")

    assert result.returncode == 1
    assert "sem seção `Documentação atualizada`" in result.stderr


def test_feature_validator_review_accepts_rejected_with_failed_ac(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(
        root,
        "docs/feature-review.md",
        "Resultado: REJECTED\n\n"
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | PASS | Busca completa coberta. |\n"
        "| AC-02 | FAIL | Regressão reproduzida. |\n",
    )
    _write_review_route(root, route="implementation", verdict="REJECTED")

    review = _run_validator(root, "review")

    assert review.returncode == 0, review.stderr


def test_feature_validator_review_accepts_rejected_with_all_ac_pass_for_scope_regression(
    tmp_path,
):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(
        root,
        "docs/feature-review.md",
        "Resultado: REJECTED\n\n"
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | PASS | Coberto. |\n"
        "| AC-02 | PASS | Coberto. |\n\n"
        "Regressão fora dos AC: contrato público incompatível.\n",
    )
    _write_review_route(root, route="scope", verdict="REJECTED")

    review = _run_validator(root, "review")

    assert review.returncode == 0, review.stderr


def test_feature_validator_review_does_not_treat_technical_fail_kind_as_ac_failure(
    tmp_path,
):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(
        root,
        "docs/feature-review.md",
        "Resultado: APPROVED\n\n"
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | PASS | Preserva a aresta de kind `fail`. |\n"
        "| AC-02 | PASS | O tipo técnico `fail` continua renderizado. |\n",
    )
    _write_review_route(root)

    review = _run_validator(root, "review")

    assert review.returncode == 0, review.stderr


def test_feature_validator_review_rejects_approved_with_failed_or_ambiguous_ac(
    tmp_path,
):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    _write(
        root,
        "docs/feature-review.md",
        "Resultado: APPROVED\n\n"
        "| AC | Status | Evidência |\n"
        "|---|---|---|\n"
        "| AC-01 | PASS | Coberto. |\n"
        "| AC-02 | FAIL | Quebrado. |\n",
    )
    _write_review_route(root)

    failed = _run_validator(root, "review")
    assert failed.returncode == 1
    assert "APPROVED exige todos os AC como PASS" in failed.stderr

    _write(
        root,
        "docs/feature-review.md",
        "Resultado: REJECTED\n\n"
        "| AC-01 | PASS | Primeira avaliação. |\n"
        "| AC-01 | FAIL | Avaliação conflitante. |\n"
        "| AC-02 | PASS | Coberto. |\n",
    )
    _write_review_route(root, route="implementation", verdict="REJECTED")

    ambiguous = _run_validator(root, "review")
    assert ambiguous.returncode == 1
    assert "status ambíguo: AC-01" in ambiguous.stderr


def test_feature_validator_review_requires_valid_structured_route(tmp_path):
    root = _base_project(tmp_path)
    _clear_discovery(root)
    report_body = "\n| AC-01 | PASS |\n| AC-02 | PASS |\n"

    _write(root, "docs/feature-review.md", report_body)
    for route_payload, expected in (
        (
            "schema_version: 1\nverdict: APPROVED\nreview_route: nowhere\nsummary: x\n",
            "review_route inválida",
        ),
        (
            "schema_version: 1\nverdict: REJECTED\nreview_route: approved\nsummary: x\n",
            "approved exige APPROVED",
        ),
        (
            "schema_version: 2\nverdict: APPROVED\nreview_route: approved\nsummary: x\n",
            "schema_version deve ser 1",
        ),
    ):
        _write(root, "docs/feature-review.yml", route_payload)
        review = _run_validator(root, "review")
        assert review.returncode == 1
        assert expected in review.stderr


def test_feature_validator_accepts_implementation_under_src(tmp_path):
    root = _base_project(tmp_path, product_dir="src")
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "src/app.py", "VALUE = 1\n")
    _write(root, "src/tests/test_app.py", "def test_value():\n    assert True\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=root,
        check=True,
    )
    _write(root, "src/app.py", "VALUE = 2\n")
    _write(root, "src/tests/test_app.py", "def test_value():\n    assert 2 == 2\n")
    _write(
        root, "docs/implementation-report.md", "| AC-01 | PASS |\n| AC-02 | PASS |\n"
    )

    implementation = _run_validator(root, "implementation")

    assert implementation.returncode == 0, implementation.stderr


def test_feature_validator_reconcile_rejects_unfinished_backlog_item(tmp_path):
    root = _base_project(tmp_path)
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "docs/feature-result.md", "PB-002\nAC-01 PASS\nAC-02 PASS\n")

    result = _run_validator(root, "reconcile")

    assert result.returncode == 1
    assert "done/accepted" in result.stderr


def test_feature_validator_reconcile_rejects_unrelated_catalog_changes(tmp_path):
    root = _base_project(tmp_path)
    unrelated_features = (
        FEATURES
        + "| FEAT-002 | active | PB-001 | Relatórios | Exporta relatórios. | cycle-01 | tests | — | Entrega inicial. |\n"
    )
    _write(root, "docs/FEATURES.md", unrelated_features)
    _snapshot_baseline(root)
    _clear_discovery(root)
    _write(root, "docs/PROJECT_BACKLOG.md", BACKLOG.replace("in_progress", "accepted"))
    _write(
        root,
        "docs/FEATURES.md",
        unrelated_features.replace("| PB-001 |", "| PB-001, PB-002 |", 1).replace(
            "| Relatórios |", "| Relatórios alterados |"
        ),
    )
    _write(root, "docs/feature-result.md", "PB-002\nAC-01 PASS\nAC-02 PASS\n")

    result = _run_validator(root, "reconcile")

    assert result.returncode == 1
    assert "registros alheios" in result.stderr
    assert "FEAT-002" in result.stderr


def test_feature_serve_script_finds_root_from_nested_local_process(tmp_path):
    root = tmp_path / "sample"
    project = root / "src"
    scripts = root / ".ft" / "process" / "feature" / "scripts"
    project.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (root / ".ft" / "manifest.yml").write_text(
        "schema_version: 1\nprocess: .ft/process/process.yml\n",
        encoding="utf-8",
    )
    (project / "health").write_text("ok\n", encoding="utf-8")
    (project / "Makefile").write_text(
        "PORT ?= 8021\n"
        "run:\n"
        "\tpython -m http.server $(PORT) --bind 127.0.0.1\n"
        "url:\n"
        "\t@echo http://127.0.0.1:$(PORT)\n",
        encoding="utf-8",
    )
    script = scripts / "serve.sh"
    shutil.copy2(TEMPLATE / "scripts" / "serve.sh", script)
    shutil.copy2(PRODUCT_HELPER, scripts / "product.sh")

    token = ""
    try:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=root,
            env={**os.environ, "PORT": str(_free_port())},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (root / ".serve_url").read_text().startswith("http://127.0.0.1:")
        token = (root / ".serve.pid").read_text().strip()
        assert token.startswith(("group:", "pid:"))
        stopped = subprocess.run(
            ["bash", str(script), "stop"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        assert stopped.returncode == 0, stopped.stderr
        assert not (root / ".serve.pid").exists()
        assert not (root / ".serve_url").exists()
        token = ""
    finally:
        if token:
            _stop_owned_process(token)
