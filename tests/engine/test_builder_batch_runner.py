"""Integração real-Git do batch interno do mvp-builder-fast."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from unittest.mock import patch

import yaml

from ft.engine.delegate import DelegateResult
from ft.engine.runner import StepRunner


PROCESS = """\
id: builder_batch_test
version: "1.0.0"
title: Builder batch test
session_policy:
  mode: sprint
  providers: [codex]
  initial_plan: internal
  parallel_strategy: fork
  recovery: rehydrate
batch_policy:
  plan_path: docs/mvp-batch-plan.yml
  request_path: docs/demanda.md
  report_path: docs/mvp-batch-report.md
  foundation_report_path: docs/mvp-batch-foundation.md
  evidence_root: docs/batches/test
  min_lanes: 2
  max_lanes: 4
  default_max_parallel: 2
  protected_paths: [.ft/, docs/demanda.md, docs/mvp-batch-plan.yml]
nodes:
  - id: batch.execute
    type: batch
    title: Executar batch
    executor: python
    sprint: build
    outputs: [docs/mvp-batch-report.md]
    validators:
      - file_exists: docs/mvp-batch-report.md
    next: end
  - id: end
    type: end
    title: Fim
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _setup(tmp_path: Path) -> tuple[StepRunner, Path]:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "batch@example.test")
    _git(root, "config", "user.name", "FT Batch Test")

    request = "Implementar duas capacidades independentes em paralelo.\n"
    docs = root / "docs"
    docs.mkdir()
    (docs / "demanda.md").write_text(request, encoding="utf-8")
    plan = {
        "schema_version": 1,
        "request_sha256": hashlib.sha256(request.encode()).hexdigest(),
        "requirements": [
            {"id": "R-001", "text": "Criar capacidade A."},
            {"id": "R-002", "text": "Criar capacidade B."},
        ],
        "foundation": {
            "goal": "Não há base compartilhada adicional.",
            "acceptance_criteria": ["Baseline já está verde."],
            "areas": [],
        },
        "lanes": [
            {
                "id": "L-01",
                "title": "Capacidade A",
                "goal": "Criar A.",
                "backlog_items": ["PB-001"],
                "requirements": ["R-001"],
                "acceptance_criteria": ["Arquivo A existe."],
                "areas": ["src/a"],
                "depends_on": [],
            },
            {
                "id": "L-02",
                "title": "Capacidade B",
                "goal": "Criar B.",
                "backlog_items": ["PB-002"],
                "requirements": ["R-002"],
                "acceptance_criteria": ["Arquivo B existe."],
                "areas": ["src/b"],
                "depends_on": [],
            },
        ],
    }
    (docs / "mvp-batch-plan.yml").write_text(
        yaml.safe_dump(plan, sort_keys=False),
        encoding="utf-8",
    )
    (root / "README.md").write_text("# test\n", encoding="utf-8")
    (root / ".gitignore").write_text("*_log.md\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "foundation")

    process_path = tmp_path / "process.yml"
    process_path.write_text(PROCESS, encoding="utf-8")
    runner = StepRunner(
        process_path=process_path,
        state_path=tmp_path / "runtime" / "state" / "engine_state.yml",
        project_root=root,
        llm_engine="codex",
        llm_model="gpt-5.6-sol",
        llm_effort="high",
    )
    runner.init_state()
    state = runner.state_mgr.load()
    state.parallel_enabled = True
    state.parallel_max_slots = 2
    runner.state_mgr.save()
    return runner, root


def _result() -> DelegateResult:
    return DelegateResult(
        success=True,
        output="DONE",
        files_created=[],
        files_modified=[],
    )


def test_batch_integrates_all_lanes_only_after_wave_succeeds(
    tmp_path: Path,
) -> None:
    runner, root = _setup(tmp_path)
    foundation_sha = _git(root, "rev-parse", "HEAD")

    def delegate(**kwargs):
        worktree = Path(kwargs["project_root"])
        if "LANE: L-01" in kwargs["task"]:
            target = worktree / "src" / "a" / "feature.txt"
        else:
            target = worktree / "src" / "b" / "feature.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok\n", encoding="utf-8")
        return _result()

    with patch.object(
        runner,
        "_delegate_with_stream_retry",
        side_effect=delegate,
    ):
        runner.run(mode="mvp")

    state = runner.state_mgr.load()
    assert state.node_status == "done"
    assert state.metrics["llm_calls"] == 2
    assert state.llm_execution_plan["source"] == "deterministic-batch"
    assert (root / "src/a/feature.txt").read_text() == "ok\n"
    assert (root / "src/b/feature.txt").read_text() == "ok\n"
    assert (root / "docs/mvp-batch-report.md").is_file()
    assert _git(root, "rev-parse", "HEAD") != foundation_sha
    runtime = yaml.safe_load(
        (runner.state_mgr.path.parent / "mvp-builder-batch.yml").read_text()
    )
    assert runtime["status"] == "done"
    assert {lane["status"] for lane in runtime["lanes"].values()} == {"merged"}
    assert _git(root, "status", "--porcelain") == ""


