"""
ft engine CLI — comandos do motor deterministico.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ft.engine.runner import StepRunner


def find_project_root() -> Path:
    """Encontra a raiz do projeto subindo ate achar project/state/."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "project" / "state").is_dir():
            return parent
    return current


def find_process_yaml(root: Path) -> Path | None:
    """Encontra o YAML do processo (prioridade: v2 > v1 > fast_track)."""
    candidates = [
        root / "process" / "test_process_v2.yml",
        root / "process" / "test_process.yml",
        root / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml",
        root / "process" / "fast_track" / "FAST_TRACK_PROCESS.yml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def get_runner(process: str | None = None) -> StepRunner:
    root = find_project_root()
    state_path = root / "project" / "state" / "engine_state.yml"

    if process:
        process_path = Path(process)
    else:
        process_path = find_process_yaml(root)
        if not process_path:
            print("ERRO: Nenhum YAML de processo encontrado.")
            sys.exit(1)

    return StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=root,
    )


def cmd_init(args):
    runner = get_runner(args.process)
    # Limpar estado anterior se existir
    if runner.state_mgr.path.exists():
        runner.state_mgr.path.unlink()
        runner.state_mgr._state = None
    runner.init_state()
    sprints = runner.graph.get_sprints()
    if sprints:
        print(f"  Sprints: {', '.join(sprints)}")


def cmd_continue(args):
    runner = get_runner(args.process)

    # Inicializar estado se nao existe
    state = runner.state_mgr.load()
    if state.current_node is None:
        runner.init_state()

    mode = "mvp" if args.mvp else ("sprint" if args.sprint else "step")
    runner.run(mode=mode)


def cmd_status(args):
    runner = get_runner(args.process)
    runner.status(full=args.full)


def cmd_approve(args):
    runner = get_runner(args.process)
    runner.approve()
    # Continuar automaticamente apos aprovacao
    if not args.no_continue:
        runner.run(mode="step")


def cmd_reject(args):
    runner = get_runner(args.process)
    runner.reject(args.reason, retry=not args.no_retry)


def cmd_graph(args):
    runner = get_runner(args.process)
    runner.status(full=True)


def main():
    parser = argparse.ArgumentParser(
        prog="ft",
        description="ft engine — motor deterministico de processos"
    )
    parser.add_argument("--process", "-p", help="Path do YAML de processo")
    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Inicializar/resetar estado do processo")

    # continue
    cont = sub.add_parser("continue", help="Avancar no processo")
    cont.add_argument("--step", action="store_true", default=True, help="Avancar 1 step (default)")
    cont.add_argument("--sprint", action="store_true", help="Avancar ate fim da sprint")
    cont.add_argument("--mvp", action="store_true", help="Avancar ate MVP (modo autonomo)")

    # status
    st = sub.add_parser("status", help="Estado atual")
    st.add_argument("--full", "-f", action="store_true", help="Mostrar grafo e artefatos")

    # approve
    ap = sub.add_parser("approve", help="Aprovar artefato pendente")
    ap.add_argument("--no-continue", action="store_true", help="Nao continuar automaticamente")

    # reject
    rj = sub.add_parser("reject", help="Rejeitar artefato pendente")
    rj.add_argument("reason", help="Motivo da rejeicao")
    rj.add_argument("--no-retry", action="store_true", help="Nao reenviar ao LLM apos rejeicao")

    # graph
    sub.add_parser("graph", help="Mostrar grafo com status")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "continue":
        cmd_continue(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "reject":
        cmd_reject(args)
    elif args.command == "graph":
        cmd_graph(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
