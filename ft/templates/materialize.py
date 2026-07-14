"""Copy-once materialization of runnable templates into a project."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from uuid import uuid4

from ft.engine import paths, process_update
from ft.engine.layout import (
    _assert_no_exclusive_startup,
    _manifest_write_lock,
    read_manifest,
    register_project_process,
)
from ft.templates.catalog import (
    ResolvedTemplate,
    TemplateCatalog,
    TemplateCatalogError,
    TemplateDescriptor,
    _load_process_payload,
    project_template_record,
    reject_bundle_symlinks,
    validate_local_template,
    validate_runnable_policy,
    validate_template_name,
)


def _assert_initialized_project(root: Path) -> None:
    manifest_path = paths.project_manifest(root)
    if manifest_path.is_symlink():
        raise TemplateCatalogError(
            f"manifesto FT não pode ser link simbólico: {manifest_path}"
        )
    if not manifest_path.is_file():
        raise TemplateCatalogError(
            f"repositório Fast Track não inicializado em {root}; execute ft init"
        )
    # Besides validating YAML/schema, this makes materialization incapable of
    # silently bootstrapping a project.
    if not read_manifest(root):
        raise TemplateCatalogError(
            f"manifesto FT vazio em {manifest_path}; execute ft init --fix"
        )


def _assert_safe_project_catalog(root: Path) -> None:
    """Ensure the lexical project metadata path does not traverse symlinks."""
    for candidate in (
        paths.project_ft_dir(root),
        paths.project_manifest(root),
        paths.project_process_dir(root),
        paths.project_cycles_dir(root),
    ):
        if candidate.is_symlink():
            raise TemplateCatalogError(
                f"layout local não pode conter link simbólico: {candidate}"
            )
    catalog = paths.project_process_dir(root)
    if not catalog.is_dir():
        raise TemplateCatalogError(
            f"catálogo local ausente em {catalog}; execute ft init --fix"
        )
    try:
        catalog.resolve().relative_to(root)
    except ValueError as exc:
        raise TemplateCatalogError(
            "catálogo local .ft/process/ escapa da raiz do projeto"
        ) from exc


def _current_global_digest(catalog: TemplateCatalog, name: str) -> str | None:
    try:
        return catalog.get(name).source_digest
    except TemplateCatalogError:
        return None


def _resolved_local(
    root: Path,
    name: str,
    record: dict,
    catalog: TemplateCatalog,
) -> ResolvedTemplate:
    process_file = validate_local_template(root, name, record)
    recorded_digest = record.get("source_digest")
    if recorded_digest is not None and not isinstance(recorded_digest, str):
        raise TemplateCatalogError(
            f"registro inválido para template '{name}': source_digest deve ser string"
        )
    current_digest = _current_global_digest(catalog, name)
    return ResolvedTemplate(
        name=name,
        process_file=process_file,
        origin="local",
        source_digest=recorded_digest,
        current_source_digest=current_digest,
        source_drift=(
            bool(recorded_digest)
            and bool(current_digest)
            and recorded_digest != current_digest
        ),
    )


def _validate_orphan_fork(root: Path, name: str) -> Path:
    """Validate an unregistered local directory without ever modifying it."""
    directory = paths.project_named_process_dir(root, name)
    process_file = paths.project_named_process_file(root, name)
    reject_bundle_symlinks(directory)
    if not process_file.is_file() or process_file.is_symlink():
        raise TemplateCatalogError(
            f"template local parcial em {directory.relative_to(root)}; "
            "execute ft init --fix"
        )
    payload = _load_process_payload(process_file)
    validate_runnable_policy(
        payload,
        template_name=name,
        process_file=process_file,
    )
    return process_file.resolve()


def _copy_to_staging(
    descriptor: TemplateDescriptor,
    name: str,
    staging: Path,
) -> Path:
    """Copy only the executable bundle and create its merge ancestor."""
    process_update.materialize_global_to(descriptor.directory, name, staging)
    staged_process = staging / "process.yml"
    # Legacy global graphs may have another filename.  The local contract is
    # always canonical regardless of the catalog source filename.
    if not staged_process.is_file():
        copied_legacy = staging / descriptor.process_file.name
        if not copied_legacy.is_file():
            raise TemplateCatalogError(
                f"materialização de '{name}' não produziu process.yml"
            )
        copied_legacy.replace(staged_process)
    process_update.write_base_snapshot(staging)
    reject_bundle_symlinks(staging)
    payload = _load_process_payload(staged_process)
    validate_runnable_policy(
        payload,
        template_name=name,
        process_file=staged_process,
    )
    return staged_process


class TemplateMaterializer:
    """Resolve local-first, materializing a global template at most once."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        catalog: TemplateCatalog | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.catalog = catalog or TemplateCatalog()

    def resolve(self, name: str) -> ResolvedTemplate:
        selected = validate_template_name(name)
        root = self.project_root
        with _manifest_write_lock(root):
            _assert_no_exclusive_startup(root)
            _assert_initialized_project(root)
            _assert_safe_project_catalog(root)

            record = project_template_record(root, selected)
            if record is not None:
                return _resolved_local(root, selected, record, self.catalog)

            descriptor = self.catalog.require(selected)
            destination = paths.project_named_process_dir(root, selected)

            # Never overwrite an unregistered fork.  If it is compatible, only
            # restore its manifest registration; if not, fail for explicit repair.
            if destination.exists() or destination.is_symlink():
                local_process = _validate_orphan_fork(root, selected)
                register_project_process(
                    root,
                    process_name=selected,
                    process_path=local_process,
                    template_id=selected,
                    entrypoint="run",
                    source_digest=descriptor.source_digest,
                    set_default=False,
                )
                restored = project_template_record(root, selected)
                if restored is None:
                    raise TemplateCatalogError(
                        f"falha ao registrar fork local '{selected}'"
                    )
                return _resolved_local(root, selected, restored, self.catalog)

            staging = destination.parent / f".{selected}.{uuid4().hex}.staging"
            installed = False
            try:
                _copy_to_staging(descriptor, selected, staging)
                # The project lock prevents another FT writer from racing this
                # rename.  os.replace publishes the complete directory at once.
                os.replace(staging, destination)
                installed = True
                local_process = paths.project_named_process_file(root, selected)
                register_project_process(
                    root,
                    process_name=selected,
                    process_path=local_process,
                    template_id=selected,
                    entrypoint="run",
                    source_digest=descriptor.source_digest,
                    set_default=False,
                )
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging)
                if installed and destination.exists():
                    shutil.rmtree(destination)
                raise

            record = project_template_record(root, selected)
            if record is None:
                # Defensive: registration is atomic and should make this
                # unreachable, but never return an unregistered executable.
                shutil.rmtree(destination)
                raise TemplateCatalogError(
                    f"materialização de '{selected}' não foi registrada"
                )
            resolved = _resolved_local(root, selected, record, self.catalog)
            return ResolvedTemplate(
                name=resolved.name,
                process_file=resolved.process_file,
                origin="materialized",
                source_digest=resolved.source_digest,
                current_source_digest=resolved.current_source_digest,
                source_drift=resolved.source_drift,
            )


def resolve_template(
    project_root: str | Path,
    name: str,
    *,
    catalog_root: str | Path | None = None,
) -> ResolvedTemplate:
    """Convenience API used by ``ft run --template`` integration."""
    catalog = TemplateCatalog(catalog_root)
    return TemplateMaterializer(project_root, catalog=catalog).resolve(name)
