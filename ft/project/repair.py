"""Read-only health checks and conservative repair of FT workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from typing import Any
from uuid import uuid4

import yaml

from ft.engine import paths
from ft.engine.layout import (
    LAYOUT_VERSION,
    LEGACY_NAMED_LAYOUT_VERSION,
    PROJECT_GITIGNORE,
    ManifestError,
    _atomic_write_manifest,
    _manifest_for_v3_write,
    _manifest_write_lock,
    _read_manifest_file,
    _validate_manifest,
    process_digest,
)
from ft.project.bootstrap import _copy_agents_playbook
from ft.project.migration import migrate_v2_manifest


@dataclass(frozen=True)
class ProjectIssue:
    code: str
    message: str
    severity: str = "error"
    path: Path | None = None
    repairable: bool = False


@dataclass(frozen=True)
class ProjectCheckResult:
    root: Path
    status: str
    issues: tuple[ProjectIssue, ...]

    @property
    def healthy(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[ProjectIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")


@dataclass(frozen=True)
class ProjectRepairResult:
    root: Path
    status: str
    actions: tuple[str, ...]
    remaining: tuple[ProjectIssue, ...]
    backup_dir: Path | None = None

    @property
    def repaired(self) -> bool:
        return self.status == "repaired"

    @property
    def errors(self) -> tuple[ProjectIssue, ...]:
        return tuple(issue for issue in self.remaining if issue.severity == "error")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _raw_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "raiz deve ser mapping"
    return payload, None


def _catalog_bundles(root: Path) -> dict[str, Path]:
    catalog = paths.project_process_dir(root)
    if not catalog.is_dir() or catalog.is_symlink():
        return {}
    bundles: dict[str, Path] = {}
    for directory in sorted(catalog.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        process_file = directory / "process.yml"
        if process_file.is_file() and not process_file.is_symlink():
            try:
                paths.project_named_process_dir(root, directory.name)
            except ValueError:
                continue
            bundles[directory.name] = process_file
    return bundles


def check_project(project_root: str | Path) -> ProjectCheckResult:
    """Inspect Git, metadata, catalog, and history without writing anything."""
    requested = Path(project_root).expanduser()
    root = requested.resolve()
    issues: list[ProjectIssue] = []
    if requested.is_symlink():
        issues.append(ProjectIssue("project.symlink", "raiz do projeto é link simbólico", path=requested))
    if not root.is_dir():
        issues.append(ProjectIssue("project.missing", "diretório do projeto ausente", path=root))
        return ProjectCheckResult(root, "broken", tuple(issues))

    git_entry = root / ".git"
    top = _git(root, "rev-parse", "--show-toplevel")
    if not git_entry.exists() or top is None or top.returncode != 0:
        issues.append(ProjectIssue("git.missing", "repositório Git próprio ausente", path=git_entry))
    else:
        try:
            owns_repo = Path(top.stdout.strip()).resolve() == root
        except OSError:
            owns_repo = False
        if not owns_repo:
            issues.append(ProjectIssue("git.not_root", "projeto não é a raiz do repositório Git", path=root))
        head = _git(root, "rev-parse", "--verify", "HEAD")
        if head is None or head.returncode != 0:
            issues.append(ProjectIssue("git.head_missing", "repositório Git não possui HEAD", path=git_entry))
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        if status is None or status.returncode != 0:
            issues.append(ProjectIssue("git.status_failed", "não foi possível consultar o status Git", path=git_entry))
        elif status.stdout.strip():
            issues.append(
                ProjectIssue(
                    "git.dirty",
                    "checkout possui mudanças não commitadas",
                    severity="warning",
                    path=root,
                )
            )
        worktrees = _git(root, "worktree", "list", "--porcelain")
        if worktrees is None or worktrees.returncode != 0:
            issues.append(
                ProjectIssue(
                    "git.worktrees_failed",
                    "não foi possível consultar os worktrees Git",
                    path=git_entry,
                )
            )

    guarded_directories = (
        (paths.project_ft_dir(root), "layout.ft_missing"),
        (paths.project_process_dir(root), "layout.process_missing"),
        (paths.project_cycles_dir(root), "layout.cycles_missing"),
    )
    for directory, code in guarded_directories:
        if directory.is_symlink():
            issues.append(ProjectIssue(code + ".symlink", "diretório estrutural é link simbólico", path=directory))
        elif not directory.is_dir():
            issues.append(ProjectIssue(code, "diretório estrutural ausente", path=directory, repairable=True))

    for file_path, code in (
        (paths.project_ft_dir(root) / ".gitignore", "layout.gitignore_missing"),
        (paths.project_cycles_dir(root) / ".gitkeep", "layout.cycles_keep_missing"),
        (paths.project_process_dir(root) / ".gitkeep", "layout.process_keep_missing"),
        (root / "AGENTS.md", "layout.agents_missing"),
    ):
        if file_path.is_symlink():
            issues.append(ProjectIssue(code + ".symlink", "arquivo estrutural é link simbólico", path=file_path))
        elif not file_path.is_file():
            issues.append(ProjectIssue(code, "arquivo estrutural ausente", path=file_path, repairable=True))

    manifest_path = paths.project_manifest(root)
    unsafe_manifest = paths.project_ft_dir(root).is_symlink() or manifest_path.is_symlink()
    if unsafe_manifest:
        issues.append(ProjectIssue("manifest.symlink", "manifest não pode ser link simbólico", path=manifest_path))
        manifest = None
        parse_error = None
    else:
        manifest, parse_error = _raw_manifest(manifest_path)
    if not unsafe_manifest and not manifest_path.is_file():
        issues.append(ProjectIssue("manifest.missing", "manifest FT ausente", path=manifest_path, repairable=True))
    elif not unsafe_manifest and parse_error:
        issues.append(
            ProjectIssue(
                "manifest.corrupt",
                f"manifest FT corrompido: {parse_error}",
                path=manifest_path,
                repairable=True,
            )
        )
    elif manifest is not None:
        version = manifest.get("schema_version")
        if version == LEGACY_NAMED_LAYOUT_VERSION:
            issues.append(
                ProjectIssue(
                    "manifest.v2",
                    "manifest V2 requer migração para V3",
                    path=manifest_path,
                    repairable=True,
                )
            )
        try:
            _validate_manifest(manifest, manifest_path)
        except ManifestError as exc:
            repairable = False
            if version in (LEGACY_NAMED_LAYOUT_VERSION, LAYOUT_VERSION):
                try:
                    candidate = _manifest_for_v3_write(manifest)
                    _validate_manifest(candidate, manifest_path)
                    repairable = True
                except (ManifestError, ValueError):
                    pass
            issues.append(ProjectIssue("manifest.invalid", str(exc), path=manifest_path, repairable=repairable))
        except ValueError as exc:
            issues.append(ProjectIssue("manifest.legacy", str(exc), path=manifest_path))

    bundles = _catalog_bundles(root)
    records = manifest.get("processes", {}) if isinstance(manifest, dict) else {}
    records = records if isinstance(records, dict) else {}
    for name, process_file in bundles.items():
        if name not in records:
            issues.append(
                ProjectIssue(
                    "catalog.orphan",
                    f"bundle local não registrado: {name}",
                    severity="warning",
                    path=process_file,
                    repairable=True,
                )
            )
        try:
            from ft.engine.graph import load_graph

            load_graph(process_file)
        except Exception as exc:
            issues.append(
                ProjectIssue(
                    "catalog.invalid_graph",
                    f"grafo inválido em {name}: {exc}",
                    path=process_file,
                )
            )
        try:
            process_digest(process_file)
        except Exception as exc:
            issues.append(
                ProjectIssue(
                    "catalog.invalid_bundle",
                    f"bundle inseguro em {name}: {exc}",
                    path=process_file,
                )
            )
    for name, record in sorted(records.items()):
        if not isinstance(name, str) or not isinstance(record, dict):
            continue
        if name not in bundles:
            try:
                missing_path = paths.project_named_process_file(root, name)
            except ValueError:
                missing_path = paths.project_process_dir(root)
            issues.append(
                ProjectIssue(
                    "catalog.bundle_missing",
                    f"processo registrado sem bundle local: {name}",
                    path=missing_path,
                )
            )

    errors = any(issue.severity == "error" for issue in issues)
    status = "broken" if errors else "warning" if issues else "healthy"
    return ProjectCheckResult(root, status, tuple(issues))


def _repair_backup_dir(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return (
        paths.ft_home()
        / "repairs"
        / paths.project_runtime_key(root)
        / f"{timestamp}-{uuid4().hex[:8]}"
    )


def _copy_backup(source: Path, backup_dir: Path, root: Path) -> None:
    if not source.exists() or source.is_symlink():
        return
    relative = source.relative_to(root)
    destination = backup_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _reconstructed_manifest(root: Path, previous: dict[str, Any] | None) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": LAYOUT_VERSION,
        "processes": {},
    }
    if isinstance(previous, dict):
        defaults = previous.get("defaults")
        if isinstance(defaults, dict):
            manifest["defaults"] = dict(defaults)
        revision = previous.get("llm_defaults_revision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            manifest["llm_defaults_revision"] = revision
        old_processes = previous.get("processes")
        if isinstance(old_processes, dict):
            for name, record in old_processes.items():
                if not isinstance(name, str) or not isinstance(record, dict):
                    continue
                try:
                    expected = paths.project_named_process_file(root, name)
                except ValueError:
                    continue
                if expected.is_file() and not expected.is_symlink():
                    candidate = dict(record)
                    candidate["path"] = f".ft/process/{name}/process.yml"
                    for field in ("template", "entrypoint"):
                        value = candidate.get(field)
                        if value is not None and (
                            not isinstance(value, str) or not value.strip()
                        ):
                            candidate.pop(field, None)
                    manifest["processes"][name] = candidate
    for name, process_file in _catalog_bundles(root).items():
        try:
            digest = process_digest(process_file)
        except (OSError, ValueError):
            continue
        manifest["processes"].setdefault(
            name,
            {"path": f".ft/process/{name}/process.yml", "base_digest": digest},
        )
    _validate_manifest(manifest, paths.project_manifest(root))
    return manifest


def repair_project(project_root: str | Path) -> ProjectRepairResult:
    """Apply only deterministic repairs and report every unresolved issue."""
    root = Path(project_root).expanduser().resolve()
    before = check_project(root)
    blockers = {
        "project.missing",
        "project.symlink",
        "git.missing",
        "git.not_root",
        "git.head_missing",
        "git.status_failed",
        "manifest.symlink",
    }
    if any(
        issue.code in blockers
        or issue.code.endswith(".symlink")
        or (
            issue.severity == "error"
            and issue.code.startswith("manifest.")
            and not issue.repairable
        )
        for issue in before.issues
    ):
        return ProjectRepairResult(root, "blocked", (), before.issues)

    actions: list[str] = []
    backup_dir: Path | None = None
    manifest_path = paths.project_manifest(root)
    previous, parse_error = _raw_manifest(manifest_path)

    with _manifest_write_lock(root):
        structural_dirs = (
            paths.project_ft_dir(root),
            paths.project_process_dir(root),
            paths.project_cycles_dir(root),
        )
        for directory in structural_dirs:
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                actions.append(f"diretório criado: {directory.relative_to(root)}")

        needs_manifest_rebuild = (
            previous is None
            or parse_error is not None
            or previous.get("schema_version") not in (
                LEGACY_NAMED_LAYOUT_VERSION,
                LAYOUT_VERSION,
            )
        )
        if needs_manifest_rebuild:
            if manifest_path.exists():
                backup_dir = _repair_backup_dir(root)
                _copy_backup(manifest_path, backup_dir, root)
            candidate = _reconstructed_manifest(root, previous)
            _atomic_write_manifest(manifest_path, candidate)
            actions.append("manifest V3 reconstruído a partir dos bundles locais")
        elif previous.get("schema_version") == LEGACY_NAMED_LAYOUT_VERSION:
            result = migrate_v2_manifest(root)
            backup_dir = result.backup_path.parent if result.backup_path else backup_dir
            actions.extend(result.actions)
        else:
            try:
                _validate_manifest(previous, manifest_path)
                candidate = _manifest_for_v3_write(previous)
            except ManifestError:
                candidate = _manifest_for_v3_write(previous)
                try:
                    _validate_manifest(candidate, manifest_path)
                except ManifestError:
                    candidate = _reconstructed_manifest(root, previous)
            if candidate != previous:
                backup_dir = backup_dir or _repair_backup_dir(root)
                _copy_backup(manifest_path, backup_dir, root)
                _atomic_write_manifest(manifest_path, candidate)
                actions.append("manifest V3 normalizado")

        # Register orphan bundles with facts that can be derived from disk.
        current = _read_manifest_file(manifest_path)
        processes = current.setdefault("processes", {})
        orphaned = False
        for name, process_file in _catalog_bundles(root).items():
            if name in processes:
                continue
            try:
                digest = process_digest(process_file)
            except (OSError, ValueError):
                continue
            processes[name] = {
                "path": f".ft/process/{name}/process.yml",
                "base_digest": digest,
            }
            actions.append(f"bundle órfão registrado: {name}")
            orphaned = True
        if orphaned:
            _validate_manifest(current, manifest_path)
            _atomic_write_manifest(manifest_path, current)

        structural_files = (
            (paths.project_ft_dir(root) / ".gitignore", PROJECT_GITIGNORE),
            (paths.project_cycles_dir(root) / ".gitkeep", ""),
            (paths.project_process_dir(root) / ".gitkeep", ""),
        )
        for file_path, content in structural_files:
            if not file_path.exists():
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                actions.append(f"arquivo criado: {file_path.relative_to(root)}")
        if _copy_agents_playbook(root):
            actions.append("AGENTS.md restaurado")

    if actions:
        backup_dir = backup_dir or _repair_backup_dir(root)
        backup_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1,
            "project": str(root),
            "repaired_at": datetime.now(timezone.utc).isoformat(),
            "actions": actions,
        }
        (backup_dir / "repair.yml").write_text(
            yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    after = check_project(root)
    remaining_errors = tuple(issue for issue in after.issues if issue.severity == "error")
    status = "blocked" if remaining_errors else "repaired" if actions else "unchanged"
    return ProjectRepairResult(root, status, tuple(actions), after.issues, backup_dir)
