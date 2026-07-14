"""Idempotent bootstrap for a Git-backed Fast Track workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from ft.engine import paths
from ft.engine.layout import (
    LAYOUT_VERSION,
    LEGACY_NAMED_LAYOUT_VERSION,
    ManifestError,
    ensure_project_layout,
    read_manifest,
)


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


def _status_entries(root: Path) -> tuple[str, ...]:
    result = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _copy_agents_playbook(root: Path) -> bool:
    destination = root / "AGENTS.md"
    if destination.exists() or destination.is_symlink():
        return False
    source = Path(__file__).resolve().parents[2] / "AGENTS.md"
    if not source.is_file():
        return False
    shutil.copyfile(source, destination)
    return True


def _common_scaffold(root: Path) -> tuple[str, ...]:
    scaffold = (
        paths.project_manifest(root),
        paths.project_ft_dir(root) / ".gitignore",
        paths.project_process_dir(root) / ".gitkeep",
        paths.project_cycles_dir(root) / ".gitkeep",
        root / "AGENTS.md",
    )
    before = {path for path in scaffold if path.is_file()}
    ensure_project_layout(root)
    process_keep = paths.project_process_dir(root) / ".gitkeep"
    process_keep.touch(exist_ok=True)
    _copy_agents_playbook(root)
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in scaffold if path.is_file() and path not in before)
    )


def bootstrap_project(
    project_root: str | Path,
    *,
    adopt: bool = False,
    commit_message: str = "chore: initialize fast track workspace",
) -> BootstrapResult:
    """Create the common FT workspace and guarantee a usable Git ``HEAD``.

    A non-empty directory without its own repository is refused by default so
    initialization cannot silently adopt arbitrary files into a new history.
    Existing repositories must be clean before the tracked scaffold is added.
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
    created_repository = False
    if not _is_git_root(root):
        if has_entries and not adopt:
            raise BootstrapError(
                "diretório não vazio sem repositório Git próprio; "
                "mova o projeto para um repositório ou use ft init --adopt"
            )
        _run_git(root, "init", "-q")
        created_repository = True

    had_head = _has_head(root)
    dirty_before = _status_entries(root)
    if dirty_before and (had_head or not created_repository) and not adopt:
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

    created_files = _common_scaffold(root)
    actions: list[str] = []
    if created_repository:
        actions.append("repositório Git criado")
    actions.extend(f"criado {item}" for item in created_files)

    status_after = _status_entries(root)
    commit: str | None = None
    if status_after:
        tracked_paths = (
            ".ft/manifest.yml",
            ".ft/.gitignore",
            ".ft/process/.gitkeep",
            ".ft/cycles/.gitkeep",
            "AGENTS.md",
        )
        present = [item for item in tracked_paths if (root / item).exists()]
        _run_git(root, "add", "--", *present)
        staged = _run_git(root, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 1:
            _run_git(
                root,
                "-c",
                "user.name=Fast Track",
                "-c",
                "user.email=ft@localhost",
                "commit",
                "-q",
                "-m",
                commit_message,
                "--",
                *present,
            )
            commit = _run_git(root, "rev-parse", "HEAD").stdout.strip()
            actions.append(f"commit criado {commit}")

    if not _has_head(root):
        raise BootstrapError(
            "bootstrap não conseguiu criar HEAD; verifique arquivos ignorados e hooks Git"
        )
    if _status_entries(root) and not adopt:
        raise BootstrapError("bootstrap terminou com checkout Git não limpo")

    changed = bool(actions)
    status = "created" if not existed or created_repository else "updated" if changed else "unchanged"
    return BootstrapResult(
        root=root,
        status=status,
        actions=tuple(actions),
        commit=commit,
        created_repository=created_repository,
    )
