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


LAYOUT_VERSION = 1

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

    scripts_dir = requested_process.parent / "scripts"
    if scripts_dir.is_dir():
        for candidate in scripts_dir.rglob("*"):
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


def resolve_project_process(
    project_root: str | Path,
    process_name: str | None = None,
) -> Path | None:
    """Resolve a project-owned process without consulting the global catalog."""
    root = Path(project_root).resolve()
    manifest = _read_yaml(paths.project_manifest(root))
    if process_name:
        processes = manifest.get("processes", {})
        if isinstance(processes, dict):
            record = processes.get(process_name, {})
            if isinstance(record, dict):
                candidate = _safe_manifest_process_path(root, record.get("path"))
                if candidate and candidate.is_file():
                    return candidate
        conventional = paths.project_named_process_file(root, process_name)
        return conventional if conventional.is_file() else None

    candidate = _safe_manifest_process_path(root, manifest.get("process"))
    if candidate and candidate.is_file():
        return candidate
    legacy = paths.project_process_file(root)
    if legacy.is_file():
        return legacy
    return None


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
    manifest = _read_yaml(manifest_path)
    process_file = Path(process_path).resolve()
    try:
        relative = process_file.relative_to(root).as_posix()
        process_file.relative_to(paths.project_process_dir(root).resolve())
    except ValueError as exc:
        raise ValueError("processo registrado deve estar dentro de .ft/process/") from exc

    processes = manifest.setdefault("processes", {})
    if not isinstance(processes, dict):
        processes = {}
        manifest["processes"] = processes
    processes[process_name] = {
        "path": relative,
        "template": template_id,
        "entrypoint": entrypoint,
        "source_digest": source_digest,
        "base_digest": process_digest(process_file),
    }
    if set_default:
        manifest["process"] = relative
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return manifest_path


def ensure_project_layout(
    project_root: str | Path,
    *,
    template_id: str | None = None,
    defaults: dict[str, Any] | None = None,
) -> Path:
    """Create tracked layout metadata without creating any execution state."""
    root = Path(project_root)
    ft_dir = paths.project_ft_dir(root)
    process_dir = paths.project_process_dir(root)
    cycles_dir = paths.project_cycles_dir(root)
    process_dir.mkdir(parents=True, exist_ok=True)
    cycles_dir.mkdir(parents=True, exist_ok=True)

    ignore = ft_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text(PROJECT_GITIGNORE, encoding="utf-8")

    keep = cycles_dir / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")

    manifest_path = paths.project_manifest(root)
    manifest = _read_yaml(manifest_path)
    manifest["schema_version"] = LAYOUT_VERSION
    manifest.setdefault("process", ".ft/process/process.yml")
    if template_id:
        template = manifest.setdefault("template", {})
        template["id"] = template_id
        template["base_digest"] = process_digest(paths.project_process_file(root))
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
    return _read_yaml(paths.project_manifest(project_root))


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
    try:
        loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"manifest inválido: {manifest_path}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"manifest inválido: raiz YAML deve ser mapping em {manifest_path}")

    manifest: dict[str, Any] = loaded
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
        if len(rel.parts) >= 2 and rel.parts[:2] == (".ft", "cycles"):
            continue
        path = root / rel
        if not path.is_file():
            continue
        if path.suffix.lower() not in _PROJECT_TEXT_SUFFIXES and path.name not in _PROJECT_TEXT_NAMES:
            continue
        candidates.append(path)
    return sorted(set(candidates))


def _rewrite_project_reference_files(root: Path, *, dry_run: bool) -> int:
    changed = 0
    for path in _project_reference_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
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


def _write_imported_cycle_record(cycle_dir: Path, cycle_id: str) -> None:
    record_path = cycle_dir / "cycle.yml"
    if record_path.exists():
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


