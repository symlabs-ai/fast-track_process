#!/usr/bin/env python3
"""
ft.py — CLI do Fast Track

Ferramenta unificada para inicialização, validação e operações do processo.
Data-driven: lê FAST_TRACK_PROCESS.yml e schemas em runtime, sem hardcodar regras.

Uso:
    python process/fast_track/tools/ft.py init          # inicializa projeto
    python process/fast_track/tools/ft.py init --check   # valida sem criar nada
    python process/fast_track/tools/ft.py validate state  # valida ft_state.yml
    python process/fast_track/tools/ft.py validate artifacts  # artefatos existem
    python process/fast_track/tools/ft.py validate gate smoke  # pre-flight de gate
    python process/fast_track/tools/ft.py tokens status   # consumo de tokens
    python process/fast_track/tools/ft.py self-check      # consistência interna
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — derivados da raiz do projeto, nunca hardcoded
# ---------------------------------------------------------------------------

ENGINE_DIR = Path.home() / ".local" / "share" / "fast-track"


def find_project_root() -> Path:
    """Encontra a raiz do projeto. Prioridade: FT_PROJECT_ROOT env > subir até achar."""
    env_root = os.environ.get("FT_PROJECT_ROOT")
    if env_root:
        return Path(env_root)

    current = Path.cwd()
    # Procurar por project/state/ft_state.yml (dinâmico) ou process/ (template)
    for parent in [current, *current.parents]:
        if (parent / "project" / "state" / "ft_state.yml").exists():
            return parent
        if (parent / "process" / "fast_track" / "FAST_TRACK_PROCESS.yml").exists():
            return parent
    print("ERRO: Não encontrei a raiz do projeto.")
    print("  Procurei por: project/state/ft_state.yml ou process/fast_track/FAST_TRACK_PROCESS.yml")
    sys.exit(1)


def find_process_dir(root: Path) -> Path:
    """Encontra o diretório process/: local no projeto > engine global."""
    local = root / "process"
    if (local / "fast_track" / "FAST_TRACK_PROCESS.yml").exists():
        return local

    global_process = ENGINE_DIR / "process"
    if (global_process / "fast_track" / "FAST_TRACK_PROCESS.yml").exists():
        return global_process

    print("ERRO: process/ nao encontrado nem localmente nem na engine global.")
    print(f"  Local:  {local}")
    print(f"  Global: {global_process}")
    print("  Rode: ft update")
    sys.exit(1)


class ProjectPaths:
    """Paths canônicos do projeto, derivados da raiz + process dir."""

    def __init__(self, root: Path, process_dir: Path | None = None):
        self.root = root
        pdir = process_dir or find_process_dir(root)
        # Estático (processo — pode ser local ou engine global)
        self.process_yml = pdir / "fast_track" / "FAST_TRACK_PROCESS.yml"
        self.process_md = pdir / "fast_track" / "FAST_TRACK_PROCESS.md"
        self.ids_md = pdir / "fast_track" / "FAST_TRACK_IDS.md"
        self.schema_dir = pdir / "fast_track" / "schemas"
        self.state_schema = self.schema_dir / "ft_state.schema.json"
        self.tools_dir = pdir / "fast_track" / "tools"
        self.templates_dir = pdir / "fast_track" / "templates"
        self.symbiotes_dir = pdir / "symbiotes"
        # Dinâmico (projeto — sempre local)
        self.state_yml = root / "project" / "state" / "ft_state.yml"
        self.project_docs = root / "project" / "docs"
        self.project_state = root / "project" / "state"
        self.src = root / "src"
        self.tests = root / "tests"
        self.artifacts = root / "artifacts"
        self.metrics_yml = root / "project" / "docs" / "metrics.yml"


# ---------------------------------------------------------------------------
# Loader — lê processo e estado, sem hardcodar nada
# ---------------------------------------------------------------------------

def load_yaml_safe(path: Path) -> dict:
    """Parse YAML sem dependência de pyyaml (usa PyYAML se disponível, senão fallback)."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: parse simplificado para YAMLs flat do Fast Track
        return _parse_yaml_flat(path)


def _parse_yaml_flat(path: Path) -> dict:
    """Parser YAML minimalista para os YAMLs do Fast Track (sem nested complex)."""
    import re
    data = {}
    with open(path) as f:
        for line in f:
            line = line.split("#")[0].rstrip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(\w[\w.]*)\s*:\s*(.+)$', line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if val == "null":
                    data[key] = None
                elif val == "true":
                    data[key] = True
                elif val == "false":
                    data[key] = False
                elif val == "[]":
                    data[key] = []
                elif val == "{}":
                    data[key] = {}
                elif val.startswith('"') and val.endswith('"'):
                    data[key] = val[1:-1]
                else:
                    try:
                        data[key] = int(val)
                    except ValueError:
                        try:
                            data[key] = float(val)
                        except ValueError:
                            data[key] = val
    return data


def load_process(paths: ProjectPaths) -> dict:
    """Carrega FAST_TRACK_PROCESS.yml."""
    if not paths.process_yml.exists():
        print(f"ERRO: {paths.process_yml} não encontrado.")
        sys.exit(1)
    return load_yaml_safe(paths.process_yml)


def load_state(paths: ProjectPaths) -> dict:
    """Carrega ft_state.yml."""
    if not paths.state_yml.exists():
        return {}
    return load_yaml_safe(paths.state_yml)


def load_schema(paths: ProjectPaths) -> dict:
    """Carrega JSON Schema do state."""
    if not paths.state_schema.exists():
        return {}
    with open(paths.state_schema) as f:
        return json.load(f)


def extract_step_ids(process: dict) -> list[str]:
    """Extrai step IDs válidos do FAST_TRACK_PROCESS.yml (data-driven)."""
    ids = []
    for phase in process.get("phases", []):
        for step in phase.get("steps", []):
            if "id" in step:
                ids.append(step["id"])
    return ids


