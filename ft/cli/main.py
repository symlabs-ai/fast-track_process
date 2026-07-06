"""
ft engine CLI — comandos do motor deterministico.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ft.engine import paths
from ft.engine.runner import StepRunner
from ft.integrations.symgateway import provision_environment


def add_llm_engine_flags(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--claude", nargs="?", const=True, metavar="MODEL",
                       help="Usar Claude CLI (opcional: modelo, ex: --claude opus)")
    group.add_argument("--codex", nargs="?", const=True, metavar="MODEL",
                       help="Usar Codex CLI (opcional: modelo, ex: --codex gpt-5.3)")
    group.add_argument("--gemini", nargs="?", const=True, metavar="MODEL",
                       help="Usar Gemini CLI (opcional: modelo, ex: --gemini gemini-2.5-pro)")


def resolve_bypass_human_gates(args) -> bool:
    """Human gates so sao pulados com o flag EXPLICITO --bypass-human-gates.

    --auto NAO implica bypass (PV-9 vibeos, 2026-07-06): modo autonomo avanca
    sozinho entre nodes LLM/validators, mas PARA em human_gate aguardando
    ft approve / ft reject.
    """
    return bool(getattr(args, "bypass_human_gates", False))


def resolve_llm_engine(args) -> str | None:
    if getattr(args, "codex", None) is not None:
        return "codex"
    if getattr(args, "claude", None) is not None:
        return "claude"
    if getattr(args, "gemini", None) is not None:
        return "gemini"
    return None


def resolve_llm_model(args) -> str | None:
    """Extrai o modelo passado junto à flag de engine (ex: --codex gpt-5.3)."""
    for attr in ("claude", "codex", "gemini"):
        val = getattr(args, attr, None)
        if val is not None and val is not True:
            return str(val)
    return None


def engine_root() -> Path:
    """Raiz do repositório do engine (onde templates/ e kb/ vivem)."""
    return Path(__file__).resolve().parent.parent.parent


def _guard_engine_repo(root: Path) -> None:
    """Impede usar o repositório do engine/template como projeto.

    Override para desenvolvimento do próprio engine: FT_ALLOW_ENGINE_REPO=1.
    """
    if os.environ.get("FT_ALLOW_ENGINE_REPO"):
        return
    if root.resolve() == engine_root().resolve():
        print("ERRO: este é o repositório do ft engine/template — não pode ser usado como projeto.")
        print("  Crie um projeto novo: ft init <nome> --template fast-track-v3")
        print("  Ou rode em outro diretório: ft run <path-do-projeto>")
        print("  (override para desenvolvimento do engine: FT_ALLOW_ENGINE_REPO=1)")
        sys.exit(1)


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

    dest = dest_dir / "process.yml"
    # Compat: se já existe FAST_TRACK_PROCESS.yml, não sobrescrever
    legacy_dest = dest_dir / "FAST_TRACK_PROCESS.yml"
    if legacy_dest.exists():
        dest = legacy_dest
    shutil.copy(yamls[0], dest)
    print(f"  Template '{template_name}' copiado para process/{dest.name}")

    # Copiar subdirs do template (docs/, src/, scripts/)
    for subdir in ("docs", "src", "scripts"):
        template_sub = src_dir / subdir
        if template_sub.is_dir():
            # scripts/ vai para process/scripts/
            dest_sub = (project_root / "process" / "scripts") if subdir == "scripts" else (project_root / subdir)
            dest_sub.mkdir(parents=True, exist_ok=True)
            for f in template_sub.iterdir():
                if f.is_file():
                    dest_f = dest_sub / f.name
                    if not dest_f.exists():
                        shutil.copy2(f, dest_f)  # copy2 preserva permissões (executable)

    # Copiar environment.yml para process/
    env_yml = src_dir / "environment.yml"
    if env_yml.exists():
        dest_env = project_root / "process" / "environment.yml"
        if not dest_env.exists():
            shutil.copy(env_yml, dest_env)

    return dest


def _copy_agents_md(project_root: Path) -> None:
    """Copia o playbook AGENTS.md do engine para a raiz do projeto (não sobrescreve)."""
    import shutil

    src = engine_root() / "AGENTS.md"
    dst = project_root / "AGENTS.md"
    if src.exists() and not dst.exists():
        shutil.copy(src, dst)
        print("  AGENTS.md (playbook do condutor) copiado para o projeto")


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
      1. {root}/process/FAST_TRACK_PROCESS.yml (padrão V3) — só se bater com process_id do state
      2. {root}/process/*.yml casando com process_id do engine_state (auto-detect)
      3. {root}/process/FAST_TRACK_PROCESS.yml sem verificação
      4. {root}/process/*.yml (qualquer, preferindo "FAST_TRACK" no nome)
      5. {root}/process/fast_track/FAST_TRACK_PROCESS_V2.yml (legacy)
    """
    import yaml as _yaml

    # Tenta ler o process_id do engine_state ativo para casar com o YAML correto
    active_process_id: str | None = None
    state_globs: list[Path] = []
    # State local (worktree ou continuous)
    local_state = root / "state" / "engine_state.yml"
    if local_state.exists():
        state_globs.append(local_state)
    # Buscar em worktrees externos e runs/ legado
    wt_home = paths.worktrees_home(root)
    if wt_home.is_dir():
        state_globs.extend(wt_home.glob("*/state/engine_state.yml"))
    if (root / "runs").is_dir():
        state_globs.extend((root / "runs").glob("*/state/engine_state.yml"))

    for state_path in sorted(state_globs, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(state_path) as _f:
                _st = _yaml.safe_load(_f)
            if _st and _st.get("process_id"):
                active_process_id = _st["process_id"]
                break
        except Exception:
            pass

    process_dir = root / "process"
    yamls = sorted(process_dir.glob("*.yml")) if process_dir.is_dir() else []

    # Se temos process_id do state, tentar casar
    if active_process_id and yamls:
        for y in yamls:
            try:
                with open(y) as _f:
                    _meta = _yaml.safe_load(_f)
                if _meta and _meta.get("id") == active_process_id:
                    return y
            except Exception:
                pass

    # Novo padrão: process/process.yml
    new_canonical = root / "process" / "process.yml"
    if new_canonical.exists():
        return new_canonical

    # Fallback: nome canônico legado
    canonical = root / "process" / "FAST_TRACK_PROCESS.yml"
    if canonical.exists():
        return canonical

    # Qualquer YAML em process/ (scan)
    if yamls:
        if len(yamls) == 1:
            return yamls[0]
        # Preferir o que tem "FAST_TRACK" no nome
        for y in yamls:
            if "FAST_TRACK" in y.name.upper():
                return y
        return yamls[0]

    # Legacy: process/fast_track/ subdir
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


def _is_cycle_dir(d: Path) -> bool:
    """Verifica se é um diretório de ciclo válido.

    Aceita qualquer diretório dentro de ~/.ft/worktrees/<project>/
    que contenha um state/ ou que siga o padrão legado 'NN' / 'cycle-NN[-...]'.
    """
    name = d.name
    if name.isdigit():
        return True
    if name.startswith("cycle-"):
        return True
    # Nomes livres (ex: cycle-03-claude, my-feature) — aceitar se tiver state/
    if (d / "state" / "engine_state.yml").exists():
        return True
    return False


def _cycle_num_strict(d: Path) -> int | None:
    """Número do ciclo de 'cycle-NN', 'cycle-NN-engine' ou 'NN'; None se não-numérico."""
    name = d.name
    try:
        if name.startswith("cycle-"):
            return int(name[6:].split("-")[0])
        return int(name)
    except ValueError:
        return None


def _cycle_num(d: Path) -> int:
    """Chave de ordenação de ciclos. Nomes sem número (ex.: worktree 'claude')
    ordenam pelo mtime — mais recente ganha, sem quebrar o sort."""
    n = _cycle_num_strict(d)
    if n is not None:
        return n
    try:
        return int(d.stat().st_mtime)
    except OSError:
        return 0


def _find_latest_state(root: Path) -> Path:
    """Encontra o state mais recente.

    Prioridade: continuous > worktrees externos > runs/ legado > legacy.
    """
    # 1. Continuous mode: state/ na raiz do projeto
    continuous = root / "state" / "engine_state.yml"
    if continuous.exists():
        return continuous

    # 2. Worktrees externos (~/.ft/worktrees/<project>/)
    wt_home = paths.worktrees_home(root)
    if wt_home.is_dir():
        wt_dirs = sorted(
            [d for d in wt_home.iterdir() if d.is_dir() and _is_cycle_dir(d)],
            key=_cycle_num, reverse=True,
        )
        for wd in wt_dirs:
            # Worktree com state direto
            state = wd / "state" / "engine_state.yml"
            if state.exists():
                return state

    # 3. Fallback legado: runs/ dentro do projeto
    runs_dir = root / "runs"
    if runs_dir.is_dir():
        run_dirs = sorted(
            [d for d in runs_dir.iterdir() if d.is_dir() and _is_cycle_dir(d)],
            key=_cycle_num, reverse=True,
        )
        for rd in run_dirs:
            # Worktrees legados com runs internas
            runs_sub = rd / "runs"
            if runs_sub.is_dir():
                sub_dirs = sorted(
                    [d for d in runs_sub.iterdir() if d.is_dir()],
                    key=lambda x: x.name, reverse=True,
                )
                for sd in sub_dirs:
                    state = sd / "state" / "engine_state.yml"
                    if state.exists():
                        return state
            state = rd / "state" / "engine_state.yml"
            if state.exists():
                return state

    # 4. Fallback legado antigo
    legacy = root / "project" / "state" / "engine_state.yml"
    if legacy.exists():
        return legacy

    # Default para novo ciclo em worktree externo
    return _worktrees_home(root) / "cycle-01" / "state" / "engine_state.yml"


def _api_health_check(project_root: Path) -> None:
    """Testa conectividade com a API antes de iniciar a run.

    Faz POST mínimo ao endpoint de messages. Aceita 200/429/529
    (API funcionando). Aborta em 400/403/405 com mensagem clara.
    """
    import json
    import urllib.error
    import urllib.request
    from ft.engine import ui as _ui

    if os.environ.get("FT_SKIP_HEALTH_CHECK"):
        return

    # Resolver base_url
    settings_file = project_root / ".claude" / "settings.local.json"
    base_url = None
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text())
            base_url = data.get("env", {}).get("ANTHROPIC_BASE_URL")
        except (json.JSONDecodeError, KeyError):
            pass

    if not base_url:
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    url = f"{base_url}/v1/messages"
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["x-api-key"] = api_key

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            host = base_url.split("//")[-1].split("/")[0]
            print(_ui.info(f"API health check: {resp.status} OK ({host})"))
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode(errors="ignore")[:200]
        if code in (429, 529) or (code == 404 and "model" in body):
            # Rate limit, overloaded ou modelo desconhecido = API respondeu e autenticou
            host = base_url.split("//")[-1].split("/")[0]
            print(_ui.info(f"API health check: {code} ({host}) — API acessível"))
        else:
            print(_ui.fail(f"API health check: {code} — {body}"))
            if code == 403:
                print("    → Projeto não registrado. Registre com: ft setup-env")
            elif code == 405:
                print("    → Rota inválida. Verifique ANTHROPIC_BASE_URL.")
            raise SystemExit(1)
    except Exception as e:
        from ft.engine import ui as _ui
        print(_ui.info(f"API health check: timeout/erro ({e}) — continuando"))


