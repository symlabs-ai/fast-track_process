"""Discovery and validation for project-local and engine templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Literal

import yaml

from ft.engine import paths
from ft.engine.layout import (
    V2_RUN_COMPATIBILITY_FIELD,
    V2_RUN_COMPATIBILITY_VERSION,
    V2_RUN_COMPATIBLE_ENTRYPOINTS,
    get_project_process_record,
    process_digest,
)


_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# V2 exposed two project process entrypoints that became templates under the
# universal V3 ``ft run --template`` command.  The old value remains inside a
# migrated manifest/process bundle so the migration never has to rewrite a
# project-owned fork.  It is accepted only when this migration-owned marker is
# present and internally consistent.
class TemplateCatalogError(ValueError):
    """Raised when a template catalog or bundle cannot be trusted."""


class TemplateNotFoundError(TemplateCatalogError):
    """Raised when a requested runnable template does not exist."""


@dataclass(frozen=True)
class TemplateDescriptor:
    """One pristine template from the engine catalog."""

    name: str
    directory: Path
    process_file: Path
    policy: dict[str, Any]
    source_digest: str


@dataclass(frozen=True)
class ResolvedTemplate:
    """Project-owned process selected for a new run.

    ``source_drift`` only reports that the distributed template changed after
    the local fork was first copied.  Resolution never updates that fork.
    """

    name: str
    process_file: Path
    origin: Literal["local", "materialized"]
    source_digest: str | None
    current_source_digest: str | None
    source_drift: bool

    @property
    def materialized(self) -> bool:
        return self.origin == "materialized"


def validate_template_name(name: object) -> str:
    """Return a canonical template identifier or reject path-like input."""
    if not isinstance(name, str) or not _TEMPLATE_NAME_RE.fullmatch(name):
        raise TemplateCatalogError(f"nome de template inválido: {name!r}")
    return name


def template_process_file(template_dir: Path) -> Path | None:
    """Find the process graph in a global bundle.

    New templates use ``process.yml``.  A single legacy YAML filename remains
    readable so existing catalog entries can be migrated on materialization.
    Ambiguous directories fail closed.
    """
    canonical = template_dir / "process.yml"
    if canonical.is_file() and not canonical.is_symlink():
        return canonical
    candidates = sorted(
        candidate
        for candidate in template_dir.glob("*.yml")
        if candidate.name != "environment.yml"
        and candidate.is_file()
        and not candidate.is_symlink()
    )
    if len(candidates) > 1:
        raise TemplateCatalogError(
            f"template ambíguo em {template_dir}: múltiplos YAMLs de processo"
        )
    return candidates[0] if candidates else None


def _load_process_payload(process_file: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(process_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateCatalogError(
            f"processo inválido em {process_file}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise TemplateCatalogError(
            f"processo inválido em {process_file}: raiz deve ser mapping"
        )
    return payload


def _execution_policy(payload: dict[str, Any], process_file: Path) -> dict[str, Any]:
    policy = payload.get("execution_policy")
    if not isinstance(policy, dict):
        raise TemplateCatalogError(
            f"template em {process_file} não declara execution_policy"
        )
    return dict(policy)


def validate_runnable_policy(
    payload: dict[str, Any],
    *,
    template_name: str,
    process_file: Path,
) -> dict[str, Any]:
    """Validate the universal ``ft run --template`` process contract."""
    policy = _execution_policy(payload, process_file)
    if policy.get("entrypoint") != "run":
        raise TemplateCatalogError(
            f"template '{template_name}' não pertence ao entrypoint run"
        )
    declared = policy.get("template")
    if declared != template_name:
        raise TemplateCatalogError(
            f"template incompatível em {process_file}: esperado "
            f"template={template_name}, recebido {declared!r}"
        )
    return policy


def v2_run_compatibility_marker(entrypoint: object) -> dict[str, Any]:
    """Build the exact marker used to expose a migrated V2 fork via V3 run."""
    if entrypoint not in V2_RUN_COMPATIBLE_ENTRYPOINTS:
        raise TemplateCatalogError(
            f"entrypoint V2 não compatível com ft run: {entrypoint!r}"
        )
    return {
        "version": V2_RUN_COMPATIBILITY_VERSION,
        "legacy_entrypoint": entrypoint,
    }


def validate_migrated_v2_run_policy(
    payload: dict[str, Any],
    record: dict[str, Any],
    *,
    template_name: str,
    process_file: Path,
) -> dict[str, Any]:
    """Validate the narrow, explicitly marked V2-to-V3 execution bridge.

    Old ``init`` graphs commonly have no ``execution_policy`` at all, while
    incremental graphs declare ``entrypoint: feature``.  Both forms remain
    byte-identical after migration.  A policy, when present, must agree with
    the legacy manifest record and may never claim another template.
    """
    if record.get("template") != template_name:
        raise TemplateCatalogError(
            f"registro V2 incompatível para template '{template_name}': "
            "template do registro não corresponde ao nome local"
        )
    legacy_entrypoint = record.get("entrypoint")
    expected_marker = v2_run_compatibility_marker(legacy_entrypoint)
    marker = record.get(V2_RUN_COMPATIBILITY_FIELD)
    if marker != expected_marker:
        raise TemplateCatalogError(
            f"registro migrado inválido para template '{template_name}': "
            f"{V2_RUN_COMPATIBILITY_FIELD} incompatível"
        )

    raw_policy = payload.get("execution_policy")
    if raw_policy is None:
        return {}
    if not isinstance(raw_policy, dict):
        raise TemplateCatalogError(
            f"processo V2 inválido em {process_file}: execution_policy deve ser mapping"
        )
    policy = dict(raw_policy)
    if policy.get("entrypoint") != legacy_entrypoint:
        raise TemplateCatalogError(
            f"processo V2 incompatível em {process_file}: entrypoint do YAML não "
            f"corresponde ao registro ({legacy_entrypoint!r})"
        )
    declared = policy.get("template")
    if declared is not None and declared != template_name:
        raise TemplateCatalogError(
            f"processo V2 incompatível em {process_file}: esperado "
            f"template={template_name}, recebido {declared!r}"
        )
    return policy


def reject_bundle_symlinks(directory: Path) -> None:
    """Reject symlinks anywhere in a template or local process bundle."""
    if directory.is_symlink():
        raise TemplateCatalogError(
            f"bundle de template não pode ser link simbólico: {directory}"
        )
    if not directory.is_dir():
        raise TemplateCatalogError(f"bundle de template ausente: {directory}")
    for candidate in directory.rglob("*"):
        if candidate.is_symlink():
            raise TemplateCatalogError(
                f"bundle de template contém link simbólico não permitido: {candidate}"
            )


class TemplateCatalog:
    """Catalog of templates distributed with one engine installation."""

    def __init__(self, root: str | Path | None = None) -> None:
        default_root = Path(__file__).resolve().parents[2] / "templates"
        self.root = Path(root).resolve() if root is not None else default_root

    def names(self) -> tuple[str, ...]:
        """List only templates executable through the universal run entrypoint."""
        if not self.root.is_dir():
            return ()
        runnable: list[str] = []
        for candidate in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            try:
                descriptor = self.get(candidate.name)
            except TemplateCatalogError:
                continue
            runnable.append(descriptor.name)
        return tuple(runnable)

    def get(self, name: str) -> TemplateDescriptor:
        """Load one runnable global template and validate its complete bundle."""
        selected = validate_template_name(name)
        directory = self.root / selected
        if not directory.exists():
            raise TemplateNotFoundError(f"template '{selected}' não encontrado")
        reject_bundle_symlinks(directory)
        process_file = template_process_file(directory)
        if process_file is None:
            raise TemplateNotFoundError(
                f"template '{selected}' não contém process.yml"
            )
        payload = _load_process_payload(process_file)
        policy = validate_runnable_policy(
            payload,
            template_name=selected,
            process_file=process_file,
        )
        digest = process_digest(process_file)
        if digest is None:
            raise TemplateCatalogError(
                f"não foi possível calcular digest do template '{selected}'"
            )
        return TemplateDescriptor(
            name=selected,
            directory=directory,
            process_file=process_file,
            policy=policy,
            source_digest=digest,
        )

    def require(self, name: str) -> TemplateDescriptor:
        """Like :meth:`get`, but include available choices in missing errors."""
        try:
            return self.get(name)
        except TemplateNotFoundError as exc:
            choices = ", ".join(self.names()) or "nenhum"
            raise TemplateNotFoundError(
                f"template '{name}' não encontrado. Templates disponíveis: {choices}"
            ) from exc
        except TemplateCatalogError as exc:
            # A valid directory owned by another entrypoint is not selectable
            # through ft run, and should be presented just like an unavailable
            # template while retaining the concrete cause.
            choices = ", ".join(self.names()) or "nenhum"
            raise TemplateCatalogError(f"{exc}. Templates disponíveis: {choices}") from exc


def project_template_record(project_root: str | Path, name: str) -> dict[str, Any] | None:
    """Return one registered project template without selecting a default."""
    selected = validate_template_name(name)
    return get_project_process_record(Path(project_root).resolve(), selected)


def validate_local_template(
    project_root: str | Path,
    name: str,
    record: dict[str, Any],
) -> Path:
    """Validate a registered, canonical project-owned template fork."""
    root = Path(project_root).resolve()
    selected = validate_template_name(name)
    expected_relative = f".ft/process/{selected}/process.yml"
    if record.get("path") != expected_relative:
        raise TemplateCatalogError(
            f"registro inválido para template '{selected}': path deve ser "
            f"{expected_relative}"
        )
    if record.get("template") != selected:
        raise TemplateCatalogError(
            f"registro inválido para template '{selected}': template incompatível"
        )
    directory = paths.project_named_process_dir(root, selected)
    process_file = paths.project_named_process_file(root, selected)
    reject_bundle_symlinks(directory)
    if not process_file.is_file() or process_file.is_symlink():
        raise TemplateCatalogError(
            f"template local registrado mas ausente: {expected_relative}"
        )
    payload = _load_process_payload(process_file)
    entrypoint = record.get("entrypoint")
    if entrypoint == "run":
        if V2_RUN_COMPATIBILITY_FIELD in record:
            raise TemplateCatalogError(
                f"registro inválido para template '{selected}': marcador V2 não "
                "pode acompanhar entrypoint run"
            )
        validate_runnable_policy(
            payload,
            template_name=selected,
            process_file=process_file,
        )
    elif entrypoint in V2_RUN_COMPATIBLE_ENTRYPOINTS:
        validate_migrated_v2_run_policy(
            payload,
            record,
            template_name=selected,
            process_file=process_file,
        )
    else:
        raise TemplateCatalogError(
            f"registro inválido para template '{selected}': entrypoint deve ser run "
            "ou uma compatibilidade V2 migrada"
        )
    return process_file.resolve()