def extract_phase_ids(process: dict) -> list[str]:
    """Extrai phase IDs válidos do FAST_TRACK_PROCESS.yml."""
    return [p["id"] for p in process.get("phases", []) if "id" in p]


def extract_phase_for_step(process: dict) -> dict[str, str]:
    """Mapa step_id -> phase_id."""
    mapping = {}
    for phase in process.get("phases", []):
        phase_id = phase.get("id", "")
        for step in phase.get("steps", []):
            if "id" in step:
                mapping[step["id"]] = phase_id
    return mapping


def extract_step_artifacts(process: dict) -> dict[str, list[str]]:
    """Mapa step_id -> lista de outputs esperados."""
    mapping = {}
    for phase in process.get("phases", []):
        for step in phase.get("steps", []):
            step_id = step.get("id", "")
            outputs = step.get("outputs", [])
            if step_id and outputs:
                mapping[step_id] = outputs if isinstance(outputs, list) else [outputs]
    return mapping


# ---------------------------------------------------------------------------
# Report — output padronizado
# ---------------------------------------------------------------------------

class Report:
    """Acumula resultados de validação e formata output."""

    def __init__(self, title: str):
        self.title = title
        self.items: list[tuple[bool, str]] = []

    def ok(self, msg: str):
        self.items.append((True, msg))

    def fail(self, msg: str):
        self.items.append((False, msg))

    def passed(self) -> bool:
        return all(ok for ok, _ in self.items)

    def print(self):
        print(f"\n{'━' * 50}")
        print(f"  {self.title}")
        print(f"{'━' * 50}")
        for ok, msg in self.items:
            icon = "[ok]" if ok else "[FAIL]"
            print(f"  {icon} {msg}")
        print(f"{'─' * 50}")
        result = "PASS" if self.passed() else "BLOCK"
        print(f"  RESULTADO: {result}")
        print(f"{'━' * 50}\n")


# ---------------------------------------------------------------------------
# Command: init
# ---------------------------------------------------------------------------