def _seed_from_previous(src: Path, dst: Path) -> int:
    """Copia artefatos do run anterior para o novo run.

    Usa allowlist — só copia outputs conhecidos de projeto.
    Nunca copia: state/, seed/, process/, node_modules/, dist/,
    arquivos de configuração do engine (pyproject.toml, CHANGELOG.md, etc).
    Retorna quantidade de itens copiados.
    """
    import shutil as _shutil

    # Allowlist de diretórios de output que fazem sentido propagar
    SEED_DIRS = {"frontend", "backend", "src", "lib", "tests", "docs"}
    # Sub-dirs do docs/ que NÃO devem ser propagados (artefatos visuais de ciclo)
    EXCLUDE_DOCS_SUBDIRS = {"screenshots", "e2e", "final"}

    count = 0
    for item in src.iterdir():
        if item.name.startswith("."):
            continue
        target = dst / item.name

        if item.is_dir() and item.name in SEED_DIRS:
            if item.name == "docs":
                # Seed docs/ excluindo screenshots e artefatos visuais
                target.mkdir(exist_ok=True)
                for sub in item.iterdir():
                    if sub.name in EXCLUDE_DOCS_SUBDIRS:
                        continue
                    sub_target = target / sub.name
                    if sub.is_dir():
                        _shutil.copytree(sub, sub_target, dirs_exist_ok=True)
                    else:
                        _shutil.copy2(sub, sub_target)
                count += 1
            else:
                _shutil.copytree(item, target, dirs_exist_ok=True,
                                 ignore=_shutil.ignore_patterns(
                                     "node_modules", "dist", "__pycache__", ".git", "*.pyc"
                                 ))
                count += 1
        # Arquivos raiz: não copiar nada (pyproject.toml, CHANGELOG.md, etc
        # são artefatos do engine ou do ciclo anterior, não outputs do projeto)

    return count


def _next_run_dir(project_root: Path) -> Path:
    """Calcula e cria o próximo diretório de run em ~/.ft/worktrees/<project>/.

    Propaga CLAUDE.md e .claude/ da raiz para o run dir
    (necessário para o SymGateway identificar o projeto).
    Copia artefatos do run anterior (seed de código).
    """
    import shutil as _shutil

    wt_home = _worktrees_home(project_root)
    next_num = _next_cycle_num(project_root)
    run_dir = wt_home / f"cycle-{next_num:02d}"
    # Se já existe (colisão), incrementar
    while run_dir.exists():
        next_num += 1
        run_dir = wt_home / f"cycle-{next_num:02d}"
    run_dir.mkdir(parents=True)

    # Propagar CLAUDE.md e .claude/ para o run dir (gateway + settings)
    claude_md = project_root / "CLAUDE.md"
    if claude_md.exists():
        _shutil.copy(claude_md, run_dir / "CLAUDE.md")
    claude_dir = project_root / ".claude"
    if claude_dir.is_dir():
        dst = run_dir / ".claude"
        if not dst.exists():
            _shutil.copytree(claude_dir, dst)

    # Propagar docs/ do projeto para o run dir (LLM roda com CWD=run dir)
    # Nova estrutura: docs/ é o padrão; seed/ é fallback legado
    docs_dir = project_root / "docs"
    seed_dir = project_root / "seed"
    if docs_dir.is_dir():
        _shutil.copytree(docs_dir, run_dir / "docs", dirs_exist_ok=True)
    elif seed_dir.is_dir():
        # Legado: copiar seed/ como docs/ no run dir
        _shutil.copytree(seed_dir, run_dir / "docs", dirs_exist_ok=True)

    # Propagar process/ para o run dir
    process_dir = project_root / "process"
    if process_dir.is_dir():
        _shutil.copytree(process_dir, run_dir / "process", dirs_exist_ok=True)

    # Seed de código do run anterior — buscar em worktrees e runs/ legado
    existing_wt = sorted(
        [d for d in wt_home.iterdir() if d.is_dir() and d != run_dir and _is_cycle_dir(d)],
        key=_cycle_num,
    )
    runs_dir = project_root / "runs"
    existing_legacy = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and _is_cycle_dir(d)],
        key=_cycle_num,
    ) if runs_dir.is_dir() else []
    prev_run = (existing_wt or existing_legacy or [None])[-1]
    if prev_run:
        count = _seed_from_previous(prev_run, run_dir)
        if count:
            print(f"  Seed: {count} artefatos copiados de {prev_run.name}/ → {run_dir.name}/")

    return run_dir


def _ensure_runs_gitignore(project_root: Path) -> None:
    """Garante que runs/ está no .gitignore da raiz do projeto.

    Ciclos são artefatos efêmeros — nunca versionados.
    """
    runs_dir = project_root / "runs"
    runs_dir.mkdir(exist_ok=True)

    # Adicionar runs/ ao .gitignore da raiz (se não estiver)
    gitignore_path = project_root / ".gitignore"
    if gitignore_path.exists():
        content = gitignore_path.read_text()
        if "runs/" not in content:
            with open(gitignore_path, "a") as f:
                f.write("\n# Ciclos Fast Track — artefatos efêmeros, nunca versionados\nruns/\n")
    else:
        gitignore_path.write_text("# Ciclos Fast Track — artefatos efêmeros, nunca versionados\nruns/\n")


def _next_cycle_num(project_root: Path) -> int:
    """Retorna o próximo número de ciclo baseado em worktrees externos e runs/ legado."""
    max_num = 0

    # Worktrees externos (~/.ft/worktrees/<project>/)
    wt_home = paths.worktrees_home(project_root)
    if wt_home.is_dir():
        for d in wt_home.iterdir():
            if d.is_dir() and _is_cycle_dir(d):
                max_num = max(max_num, _cycle_num_strict(d) or 0)

    # Fallback legado: runs/ dentro do projeto
    runs_dir = project_root / "runs"
    if runs_dir.is_dir():
        for d in runs_dir.iterdir():
            if d.is_dir() and _is_cycle_dir(d):
                max_num = max(max_num, _cycle_num_strict(d) or 0)

    # Ledger persistente: o close remove os dirs dos ciclos encerrados; sem isto
    # a numeração regride (ex.: cycle-02 fechado → censo de dirs sugere 02 de novo).
    ledger = wt_home / ".cycles"
    if ledger.exists():
        for tok in ledger.read_text().split():
            if tok.isdigit():
                max_num = max(max_num, int(tok))

    return max_num + 1


def _worktrees_home(project_root: Path) -> Path:
    """Retorna <ft_home>/worktrees/<project_name>/. Cria se não existir."""
    home = paths.worktrees_home(project_root)
    home.mkdir(parents=True, exist_ok=True)
    return home


def _engine_from_last_cycle(project_root: Path) -> str | None:
    """Lê o llm_engine do ciclo mais recente (worktree externo ou runs/ legado)."""
    import yaml as _yaml

    wt_home = paths.worktrees_home(project_root)
    candidates: list[Path] = []

    if wt_home.is_dir():
        candidates += sorted(
            [d / "state" / "engine_state.yml" for d in wt_home.iterdir()
             if d.is_dir() and _is_cycle_dir(d)],
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

    runs_dir = project_root / "runs"
    if runs_dir.is_dir():
        candidates += sorted(
            [d / "state" / "engine_state.yml" for d in runs_dir.iterdir()
             if d.is_dir() and _is_cycle_dir(d)],
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

    for state_file in candidates:
        if state_file.exists():
            try:
                data = _yaml.safe_load(state_file.read_text()) or {}
                engine = data.get("llm_engine")
                if engine:
                    return engine
            except Exception:
                pass
    return None


def _setup_worktree(project_root: Path, name: str) -> Path:
    """Cria um git worktree para rodar um ciclo em isolamento total.

    Cria: ~/.ft/worktrees/<project>/<name>
    Branch: <name>

    O nome é usado exatamente como passado — sem prefixo automático.

    Retorna o path do worktree criado.
    """
    import subprocess as _sp
    import shutil as _shutil

    git_dir = project_root / ".git"
    if not git_dir.exists():
        raise RuntimeError(
            f"Projeto não é um repositório git: {project_root}\n"
            "  Execute: git init && git add -A && git commit -m 'init'\n"
            "  Ou use ft run sem --worktree"
        )

    # Garantir que há pelo menos um commit (worktree precisa de HEAD)
    result = _sp.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Repositório sem commits — faça um commit inicial antes de usar --worktree"
        )

    branch_name = name
    worktree_dir = _worktrees_home(project_root) / branch_name

    # Verificar conflito de branch/diretório
    if worktree_dir.exists():
        raise RuntimeError(f"Worktree já existe: {worktree_dir}\nEscolha outro nome ou remova o existente.")
    branches_result = _sp.run(
        ["git", "branch", "--list", branch_name],
        cwd=project_root, capture_output=True, text=True,
    )
    if branches_result.stdout.strip():
        raise RuntimeError(f"Branch '{branch_name}' já existe. Escolha outro nome ou delete a branch.")

    # Criar worktree
    result = _sp.run(
        ["git", "worktree", "add", str(worktree_dir), "-b", branch_name],
        cwd=project_root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add falhou:\n{result.stderr}")

    # Copiar .claude/ (não está no git) para o worktree
    claude_src = project_root / ".claude"
    if claude_src.is_dir():
        claude_dst = worktree_dir / ".claude"
        if not claude_dst.exists():
            _shutil.copytree(claude_src, claude_dst)

    print(f"  Worktree: {worktree_dir} (branch: {branch_name})")
    return worktree_dir





def _worktree_root_from_state(state_path: Path) -> Path | None:
    """Se o state mora dentro de um worktree, retorna o root desse worktree."""
    # state_path é algo como ~/.ft/worktrees/<proj>/cycle-NN/state/engine_state.yml
    # O root do worktree é o parent de state/ → cycle-NN/
    candidate = state_path.parent.parent
    git_file = candidate / ".git"
    if git_file.exists() and git_file.is_file():
        # É um worktree (arquivo .git aponta para o repo original)
        return candidate
    # Pode ser diretório simples (sem git) dentro da raiz de worktrees
    if paths.is_worktree_path(candidate) and (candidate / "state").is_dir():
        return candidate
    return None


def get_runner(process: str | None = None, llm_engine: str | None = None, llm_model: str | None = None, verbose: bool = False, cycle: str | None = None) -> StepRunner:
    root = find_project_root()
    if cycle:
        # Buscar em worktrees externos primeiro, depois runs/ legado
        wt_home = paths.worktrees_home(root)
        wt_path = wt_home / cycle / "state" / "engine_state.yml"
        legacy_path = root / "runs" / cycle / "state" / "engine_state.yml"

        if wt_path.exists():
            state_path = wt_path
        elif legacy_path.exists():
            state_path = legacy_path
        else:
            print(f"ERRO: Ciclo '{cycle}' não encontrado")
            print(f"  Worktrees: {wt_home}")
            print(f"  Legado:    {root / 'runs'}")
            sys.exit(1)
    else:
        state_path = _find_latest_state(root)

    # Resolver effective_root: se o state mora num worktree, operar lá — não na main
    effective_root = root
    if state_path:
        wt_root = _worktree_root_from_state(state_path)
        if wt_root:
            effective_root = wt_root

    # Buscar processo no effective_root primeiro, fallback para root do projeto
    if process:
        process_path = Path(process)
    else:
        process_path = find_process_yaml(effective_root)
        if not process_path:
            process_path = find_process_yaml(root)
        if not process_path:
            print("ERRO: Nenhum YAML de processo encontrado em ./process/")
            print("  Use: ft init --template fast-track-v2")
            print("  Ou:  ft run . --template fast-track-v2")
            sys.exit(1)

    return StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=effective_root,
        llm_engine=llm_engine,
        llm_model=llm_model,
        verbose=verbose,
    )


def cmd_init(args):
    import os

    # Se nome fornecido, criar/entrar na pasta antes de qualquer coisa
    name = getattr(args, "name", None)
    if name:
        target = Path.cwd() / name
        target.mkdir(parents=True, exist_ok=True)
        os.chdir(target)
        print(f"  → Projeto: {target}")

    # Copiar template se fornecido e processo não existe
    template = getattr(args, "template", None)
    root = find_project_root()
    _guard_engine_repo(root)  # revalida após chdir para <nome>
    if template:
        if not find_process_yaml(root):
            copy_template(template, root)

    # Criar estrutura base: process/, docs/, src/
    (root / "process").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)

    # Playbook do condutor — todo projeto novo ganha uma cópia
    _copy_agents_md(root)

    # Provisionar ambiente SymGateway (se SYM_GATEWAY_PROJECT_KEY estiver definida)
    import os as _os
    if _os.environ.get("SYM_GATEWAY_PROJECT_KEY"):
        provision_environment(project_root=root)
        print(f"  Ambiente SymGateway provisionado")

    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False))
    # Limpar estado anterior se existir
    if runner.state_mgr.path.exists():
        runner.state_mgr.path.unlink()
        runner.state_mgr._state = None
    runner.init_state()
    sprints = runner.graph.get_sprints()
    if sprints:
        print(f"  Sprints: {', '.join(sprints)}")