def test_ownership_violation_blocks_without_changing_parent(
    tmp_path: Path,
) -> None:
    runner, root = _setup(tmp_path)
    foundation_sha = _git(root, "rev-parse", "HEAD")

    def delegate(**kwargs):
        worktree = Path(kwargs["project_root"])
        if "LANE: L-01" in kwargs["task"]:
            target = worktree / "src" / "b" / "escape.txt"
        else:
            target = worktree / "src" / "b" / "feature.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok\n", encoding="utf-8")
        return _result()

    with patch.object(
        runner,
        "_delegate_with_stream_retry",
        side_effect=delegate,
    ):
        runner.run(mode="mvp")

    state = runner.state_mgr.load()
    assert state.node_status == "blocked"
    assert "ownership violado" in (state.blocked_reason or "")
    assert _git(root, "rev-parse", "HEAD") == foundation_sha
    assert not (root / "src").exists()
    runtime = yaml.safe_load(
        (runner.state_mgr.path.parent / "mvp-builder-batch.yml").read_text()
    )
    assert runtime["status"] == "blocked"
    assert runtime["lanes"]["L-01"]["status"] == "failed"
    assert Path(runtime["lanes"]["L-01"]["worktree"]).is_dir()


def test_retry_reuses_failed_lane_and_skips_completed_lane(
    tmp_path: Path,
) -> None:
    runner, root = _setup(tmp_path)

    def first_attempt(**kwargs):
        worktree = Path(kwargs["project_root"])
        if "LANE: L-01" in kwargs["task"]:
            target = worktree / "src" / "b" / "escape.txt"
        else:
            target = worktree / "src" / "b" / "feature.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("first\n", encoding="utf-8")
        return _result()

    with patch.object(
        runner,
        "_delegate_with_stream_retry",
        side_effect=first_attempt,
    ):
        runner.run(mode="mvp")
    assert runner.state_mgr.load().node_status == "blocked"

    resumed: list[str] = []

    def retry(**kwargs):
        assert "LANE: L-01" in kwargs["task"]
        resumed.append("L-01")
        worktree = Path(kwargs["project_root"])
        (worktree / "src/b/escape.txt").unlink()
        target = worktree / "src" / "a" / "feature.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixed\n", encoding="utf-8")
        return _result()

    runner.state_mgr.unblock()
    with patch.object(
        runner,
        "_delegate_with_stream_retry",
        side_effect=retry,
    ):
        runner.run(mode="mvp")

    assert resumed == ["L-01"]
    assert runner.state_mgr.load().node_status == "done"
    assert (root / "src/a/feature.txt").read_text() == "fixed\n"
    assert (root / "src/b/feature.txt").read_text() == "first\n"
    runtime = yaml.safe_load(
        (runner.state_mgr.path.parent / "mvp-builder-batch.yml").read_text()
    )
    assert runtime["lanes"]["L-01"]["attempts"] == 2
    assert runtime["lanes"]["L-02"]["attempts"] == 1
