"""Project layout, migration, and durable cycle archival.

The repository-local ``.ft`` directory contains only versioned metadata. Raw
state and LLM logs remain under ``$FT_HOME`` and are never archived here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any

import yaml

from ft.engine import paths


LAYOUT_VERSION = 2


class LayoutMigrationRequired(ValueError):
    """Raised when a project still uses the singular/flat v1 process layout."""


class ManifestError(ValueError):
    """Raised when the project manifest cannot be trusted."""

PROJECT_GITIGNORE = """# Runtime data never belongs to the project history.
/runtime/
/cache/
/tmp/
/logs/
*.pid
"""

# Product sources of truth remain visible under docs/. Everything below is a
# record of one execution and moves to .ft/cycles/<cycle-id>/ on close.
DEFAULT_CYCLE_ARTIFACTS = (
    ".build_ok",
    "docs/task_list.md",
    "docs/TASK_LIST.md",
    "docs/screenshots/",
    "docs/screenshot-review.md",
    "docs/screenshot-review-result.json",
    "docs/smoke-report.md",
    "docs/acceptance-report.md",
    "docs/acceptance-result.json",
    "docs/acceptance-cli-report.md",
    "docs/e2e-report.md",
    "docs/e2e-browser-report.md",
    "docs/visual-check-report.md",
    "docs/stakeholder-feedback.md",
    "docs/retro.md",
    "docs/backlog-progress.md",
    "docs/PRD.next.md",
    "docs/critical-analysis.md",
    "docs/plano_de_voo.md",
    "docs/handoff.md",
    "docs/process-improvements.md",
    "docs/process-improvements.yml",
    "docs/exploration-report.md",
    "docs/frontend-prd-review.md",
    "docs/tdd-refactor-report.md",
    "docs/forgebase-audit.md",
    "docs/prd-coverage-report.md",
    "docs/guidelines-review.md",
    "docs/guidelines-review-result.json",
    "docs/plano-check-report.md",
    "docs/validation-report.md",
    "docs/SPEC.md",
)

# These are execution products even when a project-specific policy omits them.
SYSTEM_CYCLE_ARTIFACTS = (".build_ok",)

DEFAULT_CANONICAL_ARTIFACTS = (
    "docs/PRD.md",
    "docs/TECH_STACK.md",
    "docs/tech_stack.md",
    "docs/ui_criteria.md",
    "docs/PROJECT_BACKLOG.md",
    "docs/FEATURES.md",
    "docs/hipotese.md",
    "docs/demanda.md",
    "docs/api_contract.md",
    "docs/test_data.md",
)

_SAFE_CYCLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ArchiveResult:
    cycle_dir: Path
    moved: tuple[str, ...]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_manifest_file(path: Path) -> dict[str, Any]:
    """Read a manifest without silently turning corruption into an empty file."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"manifest inválido em {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"manifest inválido em {path}: raiz deve ser mapping")
    return data


def _manifest_requires_migration(manifest: dict[str, Any]) -> bool:
    version = manifest.get("schema_version")
    return (
        version == 1
        or "process" in manifest
        or "template" in manifest
        or "origin_template" in manifest
    )


def _validate_v2_manifest(manifest: dict[str, Any], path: Path) -> None:
    if not manifest:
        return
    version = manifest.get("schema_version")
    if version != LAYOUT_VERSION:
        if _manifest_requires_migration(manifest) or version is None:
            raise LayoutMigrationRequired(
                f"layout v1 detectado em {path}; execute ft migrate-layout ."
            )
        raise ManifestError(
            f"schema_version não suportado em {path}: {version!r} "
            f"(esperado {LAYOUT_VERSION})"
        )
    if "process" in manifest or "template" in manifest or "origin_template" in manifest:
        raise ManifestError(
            f"manifest v2 contém chaves legadas em {path}; execute ft migrate-layout ."
        )
    processes = manifest.get("processes", {})
    if not isinstance(processes, dict):
        raise ManifestError(f"manifest inválido em {path}: processes deve ser mapping")
    for raw_name, record in processes.items():
        if not isinstance(raw_name, str):
            raise ManifestError(
                f"manifest inválido em {path}: nome de processo deve ser string"
            )
        try:
            paths.project_named_process_dir(Path("."), raw_name)
        except ValueError as exc:
            raise ManifestError(
                f"manifest inválido em {path}: nome de processo inválido {raw_name!r}"
            ) from exc
        expected_path = f".ft/process/{raw_name}/process.yml"
        if not isinstance(record, dict):
            raise ManifestError(
                f"manifest inválido em {path}: processes.{raw_name} deve ser mapping"
            )
        if record.get("path") != expected_path:
            raise ManifestError(
                f"manifest inválido em {path}: processes.{raw_name}.path deve ser "
                f"{expected_path}"
            )
        for field in ("template", "entrypoint"):
            value = record.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ManifestError(
                    f"manifest inválido em {path}: processes.{raw_name}.{field} "
                    "deve ser string não vazia"
                )
    defaults = manifest.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ManifestError(f"manifest inválido em {path}: defaults deve ser mapping")
    default_name = manifest.get("default_process")
    if default_name is not None:
        if not isinstance(default_name, str) or not default_name.strip():
            raise ManifestError(
                f"manifest inválido em {path}: default_process deve ser nome não vazio"
            )
        if default_name not in processes:
            raise ManifestError(
                f"manifest inválido em {path}: default_process '{default_name}' "
                "não está registrado em processes"
            )


