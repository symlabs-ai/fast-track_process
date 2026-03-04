#!/usr/bin/env python3
"""
Fast Track — Token Tracker

Parseia os JSONLs de sessão do Claude Code e calcula consumo de tokens
do projeto atual. Grava snapshots em project/docs/metrics.yml para
rastreabilidade por fase/step.

Uso:
    # Ver consumo atual
    python process/fast_track/tools/token_tracker.py status

    # Gravar snapshot (ft_manager chama em cada checkpoint)
    python process/fast_track/tools/token_tracker.py snapshot --step ft.mdd.01.hipotese

    # Ver histórico de snapshots
    python process/fast_track/tools/token_tracker.py history
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Config ---

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
METRICS_FILE = "project/docs/metrics.yml"


def find_project_dir(project_root: Path) -> Path | None:
    """Encontra o diretório de sessões do Claude Code para este projeto."""
    # Claude Code usa o path do projeto como hash do diretório
    # /home/user/dev/project -> -home-user-dev-project
    project_hash = str(project_root).replace("/", "-")
    if project_hash.startswith("-"):
        candidate = CLAUDE_PROJECTS_DIR / project_hash
    else:
        candidate = CLAUDE_PROJECTS_DIR / f"-{project_hash}"

    if candidate.is_dir():
        return candidate

    # Fallback: procurar por diretórios que contenham o nome do projeto
    project_name = project_root.name
    for d in CLAUDE_PROJECTS_DIR.iterdir():
        if d.is_dir() and project_name in d.name:
            return d

    return None


def parse_session(jsonl_path: Path) -> dict:
    """Parseia um JSONL de sessão e retorna métricas agregadas."""
    input_tokens = 0
    output_tokens = 0
    cache_creation = 0
    cache_read = 0
    api_calls = 0
    first_ts = None
    last_ts = None

    with open(jsonl_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = obj.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if obj.get("type") == "assistant":
                usage = obj.get("message", {}).get("usage", {})
                if usage:
                    input_tokens += usage.get("input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)
                    cache_creation += usage.get("cache_creation_input_tokens", 0)
                    cache_read += usage.get("cache_read_input_tokens", 0)
                    api_calls += 1

    return {
        "session_id": jsonl_path.stem,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "api_calls": api_calls,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


def get_all_sessions(project_dir: Path) -> list[dict]:
    """Parseia todas as sessões do projeto."""
    sessions = []
    for jsonl in sorted(project_dir.glob("*.jsonl")):
        data = parse_session(jsonl)
        if data["api_calls"] > 0:
            sessions.append(data)
    return sessions


def aggregate(sessions: list[dict]) -> dict:
    """Agrega métricas de todas as sessões."""
    total = {
        "sessions": len(sessions),
        "input_tokens": sum(s["input_tokens"] for s in sessions),
        "output_tokens": sum(s["output_tokens"] for s in sessions),
        "cache_creation_tokens": sum(s["cache_creation_tokens"] for s in sessions),
        "cache_read_tokens": sum(s["cache_read_tokens"] for s in sessions),
        "api_calls": sum(s["api_calls"] for s in sessions),
    }
    total["total_tokens"] = total["input_tokens"] + total["output_tokens"]
    return total


def format_number(n: int) -> str:
    """Formata número com separador de milhar."""
    return f"{n:,}".replace(",", ".")


def cmd_status(project_root: Path) -> None:
    """Mostra consumo atual de tokens."""
    project_dir = find_project_dir(project_root)
    if not project_dir:
        print(f"ERRO: Diretório de sessões não encontrado para {project_root}")
        sys.exit(1)

    sessions = get_all_sessions(project_dir)
    total = aggregate(sessions)

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Token Tracker — {project_root.name}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sessões:           {total['sessions']}
API calls:         {format_number(total['api_calls'])}
Input tokens:      {format_number(total['input_tokens'])}
Output tokens:     {format_number(total['output_tokens'])}
Cache creation:    {format_number(total['cache_creation_tokens'])}
Cache read:        {format_number(total['cache_read_tokens'])}
────────────────────────────────────────
Total (in+out):    {format_number(total['total_tokens'])}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""")