def cmd_init(paths: ProjectPaths, check_only: bool = False):
    """Inicializa ou valida inicialização do projeto."""
    report = Report("INIT" + (" --check" if check_only else ""))
    process = load_process(paths)
    state = load_state(paths)

    # 1. Diretórios obrigatórios (data-driven do YAML)
    required_dirs = process.get("project_layout", {}).get("required_dirs", [])
    for d in required_dirs:
        dir_path = paths.root / d
        if dir_path.is_dir():
            report.ok(f"Diretorio: {d}")
        elif check_only:
            report.fail(f"Diretorio ausente: {d}")
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
            report.ok(f"Diretorio criado: {d}")

    # 2. Arquivos obrigatórios (data-driven do YAML)
    required_files = process.get("project_layout", {}).get("required_files", [])
    for f in required_files:
        file_path = paths.root / f
        if file_path.exists():
            report.ok(f"Arquivo: {f}")
        else:
            report.fail(f"Arquivo ausente: {f}")

    # 3. .gitignore — garantir que process/ está excluído no projeto do cliente
    gitignore_path = paths.root / ".gitignore"
    process_ignored = False
    if gitignore_path.exists():
        gi_content = gitignore_path.read_text()
        process_ignored = "\nprocess/\n" in gi_content or gi_content.startswith("process/\n")

    if process_ignored:
        report.ok(".gitignore: process/ excluido")
    elif check_only:
        report.fail(".gitignore: process/ nao esta excluido — rodar ft init para adicionar")
    else:
        # Adicionar exclusão de process/ ao .gitignore existente
        if gitignore_path.exists():
            gi_content = gitignore_path.read_text()
            if not gi_content.endswith("\n"):
                gi_content += "\n"
            gi_content += "\n# Fast Track — processo estatico (vem do template, nao evolui com o projeto)\n"
            gi_content += "# Atualizar copiando a pasta process/ de uma versao nova do template\n"
            gi_content += "process/\n"
            gitignore_path.write_text(gi_content)
        else:
            gitignore_path.write_text(
                "# Fast Track — processo estatico\nprocess/\n\n"
                "# Python\n__pycache__/\n*.pyc\n.venv/\n\n"
                "# Secrets\n.env\n.env.local\n\n"
                "# OS\n.DS_Store\n"
            )
        report.ok(".gitignore: process/ adicionado")

    # 4. Scaffold Clean/Hex em src/
    scaffold_dirs = [
        "src/domain",
        "src/domain/entities",
        "src/domain/validators",
        "src/application",
        "src/application/usecases",
        "src/application/ports",
        "src/infrastructure",
        "src/adapters",
        "src/adapters/cli",
    ]
    scaffold_hints = {
        "src": "# Raiz do pacote — nome do projeto definido em ft.plan.02.tech_stack\n",
        "src/domain": "# Domain — nucleo de negocio (PURO)\n# Sem I/O, sem imports de infrastructure ou adapters.\n# Classes base: EntityBase, ValueObjectBase (via ForgeBase)\n",
        "src/domain/entities": "# Entidades de dominio (herdam de EntityBase)\n",
        "src/domain/validators": "# Regras de negocio e invariantes de dominio\n",
        "src/application": "# Application — casos de uso e orquestracao\n# Pode importar: domain, ports (abstracoes)\n# NAO pode importar: infrastructure, adapters\n",
        "src/application/usecases": "# UseCases (herdam de UseCaseBase)\n# Executados via UseCaseRunner.run(), nunca .execute() direto\n",
        "src/application/ports": "# Ports — contratos/abstracoes (herdam de PortBase)\n# Definem interface, infrastructure implementa\n",
        "src/infrastructure": "# Infrastructure — implementacoes concretas de ports\n# Pode importar: domain, application\n# Persistencia, APIs externas, filesystem\n",
        "src/adapters": "# Adapters — interfaces externas (CLI, HTTP, WebUI)\n# Pode importar: domain, application\n# Acessa infrastructure somente via ports\n",
        "src/adapters/cli": "# CLI Adapter — ponto de entrada principal (CLI-first)\n# Todo UseCase deve ser validado via CLI antes de expor via HTTP\n",
    }
    scaffold_ok = True
    for sd in scaffold_dirs:
        init_file = paths.root / sd / "__init__.py"
        if init_file.exists():
            continue
        scaffold_ok = False
        if not check_only:
            (paths.root / sd).mkdir(parents=True, exist_ok=True)
            init_file.write_text(scaffold_hints.get(sd, ""))
    # Check src/__init__.py too
    src_init = paths.root / "src" / "__init__.py"
    if not src_init.exists():
        scaffold_ok = False
        if not check_only:
            src_init.write_text(scaffold_hints.get("src", ""))

    if scaffold_ok:
        report.ok("Scaffold Clean/Hex: src/ completo")
    elif check_only:
        report.fail("Scaffold Clean/Hex: src/ incompleto — rodar ft init para criar")
    else:
        report.ok("Scaffold Clean/Hex: src/ criado")

    # 5. Git remote — não aponta pro template?
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, cwd=paths.root
        )
        remotes = result.stdout
        if "symlabs-ai/fast-track_process" in remotes:
            report.fail("Git remote aponta para o template original — desvincular antes de prosseguir")
        elif not remotes.strip():
            report.fail("Nenhum git remote configurado")
        else:
            # Extrair URL do origin
            for line in remotes.splitlines():
                if line.startswith("origin") and "(fetch)" in line:
                    url = line.split()[1]
                    report.ok(f"Git remote: {url}")
                    break
    except FileNotFoundError:
        report.fail("Git nao encontrado no PATH")

    # 6. Ambiente (.venv e deps)
    venv_path = paths.root / ".venv"
    if venv_path.is_dir():
        report.ok("Virtualenv: .venv existe")
    elif check_only:
        report.fail("Virtualenv: .venv ausente — rodar setup_env.sh")
    else:
        setup_script = paths.root / "setup_env.sh"
        if setup_script.exists():
            print("  Executando setup_env.sh...")
            result = subprocess.run(
                ["bash", str(setup_script)],
                cwd=paths.root
            )
            if result.returncode == 0:
                report.ok("Ambiente configurado via setup_env.sh")
            else:
                report.fail("setup_env.sh falhou")
        else:
            report.fail("Virtualenv ausente e setup_env.sh nao encontrado")

    # 7. Versão sincronizada
    if state:
        process_version = process.get("version", "")
        state_version = state.get("version", "")
        if process_version == state_version:
            report.ok(f"Versao sincronizada: {process_version}")
        elif check_only:
            report.fail(f"Versao divergente: processo={process_version}, state={state_version}")
        else:
            # Atualizar versão no state
            _update_state_version(paths, process_version)
            report.ok(f"Versao sincronizada: {state_version} -> {process_version}")

    # 8. Token tracking
    if paths.metrics_yml.exists():
        report.ok("Token tracking: metrics.yml existe")
    elif check_only:
        report.fail("Token tracking: metrics.yml ausente — snapshot init nao gravado")
    else:
        # Gravar snapshot init
        token_tracker = paths.tools_dir / "token_tracker.py"
        if token_tracker.exists():
            result = subprocess.run(
                [sys.executable, str(token_tracker), "--project", str(paths.root),
                 "snapshot", "--step", "init"],
                capture_output=True, text=True, cwd=paths.root
            )
            if result.returncode == 0:
                report.ok("Token tracking: snapshot init gravado")
            else:
                report.fail(f"Token tracking: falha ao gravar snapshot — {result.stderr.strip()}")
        else:
            report.fail("Token tracking: token_tracker.py nao encontrado")

    # 9. Estado do projeto
    if state:
        phase = state.get("current_phase")
        next_step = state.get("next_step")
        if phase is None:
            report.ok("Estado: projeto novo (current_phase: null)")
        else:
            report.ok(f"Estado: fase={phase}, next_step={next_step}")

        # Detecção de PRD existente (sinaliza para o LLM decidir hyper-mode)
        prd_path = paths.project_docs / "PRD.md"
        if prd_path.exists() and prd_path.stat().st_size > 500:
            lines = len(prd_path.read_text().splitlines())
            report.ok(f"PRD.md detectado ({lines} linhas) — verificar se hyper-mode e aplicavel")
    else:
        report.fail("Estado: ft_state.yml ausente ou vazio")

    # 10. Claude Code agents — verificar se existem no global ou local
    agents_report = _check_agents(paths, check_only)
    for ok, msg in agents_report:
        if ok:
            report.ok(msg)
        else:
            report.fail(msg)

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Agent sync — Claude Code agents
# ---------------------------------------------------------------------------

# Frontmatter para cada symbiota quando instalado como agent no Claude Code
AGENT_CONFIGS = {
    "ft_gatekeeper": {
        "description": "Validador determinístico de stage gates do Fast Track. Lê arquivos, verifica condições binárias, retorna PASS ou BLOCK. Use este agente quando precisar validar um gate do processo Fast Track.",
        "tools": "Read, Grep, Glob, Bash",
    },
    "ft_acceptance": {
        "description": "Especialista em design de cenários de teste de aceitação por Value/Support Track. Gera matriz de cenários (happy/edge/error), identifica dados faltantes e demanda do stakeholder.",
        "tools": "Read, Grep, Glob",
    },
    "ft_coach": {
        "description": "Conduz MDD (hipótese, PRD), planning (task list) e feedback (retro, handoff). Delegado pelo ft_manager.",
        "tools": "Read, Grep, Glob, Write, Edit",
    },
    "forge_coder": {
        "description": "Implementa TDD (red-green), delivery (self-review, refactor, commit), smoke, E2E e acceptance tests. Orquestrado pelo ft_manager.",
        "tools": "Read, Grep, Glob, Write, Edit, Bash",
    },
    "ft_manager": {
        "description": "Orquestrador do processo Fast Track. Gerencia o fluxo completo, delega validações ao gatekeeper, interage com o stakeholder. Ponto de entrada de toda sessão.",
        "tools": "Read, Grep, Glob, Write, Edit, Bash, Agent",
    },
}