def cmd_continue(args):
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False), cycle=getattr(args, "cycle", None))
    runner._bypass_human_gates = resolve_bypass_human_gates(args)

    # Inicializar estado se nao existe
    state = runner.state_mgr.load()
    if state.current_node is None:
        runner.init_state()

    mode = "mvp" if args.auto else ("sprint" if args.sprint else "step")
    runner.run(mode=mode)


def cmd_status(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False))
    if getattr(args, "report", False):
        runner.status_report()
    else:
        runner.status(full=args.full)


def _track_heartbeat(raw: str, ctx: dict) -> str | None:
    """Atualiza o contexto do heartbeat de ``ft log --follow``.

    Recebe uma linha crua do stream-json e o dict de contexto ``ctx`` (mutado
    in-place: escreve ``ctx["desc"]`` com uma descrição legível do último
    evento). Retorna um fragmento de thinking quando houver, senão ``None``.

    Extraído para nível de módulo para ser testável — não depende de nada do
    escopo de ``cmd_log``.
    """
    import json as _json

    if not raw.startswith("{"):
        return None
    try:
        ev = _json.loads(raw)
    except Exception:
        return None
    etype = ev.get("type", "")
    if etype == "stream_event":
        inner = ev.get("event", {})
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "thinking_delta":
                ctx["desc"] = "raciocinando"
                return delta.get("thinking", "")
        return None
    if etype == "system":
        subtype = ev.get("subtype", "")
        if subtype == "thinking_tokens":
            ctx["desc"] = f"pensando (~{ev.get('estimated_tokens', 0)} tokens)"
        elif subtype == "init":
            # Evento de abertura de sessão: expõe modelo, modo de permissão e
            # nº de ferramentas em vez de um "evento system" opaco.
            model = ev.get("model") or "?"
            n_tools = len(ev.get("tools") or [])
            mode = ev.get("permissionMode") or ""
            mode_txt = f", {mode}" if mode else ""
            ctx["desc"] = f"sessão iniciada ({model}, {n_tools} tools{mode_txt})"
        elif subtype:
            ctx["desc"] = f"evento system/{subtype}"
        else:
            ctx["desc"] = "evento system"
    elif etype == "user":
        ctx["desc"] = "resultado de ferramenta recebido, processando"
    elif etype == "assistant":
        # Mostra o que o worker está fazendo (ferramenta + alvo, ou trecho do
        # texto) em vez de um "gerando resposta" genérico.
        blocks = ev.get("message", {}).get("content", []) or []
        tool = next((b for b in blocks if b.get("type") == "tool_use"), None)
        if tool:
            name = tool.get("name") or "ferramenta"
            inp = tool.get("input") or {}
            target = str(
                inp.get("file_path") or inp.get("command")
                or inp.get("pattern") or inp.get("path") or ""
            )
            # Para ferramentas de arquivo, mostra só o basename.
            if name in ("Read", "Edit", "Write", "NotebookEdit") and "/" in target:
                target = target.rsplit("/", 1)[-1]
            target = " ".join(target.split())  # colapsa quebras/espaços
            ctx["desc"] = f"{name}: {target[:60]}" if target else name
        else:
            txt = next(
                (b.get("text", "") for b in blocks
                 if b.get("type") == "text" and b.get("text", "").strip()),
                "",
            )
            if txt:
                ctx["desc"] = "escrevendo: " + " ".join(txt.split())[:60]
            elif any(b.get("type") == "thinking" for b in blocks):
                ctx["desc"] = "raciocinando"
            else:
                ctx["desc"] = "gerando resposta"
    elif etype == "result":
        # Evento final do worker: resume desfecho, turnos, tempo e custo em vez
        # de um "evento result" opaco.
        head = "resultado com erro" if ev.get("is_error") else "resultado ok"
        subtype = ev.get("subtype") or ""
        parts: list[str] = []
        if subtype and subtype != "success":
            parts.append(subtype)
        if ev.get("num_turns") is not None:
            parts.append(f"{ev['num_turns']} turnos")
        dur = ev.get("duration_ms")
        if isinstance(dur, (int, float)):
            parts.append(f"{dur / 1000:.1f}s")
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            parts.append(f"US$ {cost:.2f}")
        ctx["desc"] = head + (" — " + " · ".join(parts) if parts else "")
    elif etype:
        ctx["desc"] = f"evento {etype}"
    return None


def cmd_log(args):
    """Mostra/acompanha o log LLM do ciclo ativo, formatado para leitura humana."""
    import time as _time
    from ft.engine.delegate import _format_stream_line
    from ft.engine import ui as _ui

    # `ft log` puro (nenhum parâmetro) → help explicando os parâmetros.
    # Para ver as últimas linhas sem acompanhar, use `ft log -n 30`.
    if not (args.follow or args.raw or args.path or args.lines is not None):
        args._parser.print_help()
        return
    lines = args.lines if args.lines is not None else 30

    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args))

    def _current_log() -> Path | None:
        state = runner.state_mgr.load()
        rel = state.active_llm_log or state.last_llm_log
        if rel:
            p = Path(rel)
            if not p.is_absolute():
                p = Path(runner._work_dir) / rel
            if p.exists():
                return p
        # Fallback: arquivo mais recente em llm_logs/
        log_dir = runner.state_mgr.path.parent / "llm_logs"
        if log_dir.is_dir():
            logs = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime)
            if logs:
                return logs[-1]
        return None

    _engine = runner._resolve_llm_engine()
    _last_out: list[str | None] = [None]
    _md = getattr(args, "markdown", False)

    def _paint(s: str) -> str:
        return _ui.paint_stream_line(s) if _md else s

    def _fmt(line: str) -> str | None:
        out = _format_stream_line(_engine, line)
        if not out or (out.startswith("event ") and not args.raw):
            return None
        # Stream parcial repete o mesmo bloco várias vezes — dedupe consecutivo
        if out == _last_out[0]:
            return None
        _last_out[0] = out
        return out

    log_path = _current_log()
    if log_path is None:
        print(_ui.warn("Nenhum log LLM encontrado para o ciclo ativo"), flush=True)
        return

    if args.path:
        print(log_path, flush=True)
        return

    print(_ui.dim(f"── {log_path.name} ──"), flush=True)
    with log_path.open(errors="replace") as f:
        raw_lines = f.readlines()
    shown = [x for x in (line.rstrip() if args.raw else _fmt(line) for line in raw_lines) if x]
    for out in shown[-lines:]:
        print(_paint(out) if not args.raw else out, flush=True)

    if not args.follow:
        return

    # Follow: acompanha o arquivo e troca sozinho quando o engine abre um log novo.
    # Heartbeat: se ficar >15s sem linha impressa, mostra o que o worker está fazendo
    # (thinking tokens, último evento) para não parecer travado.
    _track = _track_heartbeat

    try:
        f = log_path.open(errors="replace")
        f.seek(0, 2)
        idle = 0.0
        last_print = _time.time()
        hb = {"desc": ""}

        def _heartbeat() -> None:
            nonlocal last_print
            now = _time.time()
            if now - last_print >= 15.0:
                desc = hb["desc"] or "aguardando eventos do LLM"
                print(_ui.dim(f"  ⋯ {desc}"), flush=True)
                last_print = now

        think_buf = ""

        def _flush_think(force: bool = False) -> None:
            nonlocal think_buf, last_print
            while "\n" in think_buf:
                head, think_buf = think_buf.split("\n", 1)
                if head.strip():
                    msg = f"✻ {head.strip()[:160]}"
                    print(_paint(msg) if _md else _ui.dim(msg), flush=True)
                    last_print = _time.time()
            if force and think_buf.strip():
                msg = f"✻ {think_buf.strip()[:160]}"
                print(_paint(msg) if _md else _ui.dim(msg), flush=True)
                think_buf = ""
                last_print = _time.time()

        while True:
            line = f.readline()
            if line:
                idle = 0.0
                frag = _track(line.strip(), hb)
                if frag is not None and not args.raw:
                    think_buf += frag
                    _flush_think()
                    continue
                out = line.rstrip() if args.raw else _fmt(line)
                if out:
                    _flush_think(force=True)
                    print(out if args.raw else _paint(out), flush=True)
                    last_print = _time.time()
                else:
                    _heartbeat()
                continue
            _time.sleep(0.5)
            idle += 0.5
            _heartbeat()
            if idle >= 3.0:
                idle = 0.0
                newer = _current_log()
                if newer and newer != log_path:
                    f.close()
                    log_path = newer
                    print(_ui.dim(f"── {log_path.name} ──"), flush=True)
                    f = log_path.open(errors="replace")
                    hb["desc"] = ""
    except KeyboardInterrupt:
        pass
    finally:
        try:
            f.close()
        except Exception:
            pass


