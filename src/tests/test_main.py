"""Tests for ft engine core functionality."""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.main import (
    ProcessLoader,
    EngineState,
    Validator,
    Engine,
)


# --- ProcessLoader tests ---


class TestProcessLoader:
    def test_load_valid_process(self, tmp_path):
        process_yaml = {
            "id": "test",
            "version": "0.1.0",
            "title": "Test Process",
            "nodes": [
                {
                    "id": "step.01",
                    "type": "discovery",
                    "title": "First step",
                    "executor": "llm_coach",
                    "outputs": ["doc.md"],
                    "validators": [{"file_exists": "doc.md"}],
                    "next": "step.02",
                },
                {"id": "step.02", "type": "end", "title": "Done"},
            ],
        }
        path = tmp_path / "process.yml"
        path.write_text(yaml.dump(process_yaml))

        process = ProcessLoader.load(str(path))
        assert process["id"] == "test"
        assert len(process["nodes"]) == 2

    def test_load_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            ProcessLoader.load("/nonexistent/process.yml")

    def test_get_node_by_id(self, tmp_path):
        process_yaml = {
            "id": "test",
            "version": "0.1.0",
            "title": "Test",
            "nodes": [
                {"id": "step.01", "type": "discovery", "title": "S1", "next": "step.02"},
                {"id": "step.02", "type": "end", "title": "S2"},
            ],
        }
        path = tmp_path / "process.yml"
        path.write_text(yaml.dump(process_yaml))

        process = ProcessLoader.load(str(path))
        node = ProcessLoader.get_node(process, "step.01")
        assert node["id"] == "step.01"
        assert node["title"] == "S1"

    def test_get_node_unknown_id_returns_none(self, tmp_path):
        process_yaml = {
            "id": "test",
            "version": "0.1.0",
            "title": "Test",
            "nodes": [{"id": "step.01", "type": "end", "title": "S1"}],
        }
        path = tmp_path / "process.yml"
        path.write_text(yaml.dump(process_yaml))

        process = ProcessLoader.load(str(path))
        assert ProcessLoader.get_node(process, "step.99") is None


# --- EngineState tests ---


class TestEngineState:
    def test_load_state(self, tmp_path):
        state_data = {
            "process_id": "test",
            "version": "0.1.0",
            "current_node": "step.01",
            "node_status": "ready",
            "completed_nodes": [],
            "gate_log": {},
            "artifacts": {},
            "blocked_reason": None,
            "metrics": {"steps_completed": 0, "steps_total": 2},
        }
        path = tmp_path / "engine_state.yml"
        path.write_text(yaml.dump(state_data))

        state = EngineState.load(str(path))
        assert state["current_node"] == "step.01"
        assert state["node_status"] == "ready"

    def test_save_state(self, tmp_path):
        path = tmp_path / "engine_state.yml"
        state = {
            "process_id": "test",
            "current_node": "step.02",
            "node_status": "completed",
            "completed_nodes": ["step.01"],
        }
        EngineState.save(str(path), state)

        loaded = yaml.safe_load(path.read_text())
        assert loaded["current_node"] == "step.02"
        assert "step.01" in loaded["completed_nodes"]

    def test_advance_state(self):
        state = {
            "current_node": "step.01",
            "node_status": "ready",
            "completed_nodes": [],
            "gate_log": {},
            "metrics": {"steps_completed": 0},
        }
        updated = EngineState.advance(state, "step.01", "step.02")
        assert updated["current_node"] == "step.02"
        assert updated["node_status"] == "ready"
        assert "step.01" in updated["completed_nodes"]
        assert updated["gate_log"]["step.01"] == "PASS"
        assert updated["metrics"]["steps_completed"] == 1

    def test_block_state(self):
        state = {
            "current_node": "step.01",
            "node_status": "ready",
            "blocked_reason": None,
        }
        updated = EngineState.block(state, "Validation failed: missing file")
        assert updated["node_status"] == "blocked"
        assert updated["blocked_reason"] == "Validation failed: missing file"


# --- Validator tests ---


