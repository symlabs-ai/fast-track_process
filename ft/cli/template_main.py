"""Editable entry point for the Fast Track template/process CLI."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _tool_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "process" / "fast_track" / "tools" / "ft.py"


def _load_tool_main():
    tool_path = _tool_path()
    if not tool_path.exists():
        raise FileNotFoundError(f"Process CLI não encontrada em: {tool_path}")

    spec = importlib.util.spec_from_file_location("fast_track_process_cli", tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Não foi possível carregar a Process CLI em: {tool_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def main():
    tool_main = _load_tool_main()
    return tool_main()


if __name__ == "__main__":
    main()
