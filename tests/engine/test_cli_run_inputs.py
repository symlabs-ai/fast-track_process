"""Regressions for ft run pre-seed inputs and exploration mode."""

from argparse import Namespace
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ft.cli import main as cli_main
from ft.engine import feature_batch as fb
from ft.engine.delegate import DelegateResult
from ft.engine.layout import ensure_project_layout, register_project_process
from ft.engine.runner import StepRunner, ValidationResult
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
        llm_effort=None,
        llm_defaults_root=None,
        llm_engine_is_override=None,
        llm_model_is_override=None,
        llm_effort_is_override=None,
        verbose=False,
    ):
        self.process_path = process_path
        self.state_path = state_path
        self.project_root = Path(project_root)
        self.llm_engine = llm_engine
        self.llm_model = llm_model
        self.llm_effort = llm_effort
        self.llm_defaults_root = llm_defaults_root
        self.llm_engine_is_override = llm_engine_is_override
        self.llm_model_is_override = llm_model_is_override
        self.llm_effort_is_override = llm_effort_is_override
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
        "cycle_name": None,
        "template": "base",
        "worktree": None,
        "auto": True,
        "claude": None,
        "codex": None,
        "gemini": None,
        "opencode": None,
        "effort": None,
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


def _write_local_process(
    project: Path,
    content: str,
    *,
    name: str = "plain-project",
    defaults: dict | None = None,
) -> Path:
    ensure_project_layout(project, defaults=defaults)
    process = project / ".ft" / "process" / name / "process.yml"
    process.parent.mkdir(parents=True, exist_ok=True)
    process.write_text(content)
    register_project_process(
        project,
        process_name=name,
        process_path=process,
        template_id=name,
        entrypoint="init",
        set_default=True,
    )
    return process


