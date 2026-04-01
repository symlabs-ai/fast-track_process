"""ft engine — deterministic process runner for Fast Track.

Loads a process definition from YAML, manages engine state,
validates artifacts with pure Python checks, and orchestrates
the step-by-step execution loop.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: str, label: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    with open(p) as f:
        return yaml.safe_load(f)


def _pass() -> dict[str, str]:
    return {"status": "PASS", "reason": ""}


def _block(reason: str) -> dict[str, str]:
    return {"status": "BLOCK", "reason": reason}


class ProcessLoader:
    """Loads and queries process definitions from YAML."""

    @staticmethod
    def load(path: str) -> dict[str, Any]:
        return _load_yaml(path, "Process file")

    @staticmethod
    def get_node(process: dict, node_id: str) -> dict | None:
        for node in process.get("nodes", []):
            if node["id"] == node_id:
                return node
        return None


class EngineState:
    """Manages engine state: load, save, advance, block."""

    @staticmethod
    def load(path: str) -> dict[str, Any]:
        return _load_yaml(path, "State file")

    @staticmethod
    def save(path: str, state: dict[str, Any]) -> None:
        with open(path, "w") as f:
            yaml.dump(state, f, default_flow_style=False, allow_unicode=True)

    @staticmethod
    def advance(state: dict, completed_node: str, next_node: str) -> dict:
        state["completed_nodes"].append(completed_node)
        state["gate_log"][completed_node] = "PASS"
        state["current_node"] = next_node
        state["node_status"] = "ready"
        state["metrics"]["steps_completed"] += 1
        return state

    @staticmethod
    def block(state: dict, reason: str) -> dict:
        state["node_status"] = "blocked"
        state["blocked_reason"] = reason
        return state


class Validator:
    """Pure-function validators for process artifacts."""

    @staticmethod
    def file_exists(path: str) -> dict[str, str]:
        if Path(path).exists():
            return _pass()
        return _block(f"File not found: {path}")

    @staticmethod
    def min_lines(path: str, minimum: int) -> dict[str, str]:
        p = Path(path)
        if not p.exists():
            return _block(f"File not found: {path}")
        count = len(p.read_text().strip().splitlines())
        if count >= minimum:
            return _pass()
        return _block(f"File has {count} lines, minimum is {minimum}")

    @staticmethod
    def has_sections(path: str, sections: list[str]) -> dict[str, str]:
        p = Path(path)
        if not p.exists():
            return _block(f"File not found: {path}")
        content = p.read_text()
        missing = [s for s in sections if not re.search(rf"#{{1,6}}\s*.*{re.escape(s)}", content)]
        if not missing:
            return _pass()
        return _block(f"Missing sections: {', '.join(missing)}")

    @staticmethod
    def tests_pass(path: str) -> dict[str, str]:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", path, "-x", "--tb=short", "-q"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return _pass()
            return _block(f"Tests failed:\n{result.stdout}\n{result.stderr}")
        except subprocess.TimeoutExpired:
            return _block("Tests timed out after 60s")


class Engine:
    """Main engine: loads process + state, validates, advances."""

    def __init__(self, process_path: str, state_path: str):
        self.process_path = process_path
        self.state_path = state_path
        self.process = ProcessLoader.load(process_path)
        self.state = EngineState.load(state_path)

    def validate_node(self, node: dict) -> list[dict[str, str]]:
        results = []
        for validator_spec in node.get("validators", []):
            if isinstance(validator_spec, dict):
                for vtype, varg in validator_spec.items():
                    results.append(self._run_validator(vtype, varg))
            elif isinstance(validator_spec, str):
                results.append(self._run_validator(validator_spec, True))
        return results

    def _current_primary_output(self) -> str:
        node = ProcessLoader.get_node(self.process, self.state["current_node"])
        outputs = node.get("outputs", []) if node else []
        return outputs[0] if outputs else ""

    def _run_validator(self, vtype: str, varg: Any) -> dict[str, str]:
        if vtype == "file_exists":
            return Validator.file_exists(str(varg))
        if vtype == "min_lines":
            return Validator.min_lines(self._current_primary_output(), int(varg))
        if vtype == "has_sections":
            return Validator.has_sections(self._current_primary_output(), varg)
        if vtype == "tests_pass":
            return Validator.tests_pass("src/tests/")
        return {"status": "BLOCK", "reason": f"Unknown validator: {vtype}"}

    def status(self) -> dict[str, Any]:
        node = ProcessLoader.get_node(self.process, self.state["current_node"])
        return {
            "current_node": self.state["current_node"],
            "node_title": node["title"] if node else "unknown",
            "node_status": self.state["node_status"],
            "completed": self.state["metrics"]["steps_completed"],
            "total": self.state["metrics"]["steps_total"],
            "completed_nodes": self.state.get("completed_nodes", []),
            "blocked_reason": self.state.get("blocked_reason"),
        }

    def run_step(self) -> dict[str, Any]:
        """Validate current node, advance or block, persist state."""
        node = ProcessLoader.get_node(self.process, self.state["current_node"])

        # End node — nothing to validate
        if node.get("type") == "end":
            return {"gate": "DONE", "node": node["id"]}

        # Run validators
        results = self.validate_node(node)
        failed = [r for r in results if r["status"] != "PASS"]

        if failed:
            reasons = "; ".join(r["reason"] for r in failed)
            self.state = EngineState.block(self.state, reasons)
            self.save()
            return {"gate": "BLOCK", "node": node["id"], "reasons": reasons}

        # All passed — advance
        next_node = node.get("next", "")
        self.state = EngineState.advance(self.state, node["id"], next_node)
        self.save()
        return {"gate": "PASS", "node": node["id"], "next": next_node}

    def save(self) -> None:
        EngineState.save(self.state_path, self.state)