def _get_agent_locations(agent_name: str, paths: ProjectPaths) -> dict:
    """Verifica onde um agent existe (global, local, ou nenhum)."""
    home = Path.home()
    global_dir = home / ".claude" / "agents"
    local_dir = paths.root / ".claude" / "agents"

    result = {"global": None, "local": None, "source": None}

    # Global: pode ser pasta/SKILL.md ou arquivo .md direto
    global_folder = global_dir / agent_name / "SKILL.md"
    global_flat = global_dir / f"{agent_name}.md"
    if global_folder.exists():
        result["global"] = global_folder
    elif global_flat.exists():
        result["global"] = global_flat

    # Local: mesma lógica
    local_folder = local_dir / agent_name / "SKILL.md"
    local_flat = local_dir / f"{agent_name}.md"
    if local_folder.exists():
        result["local"] = local_folder
    elif local_flat.exists():
        result["local"] = local_flat

    # Source: prompt no processo
    source = paths.root / "process" / "symbiotes" / agent_name / "prompt.md"
    if source.exists():
        result["source"] = source

    return result


def _build_agent_skill(agent_name: str, source_path: Path) -> str:
    """Gera conteúdo do SKILL.md a partir do prompt do symbiota."""
    config = AGENT_CONFIGS.get(agent_name, {})
    description = config.get("description", f"Symbiota {agent_name} do Fast Track")
    tools = config.get("tools", "Read, Grep, Glob")

    # Ler o prompt original, pular o frontmatter YAML se existir
    content = source_path.read_text()
    # Remove frontmatter ---...---
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].lstrip("\n")

    frontmatter = f"""---
name: {agent_name}
description: >
  {description}
tools: {tools}
model: inherit
---

"""
    return frontmatter + content


def _check_agents(paths: ProjectPaths, check_only: bool) -> list[tuple[bool, str]]:
    """Verifica e opcionalmente sincroniza agents do Claude Code."""
    results = []
    symbiotes_dir = paths.symbiotes_dir

    if not symbiotes_dir.is_dir():
        results.append((False, "Agents: process/symbiotes/ nao encontrado"))
        return results

    # Listar todos os symbiotes que têm prompt.md
    agent_names = []
    for d in sorted(symbiotes_dir.iterdir()):
        if d.is_dir() and (d / "prompt.md").exists():
            agent_names.append(d.name)

    if not agent_names:
        results.append((True, "Agents: nenhum symbiota encontrado"))
        return results

    missing = []
    found = []
    outdated = []

    for name in agent_names:
        locs = _get_agent_locations(name, paths)
        installed = locs["global"] or locs["local"]

        if installed:
            found.append(name)
            # Verificar se está atualizado (comparar tamanho como heurística simples)
            if locs["source"]:
                expected = _build_agent_skill(name, locs["source"])
                current = installed.read_text()
                if len(expected) != len(current):
                    outdated.append(name)
        else:
            missing.append(name)

    if found and not missing and not outdated:
        results.append((True, f"Agents: {len(found)} instalados ({', '.join(found)})"))
    elif found:
        results.append((True, f"Agents instalados: {', '.join(found)}"))

    if outdated:
        if check_only:
            results.append((False, f"Agents desatualizados: {', '.join(outdated)} — rodar ft init para sincronizar"))
        else:
            for name in outdated:
                locs = _get_agent_locations(name, paths)
                target = locs["global"] or locs["local"]
                content = _build_agent_skill(name, locs["source"])
                target.write_text(content)
            results.append((True, f"Agents sincronizados: {', '.join(outdated)}"))

    if missing:
        if check_only:
            results.append((False, f"Agents ausentes: {', '.join(missing)} — rodar ft init para criar"))
        else:
            # Criar no global por padrão (mesmo local do ft_gatekeeper existente)
            global_dir = Path.home() / ".claude" / "agents"
            for name in missing:
                locs = _get_agent_locations(name, paths)
                if locs["source"]:
                    agent_dir = global_dir / name
                    agent_dir.mkdir(parents=True, exist_ok=True)
                    content = _build_agent_skill(name, locs["source"])
                    (agent_dir / "SKILL.md").write_text(content)
            results.append((True, f"Agents criados em ~/.claude/agents/: {', '.join(missing)}"))

    return results


def _update_state_version(paths: ProjectPaths, new_version: str):
    """Atualiza campo version no ft_state.yml."""
    content = paths.state_yml.read_text()
    import re
    content = re.sub(
        r'^(version:\s*)"[^"]*"',
        f'\\1"{new_version}"',
        content,
        flags=re.MULTILINE
    )
    paths.state_yml.write_text(content)


# ---------------------------------------------------------------------------
# Command: validate state
# ---------------------------------------------------------------------------

