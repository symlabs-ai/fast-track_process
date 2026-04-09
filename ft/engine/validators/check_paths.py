"""
Validator: paths_clean

Verifica que nenhum arquivo fora dos paths permitidos foi modificado no worktree.
Usa `git diff --name-only` (staged + unstaged) relativo à HEAD.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def paths_clean(allowed: list[str], project_root: str = ".") -> tuple[bool, str]:
    """
    Retorna (True, msg) se todos os arquivos modificados estão dentro de `allowed`.
    Retorna (False, msg) se houver arquivos fora dos paths permitidos.

    `allowed` é uma lista de prefixos (ex: ["frontend/", "docs/", ".build_ok"]).
    """
    root = Path(project_root)

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    # Inclui também arquivos não rastreados relevantes
    result_untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    # Padrões gerados pelo engine — sempre excluídos da checagem
    ENGINE_PATTERNS = ("_log.md",)

    modified = set(result.stdout.strip().splitlines())
    untracked = set(result_untracked.stdout.strip().splitlines())
    all_changed = modified | untracked

    # Remover arquivos gerados pelo engine
    all_changed = {f for f in all_changed if not any(f.endswith(p) for p in ENGINE_PATTERNS)}

    if not all_changed:
        return True, "paths_clean: nenhuma modificação detectada (ou apenas arquivos de engine)"

    violations = [
        f for f in sorted(all_changed)
        if not any(f.startswith(p) for p in allowed)
    ]

    if not violations:
        return True, f"paths_clean: {len(all_changed)} arquivo(s) dentro dos paths permitidos"

    preview = "\n".join(f"  - {v}" for v in violations[:10])
    return False, (
        f"paths_clean FAIL: {len(violations)} arquivo(s) fora dos paths permitidos:\n{preview}\n"
        f"Paths permitidos: {allowed}"
    )
