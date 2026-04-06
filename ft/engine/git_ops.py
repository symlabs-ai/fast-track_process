"""
Git operations — commit automatico apos green+review.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def auto_commit(
    message: str,
    project_root: str = ".",
    paths: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Faz git add + commit com mensagem padrao.
    Retorna (success, detail).
    """
    cwd = project_root

    # Stage arquivos
    if paths:
        for p in paths:
            subprocess.run(["git", "add", p], cwd=cwd, capture_output=True)
    else:
        # Stage tudo exceto engine_state e runs/
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True)
        # Unstage runs/ (state e artefatos descartáveis)
        subprocess.run(
            ["git", "reset", "HEAD", "runs/"],
            cwd=cwd, capture_output=True, text=True,
        )
        # Fallback legado: unstage project/state/ se existir
        subprocess.run(
            ["git", "reset", "HEAD", "project/state/"],
            cwd=cwd, capture_output=True, text=True,
        )

    # Verificar se ha algo staged
    status = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        cwd=cwd, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        return True, "auto_commit: nada para commitar"

    # Commit
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=cwd, capture_output=True, text=True,
    )

    if result.returncode == 0:
        # Extrair hash curto
        hash_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True,
        )
        short_hash = hash_result.stdout.strip()
        return True, f"auto_commit: {short_hash} — {message}"

    return False, f"auto_commit FAIL: {result.stderr.strip()[:200]}"


def get_changed_files(project_root: str = ".") -> list[str]:
    """Retorna lista de arquivos modificados (staged + unstaged)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=project_root, capture_output=True, text=True,
    )
    modified = result.stdout.strip().splitlines() if result.stdout.strip() else []

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_root, capture_output=True, text=True,
    )
    created = untracked.stdout.strip().splitlines() if untracked.stdout.strip() else []

    return modified + created
