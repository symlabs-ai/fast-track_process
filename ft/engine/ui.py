"""
UI helpers — cores e formatacao para output de terminal.
"""

from __future__ import annotations

import os
import re
import sys


# ---------------------------------------------------------------------------
# ANSI colors (desabilitado se NO_COLOR ou pipe)
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


_COLOR = _supports_color()


def _ansi(code: str) -> str:
    return f"\033[{code}m" if _COLOR else ""


# Cores
RESET = _ansi("0")
BOLD = _ansi("1")
DIM = _ansi("2")
ITALIC = _ansi("3")

RED = _ansi("31")
GREEN = _ansi("32")
YELLOW = _ansi("33")
BLUE = _ansi("34")
MAGENTA = _ansi("35")
CYAN = _ansi("36")
WHITE = _ansi("37")

BOLD_RED = _ansi("1;31")
BOLD_GREEN = _ansi("1;32")
BOLD_YELLOW = _ansi("1;33")
BOLD_BLUE = _ansi("1;34")
BOLD_CYAN = _ansi("1;36")
BOLD_WHITE = _ansi("1;37")


# ---------------------------------------------------------------------------
# Formatadores
# ---------------------------------------------------------------------------

def header(text: str) -> str:
    """Header grande para inicio de processo."""
    w = 54
    line = "━" * w
    return f"\n{BOLD_CYAN}{line}{RESET}\n{BOLD_WHITE}  {text}{RESET}\n{BOLD_CYAN}{line}{RESET}"


def step_card(step_num: int | str, step_total: int | str, title: str,
              node_id: str, node_type: str, executor: str,
              sprint: str | None = None,
              description: str | None = None) -> str:
    """Card visual para cada step."""
    w = 54
    top = f"{'┌' + '─' * (w - 2) + '┐'}"
    bot = f"{'└' + '─' * (w - 2) + '┘'}"
    progress = f"[{step_num}/{step_total}]"
    sprint_str = f" | {sprint}" if sprint else ""

    type_color = {
        "gate": YELLOW,
        "build": BLUE,
        "document": CYAN,
        "discovery": MAGENTA,
        "review": MAGENTA,
        "decision": YELLOW,
        "retro": DIM,
        "end": GREEN,
        "test_red": RED,
        "test_green": GREEN,
        "refactor": BLUE,
    }.get(node_type, WHITE)

    desc_line = f"  {DIM}{description}{RESET}\n" if description else ""

    return (
        f"\n{DIM}{top}{RESET}\n"
        f"  {BOLD_WHITE}{progress}{RESET} {BOLD}{title}{RESET}\n"
        f"{desc_line}"
        f"  {type_color}{node_type}{RESET} | {DIM}{executor}{RESET} | {DIM}{node_id}{sprint_str}{RESET}\n"
        f"{DIM}{bot}{RESET}"
    )


def success(text: str) -> str:
    return f"  {BOLD_GREEN}✓{RESET} {text}"


def fail(text: str) -> str:
    return f"  {BOLD_RED}✗{RESET} {RED}{text}{RESET}"


def warn(text: str) -> str:
    return f"  {BOLD_YELLOW}!{RESET} {YELLOW}{text}{RESET}"


def info(text: str) -> str:
    return f"  {CYAN}→{RESET} {text}"


def dim(text: str) -> str:
    return f"  {DIM}{text}{RESET}"


def gate_pass(next_id: str | None) -> str:
    target = next_id or "fim"
    return f"  {BOLD_GREEN}GATE PASS{RESET} → {target}"


def gate_block(reason: str) -> str:
    return f"  {BOLD_RED}GATE BLOCK{RESET}: {RED}{reason}{RESET}"


def step_pass(next_id: str | None, label: str = "PASS") -> str:
    target = next_id or "fim"
    return f"  {BOLD_GREEN}{label}{RESET} → {target}"


def step_block(reason: str) -> str:
    return f"  {BOLD_RED}BLOCK{RESET}: {RED}{reason}{RESET}"


def awaiting_approval(auto: bool = False) -> str:
    if auto:
        return f"  {BOLD_GREEN}AUTO-APROVADO{RESET} {DIM}(modo MVP){RESET}"
    return f"  {BOLD_YELLOW}AGUARDANDO APROVAÇÃO{RESET} — rode: {BOLD}ft approve{RESET}"


