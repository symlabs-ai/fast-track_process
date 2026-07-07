"""Regressions for ft run pre-seed inputs and exploration mode."""

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from ft.cli import main as cli_main
from ft.engine.delegate import DelegateResult
from ft.engine.runner import StepRunner
from ft.engine.state import EngineState


class FakeRunner:
    instances: list["FakeRunner"] = []

    def __init__(
        self,
        process_path,
        state_path,
        project_root=".",
        llm_engine=None,
        llm_model=None,
        verbose=False,
    ):
        self.process_path = process_path
        self.state_path = state_path
        self.project_root = Path(project_root)
        self.llm_engine = llm_engine
        self.llm_model = llm_model
        self.verbose = verbose
        self._bypass_human_gates = False
        self.inited = False
        self.run_mode = None
        self.instances.append(self)

    def init_state(self):
        self.inited = True

    def run(self, mode="step"):
        self.run_mode = mode


def _args(project: Path, **overrides) -> Namespace:
    base = {
        "project": str(project),
        "process": None,
        "from_project": None,
        "hipotese": None,
        "demand_input": None,
        "bypass_human_gates": False,
        "force": True,
        "template": "base",
        "worktree": None,
        "auto": True,
        "claude": None,
        "codex": None,
        "gemini": None,
        "opencode": None,
        "verbose": False,
    }
    base.update(overrides)
    return Namespace(**base)


def _valid_hypothesis() -> str:
    return "\n".join([
        "# Hipótese",
        "Contexto inicial.",
        "## Problema",
        "Linha 1 do problema.",
        "Linha 2 do problema.",
        "Linha 3 do problema.",
        "## Oportunidade",
        "Linha 1 da oportunidade.",
        "Linha 2 da oportunidade.",
        "Linha 3 da oportunidade.",
        "Linha 4 da oportunidade.",
    ])


class TestRunInputs:
    def test_run_input_uses_effective_llm_engine(self, tmp_path):
        FakeRunner.instances = []
        project = tmp_path / "project"
        demand = tmp_path / "demanda.md"
        demand.write_text("Quero criar tarefas e filtrar por status.\n")

        with (
            patch("ft.cli.main.StepRunner", FakeRunner),
            patch("ft.engine.triage.classify_demand") as classify,
            patch("ft.engine.triage.generate_hypothesis", return_value=_valid_hypothesis()),
        ):
            classify.return_value = {"questions": [], "process": {}}

            cli_main.cmd_run(_args(project, demand_input=str(demand), codex=True))

        assert classify.call_args.kwargs["llm_engine"] == "codex"
        assert FakeRunner.instances[-1].llm_engine == "codex"
        assert FakeRunner.instances[-1].run_mode == "mvp"
        run_root = FakeRunner.instances[-1].project_root
        assert (run_root / "docs" / "demanda.md").exists()
        assert (run_root / "docs" / "hipotese.md").exists()

    def test_run_hipotese_uses_default_engine_without_name_error(self, tmp_path):
        FakeRunner.instances = []
        project = tmp_path / "project"
        hipotese = tmp_path / "hipotese.md"
        hipotese.write_text(_valid_hypothesis())

        with patch("ft.cli.main.StepRunner", FakeRunner):
            cli_main.cmd_run(_args(project, hipotese=str(hipotese)))

        assert FakeRunner.instances[-1].llm_engine == "claude"
        assert FakeRunner.instances[-1].run_mode == "mvp"
        run_root = FakeRunner.instances[-1].project_root
        assert (run_root / "docs" / "hipotese.md").exists()

    def test_run_input_uses_opencode_engine_and_model(self, tmp_path):
        FakeRunner.instances = []
        project = tmp_path / "project"
        demand = tmp_path / "demanda.md"
        demand.write_text("Quero criar tarefas e filtrar por status.\n")

        with (
            patch("ft.cli.main.StepRunner", FakeRunner),
            patch("ft.engine.triage.classify_demand") as classify,
            patch("ft.engine.triage.generate_hypothesis", return_value=_valid_hypothesis()),
        ):
            classify.return_value = {"questions": [], "process": {}}

            cli_main.cmd_run(
                _args(
                    project,
                    demand_input=str(demand),
                    opencode="pgx/zai-org_glm-4.7-flash",
                )
            )

        assert classify.call_args.kwargs["llm_engine"] == "opencode"
        assert FakeRunner.instances[-1].llm_engine == "opencode"
        assert FakeRunner.instances[-1].llm_model == "pgx/zai-org_glm-4.7-flash"

    def test_run_without_git_executes_inside_plain_isolated_run_dir(self, tmp_path, monkeypatch):
        FakeRunner.instances = []
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        process = project / "process" / "process.yml"
        process.parent.mkdir(parents=True)
        process.write_text(
            """
id: plain_project
version: "1.0.0"
nodes:
  - id: start
    type: build
    title: Start
    executor: python
    next: end
  - id: end
    type: end
    title: End
"""
        )

        with patch("ft.cli.main.StepRunner", FakeRunner):
            cli_main.cmd_run(_args(project, opencode=True))

        run_dir = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-01-opencode"
        assert FakeRunner.instances[-1].project_root == run_dir
        assert FakeRunner.instances[-1].process_path == run_dir / "process" / "process.yml"
        assert (run_dir / "process" / "process.yml").exists()