def process_digest(process_file: str | Path) -> str | None:
    """Hash the complete runtime bundle selected by ``process_file``.

    A process is more than its graph: the adjacent ``environment.yml`` and
    every file under ``scripts/`` can change execution semantics as well.  The
    relative path and permission mode are part of the digest so renames and
    executable-bit changes cannot bypass cycle pinning.
    """
    requested_process = Path(process_file)
    if not requested_process.is_file():
        return None

    bundle_root = requested_process.parent.resolve()
    candidates = [requested_process]
    environment = requested_process.parent / "environment.yml"
    if environment.is_file():
        candidates.append(environment)

    ignored_dirs = {
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
    }
    ignored_files = {
        ".DS_Store",
        ".serve.log",
        ".serve.pid",
        ".serve_backend.pid",
        ".serve_frontend.pid",
        ".serve_url",
    }

    def ignored(candidate: Path) -> bool:
        relative = candidate.relative_to(requested_process.parent)
        return (
            any(part in ignored_dirs for part in relative.parts)
            or candidate.name in ignored_files
            or candidate.suffix.lower() in {".pyc", ".pyo"}
        )

    scripts_dir = requested_process.parent / "scripts"
    if scripts_dir.is_symlink():
        raise ValueError(
            f"bundle de processo contém link simbólico não permitido: {scripts_dir}"
        )
    if scripts_dir.is_dir():
        for candidate in scripts_dir.rglob("*"):
            if ignored(candidate):
                continue
            if candidate.is_symlink():
                raise ValueError(
                    f"bundle de processo contém link simbólico não permitido: {candidate}"
                )
            if candidate.is_file():
                candidates.append(candidate)

    def relative_name(candidate: Path) -> str:
        return candidate.relative_to(requested_process.parent).as_posix()

    digest = hashlib.sha256()
    digest.update(b"ft-process-bundle-v1\0")
    for candidate in sorted(candidates, key=relative_name):
        if candidate.is_symlink():
            raise ValueError(
                f"bundle de processo contém link simbólico não permitido: {candidate}"
            )
        resolved = candidate.resolve()
        try:
            resolved.relative_to(bundle_root)
        except ValueError as exc:
            raise ValueError(
                f"arquivo do bundle escapa do processo local: {candidate}"
            ) from exc
        relative = relative_name(candidate)
        payload = resolved.read_bytes()
        mode = stat.S_IMODE(resolved.stat().st_mode)
        header = f"{len(relative)}:{relative}:{mode:o}:{len(payload)}:".encode("utf-8")
        digest.update(header)
        digest.update(payload)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _safe_manifest_process_path(root: Path, raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (root / relative).resolve()
    process_catalog = paths.project_process_dir(root).resolve()
    try:
        process_catalog.relative_to(root.resolve())
        candidate.relative_to(process_catalog)
    except ValueError:
        return None
    return candidate


def _path_uses_symlink(root: Path, candidate: Path) -> bool:
    """Return whether candidate or one of its project-relative parents is a link."""
    current = candidate
    while current != root:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


def _canonical_process_path(root: Path, process_name: str) -> Path:
    """Return the only valid v2 location for a named process."""
    return paths.project_named_process_file(root, process_name).resolve()


def _manifest_process_path(
    root: Path,
    manifest: dict[str, Any],
    process_name: str,
) -> Path | None:
    processes = manifest.get("processes", {})
    record = processes.get(process_name, {}) if isinstance(processes, dict) else {}
    if not isinstance(record, dict):
        return None
    raw_path = record.get("path")
    candidate = _safe_manifest_process_path(root, raw_path)
    canonical_raw = paths.project_named_process_file(root, process_name)
    canonical = canonical_raw.resolve()
    if candidate is None or candidate != canonical:
        return None
    if _path_uses_symlink(root, canonical_raw) or not candidate.is_file():
        return None
    return candidate


def validate_local_process_path(
    project_root: str | Path,
    process_path: str | Path,
    *,
    require_registered: bool = True,
) -> Path:
    """Validate a v2 process path and return its resolved local file."""
    root = Path(project_root).resolve()
    raw = Path(process_path)
    if not raw.is_absolute():
        raw = root / raw
    try:
        lexical_relative = raw.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "processo executável deve estar dentro de .ft/process/<template>/"
        ) from exc
    if ".." in lexical_relative.parts:
        raise ValueError(
            "processo executável deve estar dentro de .ft/process/<template>/"
        )
    catalog = paths.project_process_dir(root).resolve()
    candidate = raw.resolve()
    try:
        catalog.relative_to(root)
        relative = candidate.relative_to(catalog)
    except ValueError as exc:
        raise ValueError(
            "processo executável deve estar dentro de .ft/process/<template>/"
        ) from exc
    if len(relative.parts) != 2 or relative.name != "process.yml":
        raise ValueError(
            "processo executável deve usar .ft/process/<template>/process.yml"
        )
    if not candidate.is_file():
        raise FileNotFoundError(f"processo local ausente: {raw}")
    if _path_uses_symlink(root, raw):
        raise ValueError(f"processo local não pode ser simbólico: {raw}")
    process_name = relative.parts[0]
    if require_registered:
        manifest_path = paths.project_manifest(root)
        manifest = _read_manifest_file(manifest_path)
        _validate_v2_manifest(manifest, manifest_path)
        registered = _manifest_process_path(root, manifest, process_name)
        if registered is None or registered != candidate:
            raise ValueError(
                f"processo local não registrado no manifesto: {raw.relative_to(root)}"
            )
    return candidate


def resolve_project_process(
    project_root: str | Path,
    process_name: str | None = None,
) -> Path | None:
    """Resolve a project-owned process without consulting the global catalog."""
    root = Path(project_root).resolve()
    manifest_path = paths.project_manifest(root)
    manifest = _read_manifest_file(manifest_path)
    if not manifest:
        if paths.legacy_flat_process_file(root).exists() or (root / "process").exists():
            raise LayoutMigrationRequired(
                f"layout v1 detectado em {root}; execute ft migrate-layout ."
            )
        return None
    _validate_v2_manifest(manifest, manifest_path)
    selected = process_name or manifest.get("default_process")
    if not isinstance(selected, str) or not selected.strip():
        return None
    return _manifest_process_path(root, manifest, selected)


def register_project_process(
    project_root: str | Path,
    *,
    process_name: str,
    process_path: str | Path,
    template_id: str,
    entrypoint: str,
    source_digest: str | None = None,
    set_default: bool = False,
) -> Path:
    """Register one named, local process in the versioned project manifest."""
    root = Path(project_root).resolve()
    manifest_path = ensure_project_layout(root)
    manifest = _read_manifest_file(manifest_path)
    if not manifest:
        raise ManifestError(
            f"manifest inválido em {manifest_path}: arquivo vazio; "
            "execute ft migrate-layout . ou restaure o manifesto v2"
        )
    _validate_v2_manifest(manifest, manifest_path)
    raw_process_file = Path(process_path)
    if not raw_process_file.is_absolute():
        raw_process_file = root / raw_process_file
    if _path_uses_symlink(root, raw_process_file):
        raise ValueError(f"processo local não pode ser simbólico: {raw_process_file}")
    process_file = raw_process_file.resolve()
    canonical = _canonical_process_path(root, process_name)
    try:
        relative = process_file.relative_to(root).as_posix()
        process_file.relative_to(paths.project_process_dir(root).resolve())
    except ValueError as exc:
        raise ValueError("processo registrado deve estar dentro de .ft/process/") from exc

    if process_file != canonical:
        raise ValueError(
            "processo registrado deve usar o path canônico "
            f".ft/process/{process_name}/process.yml"
        )
    if not process_file.is_file():
        raise ValueError(f"processo local ausente ou simbólico: {process_file}")

    processes = manifest.setdefault("processes", {})
    if not isinstance(processes, dict):
        raise ManifestError("manifest inválido: processes deve ser mapping")
    existing = processes.get(process_name, {})
    record = dict(existing) if isinstance(existing, dict) else {}
    record.update({
        "path": relative,
        "template": template_id,
        "entrypoint": entrypoint,
    })
    if not record.get("source_digest") and source_digest:
        record["source_digest"] = source_digest
    if not record.get("base_digest"):
        record["base_digest"] = process_digest(process_file)
    processes[process_name] = record
    if set_default:
        manifest["default_process"] = process_name
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return manifest_path


def ensure_project_layout(
    project_root: str | Path,
    *,
    defaults: dict[str, Any] | None = None,
) -> Path:
    """Create tracked layout metadata without creating any execution state."""
    root = Path(project_root).resolve()
    ft_dir = paths.project_ft_dir(root)
    process_dir = paths.project_process_dir(root)
    cycles_dir = paths.project_cycles_dir(root)
    manifest_path = paths.project_manifest(root)
    for guarded in (ft_dir, manifest_path, process_dir, cycles_dir):
        _assert_project_local_path(root, guarded)
    manifest = _read_manifest_file(manifest_path)
    if manifest:
        _validate_v2_manifest(manifest, manifest_path)
    elif paths.legacy_flat_process_file(root).exists() or (root / "process").exists():
        raise LayoutMigrationRequired(
            f"layout v1 detectado em {root}; execute ft migrate-layout ."
        )

    process_dir.mkdir(parents=True, exist_ok=True)
    cycles_dir.mkdir(parents=True, exist_ok=True)

    ignore = ft_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text(PROJECT_GITIGNORE, encoding="utf-8")

    keep = cycles_dir / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")

    manifest["schema_version"] = LAYOUT_VERSION
    manifest.setdefault("processes", {})
    if defaults:
        current = manifest.setdefault("defaults", {})
        current.update({key: value for key, value in defaults.items() if value is not None})
        requested_effort = defaults.get("llm_effort")
        if (
            isinstance(requested_effort, str)
            and requested_effort.strip().lower() == "default"
        ):
            current.pop("llm_effort", None)
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return manifest_path


