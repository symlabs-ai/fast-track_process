"""Independent engine/environment separation contracts.

Template discovery, materialization and CLI initialization moved to the V3
workspace/template suites.  This module keeps only the provider-neutral
integration boundaries that are unrelated to process selection.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import yaml


class TestGateKbReviewKbPath:
    def test_with_explicit_kb_path(self, tmp_path):
        from ft.engine.validators.gates import gate_kb_review

        (tmp_path / "docs").mkdir()
        kb_dir = tmp_path / "my_kb"
        kb_dir.mkdir()

        passed, msg = gate_kb_review(
            project_root=str(tmp_path),
            kb_path=str(kb_dir),
        )

        assert passed
        assert "PASS" in msg

    def test_with_env_var(self, tmp_path):
        from ft.engine.validators.gates import gate_kb_review

        (tmp_path / "docs").mkdir()
        kb_dir = tmp_path / "env_kb"
        kb_dir.mkdir()

        with patch.dict(os.environ, {"FT_KB_PATH": str(kb_dir)}):
            passed, _msg = gate_kb_review(project_root=str(tmp_path))

        assert passed

    def test_without_kb_uses_provider_neutral_fallback(self, tmp_path):
        from ft.engine.validators.gates import gate_kb_review

        (tmp_path / "docs").mkdir()
        clean_env = {key: value for key, value in os.environ.items() if key != "FT_KB_PATH"}

        with patch.dict(os.environ, clean_env, clear=True):
            passed, _msg = gate_kb_review(project_root=str(tmp_path))

        assert passed


class TestKbPathInRunner:
    @staticmethod
    def _process(path: Path) -> Path:
        path.write_text(
            """id: test_process
version: '1.0.0'
nodes:
  - id: end
    type: end
    title: End
""",
            encoding="utf-8",
        )
        return path

    def test_kb_path_from_env(self, tmp_path):
        from ft.engine.runner import StepRunner

        process = self._process(tmp_path / "process.yml")
        state_path = tmp_path / "state" / "engine_state.yml"

        with patch.dict(os.environ, {"FT_KB_PATH": "/some/kb/path"}):
            runner = StepRunner(
                process_path=process,
                state_path=state_path,
                project_root=tmp_path,
            )

        assert runner._kb_path == "/some/kb/path"

    def test_kb_path_none_when_not_set(self, tmp_path):
        from ft.engine.runner import StepRunner

        process = self._process(tmp_path / "process.yml")
        state_path = tmp_path / "state" / "engine_state.yml"
        clean_env = {key: value for key, value in os.environ.items() if key != "FT_KB_PATH"}

        with patch.dict(os.environ, clean_env, clear=True):
            runner = StepRunner(
                process_path=process,
                state_path=state_path,
                project_root=tmp_path,
            )

        assert runner._kb_path is None


class TestExternalIntegrationSeparation:
    def test_symgateway_not_packaged_as_engine_module(self):
        assert importlib.util.find_spec("ft.integrations.symgateway") is None

    def test_symgateway_template_contains_register_script(self):
        root = Path(__file__).resolve().parents[2]
        script = root / "templates" / "symgateway" / "scripts" / "register_gateway.sh"

        assert script.exists()
        assert "SYM_GATEWAY_PROJECT_KEY" in script.read_text(encoding="utf-8")

    def test_symgateway_template_wires_script_as_hook(self):
        root = Path(__file__).resolve().parents[2]
        environment = root / "templates" / "symgateway" / "environment.yml"
        data = yaml.safe_load(environment.read_text(encoding="utf-8"))

        assert "scripts/register_gateway.sh" in data["hooks"]["on_init"]

    def test_delegate_has_no_external_integration_dependency(self):
        from ft.engine.delegate import delegate_to_llm

        assert callable(delegate_to_llm)
