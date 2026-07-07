"""
LLM Executor — interface para chamar Claude Code ou Codex como executor de construcao.
O LLM so constroi. Nao decide nada sobre o processo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# Padrões que indicam rate limit / quota esgotada no output do LLM
_RATE_LIMIT_PATTERNS = re.compile(
    r"rate.limit|429|quota.exceeded|resource.?exhausted|"
    r"too.many.requests|overloaded|try.again.in|"
    r"RESOURCE_EXHAUSTED|rateLimitExceeded",
    re.IGNORECASE,
)
# Cronograma default de backoff: ~1h40 de espera acumulada (fora o tempo de
# execução de cada tentativa) — dimensionado para atravessar indisponibilidades
# longas da API, não só picos momentâneos.
# Override por env: FT_RATE_LIMIT_BACKOFF="60,120,240" (segundos, CSV).
_RATE_LIMIT_WAIT = [60, 120, 240, 480, 900, 1800, 1800, 1800]

# Acima deste tamanho o prompt não cabe com folga num argumento de execve
# (MAX_ARG_STRLEN ≈ 128 KiB no Linux) e vai via stdin.
_MAX_ARGV_PROMPT_BYTES = 100_000

DEFAULT_OPENCODE_MODEL = "pgx/zai-org_glm-4.7-flash"


def _feed_stdin(proc: subprocess.Popen, prompt: str) -> None:
    """Escreve o prompt no stdin do executor e fecha o pipe (EOF sinaliza fim)."""
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # executor morreu antes de ler o prompt — o wait() reporta o erro


def _rate_limit_backoff_schedule() -> list[int]:
    """Cronograma de backoff para rate limit, configurável via FT_RATE_LIMIT_BACKOFF."""
    raw = os.environ.get("FT_RATE_LIMIT_BACKOFF", "").strip()
    if raw:
        try:
            schedule = [int(x) for x in raw.split(",") if x.strip()]
            if schedule:
                return schedule
        except ValueError:
            print(f"  ⚠️  FT_RATE_LIMIT_BACKOFF inválido ({raw!r}) — usando cronograma default.")
    return list(_RATE_LIMIT_WAIT)


@dataclass
class DelegateResult:
    success: bool
    output: str
    files_created: list[str]
    files_modified: list[str]
    # True quando a falha foi rate limit da API que persistiu após todo o
    # backoff — o runner NÃO deve tratar como falha de conteúdo (não consome
    # auto-fix; pausa o run para retomada via ft continue).
    rate_limited: bool = False


def _build_executor_command(
    llm_engine: str,
    prompt: str,
    project_root: str,
    max_turns: int,
    model: str | None = None,
) -> list[str]:
    """Monta o comando do executor não-interativo com bypass habilitado."""
    engine = llm_engine.lower().strip()

    if engine == "claude":
        cmd = [
            "claude",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--max-turns", str(max_turns),
        ]
        if model:
            cmd += ["--model", model]
        cmd += ["-p", prompt]
        return cmd

    if engine == "codex":
        cmd = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
            "-C", project_root,
        ]
        if model:
            cmd += ["-m", model]
        cmd.append(prompt)
        return cmd

    if engine == "gemini":
        cmd = ["gemini", "--yolo"]
        if model:
            cmd += ["-m", model]
        cmd += ["-p", prompt]
        return cmd

    if engine == "opencode":
        return [
            "opencode",
            "run",
            "--dir", project_root,
            "-m", model or DEFAULT_OPENCODE_MODEL,
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
    if llm_engine == "claude":
        if not text.startswith("{"):
            return text
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return text
        etype = event.get("type", "")
        if etype == "stream_event":
            # Chunks parciais (--include-partial-messages): consumidos por quem
            # agrega (ft log); no stream linha-a-linha sao ruido.
            return ""
        if etype == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    return _describe_tool_call(block.get("name", ""), block.get("input", {}))
                if btype == "text":
                    return f"→ {block.get('text', '').strip()[:120]}"
                if btype == "thinking":
                    t = (block.get("thinking") or "").strip()
                    if t:
                        return f"✻ {t[:120]}"
        if etype == "result":
            return f"result: {event.get('result', '')[:80]}"
        return f"event {etype}"
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


def _final_protocol_token(output: str) -> str | None:
    """Último token de protocolo (DONE/BLOCKED) emitido como marcador.

    Só conta o token no início de linha (admitindo decoração markdown leve),
    como o protocolo pede — citar a palavra em prosa NÃO conta. Lição vibeos
    cycle-02: um plano de voo que discutia nodes BLOCKED em prosa era tratado
    como falha pelo antigo `"BLOCKED" in output`. O ÚLTIMO token vence: um
    worker que menciona um bloqueio e encerra com DONE está reportando sucesso.
    """
    token = None
    for m in re.finditer(r"^[\s*_`#>\-]*(DONE|BLOCKED)\b", output, re.MULTILINE):
        token = m.group(1)
    return token


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


def _extract_claude_json_output(raw_output: str) -> str:
    """Extrai texto final do stream-json do Claude CLI (uma linha JSON por evento)."""
    # Primeiro tenta pegar o campo result do evento final
    for line in reversed(raw_output.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event.get("result", "")
            if result:
                return result

    # Fallback: concatenar textos de mensagens assistant
    parts: list[str] = []
    for line in raw_output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
    if parts:
        return "\n\n".join(parts)
    return raw_output


def _describe_tool_call(name: str, input_data: dict) -> str:
    """Formata uma tool call do Claude em texto curto para display."""
    name_lower = name.lower()
    if name_lower in ("read", "readfile"):
        path = input_data.get("file_path") or input_data.get("path", "")
        return f"Read {path}"
    if name_lower in ("write", "writefile"):
        path = input_data.get("file_path") or input_data.get("path", "")
        return f"Write {path}"
    if name_lower == "edit":
        path = input_data.get("file_path") or input_data.get("path", "")
        return f"Edit {path}"
    if name_lower == "bash":
        cmd = (input_data.get("command") or "")[:60].replace("\n", " ")
        return f"$ {cmd}"
    if name_lower == "glob":
        pat = input_data.get("pattern", "")
        return f"Glob {pat}"
    if name_lower == "grep":
        pat = input_data.get("pattern", "")
        return f"Grep {pat}"
    if name_lower == "notebookedit":
        return "NotebookEdit"
    # Generic
    return f"[{name}]"


def _live_status(llm_engine: str, line: str, ctx: dict) -> str | None:
    """Extrai texto curto para a linha de status ao vivo. Retorna None para linhas sem interesse."""
    text = line.rstrip()
    if llm_engine == "codex":
        if not text.startswith("{"):
            return None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return None
        etype = event.get("type", "")
        if etype == "turn.started":
            ctx["turn"] = ctx.get("turn", 0) + 1
            return f"turn {ctx['turn']}"
        if etype == "item.completed":
            item = event.get("item", {})
            itype = item.get("type", "")
            if itype == "command_execution":
                cmd = (item.get("command") or "")[:60].replace("\n", " ")
                return f"$ {cmd}"
            if itype == "agent_message":
                msg = (item.get("text") or "").strip().replace("\n", " ")[:60]
                return f"→ {msg}" if msg else None
            if itype == "tool_call":
                name = item.get("name") or item.get("tool", "")
                return f"tool {name}"
        if etype == "turn.completed":
            usage = event.get("usage", {})
            tok = usage.get("output_tokens", 0)
            ctx["tokens"] = ctx.get("tokens", 0) + tok
            return f"turn {ctx.get('turn', '?')} done · {ctx['tokens']:,} out tok"
        return None
    elif llm_engine == "claude":
        if not text.startswith("{"):
            return text[:80] if text else None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return text[:80] if text else None
        etype = event.get("type", "")
        if etype == "assistant":
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    desc = _describe_tool_call(block.get("name", ""), block.get("input", {}))
                    ctx["last_tool"] = desc
                    return desc
                if btype == "text":
                    snippet = block.get("text", "").strip().replace("\n", " ")[:80]
                    if snippet:
                        return f"→ {snippet}"
        if etype == "result":
            tok = event.get("usage", {}).get("output_tokens", 0) or 0
            ctx["tokens"] = ctx.get("tokens", 0) + tok
            if tok:
                return f"done · {ctx['tokens']:,} out tok"
        return None
    else:
        # Outros engines: plain text
        if text and not text.startswith("["):
            return text[:80]
        return None


_STALL_RECONCILE_SECS = 120.0


def _claude_session_transcript(cwd: str, session_id: str) -> "Path | None":
    """Path do transcript da sessão em ~/.claude/projects/<slug>/<sid>.jsonl.

    Slug do Claude Code: path absoluto do cwd com [/_.] -> "-".
    """
    import re as _re
    if not cwd or not session_id:
        return None
    slug = _re.sub(r"[/_.]", "-", str(Path(cwd).resolve()))
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


def _transcript_terminal_output(transcript: "Path | None") -> str | None:
    """Se a sessão já terminou segundo o transcript, retorna o texto final; senão None.

    Terminal = último assistant com bloco text, sem tool_use pendente e
    stop_reason end_turn (padrão de reconciliação do sym_doctor).
    """
    if transcript is None or not transcript.exists():
        return None
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines[-300:]):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = entry.get("type", "")
        if etype == "result":
            return str(entry.get("result", "")) or None
        if etype != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
            return None  # ainda no meio de tools — não é terminal
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts and msg.get("stop_reason") in ("end_turn", "stop_sequence", None):
            return "\n".join(t for t in texts if t).strip() or None
        return None
    return None


def _stream_process_output(
    proc: subprocess.Popen,
    llm_engine: str,
    log_path: str | None = None,
    stream_prefix: str | None = None,
) -> str:
    """Consome stdout/stderr combinado do subprocesso, gravando em arquivo e espelhando no terminal."""
    import shutil as _shutil
    import threading
    import select
    chunks: list[str] = []
    stream = proc.stdout
    assert stream is not None

    ctx: dict = {}
    term_width = _shutil.get_terminal_size((80, 20)).columns - 4
    last_status: list[str] = ["aguardando LLM..."]
    printed_status: list[str] = [""]  # último status que gerou uma nova linha
    start_time = time.time()

    def _print_inline(status: str, elapsed: int) -> None:
        """Evento ao vivo: imprime linha permanente no scrollback."""
        ts = time.strftime("%H:%M:%S")
        msg = f"  ⟳ [{ts}] {status} ({elapsed}s)"
        # Limpa a linha do heartbeat (que estava em \r), imprime e avança
        print(f"\r{msg:<{term_width}}", flush=True)
        printed_status[0] = status

    def _print_heartbeat():
        """Heartbeat a cada 10s: atualiza timer in-place na linha atual."""
        while proc.poll() is None:
            elapsed = int(time.time() - start_time)
            status = last_status[0]
            if log_path:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    # Ler apenas o que vem após "## Output" — ignora o prompt
                    output_marker = "## Output"
                    idx = content.rfind(output_marker)
                    if idx != -1:
                        output_section = content[idx + len(output_marker):]
                    else:
                        output_section = content[-1024:]
                    def _useful(l: str) -> bool:
                        s = l.strip()
                        if not s or len(s) < 8:
                            return False
                        if s.startswith("#") or s.startswith("---") or s.startswith("==="):
                            return False
                        if s.startswith("```") or s in ("DONE", "BLOCKED"):
                            return False
                        if s.startswith("{"):  # raw JSON line — skip
                            return False
                        return True
                    lines = [l.strip() for l in output_section.splitlines() if _useful(l)]
                    if lines:
                        status = lines[-1][:120]
                except Exception:
                    pass
            ts = time.strftime("%H:%M:%S")
            msg = f"  ⟳ [{ts}] {status} ({elapsed}s)"
            # Atualiza in-place: só sobrescreve a linha corrente sem avançar
            print(f"\r{msg:<{term_width}}", end="", flush=True)
            time.sleep(10)

    log_file = None
    heartbeat = None
    try:
        if log_path:
            log_file = Path(log_path).open("a", encoding="utf-8")

        if not stream_prefix:
            heartbeat = threading.Thread(target=_print_heartbeat, daemon=True)
            heartbeat.start()

        import queue as _queue
        line_q: "_queue.Queue[str | None]" = _queue.Queue()

        def _pump() -> None:
            try:
                for _l in iter(stream.readline, ""):
                    line_q.put(_l)
            finally:
                line_q.put(None)

        pump = threading.Thread(target=_pump, daemon=True)
        pump.start()

        session_meta: dict = {"sid": None, "cwd": None, "saw_result": False}
        last_data = time.time()

        def _reconcile_from_transcript(reason: str) -> str | None:
            """Tenta recuperar o desfecho no transcript da sessão do Claude."""
            if llm_engine != "claude" or session_meta["saw_result"]:
                return None
            tp = _claude_session_transcript(session_meta["cwd"] or "", session_meta["sid"] or "")
            final = _transcript_terminal_output(tp)
            if final is None:
                return None
            synth = json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": final}]},
                "ft_reconciled_from": str(tp),
            })
            if log_file:
                log_file.write(f"# ft: reconciliado via transcript ({reason})\n{synth}\n")
                log_file.flush()
            return synth + "\n"

        while True:
            try:
                line = line_q.get(timeout=1.0)
            except _queue.Empty:
                if (time.time() - last_data) >= _STALL_RECONCILE_SECS and proc.poll() is None:
                    synth = _reconcile_from_transcript("pipe sem dados, sessão concluída")
                    if synth is not None:
                        chunks.append(synth)
                        proc.kill()
                        break
                    last_data = time.time()  # não re-checar a cada 1s
                continue
            if line is None:
                # EOF: se o pipe morreu sem evento result, tentar o transcript
                if proc.poll() is None:
                    proc.wait(timeout=30)
                synth = _reconcile_from_transcript("EOF sem result")
                if synth is not None:
                    chunks.append(synth)
                break
            last_data = time.time()
            _stripped_probe = line.strip()
            if _stripped_probe.startswith("{"):
                try:
                    _ev = json.loads(_stripped_probe)
                    _et = _ev.get("type", "")
                    if _et == "system" and _ev.get("subtype") == "init":
                        session_meta["sid"] = _ev.get("session_id")
                        session_meta["cwd"] = _ev.get("cwd")
                    elif _et == "result":
                        session_meta["saw_result"] = True
                except json.JSONDecodeError:
                    pass
            chunks.append(line)
            if log_file:
                log_file.write(line)
                # Para engines JSON (claude stream-json, codex --json),
                # também escreve linha legível logo após o JSON bruto
                if llm_engine in ("claude", "codex"):
                    stripped = line.strip()
                    if stripped.startswith("{"):
                        try:
                            event = json.loads(stripped)
                            etype = event.get("type", "")
                            decoded: str | None = None
                            if llm_engine == "claude":
                                if etype == "assistant":
                                    msg = event.get("message", {})
                                    for block in msg.get("content", []):
                                        if not isinstance(block, dict):
                                            continue
                                        btype = block.get("type")
                                        if btype == "tool_use":
                                            decoded = _describe_tool_call(
                                                block.get("name", ""), block.get("input", {})
                                            )
                                            break
                                        if btype == "text":
                                            t = block.get("text", "").strip().replace("\n", " ")
                                            if t:
                                                decoded = f"→ {t[:120]}"
                                            break
                                elif etype == "result":
                                    tok = event.get("usage", {}).get("output_tokens", 0) or 0
                                    if tok:
                                        decoded = f"done · {tok:,} output tokens"
                            else:  # codex
                                if etype == "item.completed":
                                    item = event.get("item", {})
                                    itype = item.get("type", "")
                                    if itype == "command_execution":
                                        cmd_text = (item.get("command") or "")[:80].replace("\n", " ")
                                        decoded = f"$ {cmd_text}"
                                    elif itype == "agent_message":
                                        msg_text = (item.get("text") or "").strip().replace("\n", " ")[:120]
                                        if msg_text:
                                            decoded = f"→ {msg_text}"
                                    elif itype == "tool_call":
                                        decoded = f"tool {item.get('name') or item.get('tool', '')}"
                                elif etype == "turn.completed":
                                    usage = event.get("usage", {})
                                    tok = usage.get("output_tokens", 0)
                                    if tok:
                                        decoded = f"done · {tok:,} output tokens"
                            if decoded:
                                log_file.write(f"{decoded}\n")
                        except (json.JSONDecodeError, Exception):
                            pass
                log_file.flush()
            if stream_prefix:
                print(f"  {stream_prefix} {_format_stream_line(llm_engine, line)}")
            else:
                # Atualiza last_status com qualquer linha não-vazia do LLM
                stripped = line.strip()
                if stripped and not stripped.startswith("{"):
                    last_status[0] = stripped[:120]
                status = _live_status(llm_engine, line, ctx)
                if status:
                    status = status[:120]
                    last_status[0] = status
                    elapsed = int(time.time() - start_time)
                    # Inline: sempre nova linha — cada ação fica visível no scrollback
                    if status != printed_status[0]:
                        _print_inline(status, elapsed)
    finally:
        if log_file:
            log_file.close()
        if not stream_prefix:
            # Limpa a linha de status ao terminar
            print(f"\r{' ' * (term_width)}\r", end="", flush=True)

    return "".join(chunks)


def delegate_to_llm(
    task: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
    max_turns: int = 50,
    llm_engine: str = "claude",
    llm_model: str | None = None,
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
- DIRETORIO DE TRABALHO: {project_root} — todo o seu trabalho acontece DENTRO dele,
  com paths RELATIVOS. NUNCA leia ou escreva fora dele (nem em outros checkouts do
  mesmo projeto), exceto paths absolutos explicitamente listados abaixo. Se algum
  documento citar um caminho absoluto fora do diretorio de trabalho, IGNORE o caminho
  e use o equivalente relativo local.
- Escreva APENAS nos paths permitidos: {paths_str}
- NAO edite ft_state.yml ou qualquer arquivo de estado do motor
- NAO tome decisoes sobre o processo (o motor decide)
- Quando terminar, diga DONE e liste os arquivos criados/modificados
- Se encontrar um problema que nao consegue resolver, diga BLOCKED e explique o motivo
- ANTES do DONE, emita um bloco NODE_SUMMARY (max 10 linhas) neste formato:
NODE_SUMMARY:
- fiz: <o que foi feito, 1-2 linhas>
- decisoes: <decisoes tomadas e porque, se houver>
- verificado: <o que voce RODOU e confirmou funcionando>
- assumido: <o que voce assumiu SEM testar, se houver>
- armadilhas: <pegadinhas que o proximo node precisa saber, se houver>
"""

    cmd = _build_executor_command(llm_engine, prompt, project_root, max_turns, model=llm_model)

    # Linux limita cada argumento de execve a ~128 KiB (MAX_ARG_STRLEN).
    # Prompts hyper-mode estouram isso ([Errno 7] Argument list too long) —
    # acima do limiar, o prompt sai do argv e vai via stdin (o Claude CLI lê
    # o prompt de stdin quando -p vem sem argumento).
    stdin_prompt: str | None = None
    if (
        llm_engine.lower().strip() == "claude"
        and cmd
        and cmd[-1] == prompt
        and len(prompt.encode("utf-8")) > _MAX_ARGV_PROMPT_BYTES
    ):
        cmd = cmd[:-1]  # mantém o -p final; prompt segue via stdin
        stdin_prompt = prompt
        print(f"  ⚠️  Prompt grande ({len(prompt) // 1024} KiB) — enviando via stdin.")

    if log_path and llm_engine != "codex":
        _write_log_preamble(log_path, llm_engine, cmd, prompt)
    elif log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # Chamar executor em modo nao-interativo, com streaming para arquivo.
    # PATH completo: o template v3 tem frontend Node (npm/vite) — a poda antiga
    # de nvm/node quebrava os nodes de frontend (worker sem npm reporta BLOCKED).
    import os as _os
    _env = dict(_os.environ)

    proc = subprocess.Popen(
        cmd,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_prompt is not None else None,
        text=True,
        bufsize=1,
        env=_env,
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

    # Alimentar stdin só depois do reader ativo: o reader drena o stdout do
    # filho enquanto escrevemos, evitando deadlock de pipes cheios.
    if stdin_prompt is not None:
        _feed_stdin(proc, stdin_prompt)

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

    def _extract_output(raw: str, engine: str) -> str:
        if engine == "codex":
            return _extract_codex_output(raw)
        if engine == "claude":
            return _extract_claude_json_output(raw)
        return raw

    raw_output = output_holder["output"]
    output = _extract_output(raw_output, llm_engine)

    # Detectar rate limit e fazer retry com backoff exponencial
    if _RATE_LIMIT_PATTERNS.search(output):
        _backoff_schedule = _rate_limit_backoff_schedule()
        for attempt, wait in enumerate(_backoff_schedule, start=1):
            print(f"\n  ⚠️  Rate limit detectado ({llm_engine}). "
                  f"Aguardando {wait}s antes da tentativa {attempt}/{len(_backoff_schedule)}…")
            time.sleep(wait)
            proc2 = subprocess.Popen(
                cmd,
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if stdin_prompt is not None else None,
                text=True,
                bufsize=1,
            )
            holder2: dict[str, str] = {"output": ""}
            t2 = threading.Thread(
                target=lambda: holder2.__setitem__(
                    "output",
                    _stream_process_output(
                        proc2,
                        llm_engine=llm_engine,
                        log_path=log_path,
                        stream_prefix=stream_prefix,
                    ),
                ),
                daemon=True,
            )
            t2.start()
            if stdin_prompt is not None:
                _feed_stdin(proc2, stdin_prompt)
            try:
                rc2 = proc2.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                proc2.kill()
                t2.join(timeout=5)
                break
            t2.join(timeout=5)
            raw2 = holder2["output"]
            out2 = _extract_output(raw2, llm_engine)
            if not _RATE_LIMIT_PATTERNS.search(out2):
                output = out2
                returncode = rc2
                break
            output = out2  # última tentativa falhou também

    token = _final_protocol_token(output)
    success = returncode == 0 and token != "BLOCKED"
    rate_limited = (not success) and bool(_RATE_LIMIT_PATTERNS.search(output))

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
        rate_limited=rate_limited,
    )


def delegate_with_feedback(
    original_task: str,
    feedback: str,
    project_root: str = ".",
    allowed_paths: list[str] | None = None,
    llm_engine: str = "claude",
    llm_model: str | None = None,
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
        llm_model=llm_model,
        max_turns=max_turns,
        log_path=log_path,
        stream_prefix=stream_prefix,
    )