def cmd_runs(args):
    """Mostra tabela comparativa de todos os ciclos (worktrees externos + runs/ legado)."""
    from ft.engine import ui as _ui
    import re as _re

    project_root = Path(args.project).resolve()
    _guard_engine_repo(project_root)

    # Coletar ciclos de worktrees externos + runs/ legado
    cycles = []
    wt_home = paths.worktrees_home(project_root)
    if wt_home.is_dir():
        cycles.extend(d for d in wt_home.iterdir() if d.is_dir() and _is_cycle_dir(d))

    runs_dir = project_root / "runs"
    if runs_dir.is_dir():
        cycles.extend(d for d in runs_dir.iterdir() if d.is_dir() and _is_cycle_dir(d))

    cycles = sorted(cycles, key=_cycle_num)

    if not cycles:
        print(_ui.warn("Nenhum ciclo encontrado"))
        return

    import yaml as _yaml

    rows = []
    for cycle in cycles:
        # Serve URL — buscar .serve_url na raiz do ciclo
        serve_url = "—"
        serve_file = cycle / ".serve_url"
        if serve_file.exists():
            serve_url = serve_file.read_text().strip()

        # Fonte de verdade: engine_state.yml
        # Novo padrão: state/ diretamente no ciclo
        # Fallback legado: runs/*/state/ dentro do ciclo
        state_data = {}
        state_path = cycle / "state" / "engine_state.yml"
        if state_path.exists():
            try:
                state_data = _yaml.safe_load(state_path.read_text()) or {}
            except Exception:
                pass
        if not state_data:
            state_files = sorted((cycle / "runs").glob("*/state/engine_state.yml")) if (cycle / "runs").is_dir() else []
            if state_files:
                try:
                    state_data = _yaml.safe_load(state_files[-1].read_text()) or {}
                except Exception:
                    pass

        if not state_data:
            continue  # ciclo vazio/fantasma — sem estado

        steps_done = state_data.get("metrics", {}).get("steps_completed", len(state_data.get("completed_nodes", [])))
        steps_total = state_data.get("metrics", {}).get("steps_total", "?")
        current_node = state_data.get("current_node") or ""
        node_status = state_data.get("node_status", "")

        # Timestamp da última entrada no log de atividade
        ts = "—"
        log = next(cycle.glob("*_log.md"), None)
        if log:
            lines = [l for l in log.read_text().splitlines() if l.startswith("| 2")]
            if lines:
                last = lines[-1].split("|")
                ts = last[1].strip()[11:16] if len(last) > 1 else "—"

        # Node a exibir
        if not current_node:
            node = "DONE" if node_status == "done" else "—"
        else:
            node = current_node

        # Status colorido
        if node_status == "done":
            status_str = _ui.success(node)
        elif node_status == "blocked":
            status_str = _ui.fail(node)
        elif node_status == "awaiting_approval":
            status_str = _ui.warn(f"⏸ {node}")
        elif node_status == "delegated":
            status_str = f"   ⟳ {node}"
        else:
            status_str = f"   {node}"

        rows.append((cycle.name, f"{steps_done}/{steps_total}", ts, status_str, serve_url))

    # Header
    print()
    print(f"  {'CICLO':<22} {'STEPS':>8}  {'ÚLT.':>5}  {'NODE ATUAL':<40}  URL")
    print(f"  {'─'*22}  {'─'*8}  {'─'*5}  {'─'*40}  {'─'*25}")
    for name, steps, ts, node_str, url in rows:
        print(f"  {name:<22}  {steps:>8}  {ts:>5}  {node_str:<40}  {url}")
    print()


def cmd_approve(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False))
    message = getattr(args, "message", None)
    runner.approve(message=message)
    # Continuar automaticamente apos aprovacao
    if not args.no_continue:
        runner.run(mode="step")


def cmd_reject(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False))
    runner.reject(args.reason, retry=not args.no_retry)


def cmd_explore(args):
    """Modo de exploração livre — acumula pedidos e gera relatório ao finalizar."""
    from ft.engine import ui as _ui

    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args),
                        llm_model=resolve_llm_model(args),
                        verbose=getattr(args, "verbose", False))

    if getattr(args, "finish", False):
        runner.explore_finish()
    elif getattr(args, "skip", False):
        runner.explore_skip()
    else:
        request = getattr(args, "request", None)
        if not request:
            # Sem argumento: mostrar estado atual de exploração
            state = runner.state_mgr.load()
            if state.node_status != "exploring":
                print(_ui.warn("Ciclo não está em modo exploração."))
                print(_ui.info("Aguarde o processo chegar num node type: exploration"))
            else:
                log = state.exploration_log or []
                print(_ui.exploration_start("Exploração Livre", len(log)))
            return
        runner.explore_request(request)


def _prompt_merge_strategy(work: Path) -> tuple[str, list[str] | None]:
    """Prompt interativo para escolher estratégia de merge no ft close."""
    from ft.engine import ui as _ui

    # Listar pastas disponíveis no worktree
    available = sorted(
        p.name + ("/" if p.is_dir() else "")
        for p in work.iterdir()
        if not p.name.startswith(".") and p.name != "state"
    )

    print()
    print(_ui.header("Como deseja fazer o merge?"))
    print()
    print("  [1] Full      — merge completo (código + docs + processo)")
    print("  [2] Docs only — apenas docs/ e process/")
    print("  [3] Selective — escolher pastas específicas")
    print("  [4] None      — não mergear nada (descartar tudo)")
    print()

    choice = input("Escolha [1/2/3/4] (default: 1): ").strip() or "1"

    if choice == "1":
        return "full", None
    elif choice == "2":
        return "docs", None
    elif choice == "3":
        print()
        print(f"  Pastas disponíveis: {' '.join(available)}")
        print()
        raw = input("Quais paths mergear? (separados por espaço): ").strip()
        if not raw:
            print(_ui.warn("Nenhum path informado — cancelando merge"))
            return "none", None
        paths = raw.split()
        return "selective", paths
    elif choice == "4":
        return "none", None
    else:
        print(_ui.warn(f"Opção inválida: {choice} — usando full"))
        return "full", None


def cmd_close(args):
    """Encerra o ciclo ativo: merge interativo + remove worktree + limpa branch."""
    import subprocess as _sp
    from ft.engine import ui as _ui

    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False))
    state = runner.state_mgr.load()

    # Verificar se o ciclo terminou
    terminal = {"done", "completed"}
    if state.node_status not in terminal and not getattr(args, "force", False):
        print(_ui.fail(f"Ciclo ainda ativo: {state.current_node} ({state.node_status})"))
        print(_ui.warn("Use --force para encerrar mesmo assim, ou ft approve/continue para finalizar"))
        return

    # 1. Determinar estratégia de merge
    merge_strategy = getattr(args, "merge", None)
    merge_paths = None

    if merge_strategy == "selective":
        raw_paths = getattr(args, "merge_paths", None)
        if raw_paths:
            merge_paths = raw_paths.split()
        else:
            merge_strategy = None  # Forçar prompt

    work = Path(runner.project_root)

    merge_ok = True
    if merge_strategy:
        # Via CLI flags (não-interativo)
        merge_ok = runner.merge_on_close(merge_strategy, merge_paths)
    else:
        # Prompt interativo
        wt = runner._detect_worktree()
        if wt:
            strategy, paths = _prompt_merge_strategy(work)
            merge_ok = runner.merge_on_close(strategy, paths)
        # Se não é worktree, nada a mergear

    if merge_ok is False:
        # NUNCA destruir worktree/branch com merge falho — os commits do ciclo
        # só existem lá. (Lição vibeos cycle-02: close removeu branch com
        # conflitos abertos; recuperação exigiu resgate via SHA solto.)
        print(_ui.fail("Merge falhou — worktree e branch PRESERVADOS."))
        print(_ui.warn("Resolva o merge (ou use --merge none) e rode ft close novamente."))
        return

    # 2. Descobrir se estamos num worktree
    git_file = work / ".git"
    is_worktree = git_file.exists() and git_file.is_file()

    if is_worktree and not getattr(args, "keep_worktree", False):
        gitdir_line = git_file.read_text().strip()
        if gitdir_line.startswith("gitdir:"):
            gitdir = Path(gitdir_line.split(":", 1)[1].strip())
            original_root = gitdir.parent.parent.parent

            branch = _sp.run(
                ["git", "branch", "--show-current"],
                cwd=work, capture_output=True, text=True,
            ).stdout.strip()

            # Remover worktree
            result = _sp.run(
                ["git", "worktree", "remove", str(work), "--force"],
                cwd=original_root, capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(_ui.success(f"Worktree removido: {work.name}"))
            else:
                print(_ui.warn(f"Worktree não removido: {result.stderr.strip()[:200]}"))

            # Remover branch
            if branch:
                result = _sp.run(
                    ["git", "branch", "-D", branch],
                    cwd=original_root, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    print(_ui.success(f"Branch removida: {branch}"))
                else:
                    print(_ui.dim(f"Branch {branch} não removida: {result.stderr.strip()[:100]}"))
    elif is_worktree:
        print(_ui.dim("Worktree preservado (--keep-worktree)"))

    print(_ui.success("Ciclo encerrado."))


def cmd_graph(args):
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args), llm_model=resolve_llm_model(args), verbose=getattr(args, "verbose", False))
    runner.status(full=True)


