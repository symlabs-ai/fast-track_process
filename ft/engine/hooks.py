"""
Environment Hooks — executa scripts definidos em environment.yml.

O engine não sabe o que o script faz. Só sabe quando disparar.
Se o script falhar (exit code != 0), bloqueia como um gate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

def _selected_process_dir(
    project_root: str | Path,
    process_path: str | Path | None = None,
    process_dir: str | Path | None = None,
) -> Path:
    """Resolve the selected v2 process directory."""
    root = Path(project_root)
    if process_dir is not None:
        selected = Path(process_dir)
    elif process_path is not None:
        selected = Path(process_path).parent
    else:
        raise ValueError(
            "process_path ou process_dir é obrigatório; "
            "não existe template principal no manifesto V3"
        )

    return selected if selected.is_absolute() else root / selected


def load_environment(
    project_root: str | Path,
    process_path: str | Path | None = None,
    process_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Carrega environment.yml ao lado do processo selecionado."""
    try:
        selected = _selected_process_dir(project_root, process_path, process_dir)
    except ValueError:
        if process_path is None and process_dir is None:
            return {}
        raise
    env_path = selected / "environment.yml"
    if not env_path.exists():
        return {}
    with open(env_path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def get_hooks(environment: dict[str, Any]) -> dict[str, list[str]]:
    """Extrai hooks do environment.yml."""
    hooks = environment.get("hooks", {})
    if not isinstance(hooks, dict):
        return {}
    result: dict[str, list[str]] = {}
    for event, scripts in hooks.items():
        if isinstance(scripts, list):
            result[event] = [str(s) for s in scripts]
        elif isinstance(scripts, str):
            result[event] = [scripts]
    return result


def run_hooks(
    event: str,
    project_root: str | Path,
    environment: dict[str, Any] | None = None,
    process_path: str | Path | None = None,
    process_dir: str | Path | None = None,
) -> list[tuple[str, bool, str]]:
    """Executa hooks para um evento. Retorna [(script, success, detail)]."""
    if environment is None:
        environment = load_environment(
            project_root,
            process_path=process_path,
            process_dir=process_dir,
        )

    hooks = get_hooks(environment)
    scripts = hooks.get(event, [])
    if not scripts:
        return []

    results: list[tuple[str, bool, str]] = []
    selected_process_dir = _selected_process_dir(
        project_root, process_path, process_dir
    ).resolve()
    for script in scripts:
        requested_script = Path(script)
        script_path = (
            requested_script
            if requested_script.is_absolute()
            else selected_process_dir / requested_script
        ).resolve()
        try:
            script_path.relative_to(selected_process_dir)
        except ValueError:
            detail = f"script fora do processo local: {script_path}"
            results.append((script, False, detail))
            print(f"  HOOK {event} FAIL: {script} — fora do processo local")
            continue
        if not script_path.is_file():
            results.append((script, False, f"script não encontrado: {script_path}"))
            print(f"  HOOK {event} FAIL: {script} — script não encontrado")
            continue

        print(f"  HOOK {event}: {script}")
        try:
            proc = subprocess.run(
                [str(script_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode == 0:
                results.append((script, True, proc.stdout.strip()[:200]))
                print(f"  HOOK {event} OK: {script}")
            else:
                detail = proc.stderr.strip()[:200] or proc.stdout.strip()[:200]
                results.append((script, False, f"exit code {proc.returncode}: {detail}"))
                print(f"  HOOK {event} FAIL: {script} — exit code {proc.returncode}")
        except subprocess.TimeoutExpired:
            results.append((script, False, "timeout (300s)"))
            print(f"  HOOK {event} FAIL: {script} — timeout")
        except OSError as e:
            results.append((script, False, str(e)))
            print(f"  HOOK {event} FAIL: {script} — {e}")

    return results


def hooks_all_passed(results: list[tuple[str, bool, str]]) -> bool:
    """Verifica se todos os hooks passaram."""
    return all(success for _, success, _ in results)
