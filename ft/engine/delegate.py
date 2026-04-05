"""
LLM Executor — interface para chamar Claude Code como executor de construcao.
O LLM so constroi. Nao decide nada sobre o processo.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DelegateResult:
    success: bool
    output: str
    files_created: list[str]
    files_modified: list[str]


def delegate_to_llm(
    task: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
    max_turns: int = 50,
) -> DelegateResult:
    """
    Chama o Claude Code CLI como subprocesso para executar uma tarefa de construcao.

    O LLM recebe um prompt restritivo: so pode escrever nos paths permitidos,
    nao pode editar ft_state.yml, nao pode tomar decisoes de processo.
    """
    paths_str = ", ".join(allowed_paths) if allowed_paths else "src/, tests/, project/docs/"

    prompt = f"""Voce e um executor de construcao. Sua unica tarefa:

{task}

REGRAS:
- Escreva APENAS nos paths permitidos: {paths_str}
- NAO edite ft_state.yml ou qualquer arquivo de estado do motor
- NAO tome decisoes sobre o processo (o motor decide)
- Quando terminar, diga DONE e liste os arquivos criados/modificados
- Se encontrar um problema que nao consegue resolver, diga BLOCKED e explique o motivo
"""

    # Chamar Claude Code CLI em modo nao-interativo
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--max-turns", str(max_turns),
            "-p", prompt,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 min max (projetos complexos)
    )

    output = result.stdout or ""

    # Detectar erro 403 do SymGateway e dar mensagem acionável
    if "403" in output and "not found in workspace" in output:
        import re
        m = re.search(r"folder_name='([^']+)'", output)
        folder = m.group(1) if m else "este projeto"
        raise RuntimeError(
            f"Gateway 403: projeto '{folder}' não está registrado no SymGateway.\n"
            f"  → Registre em https://symgateway.symlabs.ai com folder_name='{folder}'"
        )

    success = result.returncode == 0 and "BLOCKED" not in output

    # Extrair arquivos criados/modificados do git status
    git_result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    modified = git_result.stdout.strip().splitlines() if git_result.stdout.strip() else []

    git_untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    created = git_untracked.stdout.strip().splitlines() if git_untracked.stdout.strip() else []

    return DelegateResult(
        success=success,
        output=output,
        files_created=created,
        files_modified=modified,
    )


def delegate_with_feedback(
    original_task: str,
    feedback: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
) -> DelegateResult:
    """Re-delega com feedback especifico dos validadores."""
    retry_task = f"""TAREFA ORIGINAL:
{original_task}

RESULTADO DA VALIDACAO (FALHOU):
{feedback}

CORRIJA especificamente os itens que falharam.
Nao modifique o que ja esta funcionando."""

    return delegate_to_llm(
        task=retry_task,
        project_root=project_root,
        allowed_paths=allowed_paths,
    )
