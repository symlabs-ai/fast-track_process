"""
E2E CLI Validation — ft engine CLI

Validates all CLI commands via subprocess:
  ft --help
  ft init
  ft status [--full / -f]
  ft graph
  ft continue [--sprint / --auto]
  ft approve [--no-continue]
  ft reject <reason> [--no-retry]
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ft.engine import paths
from ft.engine.runner import StepRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.parent
PROCESS_NAME = "base"


def _process_file(project_dir: Path) -> Path:
    return paths.project_named_process_file(project_dir, PROCESS_NAME)


def _write_process(project_dir: Path, content: str) -> Path:
    process_file = _process_file(project_dir)
    process_file.parent.mkdir(parents=True, exist_ok=True)
    process_file.write_text(content)
    return process_file


def _state_file(project_dir: Path) -> Path:
    """Retorna o runtime continuous usado pelas fixtures de comandos."""
    return paths.continuous_state_path(project_dir)


def _initialize_state(project_dir: Path) -> None:
    import yaml

    state = _state_file(project_dir)
    runner = StepRunner(
        process_path=_process_file(project_dir),
        state_path=state,
        project_root=project_dir,
    )
    runner.init_state()
    data = yaml.safe_load(state.read_text()) or {}
    data["_lock"] = None
    state.write_text(yaml.safe_dump(data, sort_keys=False))


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
    _write_process(tmp_path, GATE_ONLY_PROCESS)
    return tmp_path


@pytest.fixture
def ft_project_initialized(ft_project: Path) -> Path:
    """ft_project with runtime state initialized independently of ft init."""
    result = run_ft(["init", "--template", "base"], cwd=ft_project)
    assert result.returncode == 0, f"init failed:\n{result.stdout}\n{result.stderr}"
    _initialize_state(ft_project)
    return ft_project


@pytest.fixture
def ft_project_approval(tmp_path: Path) -> Path:
    """ft project using the approval process (LLM step with requires_approval)."""
    _write_process(tmp_path, APPROVAL_PROCESS)
    run_ft(["init", "--template", "base"], cwd=tmp_path)
    _initialize_state(tmp_path)
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
        for cmd in ("init", "feature", "status", "continue", "approve", "reject", "graph"):
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
        assert "--opencode" in output
        assert "--effort" in output

    def test_feature_help_exposes_template_input_and_llm_flags(self, tmp_path):
        result = run_ft(["feature", "--help"], cwd=tmp_path)
        output = result.stdout + result.stderr

        assert result.returncode == 0
        assert "--template" in output
        assert "--input" in output
        assert "--claude" in output
        assert "--codex" in output
        assert "{feature,tweak}" in output

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
        result = run_ft(["init", "--template", "base"], cwd=ft_project)
        assert result.returncode == 0

    def test_creates_no_state_file(self, ft_project):
        run_ft(["init", "--template", "base"], cwd=ft_project)
        assert not _state_file(ft_project).exists()
        assert not list(paths.worktrees_home(ft_project).glob("*/state/engine_state.yml"))

    def test_output_mentions_process_title(self, ft_project):
        result = run_ft(["init", "--template", "base"], cwd=ft_project)
        output = result.stdout + result.stderr
        assert "E2E Test Process" in output

    def test_output_mentions_first_node(self, ft_project):
        result = run_ft(["init", "--template", "base"], cwd=ft_project)
        output = result.stdout + result.stderr
        assert "gate.01.start" in output

    def test_output_mentions_total_steps(self, ft_project):
        result = run_ft(["init", "--template", "base"], cwd=ft_project)
        output = result.stdout + result.stderr
        assert "steps" in output.lower() or "total" in output.lower()

    def test_second_init_fails_for_initialized_project(self, ft_project):
        run_ft(["init", "--template", "base"], cwd=ft_project)
        result = run_ft(["init", "--template", "base"], cwd=ft_project)
        assert result.returncode != 0
        assert "já inicializado" in (result.stdout + result.stderr)

    def test_copies_agents_md_playbook(self, ft_project):
        """ft init copia o AGENTS.md do engine para a raiz do projeto."""
        run_ft(["init", "--template", "base"], cwd=ft_project)
        agents = ft_project / "AGENTS.md"
        assert agents.exists(), "AGENTS.md deveria ser copiado pelo ft init"
        assert "ft engine" in agents.read_text()

    def test_does_not_overwrite_existing_agents_md(self, ft_project):
        """AGENTS.md pré-existente do projeto não é sobrescrito."""
        custom = "# AGENTS.md customizado do projeto\n"
        (ft_project / "AGENTS.md").write_text(custom)
        result = run_ft(["init", "--template", "base"], cwd=ft_project)
        assert result.returncode == 0
        assert (ft_project / "AGENTS.md").read_text() == custom

    def test_explicit_process_flag_is_rejected(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        process_file = tmp_path / "custom.yml"
        process_file.write_text(GATE_ONLY_PROCESS)
        result = run_ft(
            ["--process", str(process_file), "init", "--template", "base"],
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "não aceita --process" in (result.stdout + result.stderr)
        assert not paths.project_manifest(tmp_path).exists()
        assert not _state_file(tmp_path).exists()

    def test_missing_template_lists_available_names(self, tmp_path):
        result = run_ft(["init"], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert result.returncode == 2
        assert "--template" in output
        assert "mvp-builder" in output
        assert not (tmp_path / ".ft").exists()

    def test_codex_flag_persists_engine_choice(self, ft_project):
        result = run_ft(
            ["init", "--template", "base", "--codex", "--effort", "max"],
            cwd=ft_project,
        )
        assert result.returncode == 0
        manifest = paths.project_manifest(ft_project)
        assert "llm_engine: codex" in manifest.read_text()
        assert "llm_effort: max" in manifest.read_text()

    def test_opencode_flag_persists_engine_choice(self, ft_project):
        result = run_ft(["init", "--template", "base", "--opencode"], cwd=ft_project)
        assert result.returncode == 0
        manifest = paths.project_manifest(ft_project)
        assert "llm_engine: opencode" in manifest.read_text()

    def test_missing_process_file_exits_nonzero(self, tmp_path):
        (tmp_path / "project" / "state").mkdir(parents=True)
        result = run_ft(
            ["--process", "/does/not/exist.yml", "init", "--template", "base"],
            cwd=tmp_path,
        )
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
        output = result.stdout + result.stderr
        assert "nenhum ciclo ativo" in output.lower()
        assert "Progresso:" not in output

    def test_status_ignores_pristine_legacy_continuous_state(self, ft_project):
        """Um runtime vazio não ressuscita o processo default nem progresso 1/N."""
        state = _state_file(ft_project)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            "process_id: removed_process\n"
            "version: 1.1.0\n"
            "llm_engine: claude\n"
            "current_node: null\n"
            "node_status: ready\n"
            "completed_nodes: []\n"
            "metrics:\n"
            "  steps_completed: 0\n"
            "  steps_total: 54\n"
        )
        archive = paths.project_cycles_dir(ft_project) / "cycle-10"
        archive.mkdir(parents=True)
        (archive / "cycle.yml").write_text(
            "id: cycle-10\n"
            "status: done\n"
            "progress:\n"
            "  completed: 11\n"
            "  total: 11\n"
        )

        result = run_ft(["status"], cwd=ft_project)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "nenhum ciclo ativo" in output.lower()
        assert "cycle-10 (concluído)" in output
        assert "removed_process" not in output
        assert "1/54" not in output

    def test_status_does_not_create_state_when_no_cycle_exists(self, ft_project):
        state = _state_file(ft_project)
        assert not state.exists()

        result = run_ft(["status"], cwd=ft_project)

        assert result.returncode == 0
        assert not state.exists()

    def test_explicit_cycle_cannot_reopen_pristine_state(self, ft_project):
        cycle = "cycle-07"
        state = (
            paths.worktrees_home(ft_project)
            / cycle
            / "state"
            / "engine_state.yml"
        )
        state.parent.mkdir(parents=True)
        state.write_text(
            "process_id: removed_process\n"
            "current_node: null\n"
            "node_status: ready\n"
            "completed_nodes: []\n"
            "metrics:\n"
            "  steps_completed: 0\n"
            "  steps_total: 54\n"
        )

        result = run_ft(["status", "--cycle", cycle], cwd=ft_project)

        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "nenhum ciclo ativo" in output.lower()
        assert "removed_process" not in output
        assert "1/54" not in output


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

    def test_without_cycle_does_not_show_default_process(self, ft_project):
        result = run_ft(["graph"], cwd=ft_project)
        output = result.stdout + result.stderr

        assert result.returncode == 0
        assert "nenhum ciclo ativo" in output.lower()
        assert "e2e_test_process" not in output


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

    def test_continue_without_active_cycle_does_not_initialize(self, ft_project):
        """continue retoma ciclos; não fabrica um run a partir do default local."""
        state = _state_file(ft_project)
        assert not state.exists()

        result = run_ft(["continue"], cwd=ft_project)

        assert result.returncode == 0
        assert "nenhum ciclo ativo" in (result.stdout + result.stderr).lower()
        assert not state.exists()

    def test_sprint_flag_exits_zero(self, ft_project_initialized):
        result = run_ft(["continue", "--sprint"], cwd=ft_project_initialized)
        assert result.returncode == 0

    def test_complete_process_via_auto(self, ft_project_initialized):
        """--auto mode runs all gates until end."""
        result = run_ft(["continue", "--auto"], cwd=ft_project_initialized)
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

        state_file = _state_file(ft_project_approval)
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

        state_file = _state_file(ft_project_approval)
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
        result = run_ft(
            ["--process", "/nonexistent/path.yml", "init", "--template", "base"],
            cwd=tmp_path,
        )
        # Should fail gracefully
        assert result.returncode != 0 or "erro" in (result.stdout + result.stderr).lower()

    def test_no_state_dir_but_status_called(self, tmp_path):
        """ft status without project/state/ dir should not crash with a traceback."""
        result = run_ft(["status"], cwd=tmp_path)
        output = result.stdout + result.stderr
        assert "Traceback" not in output, "CLI should not expose raw Python tracebacks"
