"""Análise empírica de custo de um ciclo (ft analyse-cycle)."""

from __future__ import annotations

from ft.engine.cycle_analysis import analyse_run_report


def _node_span(node_id: str, *, duration_ms: int = 1000, result: str = "PASS") -> dict:
    return {
        "category": "node",
        "node_id": node_id,
        "duration_ms": duration_ms,
        "result": result,
    }


def _llm_span(node_id: str, *, output: int = 100, input_: int = 500) -> dict:
    return {
        "category": "llm",
        "node_id": node_id,
        "metrics": {"output_tokens": output, "input_tokens": input_},
    }


def _report(spans: list[dict], **kwargs) -> dict:
    report = {
        "run_id": "run-1",
        "wall": {"duration_ms": 600_000},
        "llm": {"calls": 3, "output_tokens": 900, "input_tokens": 4200},
        "spans": spans,
    }
    report.update(kwargs)
    return report


def test_counts_executions_and_reexecutions():
    analysis = analyse_run_report(
        _report(
            [
                _node_span("a"),
                _node_span("review", result="FAIL"),
                _node_span("fix"),
                _node_span("review"),
            ]
        )
    )
    assert analysis.total_executions == 4
    assert analysis.total_reexecutions == 1
    assert analysis.nodes["review"].executions == 2
    assert analysis.nodes["review"].failures == 1


def test_first_pass_rate_and_rework_ratio():
    analysis = analyse_run_report(
        _report([_node_span("a"), _node_span("b"), _node_span("b"), _node_span("c")])
    )
    # a e c passaram de primeira; b repetiu
    assert analysis.first_pass_rate == 2 / 3
    assert analysis.rework_ratio == 1 / 4


def test_loops_are_ranked_by_cost():
    analysis = analyse_run_report(
        _report(
            [
                _node_span("cheap"),
                _node_span("cheap"),
                _llm_span("cheap", output=50),
                _node_span("expensive"),
                _node_span("expensive"),
                _llm_span("expensive", output=5000),
            ]
        )
    )
    assert [node.id for node in analysis.loops()] == ["expensive", "cheap"]


def test_silent_validation_layers_are_surfaced():
    analysis = analyse_run_report(
        _report(
            [
                _node_span("ft.smoke.01.review"),
                _llm_span("ft.smoke.01.review", output=3000),
                _node_span("ft.final.audit", result="FAIL"),
                _llm_span("ft.final.audit", output=1000),
                _node_span("ft.build.01"),
                _llm_span("ft.build.01", output=9000),
            ]
        )
    )
    silent = [node.id for node in analysis.silent_layers()]
    # audit reprovou algo (não é silencioso); build não é camada de validação
    assert silent == ["ft.smoke.01.review"]


def test_on_fail_rounds_come_from_state_metrics():
    analysis = analyse_run_report(
        _report([_node_span("review"), _node_span("review")]),
        {"on_fail_rounds": {"review": 3}},
    )
    assert analysis.on_fail_rounds == {"review": 3}


def test_totals_are_read_from_the_report_header():
    analysis = analyse_run_report(_report([_node_span("a")]))
    assert analysis.run_id == "run-1"
    assert analysis.wall_ms == 600_000
    assert analysis.llm_calls == 3
    assert analysis.output_tokens == 900


def test_empty_report_does_not_divide_by_zero():
    analysis = analyse_run_report({"spans": []})
    assert analysis.first_pass_rate == 0.0
    assert analysis.rework_ratio == 0.0
    assert analysis.loops() == []


def test_malformed_spans_are_ignored():
    analysis = analyse_run_report(
        _report(["not a span", {"category": "node"}, {"node_id": ""}, _node_span("a")])
    )
    assert list(analysis.nodes) == ["a"]
