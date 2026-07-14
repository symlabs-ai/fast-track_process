"""Explicit, non-destructive migration of named V2 manifests to V3."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
from uuid import uuid4

from ft.engine import paths
from ft.engine.layout import (
    LAYOUT_VERSION,
    LEGACY_NAMED_LAYOUT_VERSION,
    LayoutMigrationRequired,
    ManifestError,
    _assert_no_exclusive_startup,
    _atomic_write_manifest,
    _manifest_for_v3_write,
    _manifest_write_lock,
    _read_manifest_file,
    _validate_manifest,
)
from ft.templates.catalog import (
    V2_RUN_COMPATIBILITY_FIELD,
    V2_RUN_COMPATIBLE_ENTRYPOINTS,
    TemplateCatalogError,
    _load_process_payload,
    reject_bundle_symlinks,
    v2_run_compatibility_marker,
    validate_migrated_v2_run_policy,
)


@dataclass(frozen=True)
class MigrationResult:
    root: Path
    status: str
    actions: tuple[str, ...]
    from_version: int
    to_version: int
    dry_run: bool
    backup_path: Path | None = None
    errors: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.status in {"migrated", "would_migrate"}


def _migration_backup_path(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return (
        paths.migration_backups_home(root)
        / "manifest-v2-to-v3"
        / f"{timestamp}-{uuid4().hex[:8]}"
        / "manifest.yml"
    )


def _mark_v2_run_compatible_processes(
    root: Path,
    candidate: dict,
) -> int:
    """Mark known V2 entrypoints without rewriting their local bundles."""
    processes = candidate.get("processes", {})
    marked = 0
    for name, raw_record in processes.items():
        if not isinstance(raw_record, dict):
            # Manifest validation reports the more precise structural error.
            continue
        legacy_entrypoint = raw_record.get("entrypoint")
        if legacy_entrypoint not in V2_RUN_COMPATIBLE_ENTRYPOINTS:
            continue

        marker = v2_run_compatibility_marker(legacy_entrypoint)
        existing_marker = raw_record.get(V2_RUN_COMPATIBILITY_FIELD)
        if existing_marker is not None and existing_marker != marker:
            raise ManifestError(
                f"processes.{name}.{V2_RUN_COMPATIBILITY_FIELD} colide com a "
                "ponte V2→V3 esperada"
            )

        process_file = root / str(raw_record.get("path", ""))
        try:
            reject_bundle_symlinks(process_file.parent)
            if not process_file.is_file() or process_file.is_symlink():
                raise TemplateCatalogError(
                    f"processo local registrado mas ausente: {process_file}"
                )
            payload = _load_process_payload(process_file)
            marked_record = dict(raw_record)
            marked_record[V2_RUN_COMPATIBILITY_FIELD] = marker
            validate_migrated_v2_run_policy(
                payload,
                marked_record,
                template_name=name,
                process_file=process_file,
            )
        except TemplateCatalogError as exc:
            raise ManifestError(
                f"processo V2 '{name}' não pode receber a ponte para ft run: {exc}"
            ) from exc

        raw_record[V2_RUN_COMPATIBILITY_FIELD] = marker
        marked += 1
    return marked


def migrate_v2_manifest(
    project_root: str | Path,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    """Remove V2's default selection while preserving catalog and history.

    Only ``.ft/manifest.yml`` is changed.  Process bundles, archived cycles,
    worktrees, and active cycle state are deliberately untouched.
    """
    root = Path(project_root).resolve()
    manifest_path = paths.project_manifest(root)
    if paths.project_ft_dir(root).is_symlink() or manifest_path.is_symlink():
        raise ManifestError(f"manifest FT não pode usar link simbólico: {manifest_path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest FT ausente: {manifest_path}")

    with _manifest_write_lock(root):
        _assert_no_exclusive_startup(root)
        manifest = _read_manifest_file(manifest_path)
        version = manifest.get("schema_version")
        if version == LAYOUT_VERSION:
            _validate_manifest(manifest, manifest_path)
            return MigrationResult(
                root=root,
                status="unchanged",
                actions=(),
                from_version=LAYOUT_VERSION,
                to_version=LAYOUT_VERSION,
                dry_run=dry_run,
            )
        if version != LEGACY_NAMED_LAYOUT_VERSION:
            raise LayoutMigrationRequired(
                "migrate_v2_manifest aceita apenas manifesto nomeado V2; "
                "use migrate_legacy_layout para layouts V1/flat"
            )

        candidate = deepcopy(_manifest_for_v3_write(manifest))
        # A dangling/invalid V2 default is intentionally harmless in V3; all
        # catalog records still undergo full validation after it is removed.
        _validate_manifest(candidate, manifest_path)
        marked = _mark_v2_run_compatible_processes(root, candidate)
        default_name = manifest.get("default_process")
        actions = ["schema_version: 2 -> 3"]
        if default_name is not None:
            actions.append(f"default_process removido ({default_name})")
        actions.append(
            f"{len(candidate.get('processes', {}))} registro(s) de processo preservado(s)"
        )
        if marked:
            actions.append(
                f"{marked} processo(s) V2 marcado(s) para compatibilidade com ft run"
            )
        if dry_run:
            return MigrationResult(
                root=root,
                status="would_migrate",
                actions=tuple(actions),
                from_version=LEGACY_NAMED_LAYOUT_VERSION,
                to_version=LAYOUT_VERSION,
                dry_run=True,
            )

        backup_path = _migration_backup_path(root)
        backup_path.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(manifest_path, backup_path)
        try:
            _atomic_write_manifest(manifest_path, candidate)
        except Exception:
            # The normal writer is atomic, but restoring the byte-identical V2
            # source keeps this function safe even if a platform violates the
            # replace/fsync assumptions.
            shutil.copy2(backup_path, manifest_path)
            raise
        return MigrationResult(
            root=root,
            status="migrated",
            actions=tuple(actions),
            from_version=LEGACY_NAMED_LAYOUT_VERSION,
            to_version=LAYOUT_VERSION,
            dry_run=False,
            backup_path=backup_path,
        )