class TestRunInputs:
    def test_run_rejects_process_outside_local_named_catalog(self, tmp_path):
        project = tmp_path / "project"
        _write_local_process(
            project,
            "id: local\nversion: '1'\nnodes:\n  - id: end\n    type: end\n    title: End\n",
        )
        external = tmp_path / "global-template" / "process.yml"
        external.parent.mkdir()
        external.write_text(
            "id: external\nversion: '1'\nnodes:\n  - id: end\n    type: end\n    title: End\n"
        )

        with pytest.raises(ValueError, match="deve estar dentro de .ft/process"):
            cli_main.cmd_run(
                _args(project, process=str(external), template=None)
            )

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

            cli_main.cmd_run(
                _args(project, demand_input=str(demand), codex=True, effort="max")
            )

        assert classify.call_args.kwargs["llm_engine"] == "codex"
        assert classify.call_args.kwargs["llm_effort"] == "max"
        assert FakeRunner.instances[-1].llm_engine == "codex"
        assert FakeRunner.instances[-1].llm_effort == "max"
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

    def test_run_uses_versioned_engine_default_without_init_state(self, tmp_path, monkeypatch):
        FakeRunner.instances = []
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        _write_local_process(
            project,
            """
id: plain_project
version: "1.0.0"
nodes:
  - id: start
    type: end
    title: End
""",
            defaults={"llm_engine": "opencode", "llm_effort": "high"},
        )

        with patch("ft.cli.main.StepRunner", FakeRunner):
            cli_main.cmd_run(_args(project, template=None))

        assert FakeRunner.instances[-1].llm_engine == "opencode"
        assert FakeRunner.instances[-1].llm_effort == "high"
        assert FakeRunner.instances[-1].project_root.name == "cycle-01-opencode"

    def test_run_without_git_executes_inside_plain_isolated_run_dir(self, tmp_path, monkeypatch):
        FakeRunner.instances = []
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        _write_local_process(
            project,
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
""",
        )

        with patch("ft.cli.main.StepRunner", FakeRunner):
            cli_main.cmd_run(_args(project, opencode=True, template=None))

        run_dir = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-01-opencode"
        assert FakeRunner.instances[-1].project_root == run_dir
        assert FakeRunner.instances[-1].process_path == (
            run_dir / ".ft" / "process" / "plain-project" / "process.yml"
        )
        assert (run_dir / ".ft" / "process" / "plain-project" / "process.yml").exists()

    def test_run_without_git_accepts_explicit_cycle_name(self, tmp_path, monkeypatch):
        FakeRunner.instances = []
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        _write_local_process(
            project,
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
""",
        )

        with patch("ft.cli.main.StepRunner", FakeRunner):
            cli_main.cmd_run(
                _args(
                    project,
                    opencode=True,
                    cycle_name="cycle-11-opencode",
                    template=None,
                )
            )

        run_dir = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-11-opencode"
        assert FakeRunner.instances[-1].project_root == run_dir
        assert (run_dir / ".ft" / "process" / "plain-project" / "process.yml").exists()
        assert (tmp_path / "ft-home" / "worktrees" / "project" / ".cycles").read_text() == "11\n"

    def test_run_rejects_existing_explicit_cycle_name(self, tmp_path, monkeypatch):
        FakeRunner.instances = []
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        _write_local_process(
            project,
            """
id: plain_project
version: "1.0.0"
nodes:
  - id: start
    type: end
    title: End
""",
        )
        existing = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-11-opencode"
        existing.mkdir(parents=True)

        with patch("ft.cli.main.StepRunner", FakeRunner):
            try:
                cli_main.cmd_run(
                    _args(
                        project,
                        opencode=True,
                        cycle_name="cycle-11-opencode",
                        template=None,
                    )
                )
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("cmd_run should reject existing explicit cycle name")

        assert FakeRunner.instances == []

    def test_run_rejects_invalid_explicit_cycle_name(self, tmp_path, monkeypatch):
        FakeRunner.instances = []
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"

        with patch("ft.cli.main.StepRunner", FakeRunner):
            try:
                cli_main.cmd_run(_args(project, opencode=True, cycle_name="../bad"))
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("cmd_run should reject unsafe cycle name")

        assert FakeRunner.instances == []


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
        process = _write_local_process(
            project,
            "id: setup\nversion: '1'\nnodes:\n  - id: end\n    type: end\n    title: End\n",
            name="setup",
        )
        scripts = process.parent / "scripts"
        scripts.mkdir()
        marker = project / "configured.txt"
        script = scripts / "register_gateway.sh"
        script.write_text(f"#!/usr/bin/env bash\nset -e\ntouch {marker}\n")
        script.chmod(0o755)
        monkeypatch.setenv("SYM_GATEWAY_PROJECT_KEY", "sk-sym_test")

        cli_main.cmd_setup_env(Namespace(project=str(project)))

        assert marker.exists()

    def test_setup_env_runs_relative_project_script_from_parent_cwd(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        process = _write_local_process(
            project,
            "id: setup\nversion: '1'\nnodes:\n  - id: end\n    type: end\n    title: End\n",
            name="setup",
        )
        scripts = process.parent / "scripts"
        scripts.mkdir()
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
            "process_id: test_process\n"
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

    def test_cancelled_state_is_not_active(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        cycle = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-01"
        self._write_state(cycle / "state" / "engine_state.yml", completed=True)
        state = cycle / "state" / "engine_state.yml"
        state.write_text(state.read_text().replace("node_status: ready", "node_status: cancelled"))

        assert cli_main._check_active_run(project) is None

    def test_find_latest_state_prefers_active_over_newer_cancelled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        active = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-01"
        cancelled = tmp_path / "ft-home" / "worktrees" / "project" / "cycle-02"
        self._write_state(active / "state" / "engine_state.yml", completed=True)
        self._write_state(cancelled / "state" / "engine_state.yml", completed=True)
        cancelled_state = cancelled / "state" / "engine_state.yml"
        cancelled_state.write_text(
            cancelled_state.read_text().replace("node_status: ready", "node_status: cancelled")
        )

        assert cli_main._find_latest_state(project) == active / "state" / "engine_state.yml"


class TestStatusMultipleCycles:
    @staticmethod
    def _write_open_state(project: Path, cycle_name: str) -> None:
        state = (
            cli_main.paths.worktrees_home(project)
            / cycle_name
            / "state"
            / "engine_state.yml"
        )
        state.parent.mkdir(parents=True)
        state.write_text(
            "process_id: feature\n"
            "current_node: feature.discovery\n"
            "node_status: delegated\n",
            encoding="utf-8",
        )

    @staticmethod
    def _args(*, cycle=None, full=False, report=False) -> Namespace:
        return Namespace(
            process=None,
            cycle=cycle,
            full=full,
            report=report,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            effort=None,
            verbose=False,
        )

    @staticmethod
    def _fake_runner_factory(calls: list[dict]):
        class Runner:
            def __init__(self, cycle):
                self.cycle = cycle

            def status(self, full=False):
                calls.append({"cycle": self.cycle, "method": "status", "full": full})
                print(f"status:{self.cycle or 'auto'}")

            def status_report(self):
                calls.append({"cycle": self.cycle, "method": "report"})
                print(f"report:{self.cycle or 'auto'}")

        def factory(*args, **kwargs):
            return Runner(kwargs.get("cycle"))

        return factory

    def test_status_without_cycle_renders_every_open_runtime(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        self._write_open_state(project, "cycle-14-f-03")
        self._write_open_state(project, "cycle-13-f-01")
        calls: list[dict] = []

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(cli_main, "get_runner", side_effect=self._fake_runner_factory(calls)),
        ):
            cli_main.cmd_status(self._args(full=True))

        output = capsys.readouterr().out
        first_header = output.index("Ciclo: cycle-13-f-01")
        first_status = output.index("status:cycle-13-f-01")
        second_header = output.index("Ciclo: cycle-14-f-03")
        second_status = output.index("status:cycle-14-f-03")
        assert first_header < first_status < second_header < second_status
        assert output.count("Ciclo:") == 2
        assert calls == [
            {"cycle": "cycle-13-f-01", "method": "status", "full": True},
            {"cycle": "cycle-14-f-03", "method": "status", "full": True},
        ]

    def test_status_report_is_propagated_to_every_open_runtime(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        self._write_open_state(project, "cycle-13-f-01")
        self._write_open_state(project, "cycle-14-f-03")
        calls: list[dict] = []

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(cli_main, "get_runner", side_effect=self._fake_runner_factory(calls)),
        ):
            cli_main.cmd_status(self._args(report=True))

        assert calls == [
            {"cycle": "cycle-13-f-01", "method": "report"},
            {"cycle": "cycle-14-f-03", "method": "report"},
        ]

    def test_status_explicit_cycle_remains_single(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        self._write_open_state(project, "cycle-13-f-01")
        self._write_open_state(project, "cycle-14-f-03")
        fb.save_batch(
            fb.FeatureBatch(
                batch_id="batch-04",
                project_root=str(project),
                template="tweak",
                features=[fb.BatchFeature("F-01", "Ajustar cor")],
                waves=[],
                status="planning",
            )
        )
        calls: list[dict] = []

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(cli_main, "get_runner", side_effect=self._fake_runner_factory(calls)),
        ):
            cli_main.cmd_status(self._args(cycle="cycle-13-f-01"))

        output = capsys.readouterr().out
        assert "Ciclo:" not in output
        assert "Batch paralelo" not in output
        assert calls == [
            {"cycle": "cycle-13-f-01", "method": "status", "full": False}
        ]

    def test_status_single_runtime_preserves_unlabelled_output(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        self._write_open_state(project, "cycle-13-f-01")
        calls: list[dict] = []

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(cli_main, "get_runner", side_effect=self._fake_runner_factory(calls)),
        ):
            cli_main.cmd_status(self._args())

        output = capsys.readouterr().out
        assert "Ciclo:" not in output
        assert calls == [{"cycle": None, "method": "status", "full": False}]

    def test_status_without_runtime_preserves_no_active_output(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(cli_main, "get_runner") as get_runner,
        ):
            cli_main.cmd_status(self._args())

        output = capsys.readouterr().out
        assert "Status: nenhum ciclo ativo" in output
        get_runner.assert_not_called()

    @pytest.mark.parametrize("batch_status", ["planning", "planned"])
    def test_status_shows_parallel_batch_while_planner_has_no_cycle(
        self, tmp_path, monkeypatch, capsys, batch_status
    ):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        batch = fb.FeatureBatch(
            batch_id="batch-04",
            project_root=str(project),
            template="tweak",
            features=[
                fb.BatchFeature("F-01", "Ajustar cor"),
                fb.BatchFeature("F-02", "Ajustar label"),
                fb.BatchFeature("F-03", "Ajustar teclado"),
            ],
            waves=[],
            status=batch_status,
            planner_engine="codex",
            planner_model="gpt-5.6-sol",
            planner_effort="high",
        )
        fb.save_batch(batch)

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(cli_main, "get_runner") as get_runner,
        ):
            cli_main.cmd_status(self._args())

        output = capsys.readouterr().out
        assert "Batch paralelo: batch-04" in output
        assert "Fase: plan" in output
        assert "Template: tweak" in output
        assert "LLM engine: codex" in output
        assert "LLM model: gpt-5.6-sol" in output
        assert "LLM effort: high" in output
        assert "Demandas: 3" in output
        assert "nenhum ciclo ativo" not in output.lower()
        get_runner.assert_not_called()

    def test_open_cycles_keep_precedence_over_parallel_batch_planning(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "project"
        project.mkdir()
        self._write_open_state(project, "cycle-13-f-01")
        self._write_open_state(project, "cycle-14-f-03")
        fb.save_batch(
            fb.FeatureBatch(
                batch_id="batch-04",
                project_root=str(project),
                template="tweak",
                features=[fb.BatchFeature("F-01", "Ajustar cor")],
                waves=[],
                status="planning",
            )
        )
        calls: list[dict] = []

        with (
            patch.object(cli_main, "find_project_root", return_value=project),
            patch.object(
                cli_main,
                "get_runner",
                side_effect=self._fake_runner_factory(calls),
            ),
        ):
            cli_main.cmd_status(self._args())

        output = capsys.readouterr().out
        assert "Batch paralelo" not in output
        assert output.count("Ciclo:") == 2
        assert [call["cycle"] for call in calls] == [
            "cycle-13-f-01",
            "cycle-14-f-03",
        ]

    def test_local_worktree_status_keeps_precedence_over_batch_planning(
        self, tmp_path, monkeypatch, capsys
    ):
        ft_home = tmp_path / "ft-home"
        monkeypatch.setenv("FT_HOME", str(ft_home))
        worktree = ft_home / "worktrees" / "project" / "cycle-13-f-01"
        local_state = worktree / "state" / "engine_state.yml"
        local_state.parent.mkdir(parents=True)
        local_state.write_text(
            "process_id: feature\n"
            "current_node: feature.discovery\n"
            "node_status: delegated\n",
            encoding="utf-8",
        )
        fb.save_batch(
            fb.FeatureBatch(
                batch_id="batch-04",
                project_root=str(worktree),
                template="tweak",
                features=[fb.BatchFeature("F-01", "Ajustar cor")],
                waves=[],
                status="planning",
            )
        )
        calls: list[dict] = []

        with (
            patch.object(cli_main, "find_project_root", return_value=worktree),
            patch.object(
                cli_main,
                "get_runner",
                side_effect=self._fake_runner_factory(calls),
            ),
        ):
            cli_main.cmd_status(self._args())

        output = capsys.readouterr().out
        assert "Batch paralelo" not in output
        assert calls == [{"cycle": None, "method": "status", "full": False}]


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
            "process_id: test_process\n"
            "current_node: ft.plan.03.api_contract\n"
            "node_status: blocked\n"
        )
        other_cycle = tmp_path / "ft-home" / "worktrees" / "demo" / "cycle-01-opencode"
        (other_cycle / "state").mkdir(parents=True)
        (other_cycle / "state" / "engine_state.yml").write_text("node_status: blocked\n")

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
        assert other_cycle.exists()


class TestClose:
    @pytest.mark.parametrize(
        "state",
        [
            {"node_status": "blocked"},
            {
                "node_status": "running",
                "current_node": None,
                "metrics": {"steps_completed": 0, "steps_total": 54},
            },
        ],
    )
    def test_truncated_state_is_not_a_runtime(self, state):
        assert cli_main._state_represents_runtime(state) is False

    def test_close_ignores_pristine_runtime_without_current_node(
        self, tmp_path, capsys
    ):
        state = SimpleNamespace(
            node_status="ready",
            current_node=None,
            completed_nodes=[],
            metrics={"steps_completed": 0, "steps_total": 54},
        )

        class _StateMgr:
            def load(self):
                return state

        class _Runner:
            project_root = tmp_path
            state_mgr = _StateMgr()
            merge_called = False

            def merge_on_close(self, *_args, **_kwargs):
                self.merge_called = True
                return True

        runner = _Runner()
        args = Namespace(
            process=None,
            force=False,
            merge="full",
            merge_paths=None,
            keep_worktree=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with patch("ft.cli.main.get_runner", return_value=runner):
            cli_main.cmd_close(args)

        output = capsys.readouterr().out
        assert "nenhum ciclo ativo" in output.lower()
        assert "1/54" not in output
        assert runner.merge_called is False

    def test_close_blocks_merge_when_backlog_has_undecided_p0(self, tmp_path):
        work = tmp_path / "cycle"
        docs = work / "docs"
        docs.mkdir(parents=True)
        (docs / "PROJECT_BACKLOG.md").write_text(
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P0 | planned | PRD | Cadastro | Criar item pela UI | — | — |\n",
            encoding="utf-8",
        )

        class _StateMgr:
            def load(self):
                return SimpleNamespace(node_status="done", current_node=None)

        class _Runner:
            project_root = work
            state_mgr = _StateMgr()
            merge_called = False

            def merge_on_close(self, *_args, **_kwargs):
                self.merge_called = True
                return True

        runner = _Runner()
        args = Namespace(
            process=None,
            force=False,
            merge="full",
            merge_paths=None,
            keep_worktree=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with patch("ft.cli.main.get_runner", return_value=runner):
            cli_main.cmd_close(args)

        assert runner.merge_called is False

    def test_close_blocks_declared_features_catalog_without_delivered_coverage(self, tmp_path):
        work = tmp_path / "cycle"
        docs = work / "docs"
        docs.mkdir(parents=True)
        (docs / "PROJECT_BACKLOG.md").write_text(
            "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| PB-001 | US | P0 | done | PRD | Cadastro | Criar item pela UI | report.md | Entregue |\n",
            encoding="utf-8",
        )

        class _StateMgr:
            def load(self):
                return SimpleNamespace(node_status="done", current_node=None)

        class _Runner:
            project_root = work
            state_mgr = _StateMgr()
            graph = SimpleNamespace(
                meta={"artifact_policy": {"canonical": ["docs/FEATURES.md"]}}
            )
            merge_called = False

            def merge_on_close(self, *_args, **_kwargs):
                self.merge_called = True
                return True

        runner = _Runner()
        args = Namespace(
            process=None,
            force=False,
            merge="full",
            merge_paths=None,
            keep_worktree=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with patch("ft.cli.main.get_runner", return_value=runner):
            cli_main.cmd_close(args)

        assert runner.merge_called is False


class TestCancel:
    def test_cancel_external_worktree_writes_report_in_cycle_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FT_HOME", str(tmp_path / "ft-home"))
        project = tmp_path / "service_mate_15"
        (project / "process").mkdir(parents=True)
        monkeypatch.chdir(project)

        cycle = tmp_path / "ft-home" / "worktrees" / "service_mate_15" / "cycle-02-opencode"
        state = cycle / "state" / "engine_state.yml"
        state.parent.mkdir(parents=True)
        state.write_text(
            "process_id: test_process\n"
            "current_node: ft.plan.01.task_list\n"
            "node_status: ready\n"
            "completed_nodes:\n"
            "  - ft.start.route\n"
            "gate_log:\n"
            "  ft.start.route: PASS\n"
            "artifacts: {}\n"
            "blocked_reason: null\n"
            "_lock: null\n"
            "metrics:\n"
            "  steps_total: 44\n"
        )

        args = Namespace(
            reason="descartar ciclo interrompido",
            claude=None,
            codex=None,
            gemini=None,
            opencode=True,
        )

        with patch("ft.engine.delegate.delegate_to_llm") as delegate_mock:
            delegate_mock.return_value = DelegateResult(
                success=False,
                output="sem llm",
                files_created=[],
                files_modified=[],
            )
            cli_main.cmd_cancel(args)

        report = cycle / "CANCELLED.md"
        assert report.exists()
        data = state.read_text()
        assert "node_status: cancelled" in data
        assert "CANCELADO: descartar ciclo interrompido" in data
        kwargs = delegate_mock.call_args.kwargs
        assert kwargs["project_root"] == str(cycle)
        assert kwargs["allowed_paths"] == ["CANCELLED.md"]
        assert kwargs["llm_engine"] == "opencode"


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
                self._bypass_human_gates = None

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
            bypass_human_gates=True,
        )

        with patch("ft.cli.main.get_runner", return_value=runner):
            cli_main.cmd_retry(args)

        assert state.node_status == "ready"
        assert state.active_llm_log is None
        assert runner._auto_fix_counts == {}
        assert runner.run_mode == "step"
        assert runner._bypass_human_gates is True

    def test_retry_treats_recycled_live_pid_as_orphaned_delegation(self):
        state = EngineState(
            current_node="ft.plan.03.api_contract",
            node_status="delegated",
            active_llm_log="state/llm_logs/stale.log",
            _lock={
                "pid": os.getpid(),
                "pid_start": "not-the-current-process",
            },
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
                self._bypass_human_gates = None

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
            bypass_human_gates=False,
        )

        with patch("ft.cli.main.get_runner", return_value=runner):
            cli_main.cmd_retry(args)

        assert state.node_status == "ready"
        assert state.active_llm_log is None
        assert runner.run_mode == "step"


class TestFix:
    def test_single_fix_target_path_detects_unique_project_file(self, tmp_path):
        target = tmp_path / "project" / "tests" / "e2e" / "test_navigation.py"
        target.parent.mkdir(parents=True)
        target.write_text("def test_old():\n    pass\n", encoding="utf-8")

        detected = cli_main._single_fix_target_path(
            "Corrija somente project/tests/e2e/test_navigation.py.",
            tmp_path,
        )

        assert detected == "project/tests/e2e/test_navigation.py"

    def test_fix_done_opencode_uses_capture_for_single_target_file(self, tmp_path):
        state_path = tmp_path / "state" / "engine_state.yml"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("node_status: done\n")
        target = tmp_path / "project" / "tests" / "e2e" / "test_navigation.py"
        target.parent.mkdir(parents=True)
        target.write_text("def test_old():\n    pass\n", encoding="utf-8")
        frontend = tmp_path / "project" / "frontend" / "src" / "main.js"
        frontend.parent.mkdir(parents=True)
        frontend.write_text("const routes = {'/clientes': 'Clientes'};\n", encoding="utf-8")
        state = EngineState(
            current_node=None,
            node_status="done",
            llm_engine="opencode",
            llm_model="pgx/zai-org_glm-4.7-flash",
        )

        class StateMgr:
            path = state_path

            def load(self):
                return state

        class Runner:
            def __init__(self):
                self.state_mgr = StateMgr()
                self.project_root = tmp_path
                self.graph = SimpleNamespace(nodes={})

            def apply_fix(self, instruction):
                return False

            def _resolve_llm_engine(self, loaded_state=None, node=None):
                return loaded_state.llm_engine if loaded_state else "claude"

            def _resolve_llm_model(self, loaded_state=None, node=None):
                return loaded_state.llm_model if loaded_state else None

            def _resolve_llm_effort(self, loaded_state=None, node=None):
                return loaded_state.llm_effort if loaded_state else None

            def _capture_delegation_llm_selection(self, loaded_state, node=None):
                return SimpleNamespace(
                    engine=self._resolve_llm_engine(loaded_state, node),
                    model=self._resolve_llm_model(loaded_state, node),
                    effort=self._resolve_llm_effort(loaded_state, node),
                )

        args = Namespace(
            instruction="Corrija somente project/tests/e2e/test_navigation.py.",
            process=None,
            auto=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with (
            patch("ft.cli.main.get_runner", return_value=Runner()),
            patch("ft.engine.delegate.delegate_to_llm") as delegate,
        ):
            delegate.return_value = DelegateResult(True, "DONE", [], ["project/tests/e2e/test_navigation.py"])
            cli_main.cmd_fix(args)

        kwargs = delegate.call_args.kwargs
        assert kwargs["allowed_paths"] == ["project/tests/e2e/test_navigation.py"]
        assert kwargs["opencode_capture_output_path"] == "project/tests/e2e/test_navigation.py"
        assert "CONTEUDO ATUAL" in kwargs["task"]
        assert "def test_old" in kwargs["task"]
        assert "CONTEXTO DA UI ATUAL" in kwargs["task"]
        assert "const routes" in kwargs["task"]

    def test_postprocess_opencode_fix_rewrites_invalid_e2e_python(self, tmp_path):
        target = tmp_path / "project" / "tests" / "e2e" / "test_navigation.py"
        target.parent.mkdir(parents=True)
        target.write_text("def broken(:\n", encoding="utf-8")

        class Runner:
            project_root = tmp_path
            _work_dir = str(tmp_path)

            def _write_opencode_e2e_test(self, root):
                target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        note = cli_main._postprocess_opencode_fix_capture(
            Runner(),
            "project/tests/e2e/test_navigation.py",
        )

        assert note is not None
        assert "determinístico" in note
        assert "def test_ok" in target.read_text(encoding="utf-8")

    def test_postprocess_opencode_fix_rewrites_outerhtml_canvas_e2e(self, tmp_path):
        target = tmp_path / "project" / "tests" / "e2e" / "test_navigation.py"
        target.parent.mkdir(parents=True)
        target.write_text(
            "def test_canvas():\n"
            "    before = 'arena-board outerHTML'\n"
            "    assert before\n",
            encoding="utf-8",
        )

        class Runner:
            project_root = tmp_path
            _work_dir = str(tmp_path)

            def _write_opencode_e2e_test(self, root):
                target.write_text("def test_ok():\n    assert 'toDataURL()'\n", encoding="utf-8")

        note = cli_main._postprocess_opencode_fix_capture(
            Runner(),
            "project/tests/e2e/test_navigation.py",
        )

        assert note is not None
        assert "toDataURL" in note
        assert "outerHTML" not in target.read_text(encoding="utf-8")

    def test_arena_board_fix_adds_canvas_testid_without_delegate(self, tmp_path):
        source = tmp_path / "project" / "frontend" / "src" / "main.js"
        dist = tmp_path / "project" / "frontend" / "dist" / "src" / "main.js"
        for target in (source, dist):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('<canvas id="arena-canvas" width="280"></canvas>\n', encoding="utf-8")

        class Runner:
            project_root = tmp_path
            _work_dir = str(tmp_path)

        note = cli_main._try_apply_opencode_arena_board_fix(
            Runner(),
            'adicione data-testid="arena-board" ao canvas',
        )

        assert note is not None
        assert 'data-testid="arena-board"' in source.read_text(encoding="utf-8")
        assert 'data-testid="arena-board"' in dist.read_text(encoding="utf-8")

    def _blocked_runner(self, tmp_path):
        state_path = tmp_path / "state" / "engine_state.yml"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("node_status: blocked\n")
        state = EngineState(
            current_node="ft.plan.03.api_contract",
            node_status="blocked",
            blocked_reason="api_contract_complete FAIL",
            llm_engine="opencode",
            llm_model="pgx/zai-org_glm-4.7-flash",
        )
        node = SimpleNamespace(
            id="ft.plan.03.api_contract",
            type="document",
            outputs=["docs/api_contract.md"],
            requires_approval=False,
        )

        class Graph:
            nodes = {node.id: node}

            def get_node(self, node_id):
                return self.nodes[node_id]

            def resolve_next(self, node_id):
                return "ft.plan.04.ui_criteria"

        class StateMgr:
            path = state_path

            def __init__(self):
                self.artifacts = {}
                self.saved = False

            def load(self):
                return state

            def save(self):
                self.saved = True

            def record_artifact(self, name, path):
                self.artifacts[name] = path

        class Runner:
            def __init__(self):
                self.state_mgr = StateMgr()
                self.project_root = tmp_path
                self._run_dir = tmp_path
                self.graph = Graph()
                self._auto_approve = False
                self.run_mode = None
                self.advanced = []
                self.validation_seen = None

            def apply_fix(self, instruction):
                return False

            def _resolve_llm_engine(self, loaded_state=None, node=None):
                return loaded_state.llm_engine if loaded_state else "claude"

            def _resolve_llm_model(self, loaded_state=None, node=None):
                return loaded_state.llm_model if loaded_state else None

            def _resolve_llm_effort(self, loaded_state=None, node=None):
                return loaded_state.llm_effort if loaded_state else None

            def _capture_delegation_llm_selection(self, loaded_state, node=None):
                return SimpleNamespace(
                    engine=self._resolve_llm_engine(loaded_state, node),
                    model=self._resolve_llm_model(loaded_state, node),
                    effort=self._resolve_llm_effort(loaded_state, node),
                )

            def _print_validation(self, validation):
                self.validation_seen = validation

            def _maybe_auto_commit(self, node):
                return None

            def _record_node_summary(self, node, summary):
                self.summary = summary

            def _advance_state(self, completed_node, next_node, gate_result="PASS"):
                self.advanced.append((completed_node, next_node, gate_result))
                state.current_node = next_node
                state.node_status = "ready"
                state.blocked_reason = None

            def run(self, mode="step"):
                self.run_mode = mode

        return Runner(), state

    def test_fix_uses_state_engine_and_advances_when_validators_pass(self, tmp_path):
        runner, state = self._blocked_runner(tmp_path)
        args = Namespace(
            instruction="corrigir contrato",
            process=None,
            auto=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with (
            patch("ft.cli.main.get_runner", return_value=runner),
            patch("ft.engine.delegate.delegate_to_llm") as delegate,
            patch("ft.engine.runner.run_validators") as validate,
        ):
            delegate.return_value = DelegateResult(True, "DONE", [], ["docs/api_contract.md"])
            validate.return_value = ValidationResult(True, False, None, [])
            cli_main.cmd_fix(args)

        kwargs = delegate.call_args.kwargs
        assert kwargs["llm_engine"] == "opencode"
        assert kwargs["llm_model"] == "pgx/zai-org_glm-4.7-flash"
        assert kwargs["allowed_paths"] == ["docs/api_contract.md"]
        assert kwargs["opencode_capture_output_path"] == "docs/api_contract.md"
        assert runner.advanced == [("ft.plan.03.api_contract", "ft.plan.04.ui_criteria", "PASS")]
        assert runner.run_mode is None
        assert runner.state_mgr.artifacts == {"api_contract": "docs/api_contract.md"}
        assert state.current_node == "ft.plan.04.ui_criteria"

    def test_fix_reexecutes_node_only_when_validation_still_fails(self, tmp_path):
        runner, state = self._blocked_runner(tmp_path)
        args = Namespace(
            instruction="corrigir contrato",
            process=None,
            auto=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            verbose=False,
        )

        with (
            patch("ft.cli.main.get_runner", return_value=runner),
            patch("ft.engine.delegate.delegate_to_llm") as delegate,
            patch("ft.engine.runner.run_validators") as validate,
        ):
            delegate.return_value = DelegateResult(True, "DONE", [], ["docs/api_contract.md"])
            validate.return_value = ValidationResult(False, True, "ainda falha", [])
            cli_main.cmd_fix(args)

        assert runner.advanced == []
        assert runner.run_mode == "step"
        assert state.node_status == "running"
        assert state.blocked_reason is None
        assert state.last_approval_message == "corrigir contrato"