def human_gate_card(title: str, description: str | None = None,
                    url: str | None = None, reject_hint: str | None = None,
                    work_dir: str | None = None,
                    files: list[str] | None = None) -> str:
    """Card de checkpoint humano — foco em O QUE FAZER, não em artefatos internos."""
    w = 54
    sep = f"  {DIM}{'─' * (w - 2)}{RESET}"
    lines = [
        f"\n{BOLD_YELLOW}  ● {title}{RESET}",
        sep,
    ]
    if description:
        # Quebra em linhas de ~48 chars para caber no card
        words = description.split()
        current_line = ""
        for word in words:
            if len(current_line) + len(word) + 1 > 48:
                lines.append(f"  {current_line}")
                current_line = word
            else:
                current_line = (current_line + " " + word).strip()
        if current_line:
            lines.append(f"  {current_line}")
        lines.append(sep)
    if files:
        for f in files:
            lines.append(f"  {DIM}📄 {f}{RESET}")
        lines.append(sep)
    if url:
        lines.append(f"  {BOLD_WHITE}URL:{RESET} {BOLD_CYAN}{url}{RESET}")
        lines.append(sep)
    lines.append(f"  Aprovar:   {BOLD}ft approve{RESET}")
    if reject_hint:
        lines.append(f"  Rejeitar:  {BOLD}ft reject \"{RESET}{reject_hint}{BOLD}\"{RESET}")
    else:
        lines.append(f"  Rejeitar:  {BOLD}ft reject \"motivo\"{RESET}")
    return "\n".join(lines)


def exploration_start(title: str, count: int = 0) -> str:
    """Card exibido quando o processo entra em modo exploração."""
    w = 54
    sep = f"  {DIM}{'─' * (w - 2)}{RESET}"
    counter = f" ({count} exploração(ões) registrada(s))" if count else ""
    lines = [
        f"\n{BOLD_YELLOW}  ◈ {title}{RESET}{DIM}{counter}{RESET}",
        sep,
        f"  {DIM}Faça pedidos livres ao LLM. Tudo fica no worktree (descartável).{RESET}",
        sep,
        f"  Explorar:  {BOLD}ft explore \"seu pedido\"{RESET}",
        f"  Pular:     {BOLD}ft explore --skip{RESET}",
        f"  Encerrar:  {BOLD}ft explore --finish{RESET}",
    ]
    return "\n".join(lines)


def exploration_item(index: int, request: str) -> str:
    return f"  {BOLD_YELLOW}◈ [{index}]{RESET} {request}"


def fix_gate(message: str, feedback: str, goto: str) -> str:
    """Card exibido quando on_fail.human_gate pausa o ciclo aguardando ft fix."""
    w = 54
    sep = f"  {DIM}{'─' * (w - 2)}{RESET}"
    lines = [
        f"\n{BOLD_RED}  ✗ {message}{RESET}",
        sep,
    ]
    # Feedback do validator — primeiras 3 linhas não-vazias
    fb_lines = [l.strip() for l in feedback.splitlines() if l.strip()][:3]
    for l in fb_lines:
        lines.append(f"  {RED}{l}{RESET}")
    lines += [
        sep,
        f"  {DIM}Destino após correção: {goto}{RESET}",
        sep,
        f"  Para corrigir:  {BOLD}ft fix \"sua instrução\"{RESET}",
        f"  Para cancelar:  {BOLD}ft reject{RESET}",
    ]
    return "\n".join(lines)


def process_complete(steps_done: int | str, steps_total: int | str) -> str:
    w = 54
    line = "━" * w
    return (
        f"\n{BOLD_GREEN}{line}{RESET}\n"
        f"  {BOLD_GREEN}PROCESSO COMPLETO{RESET}\n"
        f"  Steps: {steps_done}/{steps_total}\n"
        f"{BOLD_GREEN}{line}{RESET}"
    )


def sprint_complete(sprint_name: str) -> str:
    return f"\n  {BOLD_YELLOW}Sprint {sprint_name} completa{RESET}"


def init_banner(title: str, first_node: str, first_title: str, total: int, process_file: str = "") -> str:
    w = 54
    line = "━" * w
    file_line = f"  {DIM}Processo: {process_file}{RESET}\n" if process_file else ""
    return (
        f"\n{BOLD_CYAN}{line}{RESET}\n"
        f"  {BOLD_WHITE}Processo inicializado{RESET}\n"
        f"{file_line}"
        f"  {BOLD_YELLOW}{title}{RESET}\n"
        f"  {DIM}Primeiro: {first_node} ({first_title}){RESET}\n"
        f"  {DIM}Total: {total} steps{RESET}\n"
        f"{BOLD_CYAN}{line}{RESET}"
    )


def retry(attempt: int, max_retries: int) -> str:
    return f"  {BOLD_YELLOW}RETRY{RESET} [{attempt}/{max_retries}]"


def validator_ok(detail: str) -> str:
    return f"    {GREEN}[ok]{RESET} {detail}"


def validator_fail(detail: str) -> str:
    return f"    {RED}[fail]{RESET} {detail}"