def cmd_validate_state(paths: ProjectPaths):
    """Valida ft_state.yml contra o schema e o processo."""
    report = Report("VALIDATE STATE")
    process = load_process(paths)
    state = load_state(paths)
    schema = load_schema(paths)

    if not state:
        report.fail("ft_state.yml ausente ou vazio")
        report.print()
        return 1

    valid_step_ids = extract_step_ids(process)
    valid_phase_ids = extract_phase_ids(process)
    step_to_phase = extract_phase_for_step(process)

    # 1. Validação por JSON Schema (se jsonschema disponível)
    schema_validated = False
    if schema:
        try:
            import jsonschema
            try:
                jsonschema.validate(instance=state, schema=schema)
                report.ok("JSON Schema: valido")
                schema_validated = True
            except jsonschema.ValidationError as e:
                report.fail(f"JSON Schema: {e.message}")
                # Campo e valor inválido
                if e.path:
                    field = ".".join(str(p) for p in e.path)
                    report.fail(f"  Campo: {field} = {e.instance}")
        except ImportError:
            report.ok("JSON Schema: jsonschema nao instalado, pulando (instale com pip install jsonschema)")

    # 2. Step IDs válidos (data-driven)
    next_step = state.get("next_step")
    if next_step is not None:
        if next_step in valid_step_ids:
            report.ok(f"next_step valido: {next_step}")
        else:
            report.fail(f"next_step invalido: '{next_step}' — nao existe em FAST_TRACK_PROCESS.yml")

    last_step = state.get("last_completed_step")
    if last_step is not None:
        if last_step in valid_step_ids:
            report.ok(f"last_completed_step valido: {last_step}")
        else:
            report.fail(f"last_completed_step invalido: '{last_step}'")

    completed = state.get("completed_steps", [])
    invalid_completed = [s for s in completed if s not in valid_step_ids]
    if invalid_completed:
        report.fail(f"completed_steps contem IDs invalidos: {invalid_completed}")
    else:
        report.ok(f"completed_steps: {len(completed)} IDs, todos validos")

    # 3. Consistência phase vs. next_step
    current_phase = state.get("current_phase")
    if current_phase and next_step:
        expected_phase = step_to_phase.get(next_step)
        if expected_phase and current_phase != expected_phase:
            # Pode ser válido em transições de fase, então só avisa
            report.ok(f"Phase/step: {current_phase} / {next_step} (fase esperada: {expected_phase})")
        else:
            report.ok(f"Phase/step consistentes: {current_phase} / {next_step}")

    # 4. Cobertura: min <= desired
    min_cov = state.get("min_coverage", 0)
    desired_cov = state.get("desired_coverage", 0)
    if min_cov <= desired_cov:
        report.ok(f"Cobertura: min={min_cov}% <= desired={desired_cov}%")
    else:
        report.fail(f"Cobertura inconsistente: min={min_cov}% > desired={desired_cov}%")

    # 5. Versão sincronizada
    process_version = process.get("version", "")
    state_version = state.get("version", "")
    if process_version == state_version:
        report.ok(f"Versao: {state_version}")
    else:
        report.fail(f"Versao divergente: processo={process_version}, state={state_version}")

    # 6. Blocked consistency
    blocked = state.get("blocked", False)
    reason = state.get("blocked_reason")
    if blocked and not reason:
        report.fail("blocked=true mas blocked_reason esta vazio")
    elif not blocked and reason:
        report.fail("blocked=false mas blocked_reason preenchido")
    else:
        report.ok(f"Blocked: {blocked}" + (f" — {reason}" if reason else ""))

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Command: validate artifacts
# ---------------------------------------------------------------------------

def cmd_validate_artifacts(paths: ProjectPaths):
    """Valida que artefatos esperados existem nos paths canônicos."""
    report = Report("VALIDATE ARTIFACTS")
    process = load_process(paths)
    state = load_state(paths)

    if not state:
        report.fail("ft_state.yml ausente")
        report.print()
        return 1

    completed = state.get("completed_steps", [])
    step_artifacts = extract_step_artifacts(process)

    for step_id in completed:
        outputs = step_artifacts.get(step_id, [])
        for output in outputs:
            # Substituir padrões dinâmicos (cycle-XX, sprint-XX)
            cycle = state.get("current_cycle", "cycle-01")
            output_resolved = output.replace("cycle-XX", cycle)

            # Ignorar outputs que não são paths de arquivo (ex: "decisão: approved")
            if "/" not in output_resolved:
                continue

            # Limpar sufixos descritivos (ex: "project/docs/PRD.md completo")
            path_candidate = output_resolved.split()[0]
            artifact_path = paths.root / path_candidate

            if artifact_path.exists():
                report.ok(f"{step_id} -> {path_candidate}")
            else:
                report.fail(f"{step_id} -> {path_candidate} AUSENTE")

    if not completed:
        report.ok("Nenhum step concluido — sem artefatos esperados")

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Command: validate gate
# ---------------------------------------------------------------------------

def cmd_validate_gate(paths: ProjectPaths, gate_id: str):
    """Pre-flight de gate — verifica pré-condições mecânicas."""
    report = Report(f"VALIDATE GATE: {gate_id}")
    state = load_state(paths)

    if not state:
        report.fail("ft_state.yml ausente")
        report.print()
        return 1

    cycle = state.get("current_cycle", "cycle-01")

    if gate_id == "smoke":
        # Pre-flight para gate.smoke
        gate_log = state.get("gate_log", {})
        # Todas as tasks done devem ter gate.delivery PASS
        tasks_without_gate = []
        for task_id, gates in gate_log.items():
            if "gate.delivery" not in gates:
                tasks_without_gate.append(task_id)
            elif gates["gate.delivery"] != "PASS":
                # Verificar se tem retry PASS
                if gates.get("gate.delivery.retry") != "PASS":
                    tasks_without_gate.append(task_id)

        if tasks_without_gate:
            report.fail(f"Tasks sem gate.delivery PASS: {tasks_without_gate}")
        elif not gate_log:
            report.fail("gate_log vazio — nenhuma task validada")
        else:
            report.ok(f"Todas as {len(gate_log)} tasks tem gate.delivery PASS")

        # Smoke report path
        smoke_path = paths.project_docs / f"smoke-{cycle}.md"
        report.ok(f"Path esperado do smoke report: {smoke_path.relative_to(paths.root)}")

    elif gate_id == "e2e":
        # Pre-flight para gate.e2e
        e2e_dir = paths.tests / "e2e" / cycle
        run_all = e2e_dir / "run-all.sh"
        if run_all.exists():
            report.ok(f"run-all.sh encontrado: {run_all.relative_to(paths.root)}")
        else:
            report.fail(f"run-all.sh ausente: {run_all.relative_to(paths.root)}")

    elif gate_id == "acceptance":
        interface_type = state.get("interface_type", "cli_only")
        if interface_type == "cli_only":
            report.ok("interface_type=cli_only — acceptance gate nao aplicavel")
        else:
            report.ok(f"interface_type={interface_type} — acceptance gate obrigatorio")
            acceptance_dir = paths.tests / "acceptance" / cycle
            if acceptance_dir.is_dir():
                report.ok(f"Diretorio de testes: {acceptance_dir.relative_to(paths.root)}")
            else:
                report.fail(f"Diretorio ausente: {acceptance_dir.relative_to(paths.root)}")

    elif gate_id == "handoff":
        spec_path = paths.project_docs / "SPEC.md"
        changelog = paths.root / "CHANGELOG.md"
        backlog = paths.root / "BACKLOG.md"
        for artifact, path in [("SPEC.md", spec_path), ("CHANGELOG.md", changelog), ("BACKLOG.md", backlog)]:
            if path.exists():
                report.ok(f"{artifact} existe")
            else:
                report.fail(f"{artifact} ausente")

    else:
        report.fail(f"Gate desconhecido: {gate_id}")
        report.ok("Gates validos: smoke, e2e, acceptance, handoff")

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Command: validate integration
# ---------------------------------------------------------------------------

