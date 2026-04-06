"""
ft engine CLI — comandos do motor deterministico.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ft.engine.runner import StepRunner
from ft.integrations.symgateway import provision_environment


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


def engine_root() -> Path:
    """Raiz do repositório do engine (onde templates/ e kb/ vivem)."""
    return Path(__file__).resolve().parent.parent.parent


def copy_template(template_name: str, project_root: Path) -> Path:
    """Copia um template de processo para o projeto.

    Retorna o path do YAML copiado.
    """
    import shutil

    src_dir = engine_root() / "templates" / template_name
    if not src_dir.is_dir():
        available = [d.name for d in (engine_root() / "templates").iterdir() if d.is_dir()] if (engine_root() / "templates").is_dir() else []
        print(f"ERRO: template '{template_name}' não encontrado.")
        if available:
            print(f"  Templates disponíveis: {', '.join(available)}")
        sys.exit(1)

    # Encontrar o YAML no template
    yamls = list(src_dir.glob("*.yml"))
    if not yamls:
        print(f"ERRO: template '{template_name}' não contém nenhum arquivo .yml")
        sys.exit(1)

    dest_dir = project_root / "process"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / "FAST_TRACK_PROCESS.yml"
    shutil.copy(yamls[0], dest)
    print(f"  Template '{template_name}' copiado para process/FAST_TRACK_PROCESS.yml")
    return dest


def find_project_root() -> Path:
    """Encontra a raiz do projeto subindo ate achar process/."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "process").is_dir():
            return parent
    return current


def find_process_yaml(root: Path) -> Path | None:
    """Encontra o YAML do processo no diretório do projeto.

    Prioridade (projeto-primeiro):
      1. {root}/process/FAST_TRACK_PROCESS.yml (padrão V3)
      2. {root}/process/*.yml (qualquer YAML solto em process/)
      3. {root}/process/fast_track/FAST_TRACK_PROCESS_V2.yml (legacy)
    """
    # 1. Nome canônico em process/
    canonical = root / "process" / "FAST_TRACK_PROCESS.yml"
    if canonical.exists():
        return canonical

    # 2. Qualquer YAML em process/ (scan)
    process_dir = root / "process"
    if process_dir.is_dir():
        yamls = sorted(process_dir.glob("*.yml"))
        if len(yamls) == 1:
            return yamls[0]
        if len(yamls) > 1:
            # Preferir o que tem "FAST_TRACK" no nome
            for y in yamls:
                if "FAST_TRACK" in y.name.upper():
                    return y
            return yamls[0]

    # 3. Legacy: process/fast_track/ subdir
    for name in ("FAST_TRACK_PROCESS_V2.yml", "FAST_TRACK_PROCESS.yml"):
        p = root / "process" / "fast_track" / name
        if p.exists():
            import warnings
            warnings.warn(
                f"Processo encontrado em path legado: {p.relative_to(root)}. "
                f"Mova para process/FAST_TRACK_PROCESS.yml",
                DeprecationWarning, stacklevel=2,
            )
            return p

    return None


def _find_latest_state(root: Path) -> Path:
    """Encontra o state mais recente em runs/ ou fallback para project/state/ (legado)."""
    runs_dir = root / "runs"
    if runs_dir.is_dir():
        run_dirs = sorted(
            [d for d in runs_dir.iterdir() if d.is_dir() and d.name.isdigit()],
            reverse=True,
        )
        for rd in run_dirs:
            state = rd / "state" / "engine_state.yml"
            if state.exists():
                return state
    # Fallback legado
    legacy = root / "project" / "state" / "engine_state.yml"
    if legacy.exists():
        return legacy
    # Default para novo run
    return root / "runs" / "01" / "state" / "engine_state.yml"


def _next_run_dir(project_root: Path) -> Path:
    """Calcula e cria o próximo diretório de run em runs/."""
    runs_dir = project_root / "runs"
    runs_dir.mkdir(exist_ok=True)
    existing = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name.isdigit()]
    )
    next_num = int(existing[-1].name) + 1 if existing else 1
    run_dir = runs_dir / f"{next_num:02d}"
    run_dir.mkdir()
    return run_dir