class Spinner:
    """Spinner animado para operações longas. Usa como context manager."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Processando"):
        self._message = message
        self._running = False
        self._thread = None

    def __enter__(self):
        import threading
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        # Limpar a linha do spinner
        sys.stdout.write(f"\r{' ' * (len(self._message) + 10)}\r")
        sys.stdout.flush()

    def _spin(self):
        import time
        i = 0
        while self._running:
            frame = self.FRAMES[i % len(self.FRAMES)]
            if _COLOR:
                sys.stdout.write(f"\r  {CYAN}{frame}{RESET} {DIM}{self._message}...{RESET}")
            else:
                sys.stdout.write(f"\r  {frame} {self._message}...")
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)

    def update(self, message: str):
        """Atualiza a mensagem do spinner."""
        self._message = message


def autofix_applied(description: str) -> str:
    return f"  {BOLD_GREEN}⚙ Autocorreção:{RESET} {description}"


def problem_explanation(
    what_happened: str,
    alternatives: list[str],
    node_id: str | None = None,
) -> str:
    """Mensagem amigável para erros que o engine não sabe autocorrigir."""
    w = 54
    line = "─" * w
    lines = [
        f"\n{BOLD_RED}{line}{RESET}",
        f"  {BOLD_RED}Problema encontrado no processo{RESET}",
        f"{BOLD_RED}{line}{RESET}",
        f"",
        f"  {BOLD_WHITE}O que aconteceu:{RESET}",
        f"    {what_happened}",
        f"",
        f"  {BOLD_WHITE}Alternativas:{RESET}",
    ]
    for i, alt in enumerate(alternatives, 1):
        lines.append(f"    {BOLD_YELLOW}{i}.{RESET} {alt}")
    lines.append(f"")
    lines.append(f"  {BOLD_WHITE}Para aplicar, use:{RESET}")
    lines.append(f"    {BOLD_CYAN}ft fix \"{RESET}aplique a alternativa 1{BOLD_CYAN}\"{RESET}")
    lines.append(f"")
    lines.append(f"  {DIM}Ou descreva sua própria solução:{RESET}")
    lines.append(f"    {BOLD_CYAN}ft fix \"{RESET}faça X e Y em vez disso{BOLD_CYAN}\"{RESET}")
    lines.append(f"")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Realce markdown do stream (ft log --follow --markdown)
# ---------------------------------------------------------------------------

# Prefixos de ferramentas de arquivo/busca emitidos por _format_stream_line.
_TOOL_PREFIXES = ("Read ", "Write ", "Edit ", "Glob ", "Grep ", "NotebookEdit")

# Markdown leve para linhas de prosa (o prompt do nó, que vem com ##, -, **, `).
_MD_HEADER = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")


def _md_inline(text: str) -> str:
    """Aplica ênfases inline: **negrito** e `código`."""
    text = _MD_BOLD.sub(lambda m: f"{BOLD}{m.group(1)}{RESET}", text)
    text = _MD_CODE.sub(lambda m: f"{CYAN}{m.group(1)}{RESET}", text)
    return text


def render_md(text: str) -> str:
    """Renderiza markdown leve de uma linha de prosa (header, bullet, negrito,
    código) com ANSI. Sem cor (pipe/NO_COLOR) devolve o texto CRU — não mexe na
    sintaxe, para não corromper capturas em arquivo."""
    if not _COLOR or not text:
        return text
    m = _MD_HEADER.match(text)
    if m:  # "## Titulo" → negrito branco (sem o #)
        return f"{BOLD_WHITE}{m.group(2)}{RESET}"
    b = _MD_BULLET.match(text)
    if b:  # "- item" / "* item" → bullet •
        return f"{b.group(1)}{CYAN}•{RESET} {_md_inline(b.group(2))}"
    return _md_inline(text)


def paint_stream_line(s: str) -> str:
    """Realça uma linha já formatada por `_format_stream_line`, separando por
    cor/ênfase: comandos bash, chamadas de ferramenta, resposta e raciocínio.

    Idempotente em relação a cores desabilitadas: se NO_COLOR/pipe, as
    constantes ANSI são vazias e a string volta inalterada.
    """
    if not s:
        return s
    # Comando bash: "$ <cmd>" — verde, cifrão em negrito.
    if s.startswith("$ "):
        return f"{BOLD_GREEN}${RESET} {GREEN}{s[2:]}{RESET}"
    # Raciocínio: "✻ <thinking>" — cinza itálico, recua para o fundo.
    if s.startswith("✻"):
        return f"{DIM}{ITALIC}{s}{RESET}"
    # Resposta/texto do assistente: "→ <texto>" — branco, é o modelo "falando".
    if s.startswith("→"):
        return f"{BOLD_WHITE}{s}{RESET}"
    # Resultado final do worker: "result: ..." — ciano em negrito.
    if s.startswith("result:"):
        return f"{BOLD_CYAN}{s}{RESET}"
    # Chamadas de ferramenta de arquivo/busca — azul.
    for kw in _TOOL_PREFIXES:
        if s.startswith(kw):
            return f"{BLUE}{s}{RESET}"
    # Ferramenta genérica "[Nome]" ou metadado "event ..." — apagado.
    if s.startswith("event ") or s.startswith("["):
        return f"{DIM}{s}{RESET}"
    # Prosa (o prompt do nó, texto solto): renderiza markdown leve.
    return render_md(s)
