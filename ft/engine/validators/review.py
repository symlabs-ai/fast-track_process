"""
Self-review validators — checklist automatico de revisao.
Cada funcao retorna (passed: bool, detail: str).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def no_large_files(
    max_lines: int = 500,
    paths: list[str] | None = None,
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que nenhum arquivo excede max_lines."""
    target = paths or ["src/"]
    large = []

    for target_path in target:
        full = Path(project_root) / target_path
        if full.is_dir():
            py_files = list(full.rglob("*.py"))
        elif full.is_file():
            py_files = [full]
        else:
            continue

        for f in py_files:
            lines = len(f.read_text().splitlines())
            if lines > max_lines:
                rel = f.relative_to(project_root)
                large.append(f"{rel} ({lines})")

    if large:
        return False, f"no_large_files FAIL: {len(large)} acima de {max_lines} linhas: {'; '.join(large[:3])}"
    return True, f"no_large_files: nenhum arquivo acima de {max_lines} linhas"


def no_print_statements(
    paths: list[str] | None = None,
    project_root: str = ".",
    allow_in: list[str] | None = None,
) -> tuple[bool, str]:
    """Verifica que nao ha print() no codigo de producao."""
    target = paths or ["src/"]
    allow_in = allow_in or ["cli/", "runner.py"]

    cmd = ["grep", "-rn", r"print(", "--include=*.py"]
    cmd.extend(target)

    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        return True, "no_print_statements: nenhum print() encontrado"

    matches = result.stdout.strip().splitlines()
    # Filtrar arquivos permitidos
    filtered = [m for m in matches if not any(a in m for a in allow_in)]

    if not filtered:
        return True, "no_print_statements: prints apenas em arquivos permitidos"
    return False, f"no_print_statements FAIL: {len(filtered)} print() em codigo de producao"


def changed_files_have_tests(
    project_root: str = ".",
) -> tuple[bool, str]:
    """Verifica que arquivos modificados em src/ tem testes correspondentes."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    modified = result.stdout.strip().splitlines() if result.stdout.strip() else []

    # Filtrar apenas src/
    src_files = [f for f in modified if f.startswith("src/") and f.endswith(".py")]
    if not src_files:
        return True, "changed_files_have_tests: nenhum arquivo src/ modificado"

    missing_tests = []
    for src_file in src_files:
        # src/foo/bar.py → tests/test_bar.py ou tests/foo/test_bar.py
        name = Path(src_file).stem
        test_patterns = [
            Path(project_root) / "tests" / f"test_{name}.py",
            Path(project_root) / "tests" / Path(src_file).parent.name / f"test_{name}.py",
        ]
        if not any(p.exists() for p in test_patterns):
            missing_tests.append(src_file)

    if missing_tests:
        return False, f"changed_files_have_tests FAIL: sem testes para: {', '.join(missing_tests)}"
    return True, f"changed_files_have_tests: {len(src_files)} arquivos com testes"
