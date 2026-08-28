"""Achados de processo derivados da telemetria do ciclo.

A derivação é mecânica: parte dos números do trace, não de julgamento. Nada
aqui aplica mudança — a disposição de cada achado é do operador.
"""

from __future__ import annotations

import pytest

from ft.engine.cycle_analysis import analyse_run_report
from ft.engine.cycle_improvements import (
    derive_candidates,
    next_improvement_ids,
)
from ft.engine.process_improvements import CLASSIFICATIONS, GLOBAL_CRITERIA


def _node(node_id: str, *, result: str = "PASS", ms: int = 1000) -> dict:
    return {
        "category": "node",
        "node_id": node_id,
        "duration_ms": ms,
        "result": result,
    }


def _llm(node_id: str, *, out: int = 1000) -> dict:
    return {
        "category": "llm",
        "node_id": node_id,
        "metrics": {"output_tokens": out, "input_tokens": out * 4},
    }


def _analysis(spans, metrics=None):
    return analyse_run_report(
        {
            "run_id": "r",
            "wall": {"duration_ms": 1},
            "llm": {"calls": 1},
            "spans": spans,
        },
        metrics,
    )


def test_healthy_cycle_produces_no_findings():
    """Um ciclo limpo não deve gerar ruído."""
    analysis = _analysis([_node("a"), _node("b"), _node("c"), _node("d")])
    assert derive_candidates(analysis) == []


def test_empty_cycle_is_silent():
    assert derive_candidates(_analysis([])) == []


def test_loop_becomes_a_finding_with_its_cost():
    analysis = _analysis(
        [
            _node("ft.review", result="FAIL"),
            _node("ft.fix"),
            _node("ft.review"),
            _llm("ft.review", out=5000),
            _node("ok1"),
            _node("ok2"),
            _node("ok3"),
            _node("ok4"),
            _node("ok5"),
            _node("ok6"),
        ]
    )
    loops = [c for c in derive_candidates(analysis) if c.kind == "loop"]
    assert len(loops) == 1
    assert "ft.review" in loops[0].title
    assert "2 execuções" in loops[0].evidence[0]["detail"]
    assert "5,000 tokens" in loops[0].evidence[0]["detail"]


def test_on_fail_rounds_appear_in_the_evidence():
    analysis = _analysis(
        [_node("rev", result="FAIL"), _node("rev"), _llm("rev")],
        {"on_fail_rounds": {"rev": 2}},
    )
    loop = next(c for c in derive_candidates(analysis) if c.kind == "loop")
    assert "2 rodada(s) de on_fail" in loop.evidence[0]["detail"]


def test_silent_validation_layer_is_flagged():
    analysis = _analysis([_node("ft.smoke.review"), _llm("ft.smoke.review", out=3000)])
    silent = [c for c in derive_candidates(analysis) if c.kind == "silent_layer"]
    assert len(silent) == 1
    assert "não reprovou nada" in silent[0].title
    # Um ciclo só não prova redundância — o texto precisa dizer isso.
    assert "outros ciclos" in silent[0].rationale


def test_low_first_pass_is_flagged_with_a_caveat():
    analysis = _analysis([_node("a"), _node("a"), _node("b"), _node("b")])
    finding = next(c for c in derive_candidates(analysis) if c.kind == "first_pass")
    # Não pode sugerir mexer no processo sem separar causa de produto.
    assert "defeito do produto" in finding.rationale


def test_record_matches_the_process_improvement_schema():
    analysis = _analysis([_node("rev", result="FAIL"), _node("rev"), _llm("rev")])
    candidate = derive_candidates(analysis)[0]
    record = candidate.as_record("PI-007")

    assert record["id"] == "PI-007"
    assert record["classification"] in CLASSIFICATIONS
    assert record["title"] and record["rationale"]
    assert record["evidence"] and all(
        e.get("source") and e.get("detail") for e in record["evidence"]
    )
    assert set(record["criteria"]) == set(GLOBAL_CRITERIA)
    assert all(value is False for value in record["criteria"].values()), (
        "derivação automática não pode afirmar critérios de promoção global"
    )
    assert record["change"]["applied_locally"] is False
    assert record["change"]["paths"] == []


def test_derivation_never_applies_anything():
    analysis = _analysis([_node("rev", result="FAIL"), _node("rev"), _llm("rev")])
    for candidate in derive_candidates(analysis):
        record = candidate.as_record("PI-001")
        assert record["change"]["applied_locally"] is False
        assert candidate.suggested_classification == "local"


@pytest.mark.parametrize(
    "existing,count,expected",
    [
        ([], 2, ["PI-001", "PI-002"]),
        (["PI-001"], 2, ["PI-002", "PI-003"]),
        (["PI-002", "PI-005"], 3, ["PI-001", "PI-003", "PI-004"]),
        (["lixo", None], 1, ["PI-001"]),
    ],
)
def test_ids_never_collide_with_existing(existing, count, expected):
    assert next_improvement_ids(existing, count) == expected


# --------------------------------- persistência sem terminal interativo


def test_findings_are_persisted_when_non_interactive(tmp_path, monkeypatch, capsys):
    """Sem TTY não há quem decida agora — mas o diagnóstico não pode evaporar."""
    from unittest.mock import patch

    import yaml

    from ft.cli import main as cli_main

    analysis = _analysis([_node("rev", result="FAIL"), _node("rev"), _llm("rev")])

    with patch("sys.stdin") as stdin:
        stdin.isatty.return_value = False
        cli_main._present_cycle_improvements(tmp_path, analysis)

    review = tmp_path / "docs" / "process-improvements.yml"
    assert review.is_file(), "achado perdido em close automatizado"
    payload = yaml.safe_load(review.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    # Esta telemetria produz dois achados: o loop e o first-pass derrubado por ele.
    assert [item["id"] for item in payload["improvements"]] == ["PI-001", "PI-002"]
    item = payload["improvements"][0]
    # Registrado, porém não aplicado e não promovido: a decisão segue sendo humana.
    assert item["classification"] == "local"
    assert item["change"]["applied_locally"] is False
    assert "pendente" in capsys.readouterr().out


def test_non_interactive_run_does_not_renumber_existing_findings(tmp_path):
    from unittest.mock import patch

    import yaml

    from ft.cli import main as cli_main

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "process-improvements.yml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "improvements": [{"id": "PI-001", "title": "antigo"}]}
        ),
        encoding="utf-8",
    )
    analysis = _analysis([_node("rev", result="FAIL"), _node("rev"), _llm("rev")])

    with patch("sys.stdin") as stdin:
        stdin.isatty.return_value = False
        cli_main._present_cycle_improvements(tmp_path, analysis)

    payload = yaml.safe_load((docs / "process-improvements.yml").read_text())
    ids = [item["id"] for item in payload["improvements"]]
    assert ids == ["PI-001", "PI-002", "PI-003"], (
        "IDs existentes não podem ser renumerados"
    )


def test_healthy_cycle_writes_nothing(tmp_path):
    from unittest.mock import patch

    from ft.cli import main as cli_main

    analysis = _analysis([_node("a"), _node("b"), _node("c"), _node("d")])
    with patch("sys.stdin") as stdin:
        stdin.isatty.return_value = False
        cli_main._present_cycle_improvements(tmp_path, analysis)
    assert not (tmp_path / "docs" / "process-improvements.yml").exists()