def _ensure_runs_gitignore(project_root: Path) -> None:
    """Cria runs/.gitignore se não existir."""
    runs_dir = project_root / "runs"
    runs_dir.mkdir(exist_ok=True)
    gitignore = runs_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n!.gitignore\n")


def get_runner(process: str | None = None, llm_engine: str | None = None) -> StepRunner:
    root = find_project_root()
    state_path = _find_latest_state(root)

    if process:
        process_path = Path(process)
    else:
        process_path = find_process_yaml(root)
        if not process_path:
            print("ERRO: Nenhum YAML de processo encontrado em ./process/")
            print("  Use: ft init --template fast-track-v2")
            print("  Ou:  ft run . --template fast-track-v2")
            sys.exit(1)

    return StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=root,
        llm_engine=llm_engine,
    )


def cmd_init(args):
    # Copiar template se fornecido e processo não existe
    template = getattr(args, "template", None)
    root = find_project_root()
    if template:
        if not find_process_yaml(root):
            copy_template(template, root)

    # Criar estrutura V3: process/, docs/, runs/
    (root / "process").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    _ensure_runs_gitignore(root)

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


def cmd_validate(args):
    """Valida o YAML do processo."""
    from ft.engine.graph import load_graph
    from ft.engine.process_validator import validate_process, format_report
    from ft.engine.runner import VALIDATOR_REGISTRY

    root = find_project_root()

    if args.process:
        process_path = Path(args.process)
    else:
        process_path = find_process_yaml(root)
        if not process_path:
            print("ERRO: Nenhum YAML de processo encontrado em ./process/")
            sys.exit(1)

    print(f"\nValidando {process_path.relative_to(root) if process_path.is_relative_to(root) else process_path}...\n")

    try:
        graph = load_graph(process_path)
    except (ValueError, FileNotFoundError) as e:
        print(f"  \u274c Erro ao carregar YAML: {e}")
        sys.exit(1)

    report = validate_process(graph, VALIDATOR_REGISTRY)
    total = len(graph.nodes)
    print(format_report(report, total))

    sys.exit(0 if report.passed else 1)


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

Escreva o arquivo corrigido em: docs/hipotese.md
Ao final diga DONE."""

    result = delegate_to_llm(task=prompt, project_root=str(project_root),
                             allowed_paths=["docs/"], max_turns=5,
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

    # Criar estrutura V3: docs/, runs/<N>/
    (project_root / "docs").mkdir(parents=True, exist_ok=True)
    _ensure_runs_gitignore(project_root)
    run_dir = _next_run_dir(project_root)
    (run_dir / "state").mkdir(parents=True, exist_ok=True)

    # Resolver YAML do processo
    if args.process:
        process_path = Path(args.process)
    else:
        process_path = find_process_yaml(project_root)
        if not process_path:
            # Tentar copiar template se --template fornecido
            template = getattr(args, "template", None)
            if template:
                process_path = copy_template(template, project_root)
            else:
                print("ERRO: Nenhum YAML de processo encontrado em ./process/")
                print("  Use: ft run . --template fast-track-v2")
                sys.exit(1)

    state_path = run_dir / "state" / "engine_state.yml"
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
        src = Path(args.from_project) / "docs" / "plano_de_voo.md"
        if src.exists():
            dst_docs = project_root / "docs"
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
        dst_docs = project_root / "docs"
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
    init.add_argument("--template", "-t", help="Template de processo a copiar (ex: fast-track-v2)")

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

    # validate
    sub.add_parser("validate", help="Validar YAML do processo")

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
    ru.add_argument("--template", "-t",
                    help="Template de processo a copiar (ex: fast-track-v2)")

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
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "setup-env":
        cmd_setup_env(args)
    elif args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
