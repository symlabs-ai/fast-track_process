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


def load_environment(project_root: str) -> dict[str, Any]:
    """Carrega environment.yml do diretório process/ do projeto."""
    env_path = Path(project_root) / "process" / "environment.yml"
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
    project_root: str,
    environment: dict[str, Any] | None = None,
) -> list[tuple[str, bool, str]]:
    """Executa hooks para um evento. Retorna [(script, success, detail)]."""
    if environment is None:
        environment = load_environment(project_root)

    hooks = get_hooks(environment)
    scripts = hooks.get(event, [])
    if not scripts:
        return []

    results: list[tuple[str, bool, str]] = []
    for script in scripts:
        script_path = Path(project_root) / "process" / script if not Path(script).is_absolute() else Path(script)
        if not script_path.exists():
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