def cmd_validate_integration(paths: ProjectPaths):
    """Verifica integração real: mock audit, dead code, wiring."""
    report = Report("VALIDATE INTEGRATION")
    state = load_state(paths)
    import re

    src = paths.src
    if not src.is_dir():
        report.fail("src/ nao existe")
        report.print()
        return 1

    # Coletar arquivos Python em cada camada
    usecases_dir = src / "application" / "usecases"
    ports_dir = src / "application" / "ports"
    infra_dir = src / "infrastructure"
    adapters_dir = src / "adapters"

    def find_py_files(d: Path) -> list[Path]:
        if not d.is_dir():
            return []
        return [f for f in d.rglob("*.py") if f.name != "__init__.py" and f.stat().st_size > 0]

    def find_classes(filepath: Path, base_pattern: str) -> list[str]:
        """Encontra nomes de classes que herdam de base_pattern."""
        content = filepath.read_text()
        return re.findall(rf'class\s+(\w+)\s*\([^)]*{base_pattern}[^)]*\)', content)

    def find_all_classes(filepath: Path) -> list[str]:
        """Encontra todos os nomes de classes."""
        content = filepath.read_text()
        return re.findall(r'class\s+(\w+)\s*[:\(]', content)

    def is_imported_in(class_name: str, search_dir: Path) -> bool:
        """Verifica se class_name aparece em algum arquivo do diretório."""
        for f in search_dir.rglob("*.py"):
            if class_name in f.read_text():
                return True
        return False

    # --- 1. Mock Audit: Ports sem implementação real ---
    port_files = find_py_files(ports_dir)
    infra_files = find_py_files(infra_dir)

    if port_files:
        # Ler todas as classes de ports
        port_classes = []
        for pf in port_files:
            port_classes.extend(find_all_classes(pf))

        if port_classes:
            # Ler conteúdo de infrastructure para verificar implementações
            infra_content = ""
            for inf in infra_files:
                infra_content += inf.read_text() + "\n"

            ports_without_impl = []
            for pc in port_classes:
                # Verificar se alguma classe em infrastructure referencia este port
                if pc not in infra_content:
                    ports_without_impl.append(pc)

            if ports_without_impl:
                report.fail(f"Ports sem implementacao real em infrastructure/: {ports_without_impl}")
            else:
                report.ok(f"Mock audit: {len(port_classes)} ports, todos com implementacao real")
        else:
            report.ok("Mock audit: nenhuma classe de port encontrada (scaffold vazio)")
    else:
        report.ok("Mock audit: ports/ vazio (scaffold)")

    # --- 2. Dead code: UseCases nao invocados por nenhum adapter ---
    usecase_files = find_py_files(usecases_dir)
    adapter_files = find_py_files(adapters_dir)

    if usecase_files and adapter_files:
        usecase_classes = []
        for uf in usecase_files:
            usecase_classes.extend(find_all_classes(uf))

        adapter_content = ""
        for af in adapter_files:
            adapter_content += af.read_text() + "\n"

        dead_usecases = []
        for uc in usecase_classes:
            if uc not in adapter_content:
                dead_usecases.append(uc)

        if dead_usecases:
            report.fail(f"UseCases nao invocados por nenhum adapter: {dead_usecases}")
        else:
            report.ok(f"Dead code: {len(usecase_classes)} usecases, todos referenciados por adapters")
    elif usecase_files and not adapter_files:
        report.fail("UseCases existem mas nenhum adapter os invoca (adapters/ vazio)")
    else:
        report.ok("Dead code: usecases/ vazio (scaffold)")

    # --- 3. Adapters soltos: nao referenciados no entrypoint ---
    if adapter_files:
        # Procurar entrypoints comuns
        entrypoints = []
        for name in ["main.py", "cli.py", "app.py", "__main__.py"]:
            ep = src / name
            if ep.exists():
                entrypoints.append(ep)
            # Também verificar em adapters/cli/
            for sub in adapters_dir.rglob(name):
                entrypoints.append(sub)

        if entrypoints:
            entry_content = ""
            for ep in entrypoints:
                entry_content += ep.read_text() + "\n"

            adapter_classes = []
            for af in adapter_files:
                adapter_classes.extend(find_all_classes(af))

            disconnected = [ac for ac in adapter_classes if ac not in entry_content]
            if disconnected:
                report.fail(f"Adapters nao conectados no wiring/entrypoint: {disconnected}")
            else:
                report.ok(f"Wiring: {len(adapter_classes)} adapters, todos conectados")
        else:
            report.ok("Wiring: nenhum entrypoint encontrado (verificar manualmente)")
    else:
        report.ok("Wiring: adapters/ vazio (scaffold)")

    # --- 4. Interface type enforcement ---
    interface_type = state.get("interface_type", "cli_only")
    if interface_type != "cli_only":
        # Verificar que existe pelo menos 1 adapter de UI/HTTP
        has_http = (adapters_dir / "http").is_dir() and find_py_files(adapters_dir / "http")
        has_ui = (adapters_dir / "ui").is_dir() and find_py_files(adapters_dir / "ui")
        has_web = (adapters_dir / "web").is_dir() and find_py_files(adapters_dir / "web")

        if interface_type == "api" and not has_http:
            report.fail("interface_type=api mas nenhum adapter HTTP encontrado em src/adapters/http/")
        elif interface_type == "ui" and not (has_ui or has_web):
            report.fail("interface_type=ui mas nenhum adapter UI encontrado em src/adapters/ui/ ou src/adapters/web/")
        elif interface_type == "mixed" and not has_http:
            report.fail("interface_type=mixed mas nenhum adapter HTTP encontrado")
        elif interface_type == "mixed" and not (has_ui or has_web):
            report.fail("interface_type=mixed mas nenhum adapter UI encontrado")
        else:
            report.ok(f"Interface enforcement: {interface_type} — adapters presentes")

        # Verificar design system no tech_stack.md
        tech_stack = paths.project_docs / "tech_stack.md"
        if tech_stack.exists():
            ts_content = tech_stack.read_text()
            if "design system" in ts_content.lower() or "Design System" in ts_content:
                report.ok("Design system: referenciado no tech_stack.md")
            else:
                report.fail("Design system: nao encontrado no tech_stack.md (obrigatorio quando interface_type != cli_only)")
        else:
            report.ok("Design system: tech_stack.md ainda nao criado (sera verificado no gate)")

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Command: self-check
# ---------------------------------------------------------------------------

