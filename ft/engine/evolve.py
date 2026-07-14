"""ft evolve — evolução de processo em paralelo ao ciclo.

Deriva melhorias de processo a partir do contexto de um ciclo (ativo ou
arquivado) SEM avançar nenhum step. O LLM trabalha num workspace descartável
em ``runtime_home`` (nunca em worktrees/, então um evolve jamais aparece como
ciclo); as mudanças só chegam aos alvos reais — fork local ``.ft/process/`` na
raiz do projeto e/ou template global do engine — via apply determinístico,
depois de todos os YAMLs staged validarem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import shutil

import yaml

from ft.engine import paths
from ft.engine.layout import read_manifest, validate_template_is_pristine


class EvolveError(ValueError):
    """Erro de preparação, validação ou aplicação de um evolve."""


# Status de ciclo que NÃO representam execução acionável (espelha o CLI; o
# engine não pode importar ft.cli).
_TERMINAL_STATUSES = {"done", "completed", "failed", "aborted", "cancelled", "canceled"}

# Artefatos de ciclo relevantes para derivar melhorias de processo.
_CONTEXT_GLOBS = ("*.md", "*.yml", "*.yaml", "*.json")
_CONTEXT_SKIP_DIRS = {"screenshots", "llm_logs", "state", "node_modules"}


def _engine_root() -> Path:
    """Raiz do checkout do engine (onde templates/ vive)."""
    return Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class EvolveTargets:
    """Alvos reais de uma evolução — os diretórios que o apply pode tocar."""

    project_dir: Path | None = None            # <root>/.ft/process
    global_dirs: dict[str, Path] = field(default_factory=dict)  # nome → templates/<nome>

    @property
    def labels(self) -> list[str]:
        result = []
        if self.project_dir is not None:
            result.append("project")
        result.extend(f"global:{name}" for name in sorted(self.global_dirs))
        return result


@dataclass(frozen=True)
class EvolveWorkspace:
    root: Path
    targets: EvolveTargets
    context_label: str

    @property
    def process_file(self) -> Path:
        return self.root / "process" / "process.yml"

    @property
    def state_file(self) -> Path:
        return self.root / "state" / "engine_state.yml"

    @property
    def context_dir(self) -> Path:
        return self.root / "context"

    @property
    def targets_dir(self) -> Path:
        return self.root / "targets"

    @property
    def report_dir(self) -> Path:
        return self.root / "report"

    @property
    def staged_project_dir(self) -> Path:
        return self.targets_dir / "project"

    def staged_global_dir(self, name: str) -> Path:
        return self.targets_dir / "global" / name


@dataclass(frozen=True)
class StagedChange:
    target: str        # "project" ou "global:<nome>"
    status: str        # "added" | "modified" | "removed"
    relative: str      # path relativo dentro do alvo
    staged: Path | None
    real: Path | None


def next_workspace_dir(project_root: str | Path) -> Path:
    """Próximo diretório evolve-NN livre em runtime_home/evolve."""
    home = paths.evolve_home(project_root)
    existing = []
    if home.is_dir():
        for item in home.iterdir():
            name = item.name
            if item.is_dir() and name.startswith("evolve-"):
                suffix = name[len("evolve-"):].split("-")[0]
                if suffix.isdigit():
                    existing.append(int(suffix))
    return home / f"evolve-{(max(existing) + 1 if existing else 1):02d}"


# ---------------------------------------------------------------------------
# Contexto do ciclo
# ---------------------------------------------------------------------------

def _state_is_active(state_file: Path) -> bool:
    try:
        data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(data, dict):
        return False
    if not data.get("current_node"):
        return False
    return data.get("node_status", "") not in _TERMINAL_STATUSES


def find_cycle_context(
    project_root: str | Path,
    cycle: str | None = None,
) -> tuple[str, Path | None, Path | None]:
    """Localiza a fonte de contexto: (label, dir_de_docs, state_file|None).

    Preferência: ciclo pedido → ciclo ativo mais recente → último ciclo
    arquivado em .ft/cycles/ → docs/ da raiz do projeto.
    """
    root = Path(project_root).resolve()
    wt_home = paths.worktrees_home(root)
    cycles_dir = paths.project_cycles_dir(root)

    if cycle:
        live = wt_home / cycle
        if (live / "state" / "engine_state.yml").is_file():
            return f"ciclo {cycle} (worktree)", live, live / "state" / "engine_state.yml"
        archived = cycles_dir / cycle
        if archived.is_dir():
            return f"ciclo {cycle} (arquivado)", archived, None
        raise EvolveError(f"ciclo não encontrado: {cycle}")

    candidates: list[tuple[float, Path, Path]] = []
    if wt_home.is_dir():
        for item in wt_home.iterdir():
            state_file = item / "state" / "engine_state.yml"
            if item.is_dir() and state_file.is_file() and _state_is_active(state_file):
                candidates.append((state_file.stat().st_mtime, item, state_file))
    if candidates:
        _, live, state_file = max(candidates, key=lambda entry: entry[0])
        return f"ciclo {live.name} (ativo)", live, state_file

    if cycles_dir.is_dir():
        archived = [item for item in cycles_dir.iterdir() if item.is_dir()]
        if archived:
            latest = max(archived, key=lambda item: item.stat().st_mtime)
            return f"ciclo {latest.name} (arquivado)", latest, None

    if (root / "docs").is_dir():
        return "projeto (sem ciclo)", root, None
    return "projeto (sem contexto)", None, None


def _copy_context_tree(source: Path, destination: Path) -> list[str]:
    """Copia artefatos de contexto (docs planos) sem arrastar código ou logs."""
    copied: list[str] = []
    roots = [source]
    docs = source / "docs"
    if docs.is_dir():
        roots.append(docs)
    for base in roots:
        for pattern in _CONTEXT_GLOBS:
            for item in sorted(base.glob(pattern)):
                if not item.is_file():
                    continue
                relative = item.relative_to(source)
                if any(part in _CONTEXT_SKIP_DIRS for part in relative.parts):
                    continue
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied.append(relative.as_posix())
    return copied


# ---------------------------------------------------------------------------
# Alvos e staging
# ---------------------------------------------------------------------------

def resolve_targets(
    project_root: str | Path,
    *,
    include_project: bool,
    include_global: bool,
    engine_root: Path | None = None,
) -> EvolveTargets:
    """Resolve os diretórios reais que a evolução pode alterar."""
    if not include_project and not include_global:
        raise EvolveError("informe ao menos um alvo: --project e/ou --global")

    root = Path(project_root).resolve()
    project_dir: Path | None = None
    if include_project:
        project_dir = paths.project_process_dir(root)
        if not project_dir.is_dir():
            raise EvolveError(
                "projeto sem processo local .ft/process/; rode ft init/ft feature antes"
            )

    global_dirs: dict[str, Path] = {}
    if include_global:
        templates_root = (engine_root or _engine_root()) / "templates"
        manifest = read_manifest(root)
        names: set[str] = set()
        processes = manifest.get("processes")
        if isinstance(processes, dict):
            for record in processes.values():
                if isinstance(record, dict) and record.get("template"):
                    names.add(str(record["template"]))
        if not names:
            raise EvolveError(
                "não há template global registrado no manifesto deste projeto; "
                "--global exige um processo materializado de um template"
            )
        for name in sorted(names):
            candidate = templates_root / name
            if not candidate.is_dir():
                raise EvolveError(f"template global não encontrado: {candidate}")
            global_dirs[name] = candidate

    return EvolveTargets(project_dir=project_dir, global_dirs=global_dirs)


def prepare_workspace(
    project_root: str | Path,
    *,
    template_dir: Path,
    targets: EvolveTargets,
    directive: str | None = None,
    cycle: str | None = None,
) -> EvolveWorkspace:
    """Monta o workspace descartável: playbook, contexto e staging dos alvos."""
    root = Path(project_root).resolve()
    validate_template_is_pristine(template_dir)
    if not (template_dir / "process.yml").is_file():
        raise EvolveError(f"template de evolução sem process.yml: {template_dir}")

    context_label, context_source, cycle_state = find_cycle_context(root, cycle)

    workspace_root = next_workspace_dir(root)
    workspace_root.mkdir(parents=True, exist_ok=False)
    workspace = EvolveWorkspace(
        root=workspace_root, targets=targets, context_label=context_label
    )

    try:
        # 1. Playbook de evolução (sempre a versão global mais recente).
        shutil.copytree(template_dir, workspace_root / "process")

        # 2. Contexto read-only do ciclo.
        workspace.context_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        if context_source is not None:
            copied = _copy_context_tree(context_source, workspace.context_dir / "cycle")
        if cycle_state is not None and cycle_state.is_file():
            shutil.copy2(cycle_state, workspace.context_dir / "cycle_state.yml")
        if directive and directive.strip():
            (workspace.context_dir / "directive.md").write_text(
                "# Diretriz do stakeholder\n\n" + directive.strip() + "\n",
                encoding="utf-8",
            )

        # 3. Staging integral dos alvos.
        staged_labels: list[str] = []
        if targets.project_dir is not None:
            shutil.copytree(targets.project_dir, workspace.staged_project_dir)
            staged_labels.append(
                "- `targets/project/` — fork local `.ft/process/` do projeto"
            )
        for name, source in targets.global_dirs.items():
            shutil.copytree(source, workspace.staged_global_dir(name))
            staged_labels.append(
                f"- `targets/global/{name}/` — template global `templates/{name}` do engine"
            )

        # 4. Manifesto para o playbook (o LLM lê isto primeiro).
        manifest_lines = [
            "# Workspace de evolução de processo",
            "",
            f"Contexto derivado de: {context_label}",
            "",
            "## Alvos staged (edite SOMENTE dentro de targets/)",
            *staged_labels,
            "",
            "## Contexto (somente leitura)",
        ]
        if copied:
            manifest_lines.extend(f"- `context/cycle/{name}`" for name in copied)
        else:
            manifest_lines.append("- (nenhum artefato de ciclo encontrado)")
        if cycle_state is not None:
            manifest_lines.append("- `context/cycle_state.yml` — estado do ciclo")
        if directive and directive.strip():
            manifest_lines.append("- `context/directive.md` — diretriz do stakeholder")
        manifest_lines.append("")
        (workspace.context_dir / "targets.md").write_text(
            "\n".join(manifest_lines), encoding="utf-8"
        )

        workspace.report_dir.mkdir(parents=True, exist_ok=True)
        workspace.state_file.parent.mkdir(parents=True, exist_ok=True)

        # 5. Metadados do workspace (debug/auditoria).
        (workspace_root / "workspace.yml").write_text(
            yaml.safe_dump(
                {
                    "project_root": str(root),
                    "context": context_label,
                    "targets": targets.labels,
                    "template": template_dir.name,
                    "directive": (directive or "").strip() or None,
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(workspace_root, ignore_errors=True)
        raise
    return workspace


# ---------------------------------------------------------------------------
# Validação, diff e apply
# ---------------------------------------------------------------------------

def _staged_pairs(workspace: EvolveWorkspace) -> list[tuple[str, Path, Path]]:
    """(label, staged_dir, real_dir) para cada alvo staged."""
    pairs: list[tuple[str, Path, Path]] = []
    targets = workspace.targets
    if targets.project_dir is not None:
        pairs.append(("project", workspace.staged_project_dir, targets.project_dir))
    for name, real in sorted(targets.global_dirs.items()):
        pairs.append((f"global:{name}", workspace.staged_global_dir(name), real))
    return pairs


def validate_staged(workspace: EvolveWorkspace) -> list[str]:
    """Valida o staging inteiro antes de qualquer apply. Retorna erros."""
    from ft.engine.graph import load_graph
    from ft.engine.process_validator import validate_process
    from ft.engine.runner import VALIDATOR_REGISTRY

    errors: list[str] = []
    for label, staged, _real in _staged_pairs(workspace):
        if not staged.is_dir():
            errors.append(f"{label}: staging removido pelo playbook ({staged})")
            continue
        process_files = sorted(staged.rglob("process.yml"))
        if not process_files:
            errors.append(f"{label}: nenhum process.yml restante no staging")
        for process_file in process_files:
            relative = process_file.relative_to(staged).as_posix()
            try:
                graph = load_graph(process_file)
            except (ValueError, FileNotFoundError, yaml.YAMLError) as exc:
                errors.append(f"{label}/{relative}: YAML inválido — {exc}")
                continue
            report = validate_process(graph, VALIDATOR_REGISTRY)
            if not report.passed:
                issues = "; ".join(item.message for item in report.errors[:5])
                errors.append(f"{label}/{relative}: processo inválido — {issues}")
        for env_file in sorted(staged.rglob("environment.yml")):
            relative = env_file.relative_to(staged).as_posix()
            try:
                data = yaml.safe_load(env_file.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                errors.append(f"{label}/{relative}: environment inválido — {exc}")
                continue
            if data is not None and not isinstance(data, dict):
                errors.append(f"{label}/{relative}: environment deve ser um mapping")
        if label.startswith("global:"):
            try:
                validate_template_is_pristine(staged)
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
    return errors


def _tree_files(base: Path) -> dict[str, Path]:
    if not base.is_dir():
        return {}
    return {
        item.relative_to(base).as_posix(): item
        for item in sorted(base.rglob("*"))
        if item.is_file()
    }


def diff_staged(workspace: EvolveWorkspace) -> list[StagedChange]:
    """Diferenças entre o staging e os alvos reais (o que o apply faria)."""
    changes: list[StagedChange] = []
    for label, staged_dir, real_dir in _staged_pairs(workspace):
        staged_files = _tree_files(staged_dir)
        real_files = _tree_files(real_dir)
        for relative in sorted(set(staged_files) | set(real_files)):
            staged = staged_files.get(relative)
            real = real_files.get(relative)
            if staged is None:
                changes.append(StagedChange(label, "removed", relative, None, real))
            elif real is None:
                changes.append(StagedChange(label, "added", relative, staged, None))
            elif staged.read_bytes() != real.read_bytes():
                changes.append(StagedChange(label, "modified", relative, staged, real))
    return changes


def change_fingerprint(changes: list[StagedChange]) -> tuple[tuple[str, ...], ...]:
    """Snapshot imutável dos dois lados usados pelo diff mostrado ao humano."""

    def digest(path: Path | None) -> str:
        if path is None or not path.is_file():
            return "-"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return tuple(
        (
            change.target,
            change.status,
            change.relative,
            digest(change.staged),
            digest(change.real),
        )
        for change in changes
    )


def apply_staged(
    workspace: EvolveWorkspace,
    changes: list[StagedChange] | None = None,
) -> list[str]:
    """Apply staged changes under the project catalog barrier when present."""
    project_dir = workspace.targets.project_dir
    if project_dir is None:
        return _apply_staged_unlocked(workspace, changes)

    from ft.engine.layout import (
        _assert_no_exclusive_startup,
        _manifest_write_lock,
    )

    project_root = project_dir.resolve().parent.parent
    with _manifest_write_lock(project_root):
        _assert_no_exclusive_startup(project_root)
        return _apply_staged_unlocked(workspace, changes)


def _apply_staged_unlocked(
    workspace: EvolveWorkspace,
    changes: list[StagedChange] | None = None,
) -> list[str]:
    """Espelha o staging validado de volta nos alvos reais.

    Retorna descrições das mudanças aplicadas. Nunca chame sem antes passar
    por validate_staged — o CLI é responsável por essa ordem.
    """
    if changes is None:
        changes = diff_staged(workspace)
    pairs = {label: (staged, real) for label, staged, real in _staged_pairs(workspace)}
    applied: list[str] = []
    for change in changes:
        _staged_dir, real_dir = pairs[change.target]
        real_path = real_dir / change.relative
        if change.status == "removed":
            real_path.unlink(missing_ok=True)
        else:
            real_path.parent.mkdir(parents=True, exist_ok=True)
            assert change.staged is not None
            shutil.copy2(change.staged, real_path)
        applied.append(f"{change.status:8s} {change.target}: {change.relative}")
    return applied
