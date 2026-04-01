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
