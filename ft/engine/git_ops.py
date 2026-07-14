"""
Git operations — commit automatico apos green+review.
"""

from __future__ import annotations

import subprocess
from typing import Any, Mapping


_RUNTIME_STATE_PATHS = [
    "state/",
    "runs/",
    ".ft/runtime/",
    ".ft/cache/",
    ".ft/tmp/",
    ".ft/logs/",
    ".serve_url",
    ".serve_backend.pid",
    ".serve_frontend.pid",
    ".serve.pid",
    "src/.serve.log",
    "src/.serve.pid",
    ":(glob)**/.serve_url",
    ":(glob)**/.serve*.pid",
    ":(glob)**/.serve*.log",
    ":(glob)*_log.md",
    ":(glob)**/*_log.md",
]


def verify_hooks_from_process_meta(meta: Mapping[str, Any] | None) -> bool:
    """Return the safe commit-hook policy for process metadata.

    Hook bypass is opt-in only: absent or malformed metadata preserves Git's
    normal hooks. Schema validation reports malformed policies separately.
    """
    if not isinstance(meta, Mapping):
        return True
    policy = meta.get("commit_policy")
    return not (
        isinstance(policy, Mapping)
        and policy.get("verify_hooks") is False
    )


def git_command_prefix(verify_hooks: bool) -> list[str]:
    """Build a Git argv prefix that truly disables every hook when requested."""
    if verify_hooks:
        return ["git"]
    return ["git", "-c", "core.hooksPath=/dev/null"]


def _commit_policy_flags(verify_hooks: bool) -> list[str]:
    if verify_hooks:
        return []
    return ["--no-verify", "--no-gpg-sign"]


def _unstage_runtime_state(cwd: str) -> None:
    """Remove generated engine/serve state from the index after broad git add."""
    for pathspec in _RUNTIME_STATE_PATHS:
        subprocess.run(
            ["git", "reset", "HEAD", "--", pathspec],
            cwd=cwd,
            capture_output=True,
            text=True,
        )


def auto_commit(
    message: str,
    project_root: str = ".",
    paths: list[str] | None = None,
    *,
    verify_hooks: bool = True,
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
        # Stage tudo e depois remova artefatos descartáveis do índice.
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True)
        _unstage_runtime_state(cwd)

    # Verificar se ha algo staged
    status = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        cwd=cwd, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        return True, "auto_commit: nada para commitar"

    # Commit
    result = subprocess.run(
        [
            *git_command_prefix(verify_hooks),
            "commit",
            *_commit_policy_flags(verify_hooks),
            "-m",
            message,
        ],
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


_KNOWLEDGE_PATHS = (
    "docs/",
    ".ft/process/",
    ".ft/manifest.yml",
    ".ft/.gitignore",
)


def stage_knowledge(project_root: str = ".") -> tuple[bool, bool, str]:
    """Stageia o snapshot de conhecimento sem executar hooks.

    Retorna ``(ok, staged, detalhe)``. Separar stage de commit permite que o
    caller solte locks de runtime antes de executar hooks arbitrários, mantendo
    no índice a versão coerente capturada sob coordenação.
    """
    cwd = project_root
    check = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return True, False, "commit_knowledge: não é um repo git — pulando"

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *_KNOWLEDGE_PATHS],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return False, False, f"commit_knowledge FAIL: {status.stderr.strip()[:200]}"
    if not status.stdout.strip():
        return True, False, "commit_knowledge: docs/ e .ft/process/ sem mudanças"

    staged = subprocess.run(
        ["git", "add", "--", *_KNOWLEDGE_PATHS],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if staged.returncode != 0:
        return False, False, f"commit_knowledge FAIL: {staged.stderr.strip()[:200]}"
    return True, True, ""


def commit_staged_knowledge(
    project_root: str = ".",
    label: str = "snapshot",
    *,
    verify_hooks: bool = True,
) -> tuple[bool, str]:
    """Commita o snapshot de conhecimento já capturado no índice."""
    cwd = project_root
    result = subprocess.run(
        [
            *git_command_prefix(verify_hooks),
            "commit",
            *_commit_policy_flags(verify_hooks),
            "-m",
            f"chore: {label} — docs/ e .ft/process/",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        hash_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        short_hash = hash_result.stdout.strip()
        return True, f"commit_knowledge: {short_hash} — {label}"
    return False, f"commit_knowledge FAIL: {result.stderr.strip()[:200]}"


def commit_knowledge(
    project_root: str = ".",
    label: str = "snapshot",
    *,
    verify_hooks: bool = True,
) -> tuple[bool, str]:
    """Commita docs/ e metadados versionados do processo se houver mudanças.

    Chamado nativamente pelo engine antes de iniciar um run e ao final.
    Garante que o conhecimento do projeto tem histórico no Git.
    """
    ok, staged, detail = stage_knowledge(project_root)
    if not ok or not staged:
        return ok, detail
    return commit_staged_knowledge(
        project_root,
        label,
        verify_hooks=verify_hooks,
    )


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