def archive_cycle_artifacts(
    project_root: str | Path,
    cycle_id: str,
    *,
    state: Any | None = None,
    graph_meta: dict[str, Any] | None = None,
    imported: bool = False,
) -> ArchiveResult:
    """Move durable per-cycle outputs into ``.ft/cycles/<cycle_id>``.

    The function is idempotent and deliberately excludes raw state and LLM logs.
    """
    if not _SAFE_CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(f"id de ciclo inválido: {cycle_id}")

    root = Path(project_root)
    ensure_project_layout(root)
    cycle_dir = paths.project_cycle_dir(root, cycle_id)
    cycle_dir.mkdir(parents=True, exist_ok=True)

    canonical_values, cycle_values = _artifact_policy(graph_meta)
    canonical = set(_normalized_cycle_paths(canonical_values))
    moved: list[str] = []
    for rel in _normalized_cycle_paths(cycle_values):
        if rel in canonical:
            continue
        src = root / rel
        if not src.exists() and not src.is_symlink():
            continue
        if rel == Path(".build_ok"):
            dest_rel = Path("build-marker.txt")
        else:
            dest_rel = Path(*rel.parts[1:]) if rel.parts[0] == "docs" else rel
        dst = cycle_dir / dest_rel
        _merge_move(src, dst)
        moved.append(str(dest_rel))

    for log_file in sorted(root.glob("*_log.md")):
        dst = cycle_dir / "cycle-log.md"
        _merge_move(log_file, dst)
        moved.append("cycle-log.md")

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
    (cycle_dir / "cycle.yml").write_text(
        yaml.safe_dump(record, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
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
        _merge_move(source, destination)
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
        _merge_move(source, destination / source.name)
    _write_imported_cycle_record(destination, "legacy-unscoped")


def migrate_legacy_layout(
    project_root: str | Path,
    *,
    dry_run: bool = False,
    cycle_id: str = "legacy-unscoped",
) -> list[str]:
    """Move the former ``process/`` layout and loose cycle docs explicitly."""
    root = Path(project_root).resolve()
    legacy_process = root / "process"
    target_process = paths.project_process_dir(root)
    actions: list[str] = []

    if not _SAFE_CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(f"id de ciclo inválido: {cycle_id}")

    if paths.project_process_file(root).exists():
        if legacy_process.exists():
            raise FileExistsError("process/ e .ft/process/ coexistem; remova a ambiguidade antes de migrar")
        changed = _rewrite_project_reference_files(root, dry_run=dry_run)
        if not dry_run:
            ensure_project_layout(root)
        actions.append("layout canônico já presente")
        if changed:
            actions.append(f"{changed} arquivo(s) atualizados para referências .ft/process/")
        return actions
    if not legacy_process.is_dir():
        raise FileNotFoundError("process/ legado não encontrado")
    source_process = _legacy_process_yaml(legacy_process)

    actions.append("process/ -> .ft/process/")
    if not dry_run:
        target_process.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_process), str(target_process))

        canonical = target_process / "process.yml"
        if not canonical.exists():
            preferred = target_process / source_process.relative_to(legacy_process)
            if preferred.parent == target_process:
                preferred.rename(canonical)
            else:
                shutil.copy2(preferred, canonical)

        process_text = canonical.read_text(encoding="utf-8")
        rewritten = _rewrite_process_references(process_text, broad=True)
        if rewritten != process_text:
            canonical.write_text(rewritten, encoding="utf-8")
            actions.append("referências internas do processo atualizadas para .ft/process/")

        for script in (target_process / "scripts").glob("*") if (target_process / "scripts").is_dir() else ():
            if not script.is_file():
                continue
            try:
                script_text = script.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            script_rewritten = _rewrite_process_references(script_text, broad=True)
            if '$(dirname "${BASH_SOURCE[0]}")/../../..' not in script_rewritten:
                script_rewritten = script_rewritten.replace(
                    '$(dirname "${BASH_SOURCE[0]}")/../..',
                    '$(dirname "${BASH_SOURCE[0]}")/../../..',
                )
            if '$(dirname "$0")/../../../project' not in script_rewritten:
                script_rewritten = script_rewritten.replace(
                    '$(dirname "$0")/../../project',
                    '$(dirname "$0")/../../../project',
                )
            if script_rewritten != script_text:
                script.write_text(script_rewritten, encoding="utf-8")

        ensure_project_layout(root, template_id="migrated-local")
        _import_legacy_cycle_archives(root, actions, dry_run=False)
        _import_named_handoffs(root, actions, dry_run=False)
        archived = archive_cycle_artifacts(
            root,
            cycle_id,
            graph_meta=_read_yaml(canonical),
            imported=True,
        )
        if archived.moved:
            actions.append(
                f"{len(archived.moved)} artefato(s) solto(s) -> .ft/cycles/{cycle_id}/"
            )
        changed = _rewrite_project_reference_files(root, dry_run=False)
        if changed:
            actions.append(f"{changed} arquivo(s) atuais atualizados para .ft/process/")
        _backup_legacy_runtime(root, actions, dry_run=False)
    else:
        _import_legacy_cycle_archives(root, actions, dry_run=True)
        _import_named_handoffs(root, actions, dry_run=True)
        loose = sum(1 for rel in DEFAULT_CYCLE_ARTIFACTS if (root / rel.rstrip("/")).exists())
        if loose:
            actions.append(f"{loose} artefato(s) solto(s) -> .ft/cycles/{cycle_id}/")
        changed = _rewrite_project_reference_files(root, dry_run=True)
        if changed:
            actions.append(f"{changed} arquivo(s) atuais serão atualizados para .ft/process/")
        _backup_legacy_runtime(root, actions, dry_run=True)

    return actions