def cmd_self_check(paths: ProjectPaths):
    """Verifica consistência entre CLI, schemas e processo."""
    report = Report("SELF-CHECK")
    process = load_process(paths)
    schema = load_schema(paths)

    # 1. Schema existe
    if paths.state_schema.exists():
        report.ok("Schema ft_state.schema.json existe")
    else:
        report.fail("Schema ft_state.schema.json ausente")

    # 2. Step IDs no processo
    step_ids = extract_step_ids(process)
    if step_ids:
        report.ok(f"Processo define {len(step_ids)} steps")
    else:
        report.fail("Processo nao define nenhum step")

    # 3. Phases no schema incluem todas as do processo
    if schema:
        schema_phases = schema.get("properties", {}).get("current_phase", {}).get("enum", [])
        process_phases = extract_phase_ids(process)
        missing = [p for p in process_phases if p not in schema_phases]
        if missing:
            report.fail(f"Schema nao inclui phases do processo: {missing}")
        else:
            report.ok(f"Schema cobre todas as {len(process_phases)} phases")

    # 4. FAST_TRACK_IDS.md existe
    if paths.ids_md.exists():
        report.ok("FAST_TRACK_IDS.md existe")
    else:
        report.fail("FAST_TRACK_IDS.md ausente")

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Command: generate
# ---------------------------------------------------------------------------

def cmd_generate_ids(paths: ProjectPaths):
    """Gera FAST_TRACK_IDS.md a partir do FAST_TRACK_PROCESS.yml."""
    process = load_process(paths)
    phases = process.get("phases", [])

    lines = [
        "# Fast Track — Step IDs",
        "",
        "> Convenção: `ft.<fase>.<numero>.<nome_curto>`",
        "",
        "> **GERADO AUTOMATICAMENTE** por `ft.py generate ids` — não editar manualmente.",
        "",
    ]

    step_count = 0
    table_rows = []

    for phase in phases:
        phase_id = phase.get("id", "")
        phase_title = phase.get("title", "")
        steps = phase.get("steps", [])

        lines.append(f"## {phase_title}")
        lines.append("")
        for step in steps:
            step_id = step.get("id", "")
            lines.append(f"- `{step_id}`")
            step_count += 1
            table_rows.append((step_count, step_id, phase_title.split(" — ")[0] if " — " in phase_title else phase_title, step.get("title", "")))
        lines.append("")

    # Orchestration nodes (lê do YAML se existir)
    orch_nodes = process.get("orchestration_nodes", [])
    if orch_nodes:
        lines.append("---")
        lines.append("")
        lines.append("## Orchestration Nodes (paralelização)")
        lines.append("")
        lines.append("> Nós de orquestração para execução paralela de tasks e controle de sprint. Não são steps — são nós de decisão, sincronização e orquestração no flow.")
        lines.append("")
        lines.append("| Node ID | Tipo | Descrição |")
        lines.append("|---------|------|-----------|")
        for node in orch_nodes:
            lines.append(f"| `{node.get('id', '')}` | {node.get('type', '')} | {node.get('description', '')} |")
        lines.append("")

    # Summary table
    lines.append("---")
    lines.append("")
    lines.append("## Resumo")
    lines.append("")
    lines.append("| # | Step ID | Fase | Descrição |")
    lines.append("|---|---------|------|-----------|")
    for num, step_id, phase_name, title in table_rows:
        lines.append(f"| {num} | `{step_id}` | {phase_name} | {title} |")
    lines.append("")

    content = "\n".join(lines)
    paths.ids_md.write_text(content)
    print(f"Gerado: {paths.ids_md.relative_to(paths.root)} ({step_count} steps)")
    return 0


