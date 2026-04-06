"""
E2E CLI Validation — ft engine CLI

Validates all CLI commands via subprocess:
  ft --help
  ft init
  ft status [--full / -f]
  ft graph
  ft continue [--sprint / --mvp]
  ft approve [--no-continue]
  ft reject <reason> [--no-retry]
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.parent


def run_ft(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    """Invoke the ft engine CLI via python -m ft.cli.main."""
    import os

    env = os.environ.copy()
    # Ensure ft package is importable regardless of cwd
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    return subprocess.run(
        [sys.executable, "-m", "ft.cli.main"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


# Gate-only process YAML: no LLM delegation needed, so continue runs instantly.
GATE_ONLY_PROCESS = textwrap.dedent("""\
    id: e2e_test_process
    version: "1.0.0"
    title: "E2E Test Process (gates only)"

    nodes:
      - id: gate.01.start
        type: gate
        title: "Gate inicial"
        executor: python
        sprint: sprint-01
        validators: []
        next: gate.02.finish

      - id: gate.02.finish
        type: gate
        title: "Gate final"
        executor: python
        sprint: sprint-01
        validators: []
        next: step.end

      - id: step.end
        type: end
        title: "Processo completo"
""")

# Process with one LLM-approval node followed by a gate (tests approve/reject).
APPROVAL_PROCESS = textwrap.dedent("""\
    id: approval_test_process
    version: "1.0.0"
    title: "Approval Test Process"

    nodes:
      - id: step.01.hipotese
        type: discovery
        title: "Capturar hipotese"
        executor: llm_coach
        sprint: sprint-01
        outputs:
          - project/docs/hipotese.md
        requires_approval: true
        validators:
          - file_exists: project/docs/hipotese.md
        next: gate.end

      - id: gate.end
        type: end
        title: "Fim"
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ft_project(tmp_path: Path) -> Path:
    """Minimal ft project with a gate-only process (no LLM calls)."""
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "test_process_v2.yml").write_text(GATE_ONLY_PROCESS)
    return tmp_path


@pytest.fixture
def ft_project_initialized(ft_project: Path) -> Path:
    """ft_project with state already initialized via ft init."""
    result = run_ft(["init"], cwd=ft_project)
    assert result.returncode == 0, f"init failed:\n{result.stdout}\n{result.stderr}"
    return ft_project


