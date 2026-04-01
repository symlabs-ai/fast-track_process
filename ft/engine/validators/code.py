"""
Validadores de qualidade de codigo.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def lint_clean(
    paths: list[str] | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Roda ruff check e verifica se nao ha erros."""
    cmd = ["python", "-m", "ruff", "check"]
    if paths:
        cmd.extend(paths)
    else:
        cmd.append("src/")

    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0:
        return True, "lint_clean: ruff check sem erros"

    # Contar erros
    lines = result.stdout.strip().splitlines()
    error_lines = [l for l in lines if "Found" in l]
    summary = error_lines[-1] if error_lines else f"{len(lines)} problemas"
    return False, f"lint_clean FAIL: {summary}"


def format_check(
    paths: list[str] | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Roda ruff format --check e verifica se codigo esta formatado."""
    cmd = ["python", "-m", "ruff", "format", "--check"]
    if paths:
        cmd.extend(paths)
    else:
        cmd.append("src/")

    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0:
        return True, "format_check: codigo formatado"

    lines = result.stderr.strip().splitlines() if result.stderr else result.stdout.strip().splitlines()
    unformatted = [l for l in lines if "would reformat" in l.lower()]
    count = len(unformatted)
    return False, f"format_check FAIL: {count} arquivos nao formatados"


def no_todo_fixme(
    paths: list[str] | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que nao ha TODO ou FIXME no codigo."""
    target_paths = paths or ["src/"]

    cmd = ["grep", "-rn", "-E", r"(TODO|FIXME|HACK|XXX)", "--include=*.py"]
    cmd.extend(target_paths)

    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:  # grep returns 1 when no matches
        return True, "no_todo_fixme: nenhum TODO/FIXME encontrado"

    matches = result.stdout.strip().splitlines()
    return False, f"no_todo_fixme FAIL: {len(matches)} TODO/FIXME encontrados"