def read_manifest(project_root: str | Path) -> dict[str, Any]:
    manifest_path = paths.project_manifest(project_root)
    manifest = _read_manifest_file(manifest_path)
    if manifest:
        _validate_v2_manifest(manifest, manifest_path)
    return manifest


def canonical_project_root(project_root: str | Path) -> Path:
    """Return the owning checkout when called from a linked Git worktree.

    Project-persistent metadata such as LLM defaults belongs to the main
    checkout, not to the cycle's versioned manifest snapshot.
    """
    root = Path(project_root).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return root
    if result.returncode != 0 or not result.stdout.strip():
        return root
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    common_dir = common_dir.resolve()
    owner = common_dir.parent if common_dir.name == ".git" else root
    return owner if paths.project_manifest(owner).is_file() else root


def manifest_llm_defaults(
    project_root: str | Path,
) -> tuple[str | None, str | None, str | None]:
    defaults = read_manifest(project_root).get("defaults", {})
    if not isinstance(defaults, dict):
        return None, None, None
    engine = defaults.get("llm_engine")
    model = defaults.get("llm_model")
    effort = defaults.get("llm_effort")
    return (
        str(engine) if engine else None,
        str(model) if model else None,
        str(effort) if effort else None,
    )


def update_manifest_llm_defaults(
    project_root: str | Path,
    *,
    llm_engine: str,
    llm_model: str,
    llm_effort: str | None,
) -> Path:
    """Atomically replace only the project's persisted LLM defaults.

    ``None`` means provider default and removes ``defaults.llm_effort``.  A
    caller must validate the agent/model/effort combination against a fresh
    provider capability probe before invoking this storage primitive.
    """

    engine = str(llm_engine).strip()
    model = str(llm_model).strip()
    effort = str(llm_effort).strip() if llm_effort is not None else None
    if not engine:
        raise ValueError("llm_engine não pode ser vazio")
    if not model:
        raise ValueError("llm_model não pode ser vazio")
    if effort == "":
        effort = None

    manifest_path = paths.project_manifest(Path(project_root).resolve())
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"projeto não inicializado: {manifest_path} não existe"
        )
    manifest = _read_manifest_file(manifest_path)
    if not manifest:
        raise ManifestError(
            f"manifest inválido em {manifest_path}: arquivo vazio; "
            "execute ft migrate-layout . ou restaure o manifesto v2"
        )
    _validate_v2_manifest(manifest, manifest_path)
    existing_defaults = manifest.get("defaults")
    if existing_defaults is None:
        defaults: dict[str, Any] = {}
        manifest["defaults"] = defaults
    elif isinstance(existing_defaults, dict):
        defaults = existing_defaults
    else:
        raise ValueError("manifest inválido: defaults deve ser mapping")

    defaults["llm_engine"] = engine
    defaults["llm_model"] = model
    if effort is None or effort.lower() == "default":
        defaults.pop("llm_effort", None)
    else:
        defaults["llm_effort"] = effort
    raw_revision = manifest.get("llm_defaults_revision", 0)
    if not isinstance(raw_revision, int) or isinstance(raw_revision, bool) or raw_revision < 0:
        raise ValueError("manifest inválido: llm_defaults_revision deve ser inteiro >= 0")
    manifest["llm_defaults_revision"] = raw_revision + 1

    payload = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    original_mode = stat.S_IMODE(manifest_path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        fd, raw_temporary_path = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            dir=manifest_path.parent,
        )
        temporary_path = Path(raw_temporary_path)
        os.fchmod(fd, original_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, manifest_path)
        temporary_path = None

        # Persist the directory entry as well when the platform supports it.
        try:
            directory_fd = os.open(manifest_path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return manifest_path


def validate_template_is_pristine(template_dir: str | Path) -> None:
    """Reject templates containing runtime state or previous cycle data."""
    root = Path(template_dir)
    forbidden_names = {
        "engine_state.yml",
        "ft_state.yml",
        "llm_logs",
        "state",
        "runs",
        ".serve.pid",
        ".serve_url",
    }
    offenders: list[str] = []
    for item in root.rglob("*"):
        rel = item.relative_to(root)
        if item.name in forbidden_names or any(part.startswith("cycle-") for part in rel.parts):
            offenders.append(str(rel))
    if offenders:
        shown = ", ".join(offenders[:8])
        raise ValueError(f"template contém estado de execução: {shown}")


def _safe_relative(path: str) -> Path:
    rel = Path(path.rstrip("/"))
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise ValueError(f"path de artefato inseguro: {path}")
    return rel


def _normalized_cycle_paths(values: list[str] | tuple[str, ...]) -> list[Path]:
    candidates = sorted({_safe_relative(value) for value in values}, key=lambda p: len(p.parts))
    result: list[Path] = []
    for candidate in candidates:
        if any(candidate == parent or parent in candidate.parents for parent in result):
            continue
        result.append(candidate)
    return result


def _merge_move(src: Path, dst: Path) -> None:
    if src.is_dir() and not src.is_symlink():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _merge_move(child, dst / child.name)
        src.rmdir()
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _rewrite_process_references(content: str, *, broad: bool = False) -> str:
    rewritten = content.replace(
        ".ft/process/scripts", "__FT_PROCESS_SCRIPTS__"
    ).replace(
        ".ft/process/process.yml", "__FT_PROCESS_YAML__"
    ).replace(
        "process/scripts", ".ft/process/scripts"
    ).replace(
        "process/process.yml", ".ft/process/process.yml"
    ).replace(
        "__FT_PROCESS_SCRIPTS__", ".ft/process/scripts"
    ).replace(
        "__FT_PROCESS_YAML__", ".ft/process/process.yml"
    )
    if not broad:
        return rewritten

    rewritten = rewritten.replace(
        ' / ".ft" / "process"', "__FT_PATH_DOUBLE_QUOTED__"
    ).replace(
        " / '.ft' / 'process'", "__FT_PATH_SINGLE_QUOTED__"
    )
    rewritten = re.sub(
        r' / "process"(?=\s*(?:/|\)))',
        ' / ".ft" / "process"',
        rewritten,
    )
    rewritten = re.sub(
        r" / 'process'(?=\s*(?:/|\)))",
        " / '.ft' / 'process'",
        rewritten,
    )
    rewritten = re.sub(r"(?m)^(\s*)process/$", r"\1.ft/process/", rewritten)
    rewritten = rewritten.replace(
        "__FT_PATH_DOUBLE_QUOTED__", ' / ".ft" / "process"'
    ).replace(
        "__FT_PATH_SINGLE_QUOTED__", " / '.ft' / 'process'"
    ).replace(
        "../process/", "../.ft/process/"
    ).replace(
        '"process/', '".ft/process/'
    ).replace(
        "'process/", "'.ft/process/"
    ).replace(
        "`process/", "`.ft/process/"
    )
    rewritten = re.sub(
        r'(/ "\.ft" / "process"\)\.mkdir)\(\)',
        r"\1(parents=True)",
        rewritten,
    )
    rewritten = re.sub(
        r"(/ '\.ft' / 'process'\)\.mkdir)\(\)",
        r"\1(parents=True)",
        rewritten,
    )
    rewritten = rewritten.replace(
        "['docs', 'process', 'src']", "['docs', '.ft', 'src']"
    ).replace(
        '["docs", "process", "src"]', '["docs", ".ft", "src"]'
    ).replace(
        "docs/, process/ e src/", "docs/, .ft/ e src/"
    )
    auto_open = "(depth < 1 && PRIORITY.includes(name)) || prefix === '.ft/process/'"
    rewritten = rewritten.replace(auto_open, "__FT_AUTO_OPEN_PROCESS__").replace(
        "depth < 1 && PRIORITY.includes(name)", auto_open
    ).replace("__FT_AUTO_OPEN_PROCESS__", auto_open)
    return rewritten


def _rewrite_named_process_references(
    content: str,
    process_name: str,
    *,
    broad: bool = False,
) -> str:
    """Normalize legacy/flat references to one named v2 process bundle."""
    # Path validation is centralized in paths.project_named_process_dir.
    paths.project_named_process_dir(Path("."), process_name)
    named_root = f".ft/process/{process_name}"
    protected = content.replace(
        f"{named_root}/scripts", "__FT_SELECTED_PROCESS_SCRIPTS__"
    ).replace(
        f"{named_root}/process.yml", "__FT_SELECTED_PROCESS_YAML__"
    ).replace(
        f' / ".ft" / "process" / "{process_name}" / "scripts"',
        "__FT_SELECTED_PATH_SCRIPTS_DOUBLE__",
    ).replace(
        f" / '.ft' / 'process' / '{process_name}' / 'scripts'",
        "__FT_SELECTED_PATH_SCRIPTS_SINGLE__",
    ).replace(
        f' / ".ft" / "process" / "{process_name}" / "process.yml"',
        "__FT_SELECTED_PATH_YAML_DOUBLE__",
    ).replace(
        f" / '.ft' / 'process' / '{process_name}' / 'process.yml'",
        "__FT_SELECTED_PATH_YAML_SINGLE__",
    )
    rewritten = _rewrite_process_references(protected, broad=broad)
    rewritten = rewritten.replace(
        ".ft/process/scripts", f"{named_root}/scripts"
    ).replace(
        ".ft/process/process.yml", f"{named_root}/process.yml"
    )
    rewritten = rewritten.replace(
        ' / ".ft" / "process" / "scripts"',
        f' / ".ft" / "process" / "{process_name}" / "scripts"',
    ).replace(
        " / '.ft' / 'process' / 'scripts'",
        f" / '.ft' / 'process' / '{process_name}' / 'scripts'",
    ).replace(
        ' / ".ft" / "process" / "process.yml"',
        f' / ".ft" / "process" / "{process_name}" / "process.yml"',
    ).replace(
        " / '.ft' / 'process' / 'process.yml'",
        f" / '.ft' / 'process' / '{process_name}' / 'process.yml'",
    )
    return rewritten.replace(
        "__FT_SELECTED_PROCESS_SCRIPTS__", f"{named_root}/scripts"
    ).replace(
        "__FT_SELECTED_PROCESS_YAML__", f"{named_root}/process.yml"
    ).replace(
        "__FT_SELECTED_PATH_SCRIPTS_DOUBLE__",
        f' / ".ft" / "process" / "{process_name}" / "scripts"',
    ).replace(
        "__FT_SELECTED_PATH_SCRIPTS_SINGLE__",
        f" / '.ft' / 'process' / '{process_name}' / 'scripts'",
    ).replace(
        "__FT_SELECTED_PATH_YAML_DOUBLE__",
        f' / ".ft" / "process" / "{process_name}" / "process.yml"',
    ).replace(
        "__FT_SELECTED_PATH_YAML_SINGLE__",
        f" / '.ft' / 'process' / '{process_name}' / 'process.yml'",
    )


_PROJECT_TEXT_SUFFIXES = {
    ".cfg",
    ".cjs",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_PROJECT_TEXT_NAMES = {"Dockerfile", "Makefile"}


def _project_reference_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
    )
    if result.returncode == 0:
        relative_paths = [
            Path(value.decode("utf-8", errors="surrogateescape"))
            for value in result.stdout.split(b"\0")
            if value
        ]
    else:
        relative_paths = [path.relative_to(root) for path in root.rglob("*")]

    candidates: list[Path] = []
    for rel in relative_paths:
        if not rel.parts:
            continue
        if rel.parts[0] in {".git", ".venv", "node_modules"}:
            continue
        if rel == Path(".ft/manifest.yml") or rel.parts[:2] == (".ft", "process"):
            continue
        if len(rel.parts) >= 2 and rel.parts[:2] == (".ft", "cycles"):
            continue
        path = root / rel
        if not path.is_file():
            continue
        if path.suffix.lower() not in _PROJECT_TEXT_SUFFIXES and path.name not in _PROJECT_TEXT_NAMES:
            continue
        candidates.append(path)
    return sorted(set(candidates))


def _rewrite_project_reference_files(
    root: Path,
    *,
    dry_run: bool,
    process_name: str | None = None,
) -> int:
    changed = 0
    for path in _project_reference_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if process_name:
            rewritten = _rewrite_named_process_references(
                content,
                process_name,
                broad=path.name != "AGENTS.md",
            )
        else:
            rewritten = _rewrite_process_references(
                content,
                broad=path.name != "AGENTS.md",
            )
        if rewritten == content:
            continue
        changed += 1
        if not dry_run:
            path.write_text(rewritten, encoding="utf-8")
    return changed


def _artifact_policy(graph_meta: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    policy = (graph_meta or {}).get("artifact_policy", {})
    if not isinstance(policy, dict):
        policy = {}
    canonical = policy.get("canonical", DEFAULT_CANONICAL_ARTIFACTS)
    cycle = policy.get("cycle", DEFAULT_CYCLE_ARTIFACTS)
    if not isinstance(canonical, list):
        canonical = list(DEFAULT_CANONICAL_ARTIFACTS)
    if not isinstance(cycle, list):
        cycle = list(DEFAULT_CYCLE_ARTIFACTS)
    cycle_values = [str(value) for value in cycle]
    for value in SYSTEM_CYCLE_ARTIFACTS:
        if value not in cycle_values:
            cycle_values.append(value)
    return [str(value) for value in canonical], cycle_values


def is_cycle_artifact(path: str | Path, graph_meta: dict[str, Any] | None) -> bool:
    """Return whether ``path`` is safe to replace as a per-cycle artifact.

    Canonical entries take precedence over cycle entries. A directory that
    contains a canonical artifact is protected as well, so callers never
    remove a broad output directory along with durable project knowledge.
    """
    rel = _safe_relative(str(path))
    canonical_values, cycle_values = _artifact_policy(graph_meta)
    canonical = _normalized_cycle_paths(canonical_values)
    cycle = _normalized_cycle_paths(cycle_values)

    covered_by_cycle = any(candidate == rel or candidate in rel.parents for candidate in cycle)
    if not covered_by_cycle:
        return False

    conflicts_with_canonical = any(
        candidate == rel or candidate in rel.parents or rel in candidate.parents
        for candidate in canonical
    )
    return not conflicts_with_canonical


def _cycle_artifact_inventory(cycle_dir: Path) -> list[str]:
    return sorted(
        str(item.relative_to(cycle_dir))
        for item in cycle_dir.rglob("*")
        if item.is_file() and item.name != "cycle.yml"
    )


def _load_existing_cycle_record(record_path: Path) -> dict[str, Any] | None:
    if not record_path.exists() and not record_path.is_symlink():
        return None
    try:
        record = yaml.safe_load(record_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cycle.yml inválido em {record_path}: {exc}") from exc
    if not isinstance(record, dict):
        raise ManifestError(
            f"cycle.yml inválido em {record_path}: raiz deve ser mapping"
        )
    return record


def _write_imported_cycle_record(cycle_dir: Path, cycle_id: str) -> None:
    record_path = cycle_dir / "cycle.yml"
    record = _load_existing_cycle_record(record_path)
    if record is not None:
        record["artifacts"] = _cycle_artifact_inventory(cycle_dir)
        record_path.write_text(
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return
    record = {
        "schema_version": LAYOUT_VERSION,
        "id": cycle_id,
        "status": "done",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "progress": {"completed": 0, "total": "unknown"},
        "artifacts": _cycle_artifact_inventory(cycle_dir),
    }
    record_path.write_text(
        yaml.safe_dump(record, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _cycle_artifact_move_plan(
    root: Path,
    cycle_id: str,
    graph_meta: dict[str, Any] | None,
) -> list[tuple[Path, Path, str]]:
    cycle_dir = paths.project_cycle_dir(root, cycle_id)
    canonical_values, cycle_values = _artifact_policy(graph_meta)
    canonical = set(_normalized_cycle_paths(canonical_values))
    plan: list[tuple[Path, Path, str]] = []
    for rel in _normalized_cycle_paths(cycle_values):
        if rel in canonical:
            continue
        src = root / rel
        if not src.exists() and not src.is_symlink():
            continue
        dest_rel = (
            Path("build-marker.txt")
            if rel == Path(".build_ok")
            else Path(*rel.parts[1:]) if rel.parts[0] == "docs" else rel
        )
        plan.append((src, cycle_dir / dest_rel, str(dest_rel)))
    for log_file in sorted(root.glob("*_log.md")):
        plan.append((log_file, cycle_dir / "cycle-log.md", "cycle-log.md"))
    return plan


def archive_cycle_artifacts(
    project_root: str | Path,
    cycle_id: str,
    *,
    state: Any | None = None,
    graph_meta: dict[str, Any] | None = None,
    imported: bool = False,
    overwrite_existing: bool = True,
) -> ArchiveResult:
    """Move durable per-cycle outputs into ``.ft/cycles/<cycle_id>``.

    The function is idempotent and deliberately excludes raw state and LLM logs.
    """
    if not _SAFE_CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(f"id de ciclo inválido: {cycle_id}")

    root = Path(project_root)
    if not overwrite_existing:
        for guarded in (
            paths.project_ft_dir(root),
            paths.project_manifest(root),
            paths.project_process_dir(root),
            paths.project_cycles_dir(root),
            paths.project_cycle_dir(root, cycle_id),
            paths.project_cycle_dir(root, cycle_id) / "cycle.yml",
        ):
            _assert_project_local_path(root, guarded)
        _load_existing_cycle_record(
            paths.project_cycle_dir(root, cycle_id) / "cycle.yml"
        )
    ensure_project_layout(root)
    cycle_dir = paths.project_cycle_dir(root, cycle_id)
    cycle_dir.mkdir(parents=True, exist_ok=True)

    move_plan = _cycle_artifact_move_plan(root, cycle_id, graph_meta)
    if not overwrite_existing:
        _assert_move_plan_safe(move_plan, project_root=root)
    moved: list[str] = []
    move = _merge_move if overwrite_existing else _merge_move_without_overwrite
    for src, dst, label in move_plan:
        move(src, dst)
        moved.append(label)

    gate_log = getattr(state, "gate_log", {}) if state is not None else {}
    metrics = getattr(state, "metrics", {}) if state is not None else {}
    process_meta = graph_meta or {}
    selected_process_path = (
        getattr(state, "process_path", None) if state is not None else None
    )
    selected_process = _safe_manifest_process_path(root, selected_process_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "schema_version": LAYOUT_VERSION,
        "id": cycle_id,
        "status": (
            "done"
            if imported
            else getattr(state, "node_status", "unknown") if state is not None else "unknown"
        ),
        "process": {
            "id": (
                getattr(state, "process_id", None)
                if state is not None
                else process_meta.get("id")
            ),
            "version": (
                getattr(state, "version", None)
                if state is not None
                else process_meta.get("version")
            ),
            "path": selected_process_path,
            "template": getattr(state, "template_id", None) if state is not None else None,
            "initial_digest": (
                getattr(state, "process_digest", None) if state is not None else None
            ),
            "closed_digest": process_digest(selected_process) if selected_process else None,
        },
        "git": {
            "base_commit": getattr(state, "base_commit", None) if state is not None else None,
            "worktree_branch": (
                getattr(state, "worktree_branch", None) if state is not None else None
            ),
        },
        "llm": {
            "engine": getattr(state, "llm_engine", None) if state is not None else None,
            "model": getattr(state, "llm_model", None) if state is not None else None,
            "effort": getattr(state, "llm_effort", None) if state is not None else None,
        },
        "progress": {
            "completed": (
                "unknown"
                if imported
                else metrics.get("steps_completed", 0) if isinstance(metrics, dict) else 0
            ),
            "total": (
                "unknown"
                if imported
                else metrics.get("steps_total", 0) if isinstance(metrics, dict) else 0
            ),
        },
        "gate_summary": dict(Counter(gate_log.values())) if isinstance(gate_log, dict) else {},
        "artifacts": _cycle_artifact_inventory(cycle_dir),
    }
    if imported:
        record["imported_at"] = timestamp
    else:
        record["closed_at"] = timestamp
    record_path = cycle_dir / "cycle.yml"
    if overwrite_existing or not record_path.exists():
        record_path.write_text(
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        _write_imported_cycle_record(cycle_dir, cycle_id)
    return ArchiveResult(cycle_dir=cycle_dir, moved=tuple(sorted(set(moved))))


def latest_cycle_artifact(project_root: str | Path, filename: str) -> Path | None:
    cycles = paths.project_cycles_dir(project_root)
    if not cycles.is_dir():
        return None
    candidates = [path for path in cycles.glob(f"*/{filename}") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _legacy_process_yaml(legacy_process: Path) -> Path:
    preferred = legacy_process / "process.yml"
    if preferred.exists():
        return preferred
    preferred = legacy_process / "FAST_TRACK_PROCESS.yml"
    if preferred.exists():
        return preferred
    yamls = sorted(legacy_process.rglob("*.yml"))
    if not yamls:
        raise FileNotFoundError("nenhum YAML de processo encontrado em process/")
    return yamls[0]


def _legacy_runtime_candidates(root: Path) -> list[Path]:
    candidates = [root / "state", root / "runs"]
    for parent in (root, root / "src", root / "project"):
        candidates.extend(
            parent / name
            for name in (
                ".serve_url",
                ".serve.pid",
                ".serve_backend.pid",
                ".serve_frontend.pid",
                ".serve.log",
            )
        )
    return [path for path in candidates if path.exists() or path.is_symlink()]


def _backup_legacy_runtime(root: Path, actions: list[str], *, dry_run: bool) -> None:
    candidates = _legacy_runtime_candidates(root)
    if not candidates:
        return
    actions.append(
        f"{len(candidates)} item(ns) de runtime legado -> "
        f"{paths.migration_backups_home(root)}/<timestamp>/"
    )
    if dry_run:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = paths.migration_backups_home(root) / timestamp
    for src in candidates:
        rel = src.relative_to(root)
        _merge_move(src, backup / rel)


def _import_legacy_cycle_archives(root: Path, actions: list[str], *, dry_run: bool) -> None:
    archive_root = root / "docs" / "archive"
    if not archive_root.is_dir():
        return
    for source in sorted(path for path in archive_root.iterdir() if path.is_dir()):
        cycle_id = source.name
        if not _SAFE_CYCLE_RE.fullmatch(cycle_id):
            raise ValueError(f"id de ciclo legado inválido: {cycle_id}")
        actions.append(f"docs/archive/{cycle_id}/ -> .ft/cycles/{cycle_id}/")
        if dry_run:
            continue
        destination = paths.project_cycle_dir(root, cycle_id)
        _merge_move_without_overwrite(source, destination)
        _write_imported_cycle_record(destination, cycle_id)
    if not dry_run and archive_root.exists() and not any(archive_root.iterdir()):
        archive_root.rmdir()


def _import_named_handoffs(root: Path, actions: list[str], *, dry_run: bool) -> None:
    docs_dir = root / "docs"
    handoffs = sorted(docs_dir.glob("handoff-*.md")) if docs_dir.is_dir() else []
    if not handoffs:
        return
    actions.append(
        f"{len(handoffs)} handoff(s) histórico(s) -> .ft/cycles/legacy-unscoped/"
    )
    if dry_run:
        return
    destination = paths.project_cycle_dir(root, "legacy-unscoped")
    for source in handoffs:
        _merge_move_without_overwrite(source, destination / source.name)
    _write_imported_cycle_record(destination, "legacy-unscoped")


def _slug_process_name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = slug.strip("-.")
    if not slug:
        return None
    try:
        paths.project_named_process_dir(Path("."), slug)
    except ValueError:
        return None
    return slug


def _canonical_legacy_process_name(value: object) -> str | None:
    candidate = _slug_process_name(value)
    return {
        "fast_track_v3": "mvp-builder",
        "fast-track-v3": "mvp-builder",
        "mvp_builder": "mvp-builder",
    }.get(candidate, candidate)


def _infer_legacy_default_name(
    manifest: dict[str, Any],
    process_file: Path,
) -> str:
    processes = manifest.get("processes", {})
    if isinstance(processes, dict):
        for name, record in processes.items():
            if not isinstance(record, dict):
                continue
            raw_path = record.get("path")
            if raw_path in {".ft/process/process.yml", "process/process.yml"}:
                candidate = _canonical_legacy_process_name(name)
                if candidate:
                    return candidate

    origin = manifest.get("template") or manifest.get("origin_template")
    if isinstance(origin, dict):
        origin = origin.get("id")
    if origin == "migrated-local":
        origin = None
    candidate = _canonical_legacy_process_name(origin)
    if candidate:
        return candidate

    payload = _read_yaml(process_file)
    policy = payload.get("execution_policy", {}) if isinstance(payload, dict) else {}
    if isinstance(policy, dict):
        candidate = _canonical_legacy_process_name(policy.get("template"))
        if candidate:
            return candidate
    candidate = _canonical_legacy_process_name(
        payload.get("id") if isinstance(payload, dict) else None
    )
    return candidate or "default"


def _assert_no_runtime_cycles(root: Path) -> None:
    state_files: list[Path] = []
    worktrees = paths.worktrees_home(root)
    if worktrees.is_dir():
        state_files.extend(worktrees.glob("*/state/engine_state.yml"))
    continuous = paths.continuous_state_path(root)
    if continuous.is_file():
        state_files.append(continuous)
    legacy_state = root / "state" / "engine_state.yml"
    if legacy_state.is_file() and _legacy_state_represents_runtime(legacy_state):
        state_files.append(legacy_state)
    if state_files:
        locations = ", ".join(str(path) for path in sorted(state_files))
        raise RuntimeError(
            "não é seguro migrar processos com ciclo/runtime presente; "
            f"feche ou aborte antes: {locations}"
        )


def _legacy_state_represents_runtime(state_file: Path) -> bool:
    """Distinguish an active legacy cycle from an inert file to be archived."""
    data = _read_yaml(state_file)
    if not data:
        return False
    if data.get("current_node") not in (None, ""):
        return True
    status = str(data.get("node_status") or "").strip().lower()
    if status in {"done", "completed", "failed", "aborted", "cancelled", "canceled"}:
        return False
    if status not in {"", "ready", "idle"}:
        return True
    evidence_fields = (
        "completed_nodes",
        "gate_log",
        "artifacts",
        "pending_approval",
        "pending_fix",
        "blocked_reason",
        "active_llm_log",
        "last_llm_log",
    )
    if any(data.get(field) for field in evidence_fields):
        return True
    metrics = data.get("metrics", {})
    if isinstance(metrics, dict):
        return any(
            key != "steps_total" and value not in (None, "", 0, 0.0, [], {})
            for key, value in metrics.items()
        )
    return False


def _assert_merge_move_safe(src: Path, dst: Path) -> None:
    """Preflight a recursive merge move without changing either tree."""
    if src.is_symlink():
        raise ValueError(f"migração recusou link simbólico: {src}")
    if src.is_dir():
        if dst.exists() and (not dst.is_dir() or dst.is_symlink()):
            raise FileExistsError(f"conflito durante migração: {src} -> {dst}")
        for child in src.iterdir():
            _assert_merge_move_safe(child, dst / child.name)
        return
    if src.is_file():
        if not dst.exists():
            return
        if dst.is_file() and not dst.is_symlink() and src.read_bytes() == dst.read_bytes():
            return
        raise FileExistsError(f"conflito durante migração: {src} -> {dst}")
    raise ValueError(f"fonte de migração inválida: {src}")


def _assert_project_local_path(root: Path, candidate: Path) -> None:
    """Reject paths that escape the project or traverse a symlink ancestor."""
    project = root.resolve()
    raw = candidate if candidate.is_absolute() else project / candidate
    try:
        relative = raw.relative_to(project)
    except ValueError as exc:
        raise ValueError(f"path de migração escapa do projeto: {candidate}") from exc
    if ".." in relative.parts:
        raise ValueError(f"path de migração escapa do projeto: {candidate}")

    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"migração recusou link simbólico ancestral: {current}")
    try:
        raw.resolve(strict=False).relative_to(project)
    except ValueError as exc:
        raise ValueError(f"path de migração escapa do projeto: {candidate}") from exc


def _assert_move_plan_safe(
    plan: list[tuple[Path, Path, str]],
    *,
    project_root: Path | None = None,
) -> None:
    """Validate all move destinations, including collisions within the plan."""
    planned_files: dict[Path, Path] = {}

    def inventory(src: Path, dst: Path) -> None:
        if src.is_symlink():
            raise ValueError(f"migração recusou link simbólico: {src}")
        if src.is_dir():
            for child in src.iterdir():
                inventory(child, dst / child.name)
            return
        if not src.is_file():
            raise ValueError(f"fonte de migração inválida: {src}")
        destination = dst.absolute()
        previous = planned_files.get(destination)
        if previous is not None and previous.read_bytes() != src.read_bytes():
            raise FileExistsError(
                "fontes legadas divergentes apontam para o mesmo destino: "
                f"{previous} e {src} -> {dst}"
            )
        planned_files[destination] = src

    for src, dst, _label in plan:
        if project_root is not None:
            _assert_project_local_path(project_root, src)
            _assert_project_local_path(project_root, dst)
        _assert_merge_move_safe(src, dst)
        inventory(src, dst)


def _legacy_history_move_plan(
    root: Path,
    cycle_id: str,
    graph_meta: dict[str, Any] | None,
) -> list[tuple[Path, Path, str]]:
    plan: list[tuple[Path, Path, str]] = []
    archive_root = root / "docs" / "archive"
    if archive_root.is_dir():
        for source in sorted(path for path in archive_root.iterdir() if path.is_dir()):
            if not _SAFE_CYCLE_RE.fullmatch(source.name):
                raise ValueError(f"id de ciclo legado inválido: {source.name}")
            plan.append(
                (
                    source,
                    paths.project_cycle_dir(root, source.name),
                    f"docs/archive/{source.name}/",
                )
            )
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for source in sorted(docs_dir.glob("handoff-*.md")):
            plan.append(
                (
                    source,
                    paths.project_cycle_dir(root, "legacy-unscoped") / source.name,
                    f"docs/{source.name}",
                )
            )
    plan.extend(_cycle_artifact_move_plan(root, cycle_id, graph_meta))
    return plan


def _validate_history_cycle_records(
    root: Path,
    cycle_id: str,
    plan: list[tuple[Path, Path, str]],
) -> None:
    cycles_root = paths.project_cycles_dir(root)
    cycle_names = {cycle_id}
    for _src, destination, _label in plan:
        try:
            relative = destination.relative_to(cycles_root)
        except ValueError:
            continue
        if relative.parts:
            cycle_names.add(relative.parts[0])
    for name in sorted(cycle_names):
        record_path = paths.project_cycle_dir(root, name) / "cycle.yml"
        _assert_project_local_path(root, record_path)
        _load_existing_cycle_record(record_path)


def _merge_move_without_overwrite(src: Path, dst: Path) -> None:
    """Move a migration source while refusing to overwrite divergent user data."""
    if src.is_symlink():
        raise ValueError(f"migração recusou link simbólico: {src}")
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return
    if src.is_dir() and dst.is_dir() and not dst.is_symlink():
        for child in list(src.iterdir()):
            _merge_move_without_overwrite(child, dst / child.name)
        src.rmdir()
        return
    if src.is_file() and dst.is_file() and src.read_bytes() == dst.read_bytes():
        src.unlink()
        return
    raise FileExistsError(f"conflito durante migração: {src} -> {dst}")


def _rewrite_named_bundle(bundle: Path, process_name: str) -> int:
    changed = 0
    for path in bundle.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in _PROJECT_TEXT_SUFFIXES and path.name not in _PROJECT_TEXT_NAMES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rewritten = _rewrite_named_process_references(content, process_name, broad=True)
        if path.is_relative_to(bundle / "scripts"):
            bash_root = '$(dirname "${BASH_SOURCE[0]}")/../../../..'
            project_root = '$(dirname "$0")/../../../../project'
            rewritten = rewritten.replace(bash_root, "__FT_NAMED_BASH_ROOT__")
            for legacy in (
                '$(dirname "${BASH_SOURCE[0]}")/../../..',
                '$(dirname "${BASH_SOURCE[0]}")/../..',
            ):
                rewritten = rewritten.replace(legacy, "__FT_NAMED_BASH_ROOT__")
            rewritten = rewritten.replace(
                "__FT_NAMED_BASH_ROOT__", bash_root
            )
            rewritten = rewritten.replace(project_root, "__FT_NAMED_PROJECT_ROOT__")
            for legacy in (
                '$(dirname "$0")/../../../project',
                '$(dirname "$0")/../../project',
            ):
                rewritten = rewritten.replace(legacy, "__FT_NAMED_PROJECT_ROOT__")
            rewritten = rewritten.replace(
                "__FT_NAMED_PROJECT_ROOT__", project_root
            )
        if rewritten != content:
            path.write_text(rewritten, encoding="utf-8")
            changed += 1
    return changed


def _v2_manifest_from_legacy(
    manifest: dict[str, Any],
    *,
    root: Path,
    default_name: str,
    default_process: Path,
    digest_source: Path | None = None,
) -> dict[str, Any]:
    migrated = {
        key: value
        for key, value in manifest.items()
        if key not in {"schema_version", "process", "template", "origin_template", "processes"}
    }
    migrated["schema_version"] = LAYOUT_VERSION
    migrated["default_process"] = default_name
    new_processes: dict[str, Any] = {}
    normalized_sources: dict[str, object] = {}
    old_processes = manifest.get("processes", {})
    if isinstance(old_processes, dict):
        for raw_name, raw_record in old_processes.items():
            name = _slug_process_name(raw_name)
            if not name or not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            if record.get("path") in {".ft/process/process.yml", "process/process.yml"}:
                name = default_name
                record["path"] = default_process.relative_to(root).as_posix()
                record.setdefault("entrypoint", "init")
            else:
                expected = paths.project_named_process_file(root, name)
                if expected.is_file():
                    record["path"] = expected.relative_to(root).as_posix()
            if name in normalized_sources and normalized_sources[name] != raw_name:
                raise ManifestError(
                    "manifest legado possui nomes de processo que colidem após "
                    f"normalização: {raw_name!r} -> {name!r}"
                )
            normalized_sources[name] = raw_name
            new_processes[name] = record

    origin = manifest.get("template") or manifest.get("origin_template")
    if isinstance(origin, dict):
        origin = origin.get("id")
    if origin == "migrated-local":
        origin = None
    template_name = _canonical_legacy_process_name(origin) or default_name
    default_record = dict(new_processes.get(default_name, {}))
    default_record.update({
        "path": default_process.relative_to(root).as_posix(),
        "template": default_record.get("template") or template_name,
        "entrypoint": default_record.get("entrypoint") or "init",
    })
    default_record["base_digest"] = process_digest(digest_source or default_process)
    new_processes[default_name] = default_record
    migrated["processes"] = new_processes
    return migrated


def migrate_legacy_layout(
    project_root: str | Path,
    *,
    dry_run: bool = False,
    cycle_id: str = "legacy-unscoped",
) -> list[str]:
    """Migrate a singular v1 process bundle into the uniform named v2 catalog."""
    root = Path(project_root).resolve()
    legacy_process = root / "process"
    catalog = paths.project_process_dir(root)
    flat_process = paths.legacy_flat_process_file(root)
    manifest_path = paths.project_manifest(root)
    actions: list[str] = []

    if not _SAFE_CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(f"id de ciclo inválido: {cycle_id}")

    for guarded in (
        paths.project_ft_dir(root),
        manifest_path,
        catalog,
        paths.project_cycles_dir(root),
        paths.project_cycle_dir(root, "legacy-unscoped"),
        paths.project_cycle_dir(root, cycle_id),
        legacy_process,
    ):
        _assert_project_local_path(root, guarded)

    manifest = _read_manifest_file(manifest_path)
    schema_version = manifest.get("schema_version")
    if schema_version not in (None, 1, LAYOUT_VERSION):
        raise ManifestError(
            f"schema_version não suportado em {manifest_path}: {schema_version!r}; "
            "migração automática recusada"
        )
    is_v2 = manifest.get("schema_version") == LAYOUT_VERSION
    has_legacy_manifest_keys = any(
        key in manifest for key in ("process", "template", "origin_template")
    )
    if is_v2 and not has_legacy_manifest_keys:
        _validate_v2_manifest(manifest, manifest_path)
        if flat_process.exists() or legacy_process.exists():
            raise FileExistsError(
                "layout v2 coexiste com processo flat/legado; remova a ambiguidade"
            )
        return ["layout v2 canônico já presente"]
    if is_v2 and has_legacy_manifest_keys and not (
        flat_process.is_file() or legacy_process.is_dir()
    ):
        raise ManifestError(
            "manifesto híbrido v2 contém chaves legadas, mas não existe processo "
            "flat para migrar; remova process/template/origin_template manualmente"
        )

    _assert_no_runtime_cycles(root)

    if legacy_process.exists() and flat_process.exists():
        raise FileExistsError(
            "process/ e .ft/process/process.yml coexistem; remova a ambiguidade antes de migrar"
        )
    if legacy_process.is_dir():
        if catalog.exists() and any(catalog.iterdir()):
            raise FileExistsError(
                "process/ e .ft/process/ coexistem; remova a ambiguidade antes de migrar"
            )
        source_process = _legacy_process_yaml(legacy_process)
        source_root = legacy_process
        source_label = "process/"
    elif flat_process.is_file():
        source_process = flat_process
        source_root = catalog
        source_label = ".ft/process/process.yml + bundle flat"
    else:
        raise FileNotFoundError(
            "layout v1 não encontrado; esperado process/ ou .ft/process/process.yml"
        )

    # Refuse to turn a malformed legacy graph into the canonical v2 default.
    from ft.engine.graph import load_graph

    load_graph(source_process)

    default_name = _infer_legacy_default_name(manifest, source_process)
    destination = paths.project_named_process_dir(root, default_name)
    canonical = paths.project_named_process_file(root, default_name)
    _assert_project_local_path(root, destination)
    _assert_project_local_path(root, canonical)
    actions.append(
        f"{source_label} -> .ft/process/{default_name}/"
    )

    candidate_manifest = _v2_manifest_from_legacy(
        manifest,
        root=root,
        default_name=default_name,
        default_process=canonical,
        digest_source=source_process,
    )
    _validate_v2_manifest(candidate_manifest, manifest_path)
    for process_name in candidate_manifest.get("processes", {}):
        if process_name == default_name:
            continue
        candidate_process = paths.project_named_process_file(root, process_name)
        _assert_project_local_path(root, candidate_process)
        if not candidate_process.is_file():
            raise ManifestError(
                "manifest legado referencia processo nomeado ausente: "
                f".ft/process/{process_name}/process.yml"
            )
        load_graph(candidate_process)

    movable_children: list[Path] = []
    process_move_plan: list[tuple[Path, Path, str]] = []
    if source_root == legacy_process:
        process_move_plan.append((legacy_process, destination, source_label))
        source_relative = source_process.relative_to(legacy_process)
        if source_relative.as_posix() != "process.yml":
            _assert_merge_move_safe(source_process, destination / "process.yml")
    else:
        named_dirs: set[str] = set()
        old_processes = manifest.get("processes", {})
        if isinstance(old_processes, dict):
            for record in old_processes.values():
                if not isinstance(record, dict):
                    continue
                raw_path = record.get("path")
                if not isinstance(raw_path, str):
                    continue
                parts = Path(raw_path).parts
                if len(parts) >= 4 and parts[:2] == (".ft", "process"):
                    named_dirs.add(parts[2])
        movable_children = [
            child
            for child in catalog.iterdir()
            if child != destination
            and not (
                child.is_dir()
                and (child.name in named_dirs or (child / "process.yml").is_file())
            )
        ]
        process_move_plan.extend(
            (child, destination / child.name, str(child.relative_to(root)))
            for child in movable_children
        )

    history_move_plan = _legacy_history_move_plan(
        root,
        cycle_id,
        _read_yaml(source_process),
    )
    _validate_history_cycle_records(root, cycle_id, history_move_plan)
    _assert_move_plan_safe(
        process_move_plan + history_move_plan,
        project_root=root,
    )

    if dry_run:
        _import_legacy_cycle_archives(root, actions, dry_run=True)
        _import_named_handoffs(root, actions, dry_run=True)
        loose = sum(
            1 for rel in DEFAULT_CYCLE_ARTIFACTS if (root / rel.rstrip("/")).exists()
        )
        if loose:
            actions.append(f"{loose} artefato(s) solto(s) -> .ft/cycles/{cycle_id}/")
        changed = _rewrite_project_reference_files(
            root,
            dry_run=True,
            process_name=default_name,
        )
        if changed:
            actions.append(
                f"{changed} arquivo(s) atuais serão atualizados para o processo nomeado"
            )
        _backup_legacy_runtime(root, actions, dry_run=True)
        return actions

    catalog.mkdir(parents=True, exist_ok=True)
    if source_root == legacy_process:
        _merge_move_without_overwrite(legacy_process, destination)
        preferred = destination / source_process.relative_to(legacy_process)
    else:
        destination.mkdir(parents=True, exist_ok=True)
        for child in movable_children:
            _merge_move_without_overwrite(child, destination / child.name)
        preferred = destination / "process.yml"

    if not canonical.exists():
        if not preferred.is_file():
            raise FileNotFoundError(f"YAML principal não encontrado após migração: {preferred}")
        _merge_move_without_overwrite(preferred, canonical)

    rewritten_count = _rewrite_named_bundle(destination, default_name)
    if rewritten_count:
        actions.append(
            f"{rewritten_count} arquivo(s) do bundle atualizados para o path nomeado"
        )

    migrated_manifest = _v2_manifest_from_legacy(
        manifest,
        root=root,
        default_name=default_name,
        default_process=canonical,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        yaml.safe_dump(migrated_manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    ensure_project_layout(root)
    _import_legacy_cycle_archives(root, actions, dry_run=False)
    _import_named_handoffs(root, actions, dry_run=False)
    archived = archive_cycle_artifacts(
        root,
        cycle_id,
        graph_meta=_read_yaml(canonical),
        imported=True,
        overwrite_existing=False,
    )
    if archived.moved:
        actions.append(
            f"{len(archived.moved)} artefato(s) solto(s) -> .ft/cycles/{cycle_id}/"
        )
    changed = _rewrite_project_reference_files(
        root,
        dry_run=False,
        process_name=default_name,
    )
    if changed:
        actions.append(
            f"{changed} arquivo(s) atuais atualizados para .ft/process/{default_name}/"
        )
    _backup_legacy_runtime(root, actions, dry_run=False)

    return actions
