"""Execution of ``kind: init`` template scripts and the per-machine marker.

An init template prepares the environment of one project exactly once
(``ft init``); ``ft init --fix`` re-runs the same chain to repair it. The
marker lives under ``.ft/runtime/`` — gitignored on purpose: a clone on
another machine must initialize its own environment again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

import yaml

from ft.engine import paths
from ft.templates.catalog import InitTemplateDescriptor

SCRIPT_TIMEOUT_SECONDS = 300


class InitScriptError(RuntimeError):
    """Raised when an init template script fails; blocks like a gate."""


@dataclass(frozen=True)
class InitScriptResult:
    script: str
    output: str


def init_marker_path(project_root: str | Path) -> Path:
    """Machine-local record of init templates already applied."""
    return paths.project_ft_dir(project_root) / "runtime" / "init.yml"


def read_init_marker(project_root: str | Path) -> dict[str, dict[str, str]]:
    marker = init_marker_path(project_root)
    if not marker.is_file():
        return {}
    try:
        payload = yaml.safe_load(marker.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    templates = payload.get("templates") if isinstance(payload, dict) else None
    return templates if isinstance(templates, dict) else {}


def record_init_template(
    project_root: str | Path, descriptor: InitTemplateDescriptor
) -> None:
    templates = read_init_marker(project_root)
    templates[descriptor.name] = {
        "digest": descriptor.source_digest,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    marker = init_marker_path(project_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        yaml.safe_dump({"templates": templates}, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def run_init_template(
    descriptor: InitTemplateDescriptor,
    project_root: str | Path,
    *,
    mode: str = "init",
    adopt: bool = False,
    commit_message: str | None = None,
) -> list[InitScriptResult]:
    """Run every script of one init template in order, blocking on failure."""
    root = Path(project_root).resolve()
    engine_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "FT_PROJECT_ROOT": str(root),
        "FT_TEMPLATE_DIR": str(descriptor.directory),
        "FT_ENGINE_ROOT": str(engine_root),
        "FT_INIT_MODE": mode,
        "FT_ADOPT": "1" if adopt else "0",
    }
    if commit_message:
        env["FT_COMMIT_MESSAGE"] = commit_message

    results: list[InitScriptResult] = []
    for script in descriptor.scripts:
        label = script.relative_to(descriptor.directory).as_posix()
        try:
            proc = subprocess.run(
                [str(script)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=SCRIPT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise InitScriptError(
                f"init [{descriptor.name}] {label}: timeout ({SCRIPT_TIMEOUT_SECONDS}s)"
            ) from exc
        except OSError as exc:
            raise InitScriptError(
                f"init [{descriptor.name}] {label}: {exc}"
            ) from exc
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "sem saída"
            raise InitScriptError(
                f"init [{descriptor.name}] {label}: exit code "
                f"{proc.returncode}: {detail}"
            )
        results.append(InitScriptResult(script=label, output=proc.stdout.strip()))
    return results
