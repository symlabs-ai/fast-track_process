"""
Validadores de testes — TDD red/green, cobertura por arquivo.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def tests_pass(project_root: str = ".") -> tuple[bool, str]:
    """Roda pytest e verifica se passa."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        return True, f"tests_pass: {last_line}"
    last_lines = result.stdout.strip().splitlines()[-3:] if result.stdout.strip() else []
    summary = " | ".join(last_lines)
    return False, f"tests_pass FAIL: {summary}"


def tests_fail(project_root: str = ".") -> tuple[bool, str]:
    """Verifica que testes FALHAM (TDD red phase)."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=short", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return True, "tests_fail: testes falharam como esperado (red phase)"
    return False, "tests_fail FAIL: testes passaram — deviam falhar na red phase"


def coverage_min(min_pct: int, project_root: str = ".") -> tuple[bool, str]:
    """Roda pytest com coverage e verifica minimo global."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--cov=src", "--cov-report=term-missing", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    for line in result.stdout.splitlines():
        if "TOTAL" in line:
            match = re.search(r'(\d+)%', line)
            if match:
                pct = int(match.group(1))
                if pct >= min_pct:
                    return True, f"coverage_min: {pct}% (min {min_pct}%)"
                return False, f"coverage_min FAIL: {pct}% < {min_pct}%"
    return False, "coverage_min FAIL: nao consegui extrair cobertura do output"


def coverage_per_file(
    min_pct: int,
    project_root: str = ".",
    paths: list[str] | None = None,
) -> tuple[bool, str]:
    """Verifica cobertura minima por arquivo. Se paths=None, checa todos."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--cov=src", "--cov-report=json", "-q"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )

    cov_file = Path(project_root) / "coverage.json"
    if not cov_file.exists():
        return False, "coverage_per_file FAIL: coverage.json nao gerado"

    try:
        cov_data = json.loads(cov_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False, "coverage_per_file FAIL: erro ao ler coverage.json"
    finally:
        cov_file.unlink(missing_ok=True)

    files_data = cov_data.get("files", {})
    if not files_data:
        return False, "coverage_per_file FAIL: nenhum arquivo no coverage"

    failures = []
    checked = 0
    for filepath, info in files_data.items():
        if paths and not any(filepath.endswith(p) or p in filepath for p in paths):
            continue
        pct = info.get("summary", {}).get("percent_covered", 0)
        checked += 1
        if pct < min_pct:
            failures.append(f"{filepath}: {pct:.0f}%")

    if not checked:
        return False, "coverage_per_file FAIL: nenhum arquivo matchou os paths"

    if failures:
        return False, f"coverage_per_file FAIL ({len(failures)} abaixo de {min_pct}%): {'; '.join(failures[:5])}"
    return True, f"coverage_per_file: {checked} arquivos >= {min_pct}%"


def tests_exist(test_pattern: str = "tests/", project_root: str = ".") -> tuple[bool, str]:
    """Verifica que existem arquivos de teste."""
    test_dir = Path(project_root) / test_pattern
    if test_dir.is_dir():
        test_files = list(test_dir.glob("test_*.py"))
        if test_files:
            return True, f"tests_exist: {len(test_files)} arquivos de teste em {test_pattern}"
        return False, f"tests_exist FAIL: nenhum test_*.py em {test_pattern}"
    # Pode ser um glob pattern
    test_files = list(Path(project_root).glob(test_pattern))
    if test_files:
        return True, f"tests_exist: {len(test_files)} arquivos de teste"
    return False, f"tests_exist FAIL: nenhum arquivo em {test_pattern}"