def _validate_project_structure(root: Path) -> tuple[list[str], list[str]]:
    """Valida estrutura base do projeto (docs/, process/, src/).
    Retorna (errors, warnings)."""
    errors = []
    warnings = []

    required_dirs = ["docs", "process", "src"]
    for d in required_dirs:
        if not (root / d).is_dir():
            errors.append(f"diretório '{d}/' ausente")

    # Pelo menos um YAML em process/ (direto ou em subdiretórios)
    if (root / "process").is_dir():
        yamls = list((root / "process").rglob("*.yml")) + list((root / "process").rglob("*.yaml"))
        if not yamls:
            errors.append("nenhum YAML encontrado em process/")

    # Warnings para docs opcionais mas esperados
    for doc in ["docs/PRD.md", "docs/TECH_STACK.md"]:
        if not (root / doc).exists():
            warnings.append(f"'{doc}' não encontrado")

    return errors, warnings


def cmd_validate(args):
    """Valida o YAML do processo."""
    from ft.engine.graph import load_graph
    from ft.engine.process_validator import validate_process, format_report
    from ft.engine.runner import VALIDATOR_REGISTRY

    root = find_project_root()

    # --- Validação de estrutura do projeto ---
    print("\nValidando estrutura do projeto...\n")
    struct_errors, struct_warnings = _validate_project_structure(root)
    structure_passed = len(struct_errors) == 0
    if structure_passed:
        print("  \u2705 Estrutura: docs/, process/, src/ presentes")
    else:
        for e in struct_errors:
            print(f"  \u274c {e}")
    for w in struct_warnings:
        print(f"  \u26a0\ufe0f  {w}")
    warn_note = f" ({len(struct_warnings)} warnings)" if struct_warnings else ""
    err_note = f" ({len(struct_errors)} erros)" if struct_errors else ""
    print(f"\n  Estrutura: {'PASS' if structure_passed else 'FAIL'}{err_note}{warn_note}")

    # --- Validação do YAML ---
    print()
    if args.process:
        process_path = Path(args.process)
    else:
        process_path = find_process_yaml(root)
        if not process_path:
            print("ERRO: Nenhum YAML de processo encontrado em ./process/")
            sys.exit(1)

    rel = process_path.relative_to(root) if process_path.is_relative_to(root) else process_path
    print(f"Validando {rel}...\n")

    try:
        graph = load_graph(process_path)
    except (ValueError, FileNotFoundError) as e:
        print(f"  \u274c Erro ao carregar YAML: {e}")
        sys.exit(1)

    report = validate_process(graph, VALIDATOR_REGISTRY)
    total = len(graph.nodes)
    print(format_report(report, total))

    overall_pass = structure_passed and report.passed
    sys.exit(0 if overall_pass else 1)


def cmd_lint_process(args):
    """Lint semântico — usa LLM para detectar especificidades de projeto no YAML."""
    import json as _json

    root = find_project_root()

    if args.process:
        process_path = Path(args.process)
    else:
        process_path = find_process_yaml(root)
        if not process_path:
            print("ERRO: Nenhum YAML de processo encontrado em ./process/")
            sys.exit(1)

    yaml_content = process_path.read_text()
    rel_path = process_path.relative_to(root) if process_path.is_relative_to(root) else process_path

    print(f"\nLint semântico: {rel_path}\n")

    prompt = (
        "Você é um validador de processos YAML do Fast Track.\n\n"
        "REGRA FUNDAMENTAL: O YAML de processo é pura orquestração. Ele define sequência "
        "de passos, executor, e validators. Ele NÃO deve conter especificidades de projeto.\n\n"
        "VIOLAÇÕES (error) — reporte se encontrar nos prompts ou títulos:\n"
        "- Nomes de produto/projeto (ex: 'ft-studio', 'Pokemon', 'YouNews', qualquer nome próprio)\n"
        "- Specs de design hardcoded (ex: 'Activity Bar 40px', '#0a0a1a', 'fts-*', '180x60px', cores hex)\n"
        "- Tech stack hardcoded (ex: 'Svelte + Vite', 'React', 'js-yaml', 'Flask', nomes de frameworks/libs)\n"
        "- Checklist de validação específica (em vez de 'leia ui_guidelines.md e valide')\n"
        "- Estrutura de projeto específica detalhada (ex: lista de componentes, nomes de arquivos do projeto)\n\n"
        "WARNINGS — reporte como warning:\n"
        "- Nomes de screenshots muito específicos do projeto (ex: 'graph.png', 'drawer-open.png')\n\n"
        "ACEITO — NÃO reporte:\n"
        "- Caminhos genéricos de artefatos (docs/PRD.md, docs/ui_guidelines.md, docs/tech_stack.md)\n"
        "- Validators genéricos (file_exists, has_sections, command_succeeds)\n"
        "- Estrutura de pastas genérica (frontend/src/, docs/screenshots/, frontend/dist/)\n"
        "- IDs de nodes, títulos descritivos genéricos, nomes de sprints\n"
        "- Comandos de build genéricos (npm run build, npm install, npx serve)\n"
        "- Referências a ferramentas genéricas (Playwright, curl)\n"
        "- Instruções genéricas ('Leia docs/ui_guidelines.md e siga')\n\n"
        "YAML DO PROCESSO:\n"
        "---\n"
        f"{yaml_content}\n"
        "---\n\n"
        "Responda APENAS com JSON (sem markdown, sem ```), no formato:\n"
        '{"violations": [\n'
        '  {"level": "error"|"warning", "node_id": "...", "excerpt": "trecho curto", '
        '"reason": "motivo", "suggestion": "como corrigir"}\n'
        '], "verdict": "PASS"|"FAIL"}\n\n'
        "Se não houver violações: {\"violations\": [], \"verdict\": \"PASS\"}\n"
        "verdict=FAIL se houver pelo menos 1 error. Warnings sozinhos = PASS."
    )

    from ft.engine.delegate import delegate_to_llm

    engine = resolve_llm_engine(args)
    model = resolve_llm_model(args)
    result = delegate_to_llm(
        task=prompt,
        project_root=str(root),
        allowed_paths=[],
        max_turns=5,
        llm_engine=engine,
        llm_model=model,
    )

    output = result.output.strip()
    start = output.find("{")
    end = output.rfind("}") + 1

    if start < 0 or end <= start:
        print(f"  Erro ao parsear resposta do LLM:\n{output[:500]}")
        sys.exit(1)

    try:
        data = _json.loads(output[start:end])
    except _json.JSONDecodeError:
        print(f"  JSON inválido na resposta do LLM:\n{output[start:end][:500]}")
        sys.exit(1)

    violations = data.get("violations", [])
    verdict = data.get("verdict", "FAIL")

    if not violations:
        print("  \u2705 Nenhuma especificidade de projeto detectada")
        print(f"\n  Resultado: PASS")
        sys.exit(0)

    errors = [v for v in violations if v.get("level") == "error"]
    warnings = [v for v in violations if v.get("level") == "warning"]

    for v in violations:
        icon = "\u274c" if v.get("level") == "error" else "\u26a0\ufe0f "
        node = v.get("node_id", "?")
        excerpt = v.get("excerpt", "")
        reason = v.get("reason", "")
        suggestion = v.get("suggestion", "")
        print(f"  {icon} {node}: \"{excerpt}\"")
        print(f"     \u2192 {reason}")
        if suggestion:
            print(f"     Sugestão: {suggestion}")
        print()

    has_errors = len(errors) > 0
    status = "FAIL" if has_errors else "PASS"
    parts = []
    if errors:
        parts.append(f"{len(errors)} erro(s)")
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    print(f"  Resultado: {status} ({', '.join(parts)})")

    sys.exit(1 if has_errors else 0)


def cmd_retry(args):
    """Reseta o estado blocked do node atual e retenta sem aplicar correção."""
    from ft.engine import ui as _ui

    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args),
                        llm_model=resolve_llm_model(args),
                        verbose=getattr(args, "verbose", False))

    state = runner.state_mgr.load()
    if state.node_status != "blocked":
        print(_ui.warn(f"Node atual não está bloqueado (status: {state.node_status})"))
        return

    node_id = state.current_node
    print(_ui.info(f"Retentando node: {node_id}"))

    # Limpar estado bloqueado e reset do contador de auto-fix
    state.node_status = "ready"
    state.blocked_reason = None
    runner.state_mgr.save()
    runner._auto_fix_counts.pop(node_id, None)

    mode = "mvp" if getattr(args, "auto", False) else "step"
    runner.run(mode=mode)


def cmd_fix(args):
    """Injeta instrução de correção e retoma o ciclo (on_fail) ou delega ao LLM (blocked)."""
    from ft.engine import ui as _ui

    instruction = args.instruction
    runner = get_runner(args.process, llm_engine=resolve_llm_engine(args),
                        llm_model=resolve_llm_model(args),
                        verbose=getattr(args, "verbose", False))

    # Modo 1: pending_fix (on_fail event) — injeta instrução e volta ao goto
    if runner.apply_fix(instruction):
        mode = "mvp" if getattr(args, "auto", False) else "step"
        runner.run(mode=mode)
        return

    # Modo 2: blocked genérico — delega ao LLM para corrigir arquivos
    from ft.engine.delegate import delegate_to_llm
    root = runner.project_root
    state_path = runner.state_mgr.path
    blocked_context = ""
    if state_path.exists():
        state = runner.state_mgr.load()
        if state.blocked_reason:
            blocked_context = (
                f"\n\nCONTEXTO: O processo parou no node '{state.current_node}' com o erro:\n"
                f"{state.blocked_reason}\n"
            )

    prompt = (
        f"O usuário pediu a seguinte correção:\n\n"
        f"{instruction}\n"
        f"{blocked_context}\n"
        f"Analise o problema, faça as alterações necessárias nos arquivos do projeto, "
        f"e diga DONE quando terminar."
    )

    print(_ui.info(f"Aplicando correção: {instruction}"))
    result = delegate_to_llm(
        task=prompt,
        project_root=str(root),
        allowed_paths=["src/", "tests/", "docs/", "main.py", "app.py", "server.py",
                        "frontend/", "process/"],
        llm_engine=resolve_llm_engine(args) or "claude",
    )

    if result.success:
        print(_ui.success("Correção aplicada"))
        state = runner.state_mgr.load()
        if state.node_status == "blocked":
            state.node_status = "running"
            state.blocked_reason = None
            state.last_approval_message = instruction
            runner.state_mgr.save()
            print(_ui.info("Estado desbloqueado — continuando..."))
            mode = "mvp" if getattr(args, "auto", False) else "step"
            runner.run(mode=mode)
        else:
            print(_ui.info("Para continuar o processo: ft continue --mvp"))
    else:
        print(_ui.fail(f"LLM não conseguiu aplicar: {result.output[:300]}"))


