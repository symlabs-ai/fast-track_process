"""Canonical filesystem layout for the ft engine.

There are two intentionally separate ``.ft`` namespaces:

* ``<project>/.ft`` is tracked project metadata (process and cycle history).
* ``$FT_HOME`` (default ``~/.ft``) is untracked runtime data.

Keeping every path behind this module prevents runtime state from leaking into
templates or project commits.
"""

import os
from pathlib import Path


def ft_home() -> Path:
    """Diretório base de dados do ft. Default ~/.ft, override via FT_HOME."""
    env = os.environ.get("FT_HOME")
    return Path(env) if env else Path.home() / ".ft"


def project_ft_dir(project_root: str | Path) -> Path:
    """Versioned ft metadata directory inside a project."""
    return Path(project_root) / ".ft"


def project_manifest(project_root: str | Path) -> Path:
    return project_ft_dir(project_root) / "manifest.yml"


def project_process_dir(project_root: str | Path) -> Path:
    return project_ft_dir(project_root) / "process"


def project_process_file(project_root: str | Path) -> Path:
    return project_process_dir(project_root) / "process.yml"


def project_environment_file(project_root: str | Path) -> Path:
    return project_process_dir(project_root) / "environment.yml"


def project_scripts_dir(project_root: str | Path) -> Path:
    return project_process_dir(project_root) / "scripts"


def project_named_process_dir(project_root: str | Path, process_name: str) -> Path:
    """Directory of a named local process under ``.ft/process/``."""
    if not process_name or Path(process_name).name != process_name:
        raise ValueError(f"nome de processo inválido: {process_name!r}")
    return project_process_dir(project_root) / process_name


def project_named_process_file(project_root: str | Path, process_name: str) -> Path:
    return project_named_process_dir(project_root, process_name) / "process.yml"


def project_named_environment_file(project_root: str | Path, process_name: str) -> Path:
    return project_named_process_dir(project_root, process_name) / "environment.yml"


def project_named_scripts_dir(project_root: str | Path, process_name: str) -> Path:
    return project_named_process_dir(project_root, process_name) / "scripts"


def project_cycles_dir(project_root: str | Path) -> Path:
    return project_ft_dir(project_root) / "cycles"


def project_cycle_dir(project_root: str | Path, cycle_id: str) -> Path:
    return project_cycles_dir(project_root) / cycle_id


def worktrees_root() -> Path:
    """Raiz de todos os worktrees externos."""
    return ft_home() / "worktrees"


def project_runtime_key(project_root: str | Path) -> str:
    """Return the owning project name for a main checkout or FT worktree."""
    root = Path(project_root).resolve()
    try:
        relative = root.relative_to(worktrees_root().resolve())
    except ValueError:
        return root.name
    if len(relative.parts) >= 2:
        return relative.parts[0]
    return root.name


def worktrees_home(project_root: Path) -> Path:
    """Diretório de worktrees de um projeto: <ft_home>/worktrees/<nome>."""
    return worktrees_root() / project_runtime_key(project_root)


def runtime_home(project_root: str | Path) -> Path:
    """Untracked runtime area for continuous execution of one project."""
    return ft_home() / "runtime" / project_runtime_key(project_root)


def evolve_home(project_root: str | Path) -> Path:
    """Workspaces de evolução de processo (ft evolve) de um projeto.

    Vive em runtime_home — nunca em worktrees/ — para que um evolve em
    andamento jamais apareça como ciclo (ft runs, ft continue, active-run).
    """
    return runtime_home(project_root) / "evolve"


def migration_backups_home(project_root: str | Path) -> Path:
    """Non-active backup area used while removing legacy runtime from a repo."""
    return ft_home() / "migrations" / project_runtime_key(project_root)


def continuous_state_path(project_root: str | Path) -> Path:
    return runtime_home(project_root) / "continuous" / "state" / "engine_state.yml"


def is_worktree_path(path: str | Path) -> bool:
    """True se o path está dentro da raiz de worktrees externos."""
    try:
        Path(path).resolve().relative_to(worktrees_root().resolve())
        return True
    except ValueError:
        return False
