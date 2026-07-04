"""Paths base do ft engine.

Centraliza a resolução de ~/.ft para que testes e ambientes isolados
possam redirecionar via env var FT_HOME (lida a cada chamada, não no import).
"""

import os
from pathlib import Path


def ft_home() -> Path:
    """Diretório base de dados do ft. Default ~/.ft, override via FT_HOME."""
    env = os.environ.get("FT_HOME")
    return Path(env) if env else Path.home() / ".ft"


def worktrees_root() -> Path:
    """Raiz de todos os worktrees externos."""
    return ft_home() / "worktrees"


def worktrees_home(project_root: Path) -> Path:
    """Diretório de worktrees de um projeto: <ft_home>/worktrees/<nome>."""
    return worktrees_root() / project_root.name


def is_worktree_path(path: str | Path) -> bool:
    """True se o path está dentro da raiz de worktrees externos."""
    try:
        Path(path).resolve().relative_to(worktrees_root().resolve())
        return True
    except ValueError:
        return False