def cmd_abort(args):
    """Aborta o ciclo: descarta worktree e branch sem merge nenhum."""
    import subprocess as _sp
    from ft.engine import ui as _ui

    root = find_project_root()
    work = Path(root)
    git_file = work / ".git"

    # Verificar se estamos num worktree
    if not git_file.exists() or git_file.is_dir():
        print(_ui.fail("Não está numa worktree — nada para abortar."))
        print(_ui.dim("Use ft cancel para cancelar um run no repo principal."))
        return

    gitdir_line = git_file.read_text().strip()
    if not gitdir_line.startswith("gitdir:"):
        print(_ui.fail("Formato .git inválido — não é worktree."))
        return

    gitdir = Path(gitdir_line.split(":", 1)[1].strip())
    original_root = gitdir.parent.parent.parent

    branch = _sp.run(
        ["git", "branch", "--show-current"],
        cwd=work, capture_output=True, text=True,
    ).stdout.strip()

    # Confirmação
    print()
    print(_ui.warn(f"ABORT: vai descartar TUDO do ciclo em {work.name}"))
    print(_ui.dim(f"  Worktree: {work}"))
    print(_ui.dim(f"  Branch:   {branch}"))
    print(_ui.dim(f"  Nenhum merge será feito — todo código será perdido."))
    print()
    confirm = input("Confirma? [s/N]: ").strip().lower()
    if confirm not in ("s", "sim", "y", "yes"):
        print(_ui.dim("Abortado pelo usuário."))
        return

    # Matar servidores que possam estar rodando
    for pid_file in (".serve_backend.pid", ".serve_frontend.pid", ".serve.pid"):
        pf = work / pid_file
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                os.kill(pid, 15)
            except (ValueError, ProcessLookupError, OSError):
                pass

    # Remover worktree
    result = _sp.run(
        ["git", "worktree", "remove", str(work), "--force"],
        cwd=original_root, capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(_ui.success(f"Worktree removido: {work.name}"))
    else:
        print(_ui.fail(f"Erro ao remover worktree: {result.stderr.strip()[:200]}"))
        return

    # Remover branch
    if branch:
        result = _sp.run(
            ["git", "branch", "-D", branch],
            cwd=original_root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(_ui.success(f"Branch removida: {branch}"))

    # Limpar diretório em ~/.ft/worktrees se existir
    ft_worktrees = paths.worktrees_root()
    if ft_worktrees.exists():
        for project_dir in ft_worktrees.iterdir():
            wt_dir = project_dir / work.name
            if wt_dir.exists():
                import shutil
                shutil.rmtree(wt_dir, ignore_errors=True)
                print(_ui.dim(f"  Limpou {wt_dir}"))

    print(_ui.success("Ciclo abortado. Nenhum merge realizado."))


def cmd_cancel(args):
    """Cancela o run ativo com justificativa."""
    import yaml as _yaml
    from datetime import datetime
    from ft.engine import ui as _ui

    root = find_project_root()
    reason = args.reason

    # Encontrar o run ativo
    state_path = _find_latest_state(root)
    if not state_path.exists():
        print(_ui.warn("Nenhum run ativo encontrado."))
        return

    data = _yaml.safe_load(state_path.read_text()) or {}
    current_node = data.get("current_node")
    completed = data.get("completed_nodes", [])
    total = data.get("metrics", {}).get("steps_total", "?")

    if current_node is None:
        print(_ui.warn("Processo já finalizado — nada para cancelar."))
        return

    # Matar PID se ainda estiver rodando
    lock = data.get("_lock", {})
    pid = lock.get("pid")
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, 15)  # SIGTERM
            print(_ui.info(f"Processo PID {pid} encerrado"))
        except OSError:
            pass

    # Marcar state como cancelled
    data["node_status"] = "cancelled"
    data["blocked_reason"] = f"CANCELADO: {reason}"
    data["_lock"] = None
    state_path.write_text(_yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))

    # Gerar relatório de cancelamento (determinístico)
    run_dir = state_path.parent.parent  # runs/<N>/state/ → runs/<N>/
    cancel_report = run_dir / "CANCELLED.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    gate_log = data.get("gate_log", {})
    blocked = data.get("blocked_reason", "")
    artifacts = data.get("artifacts", {})

    # Base determinística
    base_report = (
        f"# Run Cancelado\n\n"
        f"**Data:** {ts}\n"
        f"**Node atual:** {current_node}\n"
        f"**Progresso:** {len(completed)}/{total} steps\n"
        f"**Steps concluídos:** {', '.join(completed) if completed else 'nenhum'}\n"
        f"**Gates:** {', '.join(f'{k}={v}' for k, v in gate_log.items()) if gate_log else 'nenhum'}\n"
        f"**Artefatos:** {', '.join(artifacts.keys()) if artifacts else 'nenhum'}\n"
        f"**Último bloqueio:** {blocked or 'nenhum'}\n\n"
        f"## Motivo do cancelamento\n\n"
        f"{reason}\n"
    )

    print(_ui.header("Run cancelado"))
    print(_ui.info(f"Node: {current_node} ({len(completed)}/{total} steps)"))
    print(_ui.info(f"Motivo: {reason}"))

    # Análise LLM do cancelamento
    print(_ui.info("Gerando análise do cancelamento..."))
    from ft.engine.delegate import delegate_to_llm
    llm_engine = resolve_llm_engine(args) or "claude"

    analysis_prompt = (
        f"Um run do processo Fast Track foi cancelado. Analise o contexto e produza "
        f"um relatório de encerramento.\n\n"
        f"DADOS DO RUN:\n{base_report}\n\n"
        f"PRODUZA uma análise com:\n"
        f"## Análise do cancelamento\n"
        f"- O que foi concluído e o que ficou pendente\n"
        f"- Se o motivo do cancelamento indica problema de produto ou de processo\n"
        f"- Recomendação: retomar este run (ft continue) ou iniciar novo (ft run)\n\n"
        f"## Aprendizados para o próximo ciclo\n"
        f"- O que o ciclo parcial ensinou\n"
        f"- O que deveria mudar no próximo run\n\n"
        f"Escreva o relatório completo em: {cancel_report.relative_to(root)}\n"
        f"Comece com o conteúdo base que já preparei, e adicione as seções de análise.\n"
        f"Ao final diga DONE."
    )

    # Salvar base primeiro (fallback se LLM falhar)
    cancel_report.write_text(base_report)

    result = delegate_to_llm(
        task=analysis_prompt,
        project_root=str(root),
        allowed_paths=[str(cancel_report.relative_to(root))],
        max_turns=10,
        llm_engine=llm_engine,
    )

    if result.success:
        print(_ui.success("Relatório de cancelamento gerado com análise"))
    else:
        print(_ui.warn("LLM não disponível — relatório base salvo sem análise"))

    print(_ui.dim(f"Relatório: {cancel_report.relative_to(root)}"))
    print(_ui.info("Para iniciar um novo run: ft run ."))


def cmd_setup_env(args):
    """Provisiona CLAUDE.md e .claude/settings.local.json via SYM_GATEWAY_PROJECT_KEY."""
    import os
    key = os.environ.get("SYM_GATEWAY_PROJECT_KEY")
    if not key:
        print("  ✗ SYM_GATEWAY_PROJECT_KEY não definida\n")
        print("    Exporte antes de rodar:")
        print("      export SYM_GATEWAY_PROJECT_KEY=sk-sym_...")
        print("      export SYM_GATEWAY_ADMIN_KEY=sk-sym_...  # opcional")
        sys.exit(1)
    project_root = Path(args.project) if args.project else find_project_root()
    provision_environment(project_root=project_root)
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


def _resolve_run_mode(project_root: Path) -> str:
    """Lê run_mode de environment.yml. Default: isolated."""
    from ft.engine.hooks import load_environment
    env = load_environment(str(project_root))
    return env.get("run_mode", "isolated")


def _check_active_run(project_root: Path) -> str | None:
    """Verifica se há um ciclo ativo (em andamento, pausado ou bloqueado). Retorna descrição ou None."""
    import yaml as _yaml

    _TERMINAL_STATUSES = {"done", "completed", "failed", "aborted"}

    def _is_active_state(data: dict) -> bool:
        """Retorna True se o state indica ciclo em andamento (não finalizado)."""
        node_status = data.get("node_status", "")
        current_node = data.get("current_node", "")
        # Se o node_status é terminal ou não há node atual, não é ativo
        if node_status in _TERMINAL_STATUSES or not current_node:
            return False
        return True

    def _describe_state(data: dict, cycle_name: str) -> str:
        node = data.get("current_node", "?")
        status = data.get("node_status", "?")
        return f"{cycle_name} ({node} — {status})"

    # 1. Continuous mode
    state_candidate = project_root / "state" / "engine_state.yml"
    if state_candidate.exists():
        try:
            data = _yaml.safe_load(state_candidate.read_text()) or {}
            if _is_active_state(data):
                return _describe_state(data, "modo continuous")
        except Exception:
            pass

    # 2. Worktrees externos (~/.ft/worktrees/<project>/)
    wt_home = paths.worktrees_home(project_root)
    if wt_home.is_dir():
        candidates = sorted(
            [d for d in wt_home.iterdir() if d.is_dir() and _is_cycle_dir(d)],
            key=_cycle_num, reverse=True,
        )
        for wt in candidates:
            state = wt / "state" / "engine_state.yml"
            if state.exists():
                try:
                    data = _yaml.safe_load(state.read_text()) or {}
                    if _is_active_state(data):
                        return _describe_state(data, wt.name)
                except Exception:
                    pass

    # 3. Isolated runs legado (runs/)
    runs_dir = project_root / "runs"
    if runs_dir.is_dir():
        for rd in sorted(
            [d for d in runs_dir.iterdir() if d.is_dir() and _is_cycle_dir(d)],
            key=_cycle_num, reverse=True,
        ):
            state = rd / "state" / "engine_state.yml"
            if state.exists():
                try:
                    data = _yaml.safe_load(state.read_text()) or {}
                    if _is_active_state(data):
                        return _describe_state(data, rd.name)
                except Exception:
                    pass

    return None


