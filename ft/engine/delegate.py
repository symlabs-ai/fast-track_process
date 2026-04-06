"""
LLM Executor — interface para chamar Claude Code ou Codex como executor de construcao.
O LLM so constroi. Nao decide nada sobre o processo.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DelegateResult:
    success: bool
    output: str
    files_created: list[str]
    files_modified: list[str]


def _build_executor_command(
    llm_engine: str,
    prompt: str,
    project_root: str,
    max_turns: int,
) -> list[str]:
    """Monta o comando do executor não-interativo com bypass habilitado."""
    engine = llm_engine.lower().strip()

    if engine == "claude":
        return [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--max-turns", str(max_turns),
            "-p", prompt,
        ]

    if engine == "codex":
        return [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
            "-C", project_root,
            prompt,
        ]

    raise ValueError(f"Executor LLM desconhecido: {llm_engine}")


def _write_log_preamble(log_path: str, llm_engine: str, cmd: list[str], prompt: str) -> None:
    """Escreve cabeçalho útil para inspeção de um step delegado."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# LLM Delegate Log\n")
        f.write(f"started_at: {started_at}\n")
        f.write(f"llm_engine: {llm_engine}\n")
        f.write(f"command: {' '.join(cmd)}\n")
        f.write("\n## Prompt\n\n")
        f.write(prompt)
        if not prompt.endswith("\n"):
            f.write("\n")
        f.write("\n## Output\n\n")


def _format_stream_line(llm_engine: str, line: str) -> str:
    """Formata linhas do stream para observação humana no terminal."""
    text = line.rstrip()
    if llm_engine != "codex":
        return text

    if not text.startswith("{"):
        return text

    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return text

    event_type = event.get("type", "unknown")
    if event_type == "thread.started":
        return f"event thread.started thread_id={event.get('thread_id')}"
    if event_type == "turn.started":
        return "event turn.started"
    if event_type == "turn.completed":
        usage = event.get("usage", {})
        return (
            "event turn.completed "
            f"input_tokens={usage.get('input_tokens', 0)} "
            f"output_tokens={usage.get('output_tokens', 0)}"
        )
    if event_type == "item.completed":
        item = event.get("item", {})
        item_type = item.get("type")
        if item_type == "agent_message":
            return f"agent_message {item.get('text', '').strip()}"
        return f"item.completed type={item_type}"
    if event_type == "error":
        return f"error {event.get('message', text)}"

    return f"event {event_type}"


def _extract_codex_output(raw_output: str) -> str:
    """Extrai a resposta final do agent a partir do stream JSONL do Codex."""
    messages: list[str] = []
    errors: list[str] = []

    for line in raw_output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                messages.append(item["text"])
        elif event.get("type") == "error":
            errors.append(json.dumps(event, ensure_ascii=False))

    if messages:
        return "\n\n".join(messages)
    if errors:
        return "\n".join(errors)
    return raw_output


def _stream_process_output(
    proc: subprocess.Popen,
    llm_engine: str,
    log_path: str | None = None,
    stream_prefix: str | None = None,
) -> str:
    """Consome stdout/stderr combinado do subprocesso, gravando em arquivo e espelhando no terminal."""
    chunks: list[str] = []
    stream = proc.stdout
    assert stream is not None

    log_file = None
    try:
        if log_path:
            log_file = Path(log_path).open("a", encoding="utf-8")

        for line in iter(stream.readline, ""):
            chunks.append(line)
            if log_file:
                log_file.write(line)
                log_file.flush()
            if stream_prefix:
                print(f"  {stream_prefix} {_format_stream_line(llm_engine, line)}")
    finally:
        if log_file:
            log_file.close()

    return "".join(chunks)


def delegate_to_llm(
    task: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
    max_turns: int = 50,
    llm_engine: str = "claude",
    log_path: str | None = None,
    stream_prefix: str | None = None,
) -> DelegateResult:
    """
    Chama o executor LLM configurado como subprocesso para executar uma tarefa de construcao.

    O LLM recebe um prompt restritivo: so pode escrever nos paths permitidos,
    nao pode editar ft_state.yml, nao pode tomar decisoes de processo.
    """
    paths_str = ", ".join(allowed_paths) if allowed_paths else "src/, tests/, docs/"

    prompt = f"""Voce e um executor de construcao. Sua unica tarefa:

{task}

REGRAS:
- Escreva APENAS nos paths permitidos: {paths_str}
- NAO edite ft_state.yml ou qualquer arquivo de estado do motor
- NAO tome decisoes sobre o processo (o motor decide)
- Quando terminar, diga DONE e liste os arquivos criados/modificados
- Se encontrar um problema que nao consegue resolver, diga BLOCKED e explique o motivo
"""

    cmd = _build_executor_command(llm_engine, prompt, project_root, max_turns)

    if log_path and llm_engine != "codex":
        _write_log_preamble(log_path, llm_engine, cmd, prompt)
    elif log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # Chamar executor em modo nao-interativo, com streaming para arquivo.
    proc = subprocess.Popen(
        cmd,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_holder: dict[str, str] = {"output": ""}
    reader = threading.Thread(
        target=lambda: output_holder.__setitem__(
            "output",
            _stream_process_output(
                proc,
                llm_engine=llm_engine,
                log_path=log_path,
                stream_prefix=stream_prefix,
            ),
        ),
        daemon=True,
    )
    reader.start()

    try:
        returncode = proc.wait(timeout=1800)  # 30 min max (projetos complexos)
    except subprocess.TimeoutExpired:
        proc.kill()
        reader.join(timeout=5)
        timeout_msg = "\n[TIMEOUT] Executor excedeu 1800 segundos.\n"
        output = output_holder["output"] + timeout_msg
        if log_path:
            with Path(log_path).open("a", encoding="utf-8") as f:
                f.write(timeout_msg)
        return DelegateResult(
            success=False,
            output=output,
            files_created=[],
            files_modified=[],
        )

    reader.join(timeout=5)

    raw_output = output_holder["output"]
    output = _extract_codex_output(raw_output) if llm_engine == "codex" else raw_output

    # Detectar erro 403 de gateway (integração opcional)
    try:
        from ft.integrations.symgateway import check_gateway_403
        gw_msg = check_gateway_403(output)
        if gw_msg:
            raise RuntimeError(gw_msg)
    except ImportError:
        pass

    success = returncode == 0 and "BLOCKED" not in output

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
    llm_engine: str = "claude",
    max_turns: int = 50,
    log_path: str | None = None,
    stream_prefix: str | None = None,
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
        llm_engine=llm_engine,
        max_turns=max_turns,
        log_path=log_path,
        stream_prefix=stream_prefix,
    )
