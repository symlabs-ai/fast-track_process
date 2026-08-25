"""Integração real-Git do batch interno do mvp-builder-fast."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
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


def test_status_describes_parallel_mode_and_each_planned_lane(
    tmp_path: Path,
    capsys,
) -> None:
    runner, _root = _setup(tmp_path)

    runner.status()
    compact = capsys.readouterr().out
    runner.status(full=True)
    full = capsys.readouterr().out

    for rendered in (compact, full):
        assert (
            "Batch parallel: habilitado · max_parallel: 2 · wave: —/— · "
            "fan-out: iniciando"
        ) in rendered
        assert "Lanes planejadas — fan-out ainda não iniciado" in rendered
        assert "○ L-01 — Capacidade A: aguardando execução · tentativa 0" in rendered
        assert "objetivo: Criar A." in rendered
        assert "ação atual: aguardando início da execução" in rendered
        assert "○ L-02 — Capacidade B: aguardando execução · tentativa 0" in rendered


def test_status_keeps_legacy_plan_without_lane_title_or_goal_readable(
    tmp_path: Path,
    capsys,
) -> None:
    runner, root = _setup(tmp_path)
    plan_path = root / "docs" / "mvp-batch-plan.yml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["lanes"][0].pop("title")
    plan["lanes"][0].pop("goal")
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    state = runner.state_mgr.load()
    state.parallel_enabled = False
    state.parallel_max_slots = 3
    runner.state_mgr.save()

    runner.status()

    rendered = capsys.readouterr().out
    assert "Batch parallel: desabilitado · max_parallel: 3 (inativo)" in rendered
    assert "○ L-01: aguardando execução · tentativa 0" in rendered
    assert "objetivo: não informado no plano legado" in rendered


def test_status_shows_runtime_wave_and_current_lane_action(
    tmp_path: Path,
    capsys,
) -> None:
    runner, _root = _setup(tmp_path)
    log_path = runner.state_mgr.path.parent / "llm_logs" / "lane-L-01.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "lane-command",
                    "type": "command_execution",
                    "command": "pytest tests/test_lane.py -q",
                    "status": "in_progress",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_path = runner.state_mgr.path.parent / "mvp-builder-batch.yml"
    runtime_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "status": "running",
                "max_parallel": 2,
                "waves": [["L-01", "L-02"]],
                "current_wave": 0,
                "lanes": {
                    "L-01": {
                        "status": "running",
                        "attempts": 1,
                        "log_path": str(log_path),
                    },
                    "L-02": {"status": "planned", "attempts": 0},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    runner.status()

    rendered = capsys.readouterr().out
    assert "wave: 1/1 · fan-out: em andamento" in rendered
    assert "Wave 1/1 — ATUAL · 2 lanes em paralelo" in rendered
    assert "▶ L-01 — Capacidade A: executando agora · tentativa 1" in rendered
    assert "ação atual: executando testes focais em `test_lane.py`" in rendered


def test_status_makes_parallel_wave_and_completed_calls_visually_explicit(
    tmp_path: Path,
    capsys,
) -> None:
    runner, root = _setup(tmp_path)
    plan_path = root / "docs" / "mvp-batch-plan.yml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["lanes"].extend(
        [
            {"id": "L-03", "title": "Capacidade C", "goal": "Criar C."},
            {"id": "L-04", "title": "Integração", "goal": "Integrar A, B e C."},
        ]
    )
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")

    log_dir = runner.state_mgr.path.parent / "llm_logs"
    log_dir.mkdir(parents=True)

    def completed_log(lane_id: str) -> Path:
        path = log_dir / f"{lane_id}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"summary-{lane_id}",
                        "type": "agent_message",
                        "text": "NODE_SUMMARY:\n- verificado: testes verdes\nDONE",
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        old = time.time() - 401
        os.utime(path, (old, old))
        return path

    active_log = log_dir / "L-03.jsonl"
    active_log.write_text(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "active-command",
                    "type": "command_execution",
                    "command": "pytest tests/test_parallel_lane.py -q",
                    "status": "in_progress",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_path = runner.state_mgr.path.parent / "mvp-builder-batch.yml"
    runtime_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "status": "running",
                "max_parallel": 4,
                "waves": [["L-01", "L-02", "L-03"], ["L-04"]],
                "current_wave": 0,
                "lanes": {
                    "L-01": {
                        "status": "running",
                        "attempts": 1,
                        "log_path": str(completed_log("L-01")),
                    },
                    "L-02": {
                        "status": "running",
                        "attempts": 1,
                        "log_path": str(completed_log("L-02")),
                    },
                    "L-03": {
                        "status": "running",
                        "attempts": 1,
                        "log_path": str(active_log),
                    },
                    "L-04": {"status": "planned", "attempts": 0},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    runner.status()

    rendered = capsys.readouterr().out
    assert (
        "Wave 1/2 — ATUAL · 3 lanes em paralelo · 2 concluídas · "
        "1 executando · 3/4 slots alocados à wave"
    ) in rendered
    assert (
        "✓ L-01 — Capacidade A: chamada LLM concluída · aguardando integração"
    ) in rendered
    assert "▶ L-03 — Capacidade C: executando agora" in rendered
    assert "Próxima wave 2/2 · 1 lane · aguardando dependências" in rendered
    assert "○ L-04 — Integração: aguardando dependências" in rendered
    l01_line = next(line for line in rendered.splitlines() if "L-01 —" in line)
    l03_line = next(line for line in rendered.splitlines() if "L-03 —" in line)
    assert l01_line.count("✓") == 1
    assert l03_line.count("▶") == 1
    assert "✓   ✓" not in rendered
    assert "→   ▶" not in rendered
    l01_section = rendered.split("✓ L-01", 1)[1].split("✓ L-02", 1)[0]
    assert "conclusão LLM registrada às" in l01_section
    assert "log atualizado há 401s" not in l01_section
    assert "executando" not in l01_section


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


def test_batch_persists_llm_completion_before_the_wave_finishes(
    tmp_path: Path,
) -> None:
    runner, root = _setup(tmp_path)
    first_returned = threading.Event()
    release_second = threading.Event()
    errors: list[BaseException] = []

    def delegate(**kwargs):
        worktree = Path(kwargs["project_root"])
        if "LANE: L-01" in kwargs["task"]:
            target = worktree / "src" / "a" / "feature.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("first complete\n", encoding="utf-8")
            first_returned.set()
            return _result()
        target = worktree / "src" / "b" / "feature.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("second running\n", encoding="utf-8")
        if not release_second.wait(timeout=15):
            raise TimeoutError("test did not release the second lane")
        return _result()

    def run_batch() -> None:
        try:
            runner._run_builder_batch(runner.graph.get_node("batch.execute"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    with patch.object(
        runner,
        "_delegate_with_stream_retry",
        side_effect=delegate,
    ):
        worker = threading.Thread(target=run_batch, daemon=True)
        worker.start()
        try:
            assert first_returned.wait(timeout=10)
            runtime_path = runner.state_mgr.path.parent / "mvp-builder-batch.yml"
            deadline = time.monotonic() + 10
            observed_status = None
            while time.monotonic() < deadline:
                runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
                observed_status = runtime["lanes"]["L-01"]["status"]
                if observed_status == "llm_completed":
                    break
                time.sleep(0.01)

            assert observed_status == "llm_completed"
            assert runtime["lanes"]["L-01"]["llm_result"] == "success"
            assert runtime["lanes"]["L-02"]["status"] == "running"
            assert not (root / "src" / "a" / "feature.txt").exists()
        finally:
            release_second.set()
            worker.join(timeout=20)

    assert not worker.is_alive()
    assert errors == []
    assert (root / "src" / "a" / "feature.txt").read_text() == "first complete\n"
    assert (root / "src" / "b" / "feature.txt").read_text() == "second running\n"


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