def _is_pid_alive(pid: int) -> bool:
    """Verifica se um PID está rodando."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cmd_run(args):
    """Bootstrap completo: cria projeto, provisiona ambiente, inicia e roda até MVP."""
    import sys
    sys.stdout.reconfigure(line_buffering=True)

    project_root = Path(args.project).resolve()
    _guard_engine_repo(project_root)

    # Verificar se já tem um ciclo ativo (em andamento, pausado ou bloqueado)
    # Deve rodar ANTES de criar worktree para não poluir em caso de erro.
    if not getattr(args, "force", False):
        active = _check_active_run(project_root)
        if active:
            from ft.engine import ui as _ui
            print(_ui.fail(f"Já existe um ciclo ativo: {active}"))
            print(_ui.warn("Use: ft continue"))
            print(_ui.dim("Para forçar novo ciclo mesmo assim: ft run . --force"))
            sys.exit(1)

    # --worktree: criar worktree git e redirecionar project_root para ele
    # Quando --worktree é usado, o worktree externo já É o ambiente isolado —
    # o engine não deve criar outro worktree interno (flag para suprimir).
    # Engine efetivo: CLI flag > último ciclo > "claude"
    _effective_engine = resolve_llm_engine(args) or _engine_from_last_cycle(project_root)

    worktree_name = getattr(args, "worktree", None)
    _outer_worktree_used = False
    if worktree_name:
        from ft.engine import ui as _ui
        wt_name = worktree_name if isinstance(worktree_name, str) and worktree_name != "True" else (
            f"cycle-{_next_cycle_num(project_root):02d}"
        )
        project_root = _setup_worktree(project_root, wt_name)
        _outer_worktree_used = True

    (project_root / "docs").mkdir(parents=True, exist_ok=True)

    # Commitar docs/ e process/ antes de iniciar (snapshot de conhecimento)
    from ft.engine.git_ops import commit_knowledge
    ok, detail = commit_knowledge(str(project_root), label="pré-run snapshot")
    print(f"  {detail}")

    run_mode = _resolve_run_mode(project_root)

    if run_mode == "continuous":
        # Continuous: state no diretório do projeto, cycle manager avança ciclos
        state_dir = project_root / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "engine_state.yml"
        print(f"  RunMode: continuous")
    else:
        # Isolated (default): cada run em worktree git isolado
        # Fallback para runs/<N>/ se projeto não tiver git ou não tiver commits
        git_ok = (project_root / ".git").exists()
        has_commits = False
        if git_ok:
            import subprocess as _sp
            has_commits = _sp.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_root, capture_output=True,
            ).returncode == 0

        if _outer_worktree_used:
            # --worktree já criou o ambiente isolado: project_root é o worktree.
            # Usar project_root diretamente como run_dir — sem aninhamento.
            run_dir = project_root
        elif git_ok and has_commits:
            # Modo isolado padrão: worktree externo em ~/.ft/worktrees/
            # Nome = cycle-NN, não o nome do engine (lição vibeos: 'claude' como
            # nome de ciclo quebrava parsing e não identifica nada). O ledger
            # .cycles preserva a numeração mesmo depois que o close remove o dir.
            next_num = _next_cycle_num(project_root)
            wt_name = f"cycle-{next_num:02d}"
            run_dir = _setup_worktree(project_root, wt_name)
            try:
                _ledger = _worktrees_home(project_root) / ".cycles"
                _nums = set(_ledger.read_text().split()) if _ledger.exists() else set()
                _nums.add(f"{next_num:02d}")
                _ledger.write_text("\n".join(sorted(_nums)) + "\n")
            except OSError:
                pass
        else:
            # Fallback sem git: diretório simples em ~/.ft/worktrees/
            wt_home = _worktrees_home(project_root)
            next_num = _next_cycle_num(project_root)
            engine_name = _effective_engine or "run"
            run_dir = wt_home / f"cycle-{next_num:02d}-{engine_name}"
            run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / "state").mkdir(parents=True, exist_ok=True)
        state_path = run_dir / "state" / "engine_state.yml"
        print(f"  RunMode: isolated → {run_dir}")

    # Resolver YAML do processo
    if args.process:
        process_path = Path(args.process)
        if not process_path.is_absolute():
            # Relativo ao project_root (não ao CWD do shell)
            candidate = project_root / process_path
            if candidate.exists():
                process_path = candidate
    else:
        process_path = find_process_yaml(project_root)
        if not process_path:
            # Tentar copiar template se --template fornecido
            template = getattr(args, "template", None)
            if template:
                process_path = copy_template(template, project_root)
                _copy_agents_md(project_root)  # bootstrap de projeto novo via ft run
            else:
                print("ERRO: Nenhum YAML de processo encontrado em ./process/")
                print("  Use: ft run . --template fast-track-v2")
                sys.exit(1)

    llm_model = resolve_llm_model(args)

    runner = StepRunner(
        process_path=process_path,
        state_path=state_path,
        project_root=project_root,
        llm_engine=_effective_engine,
        llm_model=llm_model,
        verbose=getattr(args, "verbose", False),
    )
    runner._bypass_human_gates = resolve_bypass_human_gates(args)

    # Provisionar ambiente SymGateway (se SYM_GATEWAY_PROJECT_KEY estiver definida)
    import os as _os
    if _os.environ.get("SYM_GATEWAY_PROJECT_KEY"):
        provision_environment(project_root=project_root)
        print(f"  Ambiente SymGateway provisionado")

    # Disparar hooks on_env_setup se definidos no environment.yml
    from ft.engine.hooks import run_hooks
    run_hooks("on_env_setup", str(project_root))

    import shutil

    # Copiar plano_de_voo do ciclo anterior se fornecido
    if args.from_project:
        src = Path(args.from_project).resolve() / "docs" / "plano_de_voo.md"
        dst_docs = project_root / "docs"
        dst = dst_docs / "plano_de_voo.md"
        if src.exists():
            if src.resolve() == dst.resolve():
                print(f"  plano_de_voo.md já está em docs/ (mesmo projeto)")
            else:
                dst_docs.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)
                print(f"  plano_de_voo.md copiado de {args.from_project}")
        else:
            print(f"  AVISO: --from-project fornecido mas plano_de_voo.md não encontrado em {src}")

    # ── Triage: classificar demanda bruta (--input) ──
    demand_input = getattr(args, "demand_input", None)
    if demand_input:
        from ft.engine.triage import (
            classify_demand, generate_hypothesis, present_triage,
            adapt_process, validate_adapted_yaml,
        )
        from ft.engine import ui as _ui

        src = Path(demand_input)
        if not src.exists():
            print(f"ERRO: arquivo de demanda não encontrado: {src}")
            sys.exit(1)

        demand_text = src.read_text()

        with _ui.Spinner("Analisando demanda"):
            classification = classify_demand(
                demand=demand_text,
                process_yaml_path=process_path,
                project_root=str(project_root),
                llm_engine=llm_engine or "claude",
            )

        print(present_triage(classification))

        # Se há perguntas → coletar respostas do stakeholder e re-classificar
        questions = classification.get("questions", [])
        if questions:
            print(f"\n  {_ui.BOLD_WHITE}Responda as perguntas (uma por linha, Enter vazio para pular):{_ui.RESET}")
            answers = []
            for i, q in enumerate(questions, 1):
                try:
                    answer = input(f"    {_ui.CYAN}{i}.{_ui.RESET} ")
                except (EOFError, KeyboardInterrupt):
                    break
                if answer.strip():
                    answers.append(f"Pergunta: {q}\nResposta: {answer.strip()}")

            if answers:
                # Re-classificar com as respostas incorporadas
                enriched_demand = demand_text + "\n\nRespostas do stakeholder:\n" + "\n".join(answers)
                with _ui.Spinner("Re-analisando com suas respostas"):
                    classification = classify_demand(
                        demand=enriched_demand,
                        process_yaml_path=process_path,
                        project_root=str(project_root),
                        llm_engine=llm_engine or "claude",
                    )
                print(present_triage(classification))

        # Se há requisitos de processo → propor adaptação ao stakeholder
        process_reqs = classification.get("process", {})
        if process_reqs.get("detected") and process_reqs.get("conflicts"):
            from ft.engine.triage import (
                diff_process, apply_renames_to_state, present_adaptation_proposal,
            )

            original_yaml = process_path.read_text()

            with _ui.Spinner("Elaborando proposta de adaptação do processo"):
                adapted = adapt_process(
                    process_yaml_path=process_path,
                    requirements=process_reqs.get("requirements", []),
                    conflicts=process_reqs.get("conflicts", []),
                    project_root=str(project_root),
                    llm_engine=llm_engine or "claude",
                )

            if adapted:
                valid, report = validate_adapted_yaml(adapted)
                if valid:
                    # Calcular diff e mostrar proposta
                    import yaml as _yaml
                    orig_data = _yaml.safe_load(original_yaml)
                    adapt_data = _yaml.safe_load(adapted)
                    proc_diff = diff_process(original_yaml, adapted)

                    print(present_adaptation_proposal(
                        proc_diff,
                        len(orig_data.get("nodes", [])),
                        len(adapt_data.get("nodes", [])),
                    ))

                    # Esperar aprovação do stakeholder
                    try:
                        choice = input(f"  {_ui.BOLD_WHITE}>{_ui.RESET} ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        choice = "reject"

                    if choice in ("approve", "ft approve", "sim", "s", "yes", "y", "1"):
                        process_path.write_text(adapted)
                        print(_ui.success("Processo adaptado e salvo"))

                        # Aplicar renomeações ao state se existir
                        if proc_diff["renames"] and state_path.exists():
                            apply_renames_to_state(state_path, proc_diff["renames"])
                            print(_ui.info(f"{len(proc_diff['renames'])} nodes renomeados no state"))

                        # Recriar runner com o novo YAML
                        runner = StepRunner(
                            process_path=process_path,
                            state_path=state_path,
                            project_root=project_root,
                            llm_engine=llm_engine,
                            llm_model=llm_model,
                            verbose=getattr(args, "verbose", False),
                        )
                    else:
                        print(_ui.info("Adaptação rejeitada — usando processo padrão"))
                else:
                    print(report)
                    print(_ui.warn("YAML adaptado não passou na validação — usando processo original"))
            else:
                print(_ui.warn("Não foi possível adaptar o processo — usando original"))

        # Salvar demanda original para validação de cobertura (só na primeira run)
        dst_docs = project_root / "docs"
        dst_docs.mkdir(parents=True, exist_ok=True)
        (dst_docs / "demanda.md").write_text(demand_text)
        print(_ui.info("Demanda original salva em docs/demanda.md"))

        # Gerar hipótese limpa (só produto) e salvar
        hypothesis = generate_hypothesis(classification)
        (dst_docs / "hipotese.md").write_text(hypothesis)
        print(_ui.success("Hipótese gerada a partir da demanda"))
        _normalize_hipotese(dst_docs / "hipotese.md", project_root, llm_engine=llm_engine or "claude")

    # Copiar e normalizar hipótese inicial se fornecida (pre-seed de ft.mdd.01.hipotese)
    elif args.hipotese:
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

    # Health check da API antes de começar
    _api_health_check(project_root)

    # Init + run MVP
    if run_mode == "continuous" and state_path.exists():
        # Continuous mode with existing state: advance cycle
        from ft.engine.cycle_manager import CycleManager
        cm = CycleManager(state_path)
        first = runner.graph.first_node()
        cm.advance_cycle(first_node=first.id)
        print(f"  Ciclo avançado: {cm.current_cycle()}")
        runner._fire_hooks("on_cycle_end")
    else:
        runner.init_state()
    runner.run(mode="mvp")


def main():
    parser = argparse.ArgumentParser(
        prog="ft",
        description="ft engine — motor deterministico de processos"
    )
    parser.add_argument("--process", "-p", help="Path do YAML de processo")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Modo verboso: mostra output do LLM no terminal")
    sub = parser.add_subparsers(dest="command")

    # init
    init = sub.add_parser("init", help="Inicializar/resetar estado do processo")
    add_llm_engine_flags(init)
    init.add_argument("name", nargs="?", help="Nome do projeto a criar (opcional — default: diretório atual)")
    init.add_argument("--template", "-t", help="Template de processo a copiar (ex: fast-track-v2)")

    # resume (alias: continue para backward compat)
    cont = sub.add_parser("resume", aliases=["continue"], help="Retomar o processo")
    add_llm_engine_flags(cont)
    cont.add_argument("--step", action="store_true", default=True, help="Avancar 1 step (default)")
    cont.add_argument("--sprint", action="store_true", help="Avancar ate fim da sprint")
    cont.add_argument("--auto", action="store_true", help="Avancar ate MVP (modo autonomo; PARA em human_gates)")
    cont.add_argument("--bypass-human-gates", action="store_true", dest="bypass_human_gates",
                      help="Pular human_gates automaticamente (LLM decide)")
    cont.add_argument("--cycle", help="Ciclo específico a retomar (ex: cycle-07)")

    # status
    st = sub.add_parser("status", help="Estado atual")
    add_llm_engine_flags(st)
    st.add_argument("--full", "-f", action="store_true", help="Mostrar grafo e artefatos")
    st.add_argument("--report", "-r", action="store_true", help="Relatório de tempo e tokens por node")

    # log — acompanhar o log LLM do ciclo ativo
    lg = sub.add_parser("log", help="Mostrar/acompanhar o log LLM do ciclo ativo")
    add_llm_engine_flags(lg)
    lg.add_argument("--follow", "-f", "--tail", action="store_true", dest="follow", help="Acompanhar em tempo real (troca de log sozinho quando o node muda)")
    lg.add_argument("--lines", "-n", type=int, default=None, help="Quantas linhas mostrar inicialmente (default: 30)")
    lg.set_defaults(_parser=lg)
    lg.add_argument("--raw", action="store_true", help="NDJSON cru, sem formatação")
    lg.add_argument("--markdown", "-m", action="store_true", help="Realça a saída por cor/ênfase: comandos bash, ferramentas, resposta e raciocínio")
    lg.add_argument("--path", action="store_true", help="Só imprimir o caminho do log ativo")

    # runs — tabela comparativa de todos os ciclos
    ru2 = sub.add_parser("runs", help="Tabela comparativa de todos os ciclos em runs/")
    ru2.add_argument("project", nargs="?", default=".", help="Diretório do projeto")

    # approve
    ap = sub.add_parser("approve", help="Aprovar artefato pendente")
    add_llm_engine_flags(ap)
    ap.add_argument("message", nargs="?", default=None,
                    help="Nota opcional registrada no log (ex: 'Aprovado após revisão')")
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

    # lint-process
    lp = sub.add_parser("lint-process", help="Lint semântico — detecta especificidades de projeto no YAML")
    add_llm_engine_flags(lp)

    # explore
    ex = sub.add_parser("explore", help="Modo exploração livre — pedidos ao LLM sem avançar o processo")
    add_llm_engine_flags(ex)
    ex.add_argument("request", nargs="?", help="Pedido ao LLM (entre aspas). Omitir para ver status.")
    ex.add_argument("--finish", action="store_true", help="Encerrar exploração e gerar relatório")
    ex.add_argument("--skip", action="store_true", help="Pular o node de exploração sem gerar relatório")

    # retry
    rt = sub.add_parser("retry", help="Retenta o node atual bloqueado sem aplicar correção")
    add_llm_engine_flags(rt)
    rt.add_argument("--auto", action="store_true", help="Continuar em modo MVP após retry")

    # fix
    fx = sub.add_parser("fix", help="Corrigir problema e desbloquear o ciclo")
    add_llm_engine_flags(fx)
    fx.add_argument("instruction", help="Descrição do que corrigir (entre aspas)")
    fx.add_argument("--auto", action="store_true", help="Continuar em modo MVP após correção")

    # close
    cl = sub.add_parser("close", help="Encerrar ciclo: merge artefatos, remover worktree")
    add_llm_engine_flags(cl)
    cl.add_argument("--keep-worktree", action="store_true", dest="keep_worktree",
                     help="Preservar o worktree no disco (não remover)")
    cl.add_argument("--force", action="store_true",
                     help="Encerrar mesmo se o ciclo não terminou")
    cl.add_argument("--merge", choices=["full", "docs", "selective", "none"],
                     help="Estratégia de merge (sem prompt interativo)")
    cl.add_argument("--merge-paths", dest="merge_paths",
                     help="Paths para merge selective (separados por espaço, entre aspas)")

    # abort
    ab = sub.add_parser("abort", help="Abortar ciclo: descarta worktree e branch sem merge")
    add_llm_engine_flags(ab)

    # cancel
    ca = sub.add_parser("cancel", help="Cancelar o run ativo com justificativa")
    add_llm_engine_flags(ca)
    ca.add_argument("reason", help="Motivo do cancelamento (entre aspas)")

    # setup-env
    se = sub.add_parser("setup-env", help="Provisionar CLAUDE.md e .claude/settings.local.json (lê SYM_GATEWAY_PROJECT_KEY e SYM_GATEWAY_ADMIN_KEY do ambiente)")
    se.add_argument("--project", help="Diretório do projeto (default: CWD ou raiz detectada)")

    # run — bootstrap completo: cria projeto, provisiona, init, continue --mvp
    ru = sub.add_parser("run", help="Bootstrap completo de um novo projeto até MVP")
    add_llm_engine_flags(ru)
    ru.add_argument("project", help="Caminho do diretório do projeto (criado se não existir)")
    ru.add_argument("--process", help="YAML do processo (default: FAST_TRACK_PROCESS_V2.yml)")
    ru.add_argument("--from-project", metavar="PATH",
                    help="Copiar plano_de_voo.md do ciclo anterior (para retomada de ciclo)")
    ru.add_argument("--hipotese", metavar="FILE",
                    help="Arquivo hipotese.md pré-escrito (pula ft.mdd.01.hipotese)")
    ru.add_argument("--input", metavar="FILE", dest="demand_input",
                    help="Demanda bruta do usuário (texto livre — o engine classifica produto vs processo)")
    ru.add_argument("--bypass-human-gates", action="store_true", dest="bypass_human_gates",
                    help="Pular human_gates automaticamente (LLM decide)")
    ru.add_argument("--force", action="store_true",
                    help="Forçar novo run mesmo se já houver um ativo")
    ru.add_argument("--template", "-t",
                    help="Template de processo a copiar (ex: fast-track-v2)")
    ru.add_argument("--worktree", metavar="NAME", nargs="?", const=True,
                    help="Rodar em git worktree isolado (cycle-NN-NAME). "
                         "NAME opcional: default = engine LLM ou 'run'")
    ru.add_argument("--auto", action="store_true",
                    help="Avançar em modo autônomo até MVP (PARA em human_gates; "
                         "para pular use --bypass-human-gates)")

    args = parser.parse_args()

    # Guard global: o ft opera sempre num repo de projeto, nunca no template/engine.
    # run/runs recebem o path do projeto como argumento e validam no próprio cmd_;
    # todos os demais comandos resolvem o projeto a partir do CWD.
    if args.command not in (None, "run", "runs"):
        _guard_engine_repo(find_project_root())

    try:
        if args.command == "init":
            cmd_init(args)
        elif args.command in ("resume", "continue"):
            cmd_continue(args)
        elif args.command == "status":
            cmd_status(args)
        elif args.command == "log":
            cmd_log(args)
        elif args.command == "approve":
            cmd_approve(args)
        elif args.command == "reject":
            cmd_reject(args)
        elif args.command == "graph":
            cmd_graph(args)
        elif args.command == "validate":
            cmd_validate(args)
        elif args.command == "lint-process":
            cmd_lint_process(args)
        elif args.command == "explore":
            cmd_explore(args)
        elif args.command == "retry":
            cmd_retry(args)
        elif args.command == "fix":
            cmd_fix(args)
        elif args.command == "close":
            cmd_close(args)
        elif args.command == "abort":
            cmd_abort(args)
        elif args.command == "cancel":
            cmd_cancel(args)
        elif args.command == "setup-env":
            cmd_setup_env(args)
        elif args.command == "run":
            cmd_run(args)
        elif args.command == "runs":
            cmd_runs(args)
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("\n  Interrompido pelo usuário.")
        sys.exit(130)
    except Exception as e:
        if os.environ.get("FT_DEBUG"):
            raise
        _print_crash(e)
        sys.exit(1)


def _print_crash(exc: Exception) -> None:
    """Formata exceção não-tratada de forma legível para o usuário."""
    import traceback
    from ft.engine.ui import BOLD_RED, RED, DIM, RESET, BOLD_WHITE, YELLOW

    # Extrair traceback
    tb = traceback.extract_tb(exc.__traceback__)

    print(f"\n{BOLD_RED}{'━' * 54}{RESET}")
    print(f"  {BOLD_RED}Erro inesperado{RESET}: {BOLD_WHITE}{type(exc).__name__}{RESET}")
    print(f"  {RED}{exc}{RESET}")
    print(f"{BOLD_RED}{'━' * 54}{RESET}")

    if tb:
        print(f"\n  {YELLOW}Onde aconteceu:{RESET}")
        # Mostrar apenas os frames relevantes (do ft/, não de stdlib)
        relevant = [f for f in tb if "/ft/" in f.filename or "test" in f.filename]
        frames = relevant if relevant else tb[-3:]
        for frame in frames:
            # Simplificar path: mostrar a partir de ft/
            path = frame.filename
            for prefix in ("/ft/", "/tests/"):
                idx = path.find(prefix)
                if idx >= 0:
                    path = path[idx + 1:]
                    break
            print(f"    {DIM}•{RESET} {path}:{frame.lineno} → {DIM}{frame.name}(){RESET}")
            if frame.line:
                print(f"      {DIM}{frame.line.strip()}{RESET}")

    print(f"\n  {DIM}Para o traceback completo, rode com: FT_DEBUG=1 ft ...{RESET}\n")


if __name__ == "__main__":
    main()
