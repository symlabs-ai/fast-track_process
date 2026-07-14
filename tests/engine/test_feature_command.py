from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import shutil
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from ft.cli import main as cli_main
from ft.engine import paths
from ft.engine.layout import process_digest
from ft.engine.runner import StepRunner
from ft.engine.state import StateManager


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )


def _initialized_project(root: Path, *, git: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cli_main.copy_template("base", root)
    (root / "docs").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    if git:
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "Test")
        _git(root, "config", "user.email", "test@example.com")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
    return root


def _feature_args(**overrides) -> Namespace:
    values = {
        "command": "feature",
        "process": None,
        "verbose": False,
        "demand": "Adicionar busca por telefone",
        "feature_input": None,
        "template": "feature",
        "force": False,
        "cycle_name": None,
        "bypass_human_gates": False,
        "claude": True,
        "codex": None,
        "gemini": None,
        "opencode": None,
    }
    values.update(overrides)
    return Namespace(**values)


def _close_args(**overrides) -> Namespace:
    values = {
        "process": None,
        "force": False,
        "merge": None,
        "merge_paths": None,
        "keep_worktree": False,
        "claude": None,
        "codex": None,
        "gemini": None,
        "opencode": None,
        "verbose": False,
    }
    values.update(overrides)
    return Namespace(**values)


def _write_backlog(root: Path, selected_status: str = "accepted") -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "PROJECT_BACKLOG.md").write_text(
        "| ID | Tipo | Prioridade | Status | Origem | Título | Critérios de Aceite | Evidência | Decisão/Notas |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| PB-001 | Feature | P0 | planned | PRD | Outro item | AC | — | — |\n"
        f"| PB-002 | Feature | P1 | {selected_status} | feature-request | Busca | AC-01 | tests | Aceito |\n",
        encoding="utf-8",
    )
    (docs / "feature.md").write_text(
        "---\nbacklog_item: PB-002\n---\n\n# Feature\n",
        encoding="utf-8",
    )


def test_incremental_catalog_exposes_bug_feature_and_tweak_templates():
    incremental = cli_main.available_templates("feature")
    assert incremental == sorted(incremental)
    assert {"bug", "feature", "tweak"} <= set(incremental)
    assert cli_main.resolve_feature_template(None) == "feature"
    assert cli_main.resolve_feature_template("tweak") == "tweak"
    assert cli_main.resolve_feature_template("bug") == "bug"


