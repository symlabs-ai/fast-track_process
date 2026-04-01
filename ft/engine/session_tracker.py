"""
SessionTracker — rastreamento de sessões de agentes em project/docs/sessions/.
RF-18: sessões salvas com agente, node, timestamps e status.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class SessionTracker:
    """Rastreia sessões de agentes em um diretório de sessões."""

    def __init__(self, sessions_dir: str | Path):
        self.sessions_dir = Path(sessions_dir)

    def start_session(self, agent: str, node: str) -> str:
        """Inicia uma nova sessão e persiste em disco. Retorna session_id."""
        session_id = f"session-{uuid.uuid4().hex[:12]}"
        agent_dir = self.sessions_dir / agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "session_id": session_id,
            "agent": agent,
            "node": node,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "status": "running",
        }

        # Save at root level (for glob("*.yml")) and in agent subdir
        for file_path in [self.sessions_dir / f"{session_id}.yml", agent_dir / f"{session_id}.yml"]:
            with open(file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        return session_id

    def end_session(self, session_id: str, status: str = "completed") -> None:
        """Finaliza sessão registrando timestamp e status."""
        data = self._load_session_data(session_id)
        if data is None:
            return
        data["ended_at"] = datetime.now(timezone.utc).isoformat()
        data["status"] = status
        file_path = self._find_session_file(session_id)
        with open(file_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retorna dados de uma sessão ou None se não existir."""
        return self._load_session_data(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        """Retorna lista de todas as sessões registradas (sem duplicatas)."""
        if not self.sessions_dir.exists():
            return []
        seen: set[str] = set()
        sessions = []
        for yml_file in self.sessions_dir.rglob("*.yml"):
            with open(yml_file) as f:
                data = yaml.safe_load(f)
            if data and data.get("session_id") not in seen:
                seen.add(data["session_id"])
                sessions.append(data)
        return sessions

    def _find_session_file(self, session_id: str) -> Path | None:
        if not self.sessions_dir.exists():
            return None
        for yml_file in self.sessions_dir.rglob("*.yml"):
            if yml_file.stem == session_id:
                return yml_file
        return None

    def _load_session_data(self, session_id: str) -> dict[str, Any] | None:
        file_path = self._find_session_file(session_id)
        if file_path is None:
            return None
        with open(file_path) as f:
            return yaml.safe_load(f)