def load_metrics(project_root: Path) -> dict:
    """Carrega metrics.yml existente ou retorna estrutura vazia."""
    metrics_path = project_root / METRICS_FILE
    if not metrics_path.exists():
        return {"token_tracking": {"snapshots": []}}

    # Parse YAML simples (sem dependência de pyyaml)
    content = metrics_path.read_text()
    data = {"token_tracking": {"snapshots": []}}

    # Parse snapshots existentes
    in_snapshots = False
    current_snapshot = {}
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "snapshots:":
            in_snapshots = True
            continue
        if in_snapshots and stripped.startswith("- step:"):
            if current_snapshot:
                data["token_tracking"]["snapshots"].append(current_snapshot)
            current_snapshot = {"step": stripped.split(":", 1)[1].strip().strip('"')}
        elif in_snapshots and current_snapshot and ":" in stripped and not stripped.startswith("-"):
            key, val = stripped.split(":", 1)
            val = val.strip().strip('"')
            try:
                val = int(val)
            except ValueError:
                pass
            current_snapshot[key.strip()] = val
    if current_snapshot:
        data["token_tracking"]["snapshots"].append(current_snapshot)

    return data


def save_metrics(project_root: Path, data: dict) -> None:
    """Grava metrics.yml (YAML manual, sem dependência)."""
    metrics_path = project_root / METRICS_FILE
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Fast Track — Token Tracking",
        "# Gerado por process/fast_track/tools/token_tracker.py",
        "",
        "token_tracking:",
        "  snapshots:",
    ]

    for snap in data["token_tracking"]["snapshots"]:
        lines.append(f'    - step: "{snap["step"]}"')
        for key in ["timestamp", "sessions", "api_calls", "input_tokens",
                     "output_tokens", "cache_creation_tokens", "cache_read_tokens",
                     "total_tokens"]:
            if key in snap:
                val = snap[key]
                if isinstance(val, str):
                    lines.append(f'      {key}: "{val}"')
                else:
                    lines.append(f"      {key}: {val}")
        lines.append("")

    metrics_path.write_text("\n".join(lines) + "\n")


def cmd_snapshot(project_root: Path, step: str) -> None:
    """Grava snapshot de tokens para um step específico."""
    project_dir = find_project_dir(project_root)
    if not project_dir:
        print(f"ERRO: Diretório de sessões não encontrado para {project_root}")
        sys.exit(1)

    sessions = get_all_sessions(project_dir)
    total = aggregate(sessions)

    snapshot = {
        "step": step,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **total,
    }

    data = load_metrics(project_root)
    data["token_tracking"]["snapshots"].append(snapshot)
    save_metrics(project_root, data)

    print(f"✅ Snapshot gravado para {step}")
    print(f"   Total acumulado: {format_number(total['total_tokens'])} tokens ({total['sessions']} sessões)")

    # Calcular delta se houver snapshot anterior
    snapshots = data["token_tracking"]["snapshots"]
    if len(snapshots) >= 2:
        prev = snapshots[-2]
        delta = total["total_tokens"] - prev.get("total_tokens", 0)
        print(f"   Delta desde {prev['step']}: +{format_number(delta)} tokens")


def cmd_history(project_root: Path) -> None:
    """Mostra histórico de snapshots."""
    data = load_metrics(project_root)
    snapshots = data["token_tracking"]["snapshots"]

    if not snapshots:
        print("Nenhum snapshot registrado ainda.")
        print("Use: token_tracker.py snapshot --step <step_id>")
        return

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Token History — {project_root.name}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""")

    prev_total = 0
    for snap in snapshots:
        total = snap.get("total_tokens", 0)
        delta = total - prev_total
        delta_str = f"+{format_number(delta)}" if prev_total > 0 else format_number(total)
        print(f"  {snap['step']:<45} {format_number(total):>12} tokens  ({delta_str})")
        prev_total = total

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


def main():
    parser = argparse.ArgumentParser(description="Fast Track — Token Tracker")
    parser.add_argument("--project", default=".", help="Raiz do projeto (default: .)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Mostrar consumo atual de tokens")

    snap_parser = sub.add_parser("snapshot", help="Gravar snapshot para um step")
    snap_parser.add_argument("--step", required=True, help="Step ID (ex: ft.mdd.01.hipotese)")

    sub.add_parser("history", help="Mostrar histórico de snapshots")

    args = parser.parse_args()
    project_root = Path(args.project).resolve()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "status":
        cmd_status(project_root)
    elif args.command == "snapshot":
        cmd_snapshot(project_root, args.step)
    elif args.command == "history":
        cmd_history(project_root)


if __name__ == "__main__":
    main()