class TestValidator:
    def test_file_exists_pass(self, tmp_path):
        (tmp_path / "doc.md").write_text("content")
        result = Validator.file_exists(str(tmp_path / "doc.md"))
        assert result["status"] == "PASS"

    def test_file_exists_fail(self):
        result = Validator.file_exists("/nonexistent/file.txt")
        assert result["status"] == "BLOCK"
        assert "not found" in result["reason"].lower()

    def test_min_lines_pass(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = Validator.min_lines(str(f), 5)
        assert result["status"] == "PASS"

    def test_min_lines_fail(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("line1\nline2\n")
        result = Validator.min_lines(str(f), 5)
        assert result["status"] == "BLOCK"
        assert "2" in result["reason"]
        assert "5" in result["reason"]

    def test_has_sections_pass(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Intro\n## Hipotese\nblah\n## Visao\nblah\n")
        result = Validator.has_sections(str(f), ["Hipotese", "Visao"])
        assert result["status"] == "PASS"

    def test_has_sections_fail(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Intro\n## Hipotese\nblah\n")
        result = Validator.has_sections(str(f), ["Hipotese", "Visao"])
        assert result["status"] == "BLOCK"
        assert "Visao" in result["reason"]

    def test_tests_pass_validator(self, tmp_path):
        # Create a minimal passing test
        test_file = tmp_path / "test_sample.py"
        test_file.write_text("def test_ok():\n    assert True\n")
        result = Validator.tests_pass(str(tmp_path))
        assert result["status"] == "PASS"

    def test_tests_fail_validator(self, tmp_path):
        test_file = tmp_path / "test_sample.py"
        test_file.write_text("def test_fail():\n    assert False\n")
        result = Validator.tests_pass(str(tmp_path))
        assert result["status"] == "BLOCK"


# --- Engine tests ---


class TestEngine:
    def _make_process(self, tmp_path):
        process_yaml = {
            "id": "test",
            "version": "0.1.0",
            "title": "Test",
            "nodes": [
                {
                    "id": "step.01",
                    "type": "build",
                    "title": "Build",
                    "executor": "llm_coder",
                    "outputs": [str(tmp_path / "out.txt")],
                    "validators": [{"file_exists": str(tmp_path / "out.txt")}],
                    "next": "step.02",
                },
                {"id": "step.02", "type": "end", "title": "Done"},
            ],
        }
        path = tmp_path / "process.yml"
        path.write_text(yaml.dump(process_yaml))
        return str(path)

    def _make_state(self, tmp_path):
        state = {
            "process_id": "test",
            "version": "0.1.0",
            "current_node": "step.01",
            "node_status": "ready",
            "completed_nodes": [],
            "gate_log": {},
            "artifacts": {},
            "blocked_reason": None,
            "metrics": {"steps_completed": 0, "steps_total": 2},
        }
        path = tmp_path / "engine_state.yml"
        path.write_text(yaml.dump(state))
        return str(path)

    def test_engine_init(self, tmp_path):
        process_path = self._make_process(tmp_path)
        state_path = self._make_state(tmp_path)
        engine = Engine(process_path, state_path)
        assert engine.state["current_node"] == "step.01"

    def test_engine_validate_node_pass(self, tmp_path):
        process_path = self._make_process(tmp_path)
        state_path = self._make_state(tmp_path)
        # Create the expected output file
        (tmp_path / "out.txt").write_text("hello")

        engine = Engine(process_path, state_path)
        node = ProcessLoader.get_node(engine.process, "step.01")
        results = engine.validate_node(node)
        assert all(r["status"] == "PASS" for r in results)

    def test_engine_validate_node_fail(self, tmp_path):
        process_path = self._make_process(tmp_path)
        state_path = self._make_state(tmp_path)
        # Don't create the expected output file

        engine = Engine(process_path, state_path)
        node = ProcessLoader.get_node(engine.process, "step.01")
        results = engine.validate_node(node)
        assert any(r["status"] == "BLOCK" for r in results)

    def test_engine_status(self, tmp_path):
        process_path = self._make_process(tmp_path)
        state_path = self._make_state(tmp_path)
        engine = Engine(process_path, state_path)
        status = engine.status()
        assert status["current_node"] == "step.01"
        assert status["node_title"] == "Build"
        assert status["completed"] == 0
        assert status["total"] == 2