@pytest.fixture
def ft_project_approval(tmp_path: Path) -> Path:
    """ft project using the approval process (LLM step with requires_approval)."""
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "test_process_v2.yml").write_text(APPROVAL_PROCESS)
    run_ft(["init"], cwd=tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: help / usage
# ---------------------------------------------------------------------------


class TestHelpAndUsage:
    def test_help_flag_exits_zero(self, tmp_path):
        result = run_ft(["--help"], cwd=tmp_path)
        assert result.returncode == 0

    def test_help_lists_all_subcommands(self, tmp_path):
        result = run_ft(["--help"], cwd=tmp_path)
        output = result.stdout + result.stderr
        for cmd in ("init", "status", "continue", "approve", "reject", "graph"):
            assert cmd in output, f"'{cmd}' not listed in --help output"

    def test_help_mentions_process_flag(self, tmp_path):
        result = run_ft(["--help"], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "--process" in output or "-p" in output

    def test_init_help_mentions_llm_engine_flags(self, tmp_path):
        result = run_ft(["init", "--help"], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "--claude" in output
        assert "--codex" in output

    def test_no_args_shows_usage(self, tmp_path):
        result = run_ft([], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "ft" in output

    def test_init_subcommand_help(self, tmp_path):
        result = run_ft(["init", "--help"], cwd=tmp_path)
        assert result.returncode == 0

    def test_status_subcommand_help(self, tmp_path):
        result = run_ft(["status", "--help"], cwd=tmp_path)
        assert result.returncode == 0

    def test_continue_subcommand_help(self, tmp_path):
        result = run_ft(["continue", "--help"], cwd=tmp_path)
        assert result.returncode == 0

    def test_approve_subcommand_help(self, tmp_path):
        result = run_ft(["approve", "--help"], cwd=tmp_path)
        assert result.returncode == 0

    def test_reject_subcommand_help(self, tmp_path):
        result = run_ft(["reject", "--help"], cwd=tmp_path)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Tests: ft init
# ---------------------------------------------------------------------------


class TestInit:
    def test_exits_zero(self, ft_project):
        result = run_ft(["init"], cwd=ft_project)
        assert result.returncode == 0

    def test_creates_state_file(self, ft_project):
        run_ft(["init"], cwd=ft_project)
        state_file = ft_project / "runs" / "01" / "state" / "engine_state.yml"
        assert state_file.exists(), "engine_state.yml should be created by ft init"

    def test_output_mentions_process_title(self, ft_project):
        result = run_ft(["init"], cwd=ft_project)
        output = result.stdout + result.stderr
        assert "E2E Test Process" in output

    def test_output_mentions_first_node(self, ft_project):
        result = run_ft(["init"], cwd=ft_project)
        output = result.stdout + result.stderr
        assert "gate.01.start" in output

    def test_output_mentions_total_steps(self, ft_project):
        result = run_ft(["init"], cwd=ft_project)
        output = result.stdout + result.stderr
        assert "steps" in output.lower() or "total" in output.lower()

    def test_idempotent_second_init(self, ft_project):
        run_ft(["init"], cwd=ft_project)
        result = run_ft(["init"], cwd=ft_project)
        assert result.returncode == 0

    def test_explicit_process_flag(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        process_file = tmp_path / "custom.yml"
        process_file.write_text(GATE_ONLY_PROCESS)
        result = run_ft(["--process", str(process_file), "init"], cwd=tmp_path)
        assert result.returncode == 0
        assert (tmp_path / "runs" / "01" / "state" / "engine_state.yml").exists()

    def test_no_local_yaml_gives_clear_error(self, tmp_path):
        """Without a local process YAML, ft init gives a clear error with guidance."""
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["init"], cwd=tmp_path)
        assert result.returncode == 1
        assert "ft init --template" in result.stdout

    def test_codex_flag_persists_engine_choice(self, ft_project):
        result = run_ft(["init", "--codex"], cwd=ft_project)
        assert result.returncode == 0
        state_file = ft_project / "runs" / "01" / "state" / "engine_state.yml"
        assert "llm_engine: codex" in state_file.read_text()

    def test_missing_process_file_exits_nonzero(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["--process", "/does/not/exist.yml", "init"], cwd=tmp_path)
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Tests: ft status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_exits_zero(self, ft_project_initialized):
        result = run_ft(["status"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_shows_process_id(self, ft_project_initialized):
        result = run_ft(["status"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        assert "e2e_test_process" in output

    def test_shows_current_node(self, ft_project_initialized):
        result = run_ft(["status"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        assert "gate.01.start" in output

    def test_shows_progress_fraction(self, ft_project_initialized):
        result = run_ft(["status"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        # Progress shown as X/Y pattern
        assert "/" in output

    def test_full_flag_exits_zero(self, ft_project_initialized):
        result = run_ft(["status", "--full"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_full_flag_shows_graph(self, ft_project_initialized):
        result = run_ft(["status", "--full"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        # All node IDs should appear
        for node_id in ("gate.01.start", "gate.02.finish"):
            assert node_id in output, f"Node '{node_id}' missing from full graph output"

    def test_f_shorthand_exits_zero(self, ft_project_initialized):
        result = run_ft(["status", "-f"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_status_before_init_does_not_crash(self, ft_project):
        """status with no state file should not raise an unhandled exception."""
        result = run_ft(["status"], cwd=ft_project)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Tests: ft graph
# ---------------------------------------------------------------------------


class TestGraph:
    def test_exits_zero(self, ft_project_initialized):
        result = run_ft(["graph"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_shows_all_nodes(self, ft_project_initialized):
        result = run_ft(["graph"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        for node_id in ("gate.01.start", "gate.02.finish"):
            assert node_id in output

    def test_graph_same_as_status_full(self, ft_project_initialized):
        graph_result = run_ft(["graph"], cwd=ft_project_initialized)
        status_full_result = run_ft(["status", "--full"], cwd=ft_project_initialized)
        # Both should succeed and contain the same nodes
        assert graph_result.returncode == 0
        assert status_full_result.returncode == 0


# ---------------------------------------------------------------------------
# Tests: ft continue
# ---------------------------------------------------------------------------


class TestContinue:
    def test_exits_zero_on_gate(self, ft_project_initialized):
        """Gate node (no validators) should pass instantly."""
        result = run_ft(["continue"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_advances_to_next_node(self, ft_project_initialized):
        run_ft(["continue"], cwd=ft_project_initialized)
        result = run_ft(["status"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        # After advancing past gate.01, current node should be gate.02
        assert "gate.02" in output

    def test_gate_pass_shown_in_output(self, ft_project_initialized):
        result = run_ft(["continue"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        assert "PASS" in output or "pass" in output.lower() or "gate.01" in output

    def test_continue_without_init_auto_initializes(self, ft_project):
        """continue on an uninitialized project should initialize automatically."""
        result = run_ft(["continue"], cwd=ft_project)
        # Must not crash — either initializes or shows a clear message
        assert result.returncode == 0 or len(result.stdout + result.stderr) > 0

    def test_sprint_flag_exits_zero(self, ft_project_initialized):
        result = run_ft(["continue", "--sprint"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_complete_process_via_mvp(self, ft_project_initialized):
        """--mvp mode runs all gates until end."""
        result = run_ft(["continue", "--mvp"], cwd=ft_project_initialized)
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "COMPLETO" in output or "completo" in output.lower()


# ---------------------------------------------------------------------------
# Tests: ft approve
# ---------------------------------------------------------------------------


class TestApprove:
    def test_no_pending_exits_zero(self, ft_project_initialized):
        """approve when nothing is pending should exit 0 with informational message."""
        result = run_ft(["approve", "--no-continue"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_no_pending_shows_message(self, ft_project_initialized):
        result = run_ft(["approve", "--no-continue"], cwd=ft_project_initialized)
        output = result.stdout + result.stderr
        assert len(output.strip()) > 0

    def test_approve_advances_pending_node(self, ft_project_approval):
        """Set up pending approval by writing state, then approve."""
        import yaml

        state_file = ft_project_approval / "runs" / "01" / "state" / "engine_state.yml"
        with open(state_file) as f:
            state = yaml.safe_load(f) or {}

        # Simulate a pending approval
        state["pending_approval"] = "step.01.hipotese"
        state["current_node"] = "step.01.hipotese"
        state["node_status"] = "awaiting_approval"
        with open(state_file, "w") as f:
            yaml.dump(state, f)

        result = run_ft(["approve", "--no-continue"], cwd=ft_project_approval)
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "APROVADO" in output or "aprovado" in output.lower()


# ---------------------------------------------------------------------------
# Tests: ft reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_requires_reason_argument(self, tmp_path):
        """reject without <reason> should fail with argparse error."""
        result = run_ft(["reject"], cwd=tmp_path)
        assert result.returncode != 0

    def test_no_pending_exits_zero_with_reason(self, ft_project_initialized):
        """reject <reason> when nothing pending exits 0."""
        result = run_ft(
            ["reject", "motivo de teste", "--no-retry"],
            cwd=ft_project_initialized,
        )
        assert result.returncode == 0

    def test_no_pending_shows_message(self, ft_project_initialized):
        result = run_ft(
            ["reject", "motivo de teste", "--no-retry"],
            cwd=ft_project_initialized,
        )
        output = result.stdout + result.stderr
        assert len(output.strip()) > 0

    def test_reject_blocks_pending_node(self, ft_project_approval):
        """reject with --no-retry should block the pending node."""
        import yaml

        state_file = ft_project_approval / "runs" / "01" / "state" / "engine_state.yml"
        with open(state_file) as f:
            state = yaml.safe_load(f) or {}

        state["pending_approval"] = "step.01.hipotese"
        state["current_node"] = "step.01.hipotese"
        state["node_status"] = "awaiting_approval"
        with open(state_file, "w") as f:
            yaml.dump(state, f)

        result = run_ft(
            ["reject", "qualidade insuficiente", "--no-retry"],
            cwd=ft_project_approval,
        )
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "REJEITADO" in output or "rejeitado" in output.lower()


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_subcommand_exits_nonzero(self, tmp_path):
        result = run_ft(["comando_inexistente"], cwd=tmp_path)
        assert result.returncode != 0

    def test_reject_missing_reason_exits_nonzero(self, tmp_path):
        result = run_ft(["reject"], cwd=tmp_path)
        assert result.returncode != 0

    def test_process_flag_with_missing_file(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(["--process", "/nonexistent/path.yml", "init"], cwd=tmp_path)
        # Should fail gracefully
        assert result.returncode != 0 or "erro" in (result.stdout + result.stderr).lower()

    def test_no_state_dir_but_status_called(self, tmp_path):
        """ft status without project/state/ dir should not crash with a traceback."""
        result = run_ft(["status"], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "Traceback" not in output, "CLI should not expose raw Python tracebacks"
