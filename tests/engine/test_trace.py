from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading

from ft.engine.trace import (
    TraceRecorder,
    build_run_report,
    read_trace_events,
    write_run_report,
)


def test_trace_ordinals_survive_recorder_restart_and_state_stays_external(tmp_path):
    state_path = tmp_path / "state" / "engine_state.yml"
    state_path.parent.mkdir()
    state_path.write_text("current_node: build\n", encoding="utf-8")

    first = TraceRecorder.for_state_path(state_path, "cycle-01")
    assert first.next_ordinal("node", "build") == 1
    span = first.begin_span(
        category="node",
        name="Build",
        node_id="build",
        ordinal=1,
        attempt_id="build:1",
    )
    span.finish(status="ok", result="PASS")

    second = TraceRecorder.for_state_path(state_path, "cycle-01")
    assert second.next_ordinal("node", "build") == 2
    assert "attempt" not in state_path.read_text(encoding="utf-8")


def test_trace_writer_is_thread_safe_and_keeps_parent_relations(tmp_path):
    recorder = TraceRecorder(tmp_path / "events.jsonl", "cycle-threaded")
    parent = recorder.begin_span(category="node", name="Build", node_id="build")

    def worker(index: int) -> None:
        child = recorder.begin_span(
            category="validator",
            name=f"validator-{index}",
            node_id="build",
            parent_span_id=parent.span_id,
            invocation_id=f"validator-{index}",
        )
        child.finish(status="ok")

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    parent.finish(status="ok")

    events = read_trace_events(recorder.path)
    assert len(events) == 26
    child_starts = [
        event
        for event in events
        if event.get("event") == "span_start"
        and event.get("category") == "validator"
    ]
    assert len(child_starts) == 12
    assert {event["parent_span_id"] for event in child_starts} == {parent.span_id}


def test_report_uses_real_wall_range_and_null_for_unavailable_provider_metrics(tmp_path):
    recorder = TraceRecorder(tmp_path / "events.jsonl", "cycle-report")
    node = recorder.begin_span(category="node", name="Build", node_id="build")
    llm = recorder.begin_span(
        category="llm",
        name="run",
        node_id="build",
        parent_span_id=node.span_id,
        attributes={
            "engine": "provider-without-usage",
            "model": None,
            "effort": None,
            "log_path": "missing.log",
        },
    )
    llm.finish(status="ok")
    node.finish(status="ok")

    generated = datetime.now(timezone.utc) + timedelta(seconds=1)
    report = build_run_report(
        recorder.path,
        generated_at=generated,
        log_root=tmp_path,
    )

    assert report["wall"]["duration_ms"] >= 0
    assert report["active_time_ms"]["llm"] >= 0
    assert report["llm"] == {
        "calls": 1,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }


def test_report_enriches_llm_usage_and_marks_crashed_span_open(tmp_path):
    log = tmp_path / "provider.jsonl"
    log.write_text(
        json.dumps(
            {
                "message": {
                    "id": "m1",
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 11,
                        "cache_read_input_tokens": 13,
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    recorder = TraceRecorder(tmp_path / "events.jsonl", "cycle-crash")
    recorder.begin_span(category="node", name="Build", node_id="build")
    llm = recorder.begin_span(
        category="llm",
        name="run",
        node_id="build",
        attributes={"log_path": log.name},
    )
    llm.finish(status="ok")

    report = build_run_report(recorder.path, log_root=tmp_path)

    assert report["wall"]["status"] == "active"
    assert report["llm"]["input_tokens"] == 7
    assert report["llm"]["output_tokens"] == 11
    assert report["llm"]["cache_read_tokens"] == 13
    assert report["llm"]["cache_write_tokens"] is None


def test_cross_process_style_finish_uses_utc_duration_and_report_is_atomic(tmp_path):
    recorder = TraceRecorder(tmp_path / "events.jsonl", "cycle-human")
    human = recorder.begin_span(
        category="human",
        name="scope approval",
        node_id="scope_gate",
    )
    restarted = TraceRecorder(recorder.path, "cycle-human")

    assert restarted.finish_open_span(
        human.span_id,
        status="approved",
        result="APPROVED",
    )
    assert not restarted.finish_open_span(human.span_id, status="approved")

    destination = tmp_path / "archive" / "run-report.json"
    report = write_run_report(
        recorder.path,
        destination,
        run_id="cycle-human",
    )
    loaded = json.loads(destination.read_text(encoding="utf-8"))
    assert loaded == report
    human_row = next(row for row in loaded["spans"] if row["category"] == "human")
    assert human_row["duration_source"] == "utc"
    assert human_row["status"] == "approved"