def test_materialize_feature_template_is_complete_and_copy_once(tmp_path):
    root = _initialized_project(tmp_path / "project", git=False)

    process = cli_main.materialize_process_template(
        "feature", root, entrypoint="feature"
    )
    script = process.parent / "scripts" / "serve.sh"
    original_mode = script.stat().st_mode
    local_fork = process.read_text(encoding="utf-8") + "\n# fork local\n"
    process.write_text(local_fork, encoding="utf-8")
    manifest_path = paths.project_manifest(root)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["processes"].pop("feature")
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

    second = cli_main.materialize_process_template(
        "feature", root, entrypoint="feature"
    )

    assert second == process
    assert process.read_text(encoding="utf-8") == local_fork
    assert (process.parent / "README.md").is_file()
    assert (process.parent / "environment.yml").is_file()
    assert (process.parent / "examples" / "feature.md").is_file()
    assert original_mode & stat.S_IEXEC
    assert script.stat().st_mode & stat.S_IEXEC
    manifest = yaml.safe_load(paths.project_manifest(root).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["default_process"] == "base"
    assert manifest["processes"]["base"]["path"] == ".ft/process/base/process.yml"
    assert manifest["processes"]["feature"]["path"] == (
        ".ft/process/feature/process.yml"
    )
    assert manifest["processes"]["feature"]["entrypoint"] == "feature"


def test_materialize_rejects_partial_or_wrong_entrypoint(tmp_path):
    root = _initialized_project(tmp_path / "project", git=False)
    partial = paths.project_named_process_dir(root, "feature")
    partial.mkdir(parents=True)

    with pytest.raises(ValueError, match="processo local parcial"):
        cli_main.materialize_process_template("feature", root, entrypoint="feature")
    with pytest.raises(ValueError, match="não pertence ao entrypoint init"):
        cli_main.materialize_process_template("feature", root, entrypoint="init")


def test_materialize_rejects_existing_fork_with_incompatible_policy(tmp_path):
    root = _initialized_project(tmp_path / "project", git=False)
    local = paths.project_named_process_file(root, "feature")
    local.parent.mkdir(parents=True)
    local.write_text(
        """id: wrong
version: '1.0'
execution_policy:
  entrypoint: init
  template: other
nodes:
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fork local incompatível"):
        cli_main.materialize_process_template("feature", root, entrypoint="feature")


def test_feature_requires_initialized_git_project_without_materializing(tmp_path, monkeypatch):
    root = tmp_path / "plain"
    root.mkdir()
    monkeypatch.chdir(root)

    with pytest.raises(ValueError, match="projeto já inicializado"):
        cli_main.cmd_feature(_feature_args())

    assert not paths.project_named_process_dir(root, "feature").exists()


def test_feature_requires_exactly_one_demand(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project")
    request = tmp_path / "request.md"
    request.write_text("Feature por arquivo\n", encoding="utf-8")
    monkeypatch.chdir(root)

    with pytest.raises(ValueError, match="informe uma demanda"):
        cli_main.cmd_feature(_feature_args(demand=None, feature_input=None))
    with pytest.raises(ValueError, match="não ambos"):
        cli_main.cmd_feature(
            _feature_args(demand="texto", feature_input=str(request))
        )


def test_feature_requires_git_head_before_materializing(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project", git=False)
    monkeypatch.chdir(root)

    with pytest.raises(RuntimeError, match="Git com commit inicial"):
        cli_main.cmd_feature(_feature_args())

    assert not paths.project_named_process_dir(root, "feature").exists()


def test_feature_builds_run_args_from_input_file(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project")
    request = tmp_path / "request.md"
    request.write_text("Feature por arquivo\n", encoding="utf-8")
    monkeypatch.chdir(root)

    with patch("ft.cli.main.cmd_run") as run:
        cli_main.cmd_feature(
            _feature_args(demand=None, feature_input=str(request), codex=True, claude=None)
        )

    run_args = run.call_args.args[0]
    assert run_args.process == ".ft/process/feature/process.yml"
    assert run_args._request_text == "Feature por arquivo\n"
    assert run_args._request_path == "docs/feature-request.md"
    assert run_args._require_git_worktree is True
    assert Path(run_args.project) == root


def test_feature_without_template_preserves_feature_default(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project")
    monkeypatch.chdir(root)

    with patch("ft.cli.main.cmd_run") as run:
        cli_main.cmd_feature(_feature_args(template=None))

    run_args = run.call_args.args[0]
    assert run_args.process == ".ft/process/feature/process.yml"
    manifest = yaml.safe_load(paths.project_manifest(root).read_text(encoding="utf-8"))
    assert manifest["processes"]["feature"]["entrypoint"] == "feature"


def test_feature_selects_any_incremental_template_without_new_orchestrator(
    tmp_path, monkeypatch
):
    root = _initialized_project(tmp_path / "project")
    fake_engine = tmp_path / "engine"
    template = fake_engine / "templates" / "tweak"
    template.mkdir(parents=True)
    (template / "process.yml").write_text(
        """id: tweak
version: '1.0.0'
execution_policy:
  entrypoint: feature
  template: tweak
close_policy:
  backlog:
    mode: none
  merge: full
nodes:
  - id: tweak.end
    type: end
    title: Fim
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_main, "engine_root", lambda: fake_engine)
    monkeypatch.chdir(root)

    with patch("ft.cli.main.cmd_run") as run:
        cli_main.cmd_feature(_feature_args(template="tweak"))

    run_args = run.call_args.args[0]
    assert run_args.process == ".ft/process/tweak/process.yml"
    assert run_args._request_text == "Adicionar busca por telefone"
    manifest = yaml.safe_load(paths.project_manifest(root).read_text(encoding="utf-8"))
    assert manifest["default_process"] == "base"
    record = manifest["processes"]["tweak"]
    assert record["path"] == ".ft/process/tweak/process.yml"
    assert record["template"] == "tweak"
    assert record["entrypoint"] == "feature"
    assert record["source_digest"] == process_digest(template / "process.yml")
    assert record["base_digest"].startswith("sha256:")


def test_feature_runs_selected_local_process_inside_external_worktree(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project")
    monkeypatch.chdir(root)

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
            self.process_path = Path(process_path)
            self.state_path = Path(state_path)
            self.project_root = Path(project_root)
            self.llm_engine = llm_engine
            self.llm_model = llm_model
            self.llm_effort = llm_effort
            self.llm_defaults_root = llm_defaults_root
            self.llm_engine_is_override = llm_engine_is_override
            self.llm_model_is_override = llm_model_is_override
            self.llm_effort_is_override = llm_effort_is_override
            self._environment = {}
            self._bypass_human_gates = False
            self.inited = False
            self.run_mode = None
            self.instances.append(self)

        def init_state(self):
            self.inited = True

        def run(self, mode="step"):
            self.run_mode = mode

    with patch("ft.cli.main.StepRunner", FakeRunner):
        cli_main.cmd_feature(_feature_args())

    runner = FakeRunner.instances[-1]
    assert paths.is_worktree_path(runner.project_root)
    assert runner.process_path == (
        runner.project_root / ".ft" / "process" / "feature" / "process.yml"
    )
    assert runner.process_path.is_file()
    assert (runner.project_root / "docs" / "feature-request.md").read_text() == (
        "Adicionar busca por telefone\n"
    )
    assert not (root / "docs" / "feature-request.md").exists()
    assert runner.inited is True
    assert runner.run_mode == "mvp"


def test_run_template_materializes_named_process_when_default_exists(tmp_path):
    root = _initialized_project(tmp_path / "project", git=False)

    class FakeRunner:
        instances: list["FakeRunner"] = []

        def __init__(self, process_path, state_path, project_root=".", **_kwargs):
            self.process_path = Path(process_path)
            self.project_root = Path(project_root)
            self._environment = {}
            self._bypass_human_gates = False
            self.instances.append(self)

        def init_state(self):
            pass

        def run(self, mode="step"):
            self.mode = mode

    args = Namespace(
        project=str(root),
        process=None,
        from_project=None,
        hipotese=None,
        demand_input=None,
        bypass_human_gates=False,
        force=True,
        cycle_name=None,
        template="mvp-builder",
        worktree=None,
        auto=True,
        claude=True,
        codex=None,
        gemini=None,
        opencode=None,
        verbose=False,
    )
    with patch("ft.cli.main.StepRunner", FakeRunner):
        cli_main.cmd_run(args)

    local = root / ".ft/process/mvp-builder/process.yml"
    assert local.is_file()
    assert ".ft/process/mvp-builder/scripts/" in local.read_text(encoding="utf-8")
    runner = FakeRunner.instances[-1]
    assert runner.process_path == (
        runner.project_root / ".ft/process/mvp-builder/process.yml"
    )


def test_get_runner_resumes_process_pinned_in_cycle_state(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project", git=False)
    local_process = cli_main.materialize_process_template(
        "feature", root, entrypoint="feature"
    )
    work = paths.worktrees_home(root) / "cycle-01"
    shutil.copytree(root / ".ft", work / ".ft")
    state_path = work / "state" / "engine_state.yml"
    StateManager(state_path).init_from_graph(
        {"id": "feature", "version": "1.0.0"},
        "feature.preflight",
        12,
        process_path=".ft/process/feature/process.yml",
        process_digest=process_digest(work / ".ft/process/feature/process.yml"),
        process_immutable=True,
        template_id="feature",
    )
    monkeypatch.chdir(root)

    runner = cli_main.get_runner()

    assert runner.graph.meta["id"] == "feature"
    assert Path(runner.process_path) == work / ".ft/process/feature/process.yml"
    assert Path(runner.process_path) != local_process


def test_step_runner_pins_local_process_metadata_on_init(tmp_path):
    root = _initialized_project(tmp_path / "project", git=False)
    process = cli_main.materialize_process_template(
        "feature", root, entrypoint="feature"
    )
    state_path = paths.worktrees_home(root) / "cycle-01/state/engine_state.yml"
    work = state_path.parent.parent
    shutil.copytree(root / ".ft", work / ".ft")
    selected = work / process.relative_to(root)
    runner = StepRunner(selected, state_path, project_root=work)

    runner.init_state()
    state = runner.state_mgr.load()

    assert state.process_path == ".ft/process/feature/process.yml"
    assert state.process_digest == process_digest(selected)
    assert state.process_immutable is True
    assert state.template_id == "feature"
    assert state.current_cycle == "cycle-01"


def test_get_runner_never_falls_back_when_pinned_process_is_missing(tmp_path, monkeypatch):
    root = _initialized_project(tmp_path / "project", git=False)
    work = paths.worktrees_home(root) / "cycle-01"
    shutil.copytree(root / ".ft", work / ".ft")
    state_path = work / "state" / "engine_state.yml"
    StateManager(state_path).init_from_graph(
        {"id": "feature"},
        "feature.preflight",
        1,
        process_path=".ft/process/feature/process.yml",
    )
    monkeypatch.chdir(root)

    with pytest.raises(FileNotFoundError, match="fixado no ciclo não existe"):
        cli_main.get_runner()


def test_get_runner_rejects_digest_drift_even_if_local_only_flag_is_removed(
    tmp_path, monkeypatch
):
    root = _initialized_project(tmp_path / "project", git=False)
    cli_main.materialize_process_template("feature", root, entrypoint="feature")
    work = paths.worktrees_home(root) / "cycle-01"
    shutil.copytree(root / ".ft", work / ".ft")
    selected = work / ".ft/process/feature/process.yml"
    expected_digest = process_digest(selected)
    state_path = work / "state" / "engine_state.yml"
    StateManager(state_path).init_from_graph(
        {"id": "feature"},
        "feature.preflight",
        1,
        process_path=".ft/process/feature/process.yml",
        process_digest=expected_digest,
        process_immutable=True,
    )
    payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
    payload["execution_policy"].pop("runtime_source")
    selected.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(root)

    with pytest.raises(ValueError, match="divergiu do digest"):
        cli_main.get_runner()


class _DoneStateManager:
    def load(self):
        return SimpleNamespace(node_status="done", current_node=None)


class _CloseRunner:
    def __init__(self, root: Path):
        self.project_root = root
        self.state_mgr = _DoneStateManager()
        self.graph = SimpleNamespace(
            meta={
                "close_policy": {
                    "backlog": {
                        "mode": "referenced",
                        "references_path": "docs/feature.md",
                        "reference_field": "backlog_item",
                        "required_count": 1,
                    },
                    "merge": "full",
                }
            }
        )
        self.merge_calls: list[tuple[str, object]] = []

    def merge_on_close(self, strategy, merge_paths):
        self.merge_calls.append((strategy, merge_paths))
        return True

    def _detect_worktree(self):
        return None


def test_feature_close_ignores_unrelated_open_backlog_and_defaults_full_merge(tmp_path):
    work = tmp_path / "cycle"
    _write_backlog(work, selected_status="accepted")
    runner = _CloseRunner(work)

    with patch("ft.cli.main.get_runner", return_value=runner):
        cli_main.cmd_close(_close_args())

    assert runner.merge_calls == [("full", None)]


def test_feature_close_blocks_selected_unfinished_backlog(tmp_path):
    work = tmp_path / "cycle"
    _write_backlog(work, selected_status="in_progress")
    runner = _CloseRunner(work)

    with patch("ft.cli.main.get_runner", return_value=runner):
        cli_main.cmd_close(_close_args())

    assert runner.merge_calls == []


def test_feature_close_rejects_merge_that_violates_policy(tmp_path):
    work = tmp_path / "cycle"
    _write_backlog(work, selected_status="accepted")
    runner = _CloseRunner(work)

    with patch("ft.cli.main.get_runner", return_value=runner):
        cli_main.cmd_close(_close_args(merge="none"))

    assert runner.merge_calls == []


def test_close_backlog_mode_none_skips_only_backlog_governance(tmp_path):
    work = tmp_path / "cycle"
    _write_backlog(work, selected_status="in_progress")
    runner = _CloseRunner(work)
    runner.graph.meta["close_policy"]["backlog"] = {"mode": "none"}

    with patch("ft.cli.main.get_runner", return_value=runner):
        cli_main.cmd_close(_close_args())

    assert runner.merge_calls == [("full", None)]


@pytest.mark.parametrize("mode", ["typo", ["none"]])
def test_close_rejects_unknown_backlog_mode_even_without_backlog(
    tmp_path, capsys, mode
):
    work = tmp_path / "cycle"
    runner = _CloseRunner(work)
    runner.graph.meta["close_policy"]["backlog"] = {"mode": mode}

    with patch("ft.cli.main.get_runner", return_value=runner):
        cli_main.cmd_close(_close_args())

    assert runner.merge_calls == []
    assert "mode desconhecido" in capsys.readouterr().out


def _write_rejection_graph(root: Path) -> Path:
    process = root / ".ft" / "process" / "feature" / "process.yml"
    process.parent.mkdir(parents=True)
    process.write_text(
        """
id: rejection_feature
version: "1"
correction_policy:
  follow_graph_after_retry: true
  mandatory_after_implementation:
    - review
nodes:
  - id: scope
    type: human_gate
    title: Scope
    executor: python
    reject_next: implement
    next: implement
  - id: implement
    type: build
    title: Implement
    executor: claude
    outputs: [project/]
    next: review
  - id: review
    type: review
    title: Review
    executor: claude
    outputs: [docs/review.md]
    next: acceptance
  - id: acceptance
    type: human_gate
    title: Acceptance
    executor: python
    env_teardown: ["touch acceptance-stopped"]
    reject_next: implement
    next: end
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )
    return process


def test_reject_with_graph_policy_rewinds_and_requires_review_again(tmp_path):
    process = _write_rejection_graph(tmp_path)
    review = tmp_path / "docs" / "review.md"
    review.parent.mkdir(parents=True)
    review.write_text("Resultado: APPROVED\n", encoding="utf-8")
    state_path = tmp_path / "state" / "engine_state.yml"
    runner = StepRunner(process, state_path, project_root=tmp_path)
    runner.init_state()
    state = runner.state_mgr.state
    state.completed_nodes = ["scope", "implement", "review"]
    state.current_node = "acceptance"
    state.node_status = "awaiting_approval"
    state.pending_approval = "acceptance"
    state.metrics["steps_completed"] = 3
    runner.state_mgr.save()

    runner.reject("resultado incorreto")
    state = runner.state_mgr.load()

    assert state.current_node == "implement"
    assert state.node_status == "ready"
    assert state.completed_nodes == ["scope"]
    assert "review" not in state.gate_log
    assert "resultado incorreto" in (state.last_approval_message or "")
    # O arquivo pode continuar como contexto para a correção, mas a policy
    # mandatory_after_implementation obrigará sua regeneração no review.
    assert review.is_file()
    assert (tmp_path / "acceptance-stopped").is_file()


def test_close_refuses_worktree_branch_different_from_pinned_state(tmp_path):
    work = tmp_path / "cycle"
    original = tmp_path / "project"
    original.mkdir()
    process = _write_rejection_graph(work)
    runner = StepRunner(process, work / "state" / "engine_state.yml", project_root=work)
    runner.init_state()
    runner.state_mgr.state.worktree_branch = "ft/feature-cycle-01"
    runner.state_mgr.save()

    detected = (work, original, "unexpected-branch")
    with patch.object(runner, "_detect_worktree", return_value=detected):
        merged = runner.merge_on_close("full")

    assert merged is False
    assert not (work / ".ft" / "cycles" / "cycle-01" / "cycle.yml").exists()
