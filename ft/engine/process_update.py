"""Drift e sincronização entre processos locais e templates globais.

O engine materializa templates com ``copy_once``/``local_only``: o fork local
nunca é sobrescrito automaticamente. Este módulo fecha a direção global→local
com um modelo de merge 3-way clássico:

- **base**: snapshot pristino do bundle no momento da materialização (ou da
  última sincronização), guardado em ``.ft/process/<nome>/.base/``. É o
  ancestral comum — sempre em *coordenadas locais* (paths reescritos).
- **local**: o fork versionado em ``.ft/process/<nome>/``.
- **global**: o template em ``templates/<nome>/`` do engine, reescrito para
  coordenadas locais antes de qualquer comparação ou merge.

Comparando os digests dos três lados, cada processo cai em um estado com ação
óbvia: em sincronia (nada), fast-forward (recopiar é seguro), fork local
(nada a atualizar) ou divergência real (merge 3-way via ``git merge-file``).
Nenhuma função aqui escreve no processo local sem passar por staging + backup;
a aprovação humana pertence ao CLI.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ft.engine import paths
from ft.engine.layout import process_digest, read_manifest, refresh_process_digests

BASE_SNAPSHOT_DIR = ".base"
BACKUP_ROOT = ".backup"
STAGING_ROOT = ".staging"

# Estados possíveis de um processo local frente ao template global.
STATE_IN_SYNC = "in_sync"
STATE_FAST_FORWARD = "fast_forward"
STATE_LOCAL_FORK = "local_fork"
STATE_DIVERGED = "diverged"
STATE_DIVERGED_NO_BASE = "diverged_no_base"
STATE_TEMPLATE_MISSING = "template_missing"
STATE_BROKEN = "broken"

ACTIONABLE_STATES = frozenset({STATE_FAST_FORWARD, STATE_DIVERGED, STATE_DIVERGED_NO_BASE})

# Mesmos sufixos que a materialização considera texto ao reescrever paths.
_TEXT_SUFFIXES = {".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}


@dataclass
class ProcessDriftState:
    """Fotografia de um processo local frente ao seu template global."""

    name: str
    template_id: str
    entrypoint: str
    local_dir: Path
    local_process: Path
    template_dir: Path
    state: str
    detail: str = ""
    local_digest: str | None = None
    global_digest: str | None = None
    base_digest: str | None = None
    # De onde vem o ancestral: "snapshot" (dir .base existente), "local" ou
    # "global" (reconstruível — o lado provadamente intocado desde a
    # materialização) ou None (ancestral perdido).
    base_source: str | None = None


@dataclass
class MergeResult:
    """Resultado de um merge 3-way montado em staging (nunca aplicado aqui)."""

    staging_dir: Path
    changed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.conflicts


def template_process_file(template_dir: Path) -> Path | None:
    """Resolve o YAML de processo sem confundi-lo com environment.yml."""
    canonical = template_dir / "process.yml"
    if canonical.is_file():
        return canonical
    legacy = sorted(
        path for path in template_dir.glob("*.yml")
        if path.name != "environment.yml"
    )
    return legacy[0] if legacy else None


def rewrite_local_refs(directory: Path, process_name: str) -> None:
    """Torna referências runtime de um bundle locais ao fork nomeado.

    Mesma reescrita aplicada na materialização; precisa ser reaplicada em toda
    cópia do template global antes de comparar ou mesclar com o fork local,
    senão o diff acusaria mudanças que são só mudança de coordenadas.
    """
    named_scripts = f".ft/process/{process_name}/scripts"
    named_process = f".ft/process/{process_name}/process.yml"
    scripts_root = directory / "scripts"
    for file_path in directory.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rewritten = content.replace(
            ".ft/process/scripts", named_scripts
        ).replace(
            ".ft/process/process.yml", named_process
        )
        if file_path.is_relative_to(scripts_root):
            # Scripts de bundles nomeados vivem um nível mais fundo que no
            # layout flat: reancora os paths relativos ao root do projeto.
            rewritten = rewritten.replace(
                '$(dirname "${BASH_SOURCE[0]}")/../../../..',
                "__FT_NAMED_BASH_ROOT__",
            ).replace(
                '$(dirname "${BASH_SOURCE[0]}")/../../..',
                "__FT_NAMED_BASH_ROOT__",
            ).replace(
                "__FT_NAMED_BASH_ROOT__",
                '$(dirname "${BASH_SOURCE[0]}")/../../../..',
            ).replace(
                '$(dirname "$0")/../../../../project',
                "__FT_NAMED_PROJECT_ROOT__",
            ).replace(
                '$(dirname "$0")/../../../project',
                "__FT_NAMED_PROJECT_ROOT__",
            ).replace(
                "__FT_NAMED_PROJECT_ROOT__",
                '$(dirname "$0")/../../../../project',
            )
        if rewritten != content:
            file_path.write_text(rewritten, encoding="utf-8")


# Fora do bundle: áreas internas do update e sementes de produto (docs/ e
# src/ de templates são seed do projeto, nunca parte do processo local —
# mesma exclusão da materialização).
_BUNDLE_SKIP = frozenset({BASE_SNAPSHOT_DIR, BACKUP_ROOT, STAGING_ROOT, "docs", "src"})


def _copy_bundle(source: Path, destination: Path) -> None:
    """Copia um bundle de processo com as mesmas exclusões da materialização."""
    destination.mkdir(parents=True)
    for child in source.iterdir():
        if child.name in _BUNDLE_SKIP:
            continue
        if child.is_symlink():
            raise ValueError(
                f"bundle de processo contém link simbólico não permitido: {child}"
            )
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def materialize_global_to(template_dir: Path, process_name: str, destination: Path) -> None:
    """Copia o template global já reescrito em coordenadas locais."""
    _copy_bundle(template_dir, destination)
    rewrite_local_refs(destination, process_name)


def base_snapshot_dir(local_dir: Path) -> Path:
    return local_dir / BASE_SNAPSHOT_DIR


def write_base_snapshot(local_dir: Path, source_dir: Path | None = None) -> Path:
    """Grava o snapshot base a partir de ``source_dir`` (default: o próprio fork).

    O snapshot registra o último estado global integrado, em coordenadas
    locais — o ancestral dos merges futuros. É versionado junto do fork.
    """
    snapshot = base_snapshot_dir(local_dir)
    if snapshot.exists():
        shutil.rmtree(snapshot)
    _copy_bundle(source_dir or local_dir, snapshot)
    return snapshot


def _bundle_digest(directory: Path) -> str | None:
    process_file = template_process_file(directory)
    if process_file is None:
        return None
    return process_digest(process_file)


def _rewritten_global_digest(template_dir: Path, process_name: str) -> str | None:
    """Digest do template global em coordenadas locais, via cópia temporária."""
    with tempfile.TemporaryDirectory(prefix="ft-global-") as tmp:
        staged = Path(tmp) / process_name
        materialize_global_to(template_dir, process_name, staged)
        return _bundle_digest(staged)


def _resolve_base(
    local_dir: Path,
    template_dir: Path,
    record: dict,
    local_digest: str | None,
    global_digest: str | None,
) -> tuple[str | None, str | None]:
    """Determina (digest, origem) do ancestral sem escrever nada.

    Sem o dir ``.base``, o ancestral ainda é provável quando um dos lados
    comprovadamente não mudou desde a materialização (digests do manifest);
    caso contrário ele se perdeu e o merge 3-way fica indisponível.
    """
    snapshot = base_snapshot_dir(local_dir)
    if snapshot.is_dir():
        return _bundle_digest(snapshot), "snapshot"

    recorded_base = record.get("base_digest")
    if recorded_base and local_digest and local_digest == recorded_base:
        # Fork intocado: ele próprio ainda é o estado materializado.
        return local_digest, "local"

    recorded_source = record.get("source_digest")
    global_file = template_process_file(template_dir)
    global_raw_digest = process_digest(global_file) if global_file else None
    if recorded_source and global_raw_digest and global_raw_digest == recorded_source:
        # Template global intocado: reescrito, ele é o ancestral do fork.
        return global_digest, "global"
    return None, None


def ensure_base_snapshot(state: ProcessDriftState) -> Path | None:
    """Persiste o snapshot base quando ele não existe mas ainda é provável.

    Chamado apenas nos caminhos de escrita explícitos (``ft process update``);
    o scan nunca escreve para poder rodar em preflights sem sujar a árvore.
    """
    snapshot = base_snapshot_dir(state.local_dir)
    if snapshot.is_dir():
        return snapshot
    if state.base_source == "local":
        return write_base_snapshot(state.local_dir)
    if state.base_source == "global":
        with tempfile.TemporaryDirectory(prefix="ft-base-") as tmp:
            staged = Path(tmp) / state.name
            materialize_global_to(state.template_dir, state.name, staged)
            return write_base_snapshot(state.local_dir, staged)
    return None


def scan_processes(
    project_root: str | Path,
    templates_root: str | Path,
    process_name: str | None = None,
) -> list[ProcessDriftState]:
    """Classifica cada processo local do manifest frente ao template global.

    Estritamente somente leitura: roda em preflights e em ``--check`` sem
    sujar a árvore do projeto. A persistência do snapshot base fica nos
    caminhos explícitos de escrita (``ensure_base_snapshot``/``apply_update``).
    """
    root = Path(project_root).resolve()
    templates = Path(templates_root)
    manifest = read_manifest(root)
    processes = manifest.get("processes")
    if not isinstance(processes, dict):
        processes = {}

    states: list[ProcessDriftState] = []
    for name, record in sorted(processes.items()):
        if process_name and name != process_name:
            continue
        if not isinstance(record, dict):
            continue
        raw_path = record.get("path")
        local_process = (root / raw_path) if isinstance(raw_path, str) else None
        template_id = str(record.get("template") or name)
        entrypoint = str(record.get("entrypoint") or "init")
        template_dir = templates / template_id

        if local_process is None or not local_process.is_file():
            states.append(ProcessDriftState(
                name=name, template_id=template_id, entrypoint=entrypoint,
                local_dir=root / ".ft" / "process" / name,
                local_process=local_process or Path("?"),
                template_dir=template_dir,
                state=STATE_BROKEN,
                detail="processo registrado no manifest não existe no disco",
            ))
            continue

        local_dir = local_process.parent
        if template_process_file(template_dir) is None:
            states.append(ProcessDriftState(
                name=name, template_id=template_id, entrypoint=entrypoint,
                local_dir=local_dir, local_process=local_process,
                template_dir=template_dir,
                state=STATE_TEMPLATE_MISSING,
                detail="template global não encontrado no engine",
            ))
            continue

        try:
            local_digest = _bundle_digest(local_dir)
            global_digest = _rewritten_global_digest(template_dir, name)
            base_digest, base_source = _resolve_base(
                local_dir, template_dir, record, local_digest, global_digest
            )
        except ValueError as exc:
            states.append(ProcessDriftState(
                name=name, template_id=template_id, entrypoint=entrypoint,
                local_dir=local_dir, local_process=local_process,
                template_dir=template_dir,
                state=STATE_BROKEN, detail=str(exc),
            ))
            continue

        if local_digest and local_digest == global_digest:
            state, detail = STATE_IN_SYNC, "local idêntico ao template global"
        elif base_digest is None:
            state = STATE_DIVERGED_NO_BASE
            detail = (
                "local e global divergem e o snapshot base não pôde ser "
                "reconstruído; merge 3-way indisponível"
            )
        elif local_digest == base_digest:
            state = STATE_FAST_FORWARD
            detail = "global evoluiu; fork local intocado — atualização segura"
        elif global_digest == base_digest:
            state = STATE_LOCAL_FORK
            detail = "fork local customizado; template global não mudou"
        else:
            state = STATE_DIVERGED
            detail = "fork local customizado e template global evoluiu"

        states.append(ProcessDriftState(
            name=name, template_id=template_id, entrypoint=entrypoint,
            local_dir=local_dir, local_process=local_process,
            template_dir=template_dir, state=state, detail=detail,
            local_digest=local_digest, global_digest=global_digest,
            base_digest=base_digest, base_source=base_source,
        ))
    return states


def _iter_bundle_files(directory: Path) -> dict[str, Path]:
    """Todos os arquivos do bundle por path relativo, fora das áreas internas."""
    files: dict[str, Path] = {}
    if not directory.is_dir():
        return files
    for candidate in directory.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(directory)
        if relative.parts and relative.parts[0] in _BUNDLE_SKIP:
            continue
        files[relative.as_posix()] = candidate
    return files


def _read_side(path: Path | None) -> bytes | None:
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def _is_text(*payloads: bytes | None) -> bool:
    for payload in payloads:
        if payload is None:
            continue
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def _git_merge_file(base: bytes, local: bytes, other: bytes, workdir: Path) -> tuple[bytes, bool]:
    """Merge 3-way de um arquivo via ``git merge-file --diff3``.

    Retorna (conteúdo, conflitou). O conteúdo com marcadores é preservado
    mesmo em conflito — é o input natural de uma resolução assistida.
    """
    base_file = workdir / "base"
    local_file = workdir / "local"
    other_file = workdir / "global"
    base_file.write_bytes(base)
    local_file.write_bytes(local)
    other_file.write_bytes(other)
    result = subprocess.run(
        [
            "git", "merge-file", "--diff3",
            "-L", "local", "-L", "base", "-L", "global",
            str(local_file), str(base_file), str(other_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode < 0 or result.returncode > 127:
        raise RuntimeError(
            f"git merge-file falhou: {result.stderr.strip() or result.returncode}"
        )
    return local_file.read_bytes(), result.returncode > 0


def build_merge_staging(
    state: ProcessDriftState,
    staging_dir: Path,
) -> MergeResult:
    """Monta em staging o merge 3-way base × local × global (reescrito).

    Nunca toca o fork local. Arquivos conflitados ficam no staging com os
    marcadores ``<<<<<<< local / ||||||| base / ======= / >>>>>>> global``.
    """
    snapshot = base_snapshot_dir(state.local_dir)
    if not snapshot.is_dir():
        raise ValueError(
            f"snapshot base ausente em {snapshot}; "
            "rode ensure_base_snapshot antes do merge"
        )

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    result = MergeResult(staging_dir=staging_dir)

    with tempfile.TemporaryDirectory(prefix="ft-merge-") as tmp:
        tmp_path = Path(tmp)
        global_dir = tmp_path / "global"
        materialize_global_to(state.template_dir, state.name, global_dir)
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        base_files = _iter_bundle_files(snapshot)
        local_files = _iter_bundle_files(state.local_dir)
        global_files = _iter_bundle_files(global_dir)
        every_path = sorted(set(base_files) | set(local_files) | set(global_files))

        for relative in every_path:
            base_payload = _read_side(base_files.get(relative))
            local_payload = _read_side(local_files.get(relative))
            global_payload = _read_side(global_files.get(relative))

            content, conflicted, action = _merge_one(
                base_payload, local_payload, global_payload, scratch
            )
            if content is None:
                if local_payload is not None:
                    result.changed.append(f"removido: {relative}")
                continue

            target = staging_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            _apply_mode(target, local_files.get(relative), global_files.get(relative))

            if conflicted:
                result.conflicts.append(relative)
            elif local_payload is None:
                result.changed.append(f"adicionado: {relative}")
            elif content != local_payload:
                result.changed.append(f"atualizado: {relative}")

    return result


def _merge_one(
    base: bytes | None,
    local: bytes | None,
    other: bytes | None,
    scratch: Path,
) -> tuple[bytes | None, bool, str]:
    """Decide um arquivo do merge. Retorna (conteúdo|None=ausente, conflitou, ação)."""
    if local == other:
        return local, False, "igual"
    if base == local:
        # Só o global mudou (ou removeu) — lado global vence.
        return other, False, "global"
    if base == other:
        # Só o local mudou (ou removeu) — customização preservada.
        return local, False, "local"

    # Os três diferem: merge textual quando der, conflito estrutural senão.
    if base is not None and local is not None and other is not None and _is_text(base, local, other):
        content, conflicted = _git_merge_file(base, local, other, scratch)
        return content, conflicted, "merge"

    # Adições/remoções concorrentes ou binário: preserva o lado local (quando
    # existe) e sinaliza conflito para resolução assistida.
    return (local if local is not None else other), True, "conflito"


def _apply_mode(target: Path, local: Path | None, global_side: Path | None) -> None:
    source = local if local is not None and local.is_file() else global_side
    if source is not None and source.is_file():
        target.chmod(source.stat().st_mode & 0o777)


def backup_dir_for(project_root: Path, process_name: str) -> Path:
    return paths.project_process_dir(project_root) / BACKUP_ROOT / process_name


def staging_dir_for(project_root: Path, process_name: str) -> Path:
    return paths.project_process_dir(project_root) / STAGING_ROOT / process_name


def apply_update(
    project_root: str | Path,
    state: ProcessDriftState,
    staged_dir: Path,
) -> Path:
    """Substitui o fork local pelo staging aprovado, com backup e novo ancestral.

    O snapshot base resultante é sempre o template global recém-integrado (em
    coordenadas locais) — nunca o resultado do merge —, para que customizações
    locais sobreviventes continuem aparecendo como fork nos próximos scans.
    """
    root = Path(project_root).resolve()
    backup = backup_dir_for(root, state.name)
    if backup.exists():
        shutil.rmtree(backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(state.local_dir), str(backup))

    try:
        _copy_bundle(staged_dir, state.local_dir)
        snapshot_source = state.local_dir / ".global-ancestor"
        materialize_global_to(state.template_dir, state.name, snapshot_source)
        write_base_snapshot(state.local_dir, snapshot_source)
        shutil.rmtree(snapshot_source)

        global_file = template_process_file(state.template_dir)
        # O registro é write-once nos digests; a sincronização explícita é o
        # único fluxo autorizado a reancorá-los.
        refresh_process_digests(
            root,
            state.name,
            source_digest=process_digest(global_file) if global_file else None,
        )
    except Exception:
        # Restaura o fork original: o update é atômico do ponto de vista do usuário.
        if state.local_dir.exists():
            shutil.rmtree(state.local_dir)
        shutil.move(str(backup), str(state.local_dir))
        raise
    finally:
        if staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)

    return backup


def prepare_fast_forward(
    project_root: str | Path, state: ProcessDriftState
) -> tuple[Path, list[str]]:
    """Prepara em staging a atualização segura de um fork intocado.

    Retorna (staging, mudanças). Aplicar é decisão do chamador, via
    ``apply_update`` — o mesmo contrato do caminho de merge.
    """
    root = Path(project_root).resolve()
    staging = staging_dir_for(root, state.name)
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    materialize_global_to(state.template_dir, state.name, staging)

    before = _iter_bundle_files(state.local_dir)
    after = _iter_bundle_files(staging)
    changed = sorted(
        f"adicionado: {relative}" for relative in set(after) - set(before)
    ) + sorted(
        f"atualizado: {relative}" for relative in set(after) & set(before)
        if after[relative].read_bytes() != before[relative].read_bytes()
    ) + sorted(f"removido: {relative}" for relative in set(before) - set(after))

    return staging, changed
