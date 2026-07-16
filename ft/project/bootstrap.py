"""Idempotent bootstrap for a Git-backed Fast Track workspace.

The engine owns the invariants: pre-conditions (safe directory, clean
checkout), the ``.ft/`` scaffold, and post-conditions (usable HEAD, clean
tree). The mechanics of preparing the project — ``git init``, base files,
initial commit — live in the ``init-default`` template (``kind: init``) so
each workspace can customize them without patching the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from ft.engine import paths
from ft.engine.layout import (
    LAYOUT_VERSION,
    LEGACY_NAMED_LAYOUT_VERSION,
    ManifestError,
    ensure_project_layout,
    read_manifest,
)
from ft.project.init_scripts import (
    InitScriptError,
    read_init_marker,
    record_init_template,
    run_init_template,
)
from ft.templates.catalog import (
    InitTemplateDescriptor,
    TemplateCatalog,
    TemplateCatalogError,
)

DEFAULT_INIT_TEMPLATE = "init-default"


class BootstrapError(RuntimeError):
    """Raised before a bootstrap would make an unsafe repository change."""


@dataclass(frozen=True)
class BootstrapResult:
    root: Path
    status: str
    actions: tuple[str, ...]
    commit: str | None
    created_repository: bool
    errors: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.status in {"created", "updated"}


def _run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"não foi possível executar git: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "erro desconhecido"
        raise BootstrapError(f"git {' '.join(args)} falhou: {detail}")
    return result


def _is_git_root(root: Path) -> bool:
    if not (root / ".git").exists():
        return False
    result = _run_git(root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return False
    try:
        return Path(result.stdout.strip()).resolve() == root
    except OSError:
        return False


def _has_head(root: Path) -> bool:
    return _run_git(root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0


def _head_commit(root: Path) -> str | None:
    result = _run_git(root, "rev-parse", "--verify", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _status_entries(root: Path) -> tuple[str, ...]:
    result = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _common_scaffold(root: Path) -> tuple[str, ...]:
    scaffold = (
        paths.project_manifest(root),
        paths.project_ft_dir(root) / ".gitignore",
        paths.project_process_dir(root) / ".gitkeep",
        paths.project_cycles_dir(root) / ".gitkeep",
    )
    before = {path for path in scaffold if path.is_file()}
    ensure_project_layout(root)
    process_keep = paths.project_process_dir(root) / ".gitkeep"
    process_keep.touch(exist_ok=True)
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in scaffold if path.is_file() and path not in before)
    )


def load_init_descriptor(
    name: str, *, catalog_root: str | Path | None = None
) -> InitTemplateDescriptor:
    """Resolve one ``kind: init`` template from the engine catalog."""
    try:
        return TemplateCatalog(catalog_root).get_init(name)
    except TemplateCatalogError as exc:
        raise BootstrapError(str(exc)) from exc


def bootstrap_project(
    project_root: str | Path,
    *,
    adopt: bool = False,
    commit_message: str = "chore: initialize fast track workspace",
    catalog_root: str | Path | None = None,
) -> BootstrapResult:
    """Create the common FT workspace and guarantee a usable Git ``HEAD``.

    A non-empty directory without its own repository is refused by default so
    initialization cannot silently adopt arbitrary files into a new history.
    Existing repositories must be clean before the tracked scaffold is added.
    The project mechanics (``git init``, base files, initial commit) run via
    the ``init-default`` template exactly once; the marker under
    ``.ft/runtime/`` skips them on re-runs (``ft init --fix`` re-executes).
    """
    requested = Path(project_root).expanduser()
    if requested.is_symlink():
        raise BootstrapError(f"raiz do projeto não pode ser link simbólico: {requested}")
    existed = requested.exists()
    if existed and not requested.is_dir():
        raise BootstrapError(f"raiz do projeto não é diretório: {requested}")
    requested.mkdir(parents=True, exist_ok=True)
    root = requested.resolve()

    has_entries = any(root.iterdir())
    was_git_root = _is_git_root(root)
    if not was_git_root and has_entries and not adopt:
        raise BootstrapError(
            "diretório não vazio sem repositório Git próprio; "
            "mova o projeto para um repositório ou use ft init --adopt"
        )

    if was_git_root and not adopt:
        dirty_before = _status_entries(root)
        if dirty_before:
            shown = ", ".join(entry[3:] for entry in dirty_before[:5])
            raise BootstrapError(
                "checkout Git deve estar limpo antes do bootstrap"
                + (f": {shown}" if shown else "")
                + "; commite as mudanças ou use ft init --adopt"
            )

    ft_dir = paths.project_ft_dir(root)
    if ft_dir.is_symlink():
        raise BootstrapError(f"diretório .ft não pode ser link simbólico: {ft_dir}")
    manifest_path = paths.project_manifest(root)
    if manifest_path.is_symlink():
        raise BootstrapError(f"manifest FT não pode ser link simbólico: {manifest_path}")
    if manifest_path.is_file():
        try:
            manifest = read_manifest(root)
        except (ManifestError, ValueError) as exc:
            raise BootstrapError(
                f"workspace FT inconsistente; execute o reparo antes: {exc}"
            ) from exc
        if manifest.get("schema_version") == LEGACY_NAMED_LAYOUT_VERSION:
            raise BootstrapError(
                "manifest v2 requer migração; execute ft init --fix"
            )
        if manifest.get("schema_version") != LAYOUT_VERSION:
            raise BootstrapError("manifest FT possui versão não suportada")

    head_before = _head_commit(root) if was_git_root else None
    created_files = _common_scaffold(root)
    actions: list[str] = []
    actions.extend(f"criado {item}" for item in created_files)

    descriptor = load_init_descriptor(DEFAULT_INIT_TEMPLATE, catalog_root=catalog_root)
    already_initialized = descriptor.name in read_init_marker(root)
    if not already_initialized:
        try:
            script_results = run_init_template(
                descriptor,
                root,
                mode="init",
                adopt=adopt,
                commit_message=commit_message,
            )
        except InitScriptError as exc:
            raise BootstrapError(str(exc)) from exc
        for result in script_results:
            actions.extend(
                line.strip() for line in result.output.splitlines() if line.strip()
            )

    created_repository = not was_git_root and _is_git_root(root)

    if not _has_head(root):
        raise BootstrapError(
            "bootstrap não conseguiu criar HEAD; verifique arquivos ignorados e hooks Git"
        )
    if _status_entries(root) and not adopt:
        raise BootstrapError("bootstrap terminou com checkout Git não limpo")

    if not already_initialized:
        record_init_template(root, descriptor)

    head_after = _head_commit(root)
    commit = head_after if head_after != head_before else None

    changed = bool(actions)
    status = "created" if not existed or created_repository else "updated" if changed else "unchanged"
    return BootstrapResult(
        root=root,
        status=status,
        actions=tuple(actions),
        commit=commit,
        created_repository=created_repository,
    )