class TestExplore:
    def test_explore_request_and_finish_write_logs_under_llm_logs(self, tmp_path):
        process = tmp_path / "process.yml"
        process.write_text(
            """
id: explore_process
version: "1.0.0"
title: Explore
nodes:
  - id: explore
    type: exploration
    title: Explore
    optional: true
    next: end
  - id: end
    type: end
    title: End
"""
        )
        runner = StepRunner(
            process_path=process,
            state_path=tmp_path / "state" / "engine_state.yml",
            project_root=tmp_path,
        )
        runner.init_state()
        runner._run_exploration(runner.graph.get_node("explore"))

        result = DelegateResult(True, "DONE", [], [])
        with patch("ft.engine.delegate.delegate_to_llm", return_value=result) as delegate:
            runner.explore_request("ajuste rápido")
            runner.explore_finish()

        log_paths = [call.kwargs["log_path"] for call in delegate.call_args_list]
        assert str(tmp_path / "state" / "llm_logs" / "exploration_01.log") in log_paths
        assert str(tmp_path / "state" / "llm_logs" / "exploration_report.log") in log_paths
        state = runner.state_mgr.load()
        assert state.current_node == "end"


class TestSetupEnv:
    def test_setup_env_runs_project_script(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        scripts = project / "process" / "scripts"
        scripts.mkdir(parents=True)
        marker = project / "configured.txt"
        script = scripts / "register_gateway.sh"
        script.write_text(f"#!/usr/bin/env bash\nset -e\ntouch {marker}\n")
        script.chmod(0o755)
        monkeypatch.setenv("SYM_GATEWAY_PROJECT_KEY", "sk-sym_test")

        cli_main.cmd_setup_env(Namespace(project=str(project)))

        assert marker.exists()

    def test_setup_env_runs_relative_project_script_from_parent_cwd(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        scripts = project / "process" / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / "register_gateway.sh"
        script.write_text("#!/usr/bin/env bash\nset -e\ntouch configured-from-cwd.txt\n")
        script.chmod(0o755)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SYM_GATEWAY_PROJECT_KEY", "sk-sym_test")

        cli_main.cmd_setup_env(Namespace(project="project"))

        assert (project / "configured-from-cwd.txt").exists()


class TestActiveRunDetection:
    def _write_state(self, state_file: Path, *, completed: bool = False) -> None:
        state_file.parent.mkdir(parents=True)
        completed_nodes = "- ft.start.route\n" if completed else ""
        steps_completed = 1 if completed else 0
        state_file.write_text(
            "process_id: fast_track_v3\n"
            "version: 1.0.0\n"
            "llm_engine: claude\n"
            "llm_model: null\n"
            "active_llm_log: null\n"
            "last_llm_log: null\n"
            "current_node: ft.start.route\n"
            "node_status: ready\n"
            "completed_nodes:\n"
            f"{completed_nodes}"
            "current_cycle: cycle-01\n"
            "current_sprint: null\n"
            "sprint_status: null\n"
            "gate_log: {}\n"
            "artifacts: {}\n"
            "blocked_reason: null\n"
            "pending_approval: null\n"
            "last_approval_message: null\n"
            "pending_fix: null\n"
            "exploration_log: []\n"
            "metrics:\n"
            f"  steps_completed: {steps_completed}\n"
            "  steps_total: 44\n"
            "  tests_passing: 0\n"
            "  coverage: 0\n"
            "  llm_calls: 0\n"
            "  tokens_used: 0\n"
        )

    def test_pristine_init_state_is_not_active_and_is_cleaned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "service_mate_15"
        project.mkdir()
        cycle = tmp_path / "ft-home" / "worktrees" / "service_mate_15" / "cycle-01"
        self._write_state(cycle / "state" / "engine_state.yml")
        (cycle / "cycle-01_log.md").write_text("| INIT | PASS |\n")

        assert cli_main._check_active_run(project) is None

        assert cli_main._cleanup_pristine_runs(project) == 1
        assert not cycle.exists()

    def test_empty_cycle_without_state_is_cleaned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "service_mate_15"
        project.mkdir()
        cycle = tmp_path / "ft-home" / "worktrees" / "service_mate_15" / "cycle-01-opencode"
        (cycle / "state").mkdir(parents=True)

        assert cli_main._cleanup_pristine_runs(project) == 1
        assert not cycle.exists()

    def test_state_with_completed_nodes_still_counts_as_active(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        cycle = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-01"
        self._write_state(cycle / "state" / "engine_state.yml", completed=True)

        active = cli_main._check_active_run(project)

        assert active == "cycle-01 (ft.start.route — ready)"
        assert cli_main._cleanup_pristine_runs(project) == 0
        assert cycle.exists()


class TestApiHealthCheck:
    def test_opencode_skips_anthropic_health_check(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FT_SKIP_HEALTH_CHECK", raising=False)

        with patch("urllib.request.urlopen") as urlopen:
            cli_main._api_health_check(tmp_path, "opencode")

        urlopen.assert_not_called()


class TestAbort:
    def test_abort_from_project_root_removes_plain_external_worktree(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "service_mate_15"
        (project / "process").mkdir(parents=True)
        monkeypatch.chdir(project)

        cycle = tmp_path / "ft-home" / "worktrees" / "service_mate_15" / "cycle-01-opencode"
        state = cycle / "state" / "engine_state.yml"
        state.parent.mkdir(parents=True)
        state.write_text(
            "process_id: fast_track_v3\n"
            "current_node: ft.plan.03.api_contract\n"
            "node_status: blocked\n"
        )

        args = Namespace(
            process=None,
            force=True,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )
        cli_main.cmd_abort(args)

        assert not cycle.exists()


class TestRetry:
    def test_retry_recovers_orphaned_delegated_state(self):
        state = EngineState(
            current_node="ft.plan.03.api_contract",
            node_status="delegated",
            active_llm_log="state/llm_logs/stale.log",
            _lock={"pid": 999999},
        )

        class StateMgr:
            def load(self):
                return state

            def save(self):
                self.saved = True

        class Runner:
            def __init__(self):
                self.state_mgr = StateMgr()
                self._auto_fix_counts = {"ft.plan.03.api_contract": 1}
                self.run_mode = None

            def run(self, mode="step"):
                self.run_mode = mode

        runner = Runner()
        args = Namespace(
            process=None,
            auto=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with patch("ft.cli.main.get_runner", return_value=runner):
            cli_main.cmd_retry(args)

        assert state.node_status == "ready"
        assert state.active_llm_log is None
        assert runner._auto_fix_counts == {}
        assert runner.run_mode == "step"