def cmd_generate_check(paths: ProjectPaths):
    """Verifica consistência entre YAML e MD do processo."""
    report = Report("GENERATE CHECK (YAML <-> MD)")
    process = load_process(paths)

    yaml_step_ids = extract_step_ids(process)
    yaml_phase_ids = extract_phase_ids(process)

    # Verificar FAST_TRACK_IDS.md
    if paths.ids_md.exists():
        ids_content = paths.ids_md.read_text()
        missing_in_ids = [s for s in yaml_step_ids if f"`{s}`" not in ids_content]
        extra_in_ids = []
        import re
        md_ids = re.findall(r'`(ft\.\w+\.\d+\.\w+)`', ids_content)
        extra_in_ids = [s for s in md_ids if s not in yaml_step_ids]

        if missing_in_ids:
            report.fail(f"FAST_TRACK_IDS.md faltam steps do YAML: {missing_in_ids}")
        else:
            report.ok(f"FAST_TRACK_IDS.md: todos os {len(yaml_step_ids)} steps presentes")

        if extra_in_ids:
            report.fail(f"FAST_TRACK_IDS.md contem steps que nao existem no YAML: {extra_in_ids}")
        else:
            report.ok("FAST_TRACK_IDS.md: sem steps extras")
    else:
        report.fail("FAST_TRACK_IDS.md ausente — rodar ft generate ids")

    # Verificar FAST_TRACK_PROCESS.md
    if paths.process_md.exists():
        md_content = paths.process_md.read_text()
        import re
        md_step_ids = re.findall(r'####\s+(ft\.\w+\.\d+\.\w+)', md_content)

        missing_in_md = [s for s in yaml_step_ids if s not in md_step_ids]
        extra_in_md = [s for s in md_step_ids if s not in yaml_step_ids]

        if missing_in_md:
            report.fail(f"FAST_TRACK_PROCESS.md faltam steps: {missing_in_md}")
        else:
            report.ok(f"FAST_TRACK_PROCESS.md: todos os {len(yaml_step_ids)} steps presentes")

        if extra_in_md:
            report.fail(f"FAST_TRACK_PROCESS.md contem steps extras: {extra_in_md}")
        else:
            report.ok("FAST_TRACK_PROCESS.md: sem steps extras")
    else:
        report.fail("FAST_TRACK_PROCESS.md ausente")

    # Verificar SUMMARY_FOR_AGENTS.md
    summary_path = paths.root / "process" / "fast_track" / "SUMMARY_FOR_AGENTS.md"
    if summary_path.exists():
        summary_content = summary_path.read_text()
        import re
        summary_ids = re.findall(r'(ft\.\w+\.\d+\.\w+)', summary_content)
        missing_in_summary = [s for s in yaml_step_ids if s not in summary_ids]
        if missing_in_summary:
            report.fail(f"SUMMARY_FOR_AGENTS.md faltam steps: {missing_in_summary}")
        else:
            report.ok(f"SUMMARY_FOR_AGENTS.md: todos os {len(yaml_step_ids)} steps referenciados")
    else:
        report.fail("SUMMARY_FOR_AGENTS.md ausente")

    report.print()
    return 0 if report.passed() else 1


# ---------------------------------------------------------------------------
# Command: tokens (delega para token_tracker.py)
# ---------------------------------------------------------------------------

def cmd_tokens(paths: ProjectPaths, args: list[str]):
    """Proxy para token_tracker.py."""
    tracker = paths.tools_dir / "token_tracker.py"
    if not tracker.exists():
        print(f"ERRO: {tracker} nao encontrado.")
        return 1
    result = subprocess.run(
        [sys.executable, str(tracker), "--project", str(paths.root)] + args,
        cwd=paths.root
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Main — argparse
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="ft",
        description="Fast Track CLI — inicialização, validação e operações do processo"
    )
    sub = parser.add_subparsers(dest="command")

    # init
    init_parser = sub.add_parser("init", help="Inicializar projeto")
    init_parser.add_argument("--check", action="store_true",
                             help="Apenas validar, sem criar/modificar nada")

    # validate
    validate_parser = sub.add_parser("validate", help="Validar estado, artefatos ou gates")
    validate_sub = validate_parser.add_subparsers(dest="target")
    validate_sub.add_parser("state", help="Validar ft_state.yml")
    validate_sub.add_parser("artifacts", help="Validar artefatos esperados")
    validate_sub.add_parser("integration", help="Mock audit, dead code, wiring")
    gate_parser = validate_sub.add_parser("gate", help="Pre-flight de gate")
    gate_parser.add_argument("gate_id", help="Gate: smoke, e2e, acceptance, handoff")

    # tokens
    tokens_parser = sub.add_parser("tokens", help="Token tracking")
    tokens_parser.add_argument("tokens_args", nargs="*", help="Argumentos do token_tracker")

    # generate
    generate_parser = sub.add_parser("generate", help="Gerar artefatos derivados do YAML")
    generate_sub = generate_parser.add_subparsers(dest="artifact")
    generate_sub.add_parser("ids", help="Gerar FAST_TRACK_IDS.md")
    generate_sub.add_parser("check", help="Verificar consistencia YAML <-> MD")

    # self-check
    sub.add_parser("self-check", help="Verificar consistencia interna da CLI")

    args = parser.parse_args()
    root = find_project_root()
    paths = ProjectPaths(root)

    if args.command == "init":
        sys.exit(cmd_init(paths, check_only=args.check))
    elif args.command == "validate":
        if args.target == "state":
            sys.exit(cmd_validate_state(paths))
        elif args.target == "artifacts":
            sys.exit(cmd_validate_artifacts(paths))
        elif args.target == "integration":
            sys.exit(cmd_validate_integration(paths))
        elif args.target == "gate":
            sys.exit(cmd_validate_gate(paths, args.gate_id))
        else:
            validate_parser.print_help()
    elif args.command == "tokens":
        sys.exit(cmd_tokens(paths, args.tokens_args or []))
    elif args.command == "generate":
        if args.artifact == "ids":
            sys.exit(cmd_generate_ids(paths))
        elif args.artifact == "check":
            sys.exit(cmd_generate_check(paths))
        else:
            generate_parser.print_help()
    elif args.command == "self-check":
        sys.exit(cmd_self_check(paths))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
