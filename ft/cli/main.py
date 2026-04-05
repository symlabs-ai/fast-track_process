"""
ft engine CLI — comandos do motor deterministico.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ft.engine.runner import StepRunner, provision_environment


def add_llm_engine_flags(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--claude", action="store_true", help="Usar Claude CLI para delegação LLM")
    group.add_argument("--codex", action="store_true", help="Usar Codex CLI para delegação LLM")


def resolve_llm_engine(args) -> str | None:
    if getattr(args, "codex", False):
        return "codex"
    if getattr(args, "claude", False):
        return "claude"
    return None


def find_project_root() -> Path:
    """Encontra a raiz do projeto subindo ate achar project/state/."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "project" / "state").is_dir():
            return parent
    return current


def find_process_yaml(root: Path) -> Path | None:
    """Encontra o YAML do processo.

    Prioridade:
      1. Processo de teste local (desenvolvimento do engine)
      2. FAST_TRACK_PROCESS_V2.yml no fast-track repo (via __file__) — fonte canônica
      3. YAML legado dentro do project_root (compatibilidade)
    """
    # 1. Processos de teste locais (desenvolvimento do engine)
    for name in ("test_process_v2.yml", "test_process.yml"):
        p = root / "process" / name
        if p.exists():
            return p

    # 2. Fonte canônica: fast-track repo derivado da localização deste arquivo
    ft_root = Path(__file__).resolve().parent.parent.parent
    canonical = ft_root / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
    if canonical.exists():
        return canonical

    # 3. Fallback legado: YAML dentro do project_root
    for name in ("FAST_TRACK_PROCESS_V2.yml", "FAST_TRACK_PROCESS.yml"):
        p = root / "process" / "fast_track" / name
        if p.exists():
            return p

    return None


def get_runner(process: str | None = None, llm_engine: str | None = None) -> StepRunner:
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
        llm_engine=llm_engine,
    )


def cmd_init(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args))
    # Limpar estado anterior se existir
    if runner.state_mgr.path.exists():
        runner.state_mgr.path.unlink()
        runner.state_mgr._state = None
    runner.init_state()
    sprints = runner.graph.get_sprints()
    if sprints:
        print(f"  Sprints: {', '.join(sprints)}")


def cmd_continue(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args))

    # Inicializar estado se nao existe
    state = runner.state_mgr.load()
    if state.current_node is None:
        runner.init_state()

    mode = "mvp" if args.mvp else ("sprint" if args.sprint else "step")
    runner.run(mode=mode)


def cmd_status(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args))
    runner.status(full=args.full)


def cmd_approve(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args))
    runner.approve()
    # Continuar automaticamente apos aprovacao
    if not args.no_continue:
        runner.run(mode="step")


def cmd_reject(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args))
    runner.reject(args.reason, retry=not args.no_retry)


def cmd_graph(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args))
    runner.status(full=True)


def cmd_setup_env(args):
    """Provisiona CLAUDE.md e .claude/settings.local.json a partir de uma API key."""
    project_root = Path(args.project) if args.project else find_project_root()
    provision_environment(project_root=project_root, key=args.key)
    print(f"  Projeto: {project_root}")
    print(f"  gateway_project: {project_root.name}")


def _normalize_hipotese(hipotese_path: Path, project_root: Path, llm_engine: str = "claude") -> None:
    """Verifica se hipotese.md está no formato correto; corrige via LLM se não estiver.

    Critérios obrigatórios (espelham os validators do node ft.mdd.01.hipotese):
      - pelo menos 10 linhas
      - seção ## Problema
      - seção ## Oportunidade
    """
    from ft.engine.validators.artifacts import file_exists, min_lines, has_sections
    from ft.engine.delegate import delegate_to_llm

    rel = str(hipotese_path.relative_to(project_root))

    ok_exists, _ = file_exists(rel, project_root=str(project_root))
    ok_lines, _ = min_lines(rel, 10, project_root=str(project_root))
    ok_sections, _ = has_sections(rel, ["Problema", "Oportunidade"], project_root=str(project_root))

    if ok_exists and ok_lines and ok_sections:
        print(f"  hipotese.md validada — formato OK")
        return

    missing = []
    if not ok_lines:
        missing.append("menos de 10 linhas")
    if not ok_sections:
        missing.append("seções obrigatórias ausentes (## Problema e/ou ## Oportunidade)")

    print(f"  hipotese.md fora do formato ({', '.join(missing)}) — corrigindo via LLM...")

    conteudo = hipotese_path.read_text()
    prompt = f"""O usuário forneceu uma hipótese de produto em formato livre.
Reformate-a no padrão obrigatório, preservando TODO o conteúdo original — não invente informações.

Conteúdo fornecido:
---
{conteudo}
---

Formato obrigatório:
- Arquivo markdown com pelo menos 10 linhas
- Seção ## Problema — descreva o problema que o produto resolve
- Seção ## Oportunidade — descreva a oportunidade de mercado/negócio
- Pode ter outras seções adicionais se o conteúdo original as tiver

Escreva o arquivo corrigido em: project/docs/hipotese.md
Ao final diga DONE."""

    result = delegate_to_llm(task=prompt, project_root=str(project_root),
                             allowed_paths=["project/docs/"], max_turns=5,
                             llm_engine=llm_engine)

    if not result.success:
        print(f"  AVISO: LLM não conseguiu corrigir hipotese.md — o processo vai solicitar reescrita")
        return

    # Re-validar após correção
    ok_lines2, _ = min_lines(rel, 10, project_root=str(project_root))
    ok_sections2, _ = has_sections(rel, ["Problema", "Oportunidade"], project_root=str(project_root))
    if ok_lines2 and ok_sections2:
        print(f"  hipotese.md corrigida e validada")
    else:
        print(f"  AVISO: hipotese.md ainda fora do formato após correção — o processo vai solicitar reescrita")


