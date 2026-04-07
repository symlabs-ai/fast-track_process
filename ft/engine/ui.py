"""
UI helpers — cores e formatacao para output de terminal.
"""

from __future__ import annotations

import os
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
              sprint: str | None = None) -> str:
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

    return (
        f"\n{DIM}{top}{RESET}\n"
        f"  {BOLD_WHITE}{progress}{RESET} {BOLD}{title}{RESET}\n"
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


def awaiting_approval() -> str:
    return f"  {BOLD_YELLOW}AGUARDANDO APROVAÇÃO{RESET} — rode: {BOLD}ft approve{RESET}"


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


def init_banner(title: str, first_node: str, first_title: str, total: int) -> str:
    w = 54
    line = "━" * w
    return (
        f"\n{BOLD_CYAN}{line}{RESET}\n"
        f"  {BOLD_WHITE}Processo inicializado{RESET}\n"
        f"  {title}\n"
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
