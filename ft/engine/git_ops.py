"""
Git operations — commit automatico apos green+review.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

from ft.engine.engine_artifacts import GIT_RUNTIME_PATHSPECS

#: O que a engine escreveu durante o run sai do commit automático. A lista é
#: declarada uma vez em `engine_artifacts`; manter uma cópia aqui foi o que
#: deixou este módulo e os validadores de template discordarem sobre
#: `<projeto>_log.md`.
_RUNTIME_STATE_PATHS = list(GIT_RUNTIME_PATHSPECS)


def verify_hooks_from_process_meta(meta: Mapping[str, Any] | None) -> bool:
    """Return the safe commit-hook policy for process metadata.

    Hook bypass is opt-in only: absent or malformed metadata preserves Git's
    normal hooks. Schema validation reports malformed policies separately.
    """
    if not isinstance(meta, Mapping):
        return True
    policy = meta.get("commit_policy")
    return not (isinstance(policy, Mapping) and policy.get("verify_hooks") is False)


def git_command_prefix(verify_hooks: bool) -> list[str]:
    """Build a Git argv prefix that truly disables every hook when requested."""
    if verify_hooks:
        return ["git"]
    return ["git", "-c", "core.hooksPath=/dev/null"]


def _commit_policy_flags(verify_hooks: bool) -> list[str]:
    if verify_hooks:
        return []
    return ["--no-verify", "--no-gpg-sign"]


def _unstage(cwd: str, pathspecs: Iterable[str]) -> None:
    """Tira do index tudo que casa com ``pathspecs``."""
    for pathspec in pathspecs:
        subprocess.run(
            ["git", "reset", "HEAD", "--", pathspec],
            cwd=cwd,
            capture_output=True,
            text=True,
        )


def _unstage_runtime_state(cwd: str) -> None:
    """Remove generated engine/serve state from the index after broad git add."""
    _unstage(cwd, _RUNTIME_STATE_PATHS)


def auto_commit(
    message: str,
    project_root: str = ".",
    paths: list[str] | None = None,
    *,
    exclude_pathspecs: Iterable[str] | None = None,
    verify_hooks: bool = True,
) -> tuple[bool, str]:
    """
    Faz git add + commit com mensagem padrao.
    Retorna (success, detail).

    Um pathspec que nao casa com nada entra no detalhe retornado. Engolir esse
    erro ja escondeu implementacao inteira ficando de fora do commit: o escopo
    apontava para diretorios que o projeto nao tem, cada `git add` falhava em
    silencio e o problema so aparecia num gate adiante, falando de outra coisa.
    """
    cwd = project_root

    # Stage arquivos
    unmatched: list[str] = []
    if paths:
        for p in paths:
            result = subprocess.run(
                ["git", "add", p], cwd=cwd, capture_output=True, text=True
            )
            if result.returncode != 0:
                unmatched.append(p)
    else:
        # Stage tudo; artefatos descartáveis são removidos abaixo.
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True)
    _unstage_runtime_state(cwd)
    if exclude_pathspecs:
        _unstage(cwd, exclude_pathspecs)

    aviso = (
        f" [pathspec sem correspondencia: {', '.join(unmatched)}]" if unmatched else ""
    )

    # Verificar se ha algo staged
    status = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        return True, "auto_commit: nada para commitar" + aviso

    # Commit
    result = subprocess.run(
        [
            *git_command_prefix(verify_hooks),
            "commit",
            *_commit_policy_flags(verify_hooks),
            "-m",
            message,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # Extrair hash curto
        hash_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        short_hash = hash_result.stdout.strip()
        return True, f"auto_commit: {short_hash} — {message}{aviso}"

    return False, f"auto_commit FAIL: {result.stderr.strip()[:200]}{aviso}"


_KNOWLEDGE_PATHS = (
    ".ft/process/",
    ".ft/manifest.yml",
    ".ft/project.yml",
    ".ft/project-readiness.yml",
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
        return True, False, "commit_knowledge: catálogo de templates sem mudanças"

    stage_paths: list[str] = []
    for path in _KNOWLEDGE_PATHS:
        if (Path(cwd) / path.rstrip("/")).exists():
            stage_paths.append(path)
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if tracked.returncode == 0:
            stage_paths.append(path)
    if not stage_paths:
        return True, False, "commit_knowledge: catálogo de templates sem mudanças"

    staged = subprocess.run(
        ["git", "add", "--", *stage_paths],
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
            f"chore: {label} — .ft/process/",
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
    """Commita o bundle de processo em `.ft/`, se houver mudanças.

    Chamado pelo engine antes de iniciar um run e ao final. O escopo é
    exatamente `_KNOWLEDGE_PATHS`: o bundle materializado, o manifest, o
    project.yml e o readiness. **Não** inclui `docs/`, e não deve incluir —
    documento canônico é escrito por node, e node commita o próprio delta.
    Varrer `docs/` aqui reintroduziria o commit que descreve a árvore em vez
    do trabalho, e arrastaria junto os artefatos descartáveis do ciclo.

    Este docstring já disse "commita docs/", e a mentira custou caro: ao
    investigar por que a reconciliação de um ciclo tinha sumido, ela apontou
    para o lugar errado. `test_commit_knowledge_cobre_apenas_o_bundle` existe
    para que a promessa e o código não voltem a divergir.
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
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    modified = result.stdout.strip().splitlines() if result.stdout.strip() else []

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    created = untracked.stdout.strip().splitlines() if untracked.stdout.strip() else []

    return modified + created
