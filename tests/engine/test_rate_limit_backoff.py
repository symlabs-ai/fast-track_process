"""Testes do mecanismo de backoff para rate limit da API (429).

Cobre as duas camadas:
- delegate: cronograma de backoff longo/configurável + flag rate_limited no resultado
- runner: pausa sem consumir auto-fix quando a falha é de infra ([RATE_LIMIT])
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ft.engine.delegate import (
    DelegateResult,
    _RATE_LIMIT_PATTERNS,
    _RATE_LIMIT_WAIT,
    _rate_limit_backoff_schedule,
    delegate_to_llm,
)
from ft.engine.runner import RATE_LIMIT_MARKER, StepRunner


_PROCESS_YAML = """
process_id: test-rate-limit
version: "3.0"
sprints:
  - id: sprint-01
    title: "Sprint 1"
nodes:
  - id: step.01.doc
    type: document
    title: "Doc inicial"
    executor: llm_writer
    sprint: sprint-01
    outputs:
      - project/docs/doc.md
    validators:
      - file_exists: project/docs/doc.md
    next: step.02.end
  - id: step.02.end
    type: end
    title: "Fim"
"""


@pytest.fixture
def runner(tmp_path):
    process_path = tmp_path / "process.yml"
    process_path.write_text(_PROCESS_YAML)
    return StepRunner(
        process_path=process_path,
        state_path=tmp_path / "state.yml",
        project_root=str(tmp_path),
    )


class TestBackoffSchedule:
    def test_default_schedule_covers_long_outages(self):
        schedule = _rate_limit_backoff_schedule()
        assert schedule == _RATE_LIMIT_WAIT
        # ~1h40 de espera acumulada — dimensionado para atravessar
        # indisponibilidades de horas (o incidente real durou ~2h)
        assert sum(schedule) >= 5400

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FT_RATE_LIMIT_BACKOFF", "5, 10,20")
        assert _rate_limit_backoff_schedule() == [5, 10, 20]

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("FT_RATE_LIMIT_BACKOFF", "abc,10")
        assert _rate_limit_backoff_schedule() == _RATE_LIMIT_WAIT

    def test_empty_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("FT_RATE_LIMIT_BACKOFF", "  ")
        assert _rate_limit_backoff_schedule() == _RATE_LIMIT_WAIT


class TestRateLimitedFlag:
    def test_delegate_result_defaults_to_not_rate_limited(self):
        result = DelegateResult(
            success=False, output="x", files_created=[], files_modified=[]
        )
        assert result.rate_limited is False

    def test_pattern_matches_real_claude_429_output(self):
        # Mensagem real do incidente de 2026-07-05
        output = (
            "API Error: Server is temporarily limiting requests (not your usage "
            "limit) · This request would exceed your account's rate limit."
        )
        assert _RATE_LIMIT_PATTERNS.search(output)

    def test_delegate_sets_flag_after_backoff_exhausted(self, tmp_path, monkeypatch):
        """Sobe um delegate com Popen falso sempre retornando 429 e cronograma
        zerado — o resultado final deve vir com rate_limited=True."""
        monkeypatch.setenv("FT_RATE_LIMIT_BACKOFF", "0,0")

        raw_429 = (
            '{"type":"result","subtype":"success","is_error":true,'
            '"result":"API Error: 429 rate_limit — try again later"}'
        )

        class FakeProc:
            def wait(self, timeout=None):
                return 1

            def kill(self):
                pass

        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return FakeProc()

        fake_git = SimpleNamespace(stdout="", stderr="", returncode=0)
        with (
            patch("ft.engine.delegate.subprocess.Popen", side_effect=fake_popen),
            patch("ft.engine.delegate.subprocess.run", return_value=fake_git),
            patch(
                "ft.engine.delegate._stream_process_output",
                return_value=raw_429,
            ),
        ):
            result = delegate_to_llm(
                task="qualquer",
                project_root=str(tmp_path),
                llm_engine="claude",
            )

        assert result.success is False
        assert result.rate_limited is True
        # tentativa inicial + 2 retries do cronograma
        assert len(popen_calls) == 3

    def test_content_failure_is_not_flagged(self, tmp_path):
        """Falha comum (BLOCKED sem menção a rate limit) não vira rate_limited."""
        raw_blocked = (
            '{"type":"result","subtype":"success","is_error":true,'
            '"result":"BLOCKED: arquivo de entrada inexistente"}'
        )

        class FakeProc:
            def wait(self, timeout=None):
                return 1

            def kill(self):
                pass

        fake_git = SimpleNamespace(stdout="", stderr="", returncode=0)
        with (
            patch(
                "ft.engine.delegate.subprocess.Popen",
                return_value=FakeProc(),
            ),
            patch("ft.engine.delegate.subprocess.run", return_value=fake_git),
            patch(
                "ft.engine.delegate._stream_process_output",
                return_value=raw_blocked,
            ),
        ):
            result = delegate_to_llm(
                task="qualquer",
                project_root=str(tmp_path),
                llm_engine="claude",
            )

        assert result.success is False
        assert result.rate_limited is False


class TestRunnerPause:
    def test_pause_resets_node_to_ready_without_consuming_auto_fix(self, runner):
        runner.init_state()
        state = runner.state_mgr.load()
        node = runner.graph.get_node(state.current_node)
        runner.state_mgr.block(f"{RATE_LIMIT_MARKER} API indisponível no node {node.id}")

        runner._pause_for_rate_limit(node, "sprint-01")

        state = runner.state_mgr.load()
        assert state.node_status == "ready"
        assert state.blocked_reason is None
        assert runner._auto_fix_counts.get(node.id, 0) == 0

    def test_llm_step_blocks_with_marker_on_rate_limited_result(self, runner):
        runner.init_state()
        state = runner.state_mgr.load()
        node = runner.graph.get_node(state.current_node)

        rate_limited = DelegateResult(
            success=False,
            output="API Error: 429 rate_limit",
            files_created=[],
            files_modified=[],
            rate_limited=True,
        )

        with patch("ft.engine.runner.delegate_to_llm", return_value=rate_limited):
            runner._run_llm_step(node)

        state = runner.state_mgr.load()
        assert state.node_status == "blocked"
        assert (state.blocked_reason or "").startswith(RATE_LIMIT_MARKER)

    def test_auto_fix_rate_limit_marks_block_and_clears_loop_detector(self, runner):
        runner.init_state()
        state = runner.state_mgr.load()
        node = runner.graph.get_node(state.current_node)
        runner.state_mgr.block("erro qualquer de conteúdo")

        rate_limited = DelegateResult(
            success=False,
            output="API Error: 429 rate_limit",
            files_created=[],
            files_modified=[],
            rate_limited=True,
        )

        with patch("ft.engine.runner.delegate_to_llm", return_value=rate_limited):
            fixed = runner._run_auto_fix(node, "erro qualquer de conteúdo")

        assert fixed is False
        state = runner.state_mgr.load()
        assert (state.blocked_reason or "").startswith(RATE_LIMIT_MARKER)
        # detector de "mesmo erro" não deve reter erro de infra
        assert node.id not in runner._auto_fix_prev_error
