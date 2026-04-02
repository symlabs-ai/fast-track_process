"""
Gate validators compostos.
Cada gate e uma combinacao de validadores basicos.
Retorna (passed: bool, detail: str).
"""

from __future__ import annotations

from pathlib import Path

from ft.engine.validators.artifacts import (
    file_exists,
    has_sections,
    min_lines,
    tests_pass,
    coverage_min,
)


def gate_delivery(
    outputs: list[str],
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate de delivery — verifica que todos os outputs existem e testes passam."""
    failures = []

    for output_path in outputs:
        passed, detail = file_exists(output_path, project_root)
        if not passed:
            failures.append(detail)

    t_passed, t_detail = tests_pass(project_root)
    if not t_passed:
        failures.append(t_detail)

    if failures:
        return False, f"gate_delivery FAIL: {'; '.join(failures)}"
    return True, f"gate_delivery: {len(outputs)} arquivos + testes OK"


def gate_smoke(
    project_root: str = ".",
    smoke_cmd: str | None = None,
) -> tuple[bool, str]:
    """Gate de smoke test — roda testes e opcionalmente um comando custom."""
    import subprocess

    t_passed, t_detail = tests_pass(project_root)
    if not t_passed:
        return False, f"gate_smoke FAIL: {t_detail}"

    if smoke_cmd:
        try:
            result = subprocess.run(
                smoke_cmd,
                shell=True,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return False, f"gate_smoke FAIL: comando '{smoke_cmd}' retornou {result.returncode}"
        except subprocess.TimeoutExpired:
            return False, f"gate_smoke FAIL: comando '{smoke_cmd}' timeout"

    return True, "gate_smoke: testes + smoke OK"


def gate_mvp(
    required_docs: list[str],
    min_coverage: int = 70,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate MVP — verifica docs, testes e cobertura."""
    failures = []

    for doc_path in required_docs:
        passed, detail = file_exists(doc_path, project_root)
        if not passed:
            failures.append(detail)

    t_passed, t_detail = tests_pass(project_root)
    if not t_passed:
        failures.append(t_detail)

    c_passed, c_detail = coverage_min(min_coverage, project_root)
    if not c_passed:
        failures.append(c_detail)

    if failures:
        return False, f"gate_mvp FAIL: {'; '.join(failures)}"
    return True, f"gate_mvp: docs + testes + cobertura >= {min_coverage}% OK"


def gate_tdd_sequence(
    tdd_log: dict,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate TDD — exige red phase antes de green (testes passando)."""
    if not tdd_log.get("red_phase_completed"):
        return False, "gate_tdd_sequence FAIL: red phase não foi completada"
    if not tdd_log.get("tests_passing"):
        return False, "gate_tdd_sequence FAIL: testes não estão passando"
    return True, "gate_tdd_sequence: OK — red→green sequencial confirmado"


def gate_coverage_80(
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate de cobertura — bloqueia se cobertura < 80%."""
    passed, detail = coverage_min(80, project_root)
    if not passed:
        return False, f"gate_coverage_80 FAIL: {detail}"
    return True, f"gate_coverage_80: PASS — {detail}"


def gate_e2e_all_pass(
    scenarios: list[dict],
    project_root: str = ".",
) -> tuple[bool, str]:
    """Gate E2E — falha se qualquer cenário não passar ou lista vazia."""
    if not scenarios:
        return False, "gate_e2e_all_pass FAIL: lista de cenários vazia"
    failed = [s["id"] for s in scenarios if not s.get("passed")]
    if failed:
        return False, f"gate_e2e_all_pass FAIL: cenários falharam: {', '.join(failed)}"
    return True, f"gate_e2e_all_pass: PASS — {len(scenarios)} cenários OK"


def gate_frontend(project_root: str = ".") -> tuple[bool, str]:
    """Gate de frontend — verifica estrutura minima de PWA."""
    import json
    failures = []
    for path in ["frontend/package.json", "frontend/public/manifest.json", "frontend/src/"]:
        full = Path(project_root) / path
        if not full.exists():
            failures.append(f"{path} nao encontrado")
    manifest = Path(project_root) / "frontend/public/manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            for field in ("name", "start_url", "display"):
                if field not in data:
                    failures.append(f"manifest.json sem campo '{field}'")
        except Exception:
            failures.append("manifest.json invalido")
    if failures:
        return False, f"gate_frontend FAIL: {'; '.join(failures)}"
    return True, "gate_frontend: estrutura PWA OK"