def cmd_run(args):
    """Bootstrap completo: cria projeto, provisiona ambiente, inicia e roda até MVP."""
    project_root = Path(args.project).resolve()

    # Criar estrutura mínima
    (project_root / "project" / "state").mkdir(parents=True, exist_ok=True)

    # Resolver YAML do processo
    if args.process:
        process_path = Path(args.process)
    else:
        # Default: FAST_TRACK_PROCESS_V2.yml relativo ao repo fast-track
        ft_root = Path(__file__).resolve().parent.parent.parent
        process_path = ft_root / "process" / "fast_track" / "FAST_TRACK_PROCESS_V2.yml"
        if not process_path.exists():
            print(f"ERRO: processo padrão não encontrado em {process_path}")
            sys.exit(1)

    state_path = project_root / "project" / "state" / "engine_state.yml"
    llm_engine = resolve_llm_engine(args)

    runner = StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=project_root,
        llm_engine=llm_engine,
    )

    # Provisionar ambiente antes do init
    if args.key:
        admin_key = getattr(args, "admin_key", None)
        provision_environment(project_root=project_root, key=args.key, admin_key=admin_key)
        print(f"  Ambiente provisionado com key fornecida")
    else:
        print(f"  Sem --key: usando ANTHROPIC_API_KEY do ambiente")

    import shutil

    # Copiar plano_de_voo do ciclo anterior se fornecido
    if args.from_project:
        src = Path(args.from_project) / "project" / "docs" / "plano_de_voo.md"
        if src.exists():
            dst_docs = project_root / "project" / "docs"
            dst_docs.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst_docs / "plano_de_voo.md")
            print(f"  plano_de_voo.md copiado de {args.from_project}")
        else:
            print(f"  AVISO: --from-project fornecido mas plano_de_voo.md não encontrado em {src}")

    # Copiar e normalizar hipótese inicial se fornecida (pre-seed de ft.mdd.01.hipotese)
    if args.hipotese:
        src = Path(args.hipotese)
        if not src.exists():
            print(f"ERRO: arquivo de hipótese não encontrado: {src}")
            sys.exit(1)
        dst_docs = project_root / "project" / "docs"
        dst_docs.mkdir(parents=True, exist_ok=True)
        dst = dst_docs / "hipotese.md"
        shutil.copy(src, dst)
        print(f"  hipotese.md copiado de {src}")
        _normalize_hipotese(dst, project_root, llm_engine=llm_engine or "claude")

    # Init + run MVP
    runner.init_state()
    runner.run(mode="mvp")


def main():
    parser = argparse.ArgumentParser(
        prog="ft",
        description="ft engine — motor deterministico de processos"
    )
    parser.add_argument("--process", "-p", help="Path do YAML de processo")
    sub = parser.add_subparsers(dest="command")

    # init
    init = sub.add_parser("init", help="Inicializar/resetar estado do processo")
    add_llm_engine_flags(init)

    # continue
    cont = sub.add_parser("continue", help="Avancar no processo")
    add_llm_engine_flags(cont)
    cont.add_argument("--step", action="store_true", default=True, help="Avancar 1 step (default)")
    cont.add_argument("--sprint", action="store_true", help="Avancar ate fim da sprint")
    cont.add_argument("--mvp", action="store_true", help="Avancar ate MVP (modo autonomo)")

    # status
    st = sub.add_parser("status", help="Estado atual")
    add_llm_engine_flags(st)
    st.add_argument("--full", "-f", action="store_true", help="Mostrar grafo e artefatos")

    # approve
    ap = sub.add_parser("approve", help="Aprovar artefato pendente")
    add_llm_engine_flags(ap)
    ap.add_argument("--no-continue", action="store_true", help="Nao continuar automaticamente")

    # reject
    rj = sub.add_parser("reject", help="Rejeitar artefato pendente")
    add_llm_engine_flags(rj)
    rj.add_argument("reason", help="Motivo da rejeicao")
    rj.add_argument("--no-retry", action="store_true", help="Nao reenviar ao LLM apos rejeicao")

    # graph
    graph = sub.add_parser("graph", help="Mostrar grafo com status")
    add_llm_engine_flags(graph)

    # setup-env
    se = sub.add_parser("setup-env", help="Provisionar CLAUDE.md e .claude/settings.local.json")
    se.add_argument("key", help="API key do SymGateway (sk-sym_...)")
    se.add_argument("--project", help="Diretório do projeto (default: CWD ou raiz detectada)")

    # run — bootstrap completo: cria projeto, provisiona, init, continue --mvp
    ru = sub.add_parser("run", help="Bootstrap completo de um novo projeto até MVP")
    add_llm_engine_flags(ru)
    ru.add_argument("project", help="Caminho do diretório do projeto (criado se não existir)")
    ru.add_argument("--key", help="API key do SymGateway (sk-sym_...)")
    ru.add_argument("--process", help="YAML do processo (default: FAST_TRACK_PROCESS_V2.yml)")
    ru.add_argument("--from-project", metavar="PATH",
                    help="Copiar plano_de_voo.md do ciclo anterior (para retomada de ciclo)")
    ru.add_argument("--admin-key", metavar="KEY",
                    help="API key admin do SymGateway para registrar o projeto (se --key não tiver role admin)")
    ru.add_argument("--hipotese", metavar="FILE",
                    help="Arquivo hipotese.md pré-escrito (pula ft.mdd.01.hipotese)")

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
    elif args.command == "setup-env":
        cmd_setup_env(args)
    elif args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
