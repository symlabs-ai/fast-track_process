"""Circuit breaker do loop on_fail (review→fix→review).

Um loop de correção autônomo sem teto é a maior fonte de custo em processos
com review perfeccionista: cada rodada gasta duas chamadas LLM grandes e o
engine não tinha limite. Estes testes fixam o contrato do teto e do
escalonamento para decisão humana.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ft.engine.runner import MAX_ON_FAIL_ROUNDS, StepRunner

_PROCESS = """
id: test_rounds
version: "0.1.0"
title: "Test"
nodes:
  - id: review
    type: review
    title: Review
    executor: claude
    outputs:
      - docs/review.md
    on_fail:
      human_gate: "Review reprovou."
      goto: fix
      automatic: true
{extra}
    next: end
  - id: fix
    type: build
    title: Fix
    executor: claude
    next: review
  - id: end
    type: end
    title: End
"""


def _runner(tmp_path: Path, extra: str = "") -> StepRunner:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "state").mkdir()
    process = tmp_path / "process.yml"
    process.write_text(_PROCESS.format(extra=extra), encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=project / "state" / "engine_state.yml",
        project_root=project,
    )
    runner.init_state()
    runner._auto_approve = True
    return runner


def test_default_limit_is_conservative():
    assert MAX_ON_FAIL_ROUNDS == 2


def test_rounds_below_limit_still_autofix(tmp_path):
    runner = _runner(tmp_path)
    node = runner.graph.get_node("review")

    with patch.object(runner, "apply_fix") as apply_fix:
        runner._handle_on_fail(node, "finding A")
        assert apply_fix.call_count == 1
        runner._handle_on_fail(node, "finding A ainda presente")
        assert apply_fix.call_count == 2

    state = runner.state_mgr.load()
    assert state.metrics["on_fail_rounds"]["review"] == 2
    assert state.node_status == "pending_fix"


def test_exceeding_limit_escalates_instead_of_looping(tmp_path, capsys):
    runner = _runner(tmp_path)
    node = runner.graph.get_node("review")

    with patch.object(runner, "apply_fix") as apply_fix:
        for _ in range(MAX_ON_FAIL_ROUNDS):
            runner._handle_on_fail(node, "finding persistente")
        assert apply_fix.call_count == MAX_ON_FAIL_ROUNDS
        capsys.readouterr()
        # A rodada seguinte excede o teto: nenhuma correção automática.
        runner._handle_on_fail(node, "finding persistente")
        assert apply_fix.call_count == MAX_ON_FAIL_ROUNDS

    output = capsys.readouterr().out
    assert "escalado ao stakeholder" in output
    # O ciclo continua acionável pelo stakeholder, não bloqueado.
    state = runner.state_mgr.load()
    assert state.node_status == "pending_fix"
    assert state.pending_fix["goto"] == "fix"


def test_node_can_override_the_limit(tmp_path):
    runner = _runner(tmp_path, extra="      max_rounds: 4")
    node = runner.graph.get_node("review")
    assert runner._on_fail_round_limit(node) == 4

    with patch.object(runner, "apply_fix") as apply_fix:
        for _ in range(5):
            runner._handle_on_fail(node, "finding")
    assert apply_fix.call_count == 4


def test_zero_disables_the_breaker(tmp_path):
    runner = _runner(tmp_path, extra="      max_rounds: 0")
    node = runner.graph.get_node("review")
    assert runner._on_fail_round_limit(node) == 0

    with patch.object(runner, "apply_fix") as apply_fix:
        for _ in range(6):
            runner._handle_on_fail(node, "finding")
    assert apply_fix.call_count == 6


def test_counter_resets_when_node_finally_passes(tmp_path):
    runner = _runner(tmp_path)
    node = runner.graph.get_node("review")

    with patch.object(runner, "apply_fix"):
        runner._handle_on_fail(node, "finding")
    assert runner._on_fail_rounds("review") == 1

    runner._reset_on_fail_rounds("review")
    runner.state_mgr.save()
    assert runner._on_fail_rounds("review") == 0
    # Um novo defeito mais tarde recomeça com o orçamento cheio.
    with patch.object(runner, "apply_fix") as apply_fix:
        runner._handle_on_fail(node, "novo finding")
        assert apply_fix.call_count == 1


@pytest.mark.parametrize("bad", ["abc", None])
def test_invalid_limit_falls_back_to_default(tmp_path, bad):
    runner = _runner(tmp_path)
    node = runner.graph.get_node("review")
    node.on_fail = dict(node.on_fail or {})
    node.on_fail["max_rounds"] = bad
    assert runner._on_fail_round_limit(node) == MAX_ON_FAIL_ROUNDS


# ------------------------------ recibo malformado ≠ defeito de produto


_REVIEW_PROCESS = """
id: test_receipt
version: "0.1.0"
title: "Test"
nodes:
  - id: review
    type: review
    title: Review
    executor: claude
    outputs:
      - docs/review.md
    on_fail:
      human_gate: "Review reprovou."
      goto: fix
      automatic: true
    next: end
  - id: fix
    type: build
    title: Fix
    executor: claude
    next: review
  - id: end
    type: end
    title: End
"""


def _review_runner(tmp_path: Path) -> StepRunner:
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "state").mkdir()
    process = tmp_path / "process.yml"
    process.write_text(_REVIEW_PROCESS, encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=project / "state" / "engine_state.yml",
        project_root=project,
    )
    runner.init_state()
    runner._auto_approve = True
    return runner


@pytest.mark.parametrize(
    "feedback",
    [
        "review_outcome_valid FAIL: o relatório Markdown deve conter exatamente "
        "um veredito explícito APPROVED, APPROVED_WITH_FINDINGS ou REJECTED",
        "review_outcome_valid FAIL: scope_sha256 não corresponde aos bytes do escopo",
        "review_outcome_valid FAIL: cobertura do review divergente; ausentes=['C2']",
        "review_outcome_valid FAIL: verdict REJECTED exige ao menos um finding P0",
    ],
)
def test_malformed_receipt_reruns_the_review(tmp_path, feedback):
    runner = _review_runner(tmp_path)
    node = runner.graph.get_node("review")

    with patch.object(runner, "apply_fix") as apply_fix:
        runner._handle_on_fail(node, feedback)

    apply_fix.assert_not_called()
    state = runner.state_mgr.load()
    assert state.current_node == "review"
    assert state.node_status == "ready"
    # não consome orçamento do loop de correção do produto
    assert not (state.metrics.get("on_fail_rounds") or {}).get("review")


def test_real_rejection_still_routes_to_the_product_fix(tmp_path):
    runner = _review_runner(tmp_path)
    node = runner.graph.get_node("review")

    with patch.object(runner, "apply_fix") as apply_fix:
        runner._handle_on_fail(
            node, "review_outcome_valid FAIL: este gate exige verdict APPROVED"
        )

    apply_fix.assert_called_once()
    assert runner._on_fail_rounds("review") == 1


def test_non_review_nodes_are_unaffected(tmp_path):
    runner = _review_runner(tmp_path)
    node = runner.graph.get_node("review")
    node.type = "build"

    with patch.object(runner, "apply_fix") as apply_fix:
        runner._handle_on_fail(node, "review_outcome_valid FAIL: scope_sha256 ruim")

    apply_fix.assert_called_once()
