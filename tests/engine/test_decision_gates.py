from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ft.engine.decision_gates import (
    DecisionGateContext,
    build_decision_gate_context,
)
from ft.engine.graph import Node, ProcessGraph, load_graph
from ft.engine.process_validator import validate_process
from ft.engine.runner import StepRunner
from ft.engine.ui import human_gate_card


def _legacy_graph() -> tuple[ProcessGraph, Node]:
    evidence = Node(
        id="review",
        type="build",
        title="Revisão concluída",
        outputs=["docs/review.md"],
        next="stakeholder",
    )
    stakeholder = Node(
        id="stakeholder",
        type="human_gate",
        title="Revisão da hipótese",
        description=(
            "Abra o produto pela entrada normal. "
            "Confirme o fluxo principal sem atalhos internos."
        ),
        next="done",
    )
    graph = ProcessGraph(
        [
            evidence,
            stakeholder,
            Node(id="done", type="end", title="Fim"),
        ],
        {},
    )
    return graph, stakeholder


def test_legacy_human_gate_derives_complete_context(tmp_path: Path) -> None:
    graph, stakeholder = _legacy_graph()
    report = tmp_path / "docs" / "review.md"
    report.parent.mkdir()
    report.write_text("APPROVED\n", encoding="utf-8")

    context = build_decision_gate_context(
        graph=graph,
        node=stakeholder,
        state=SimpleNamespace(completed_nodes=["review"]),
        project_root=tmp_path,
    )

    assert "Revisão da hipótese" in context.decision
    assert "Revisão concluída" in context.why_now
    assert context.review_paths == (str(report.resolve()),)
    assert context.checklist[:2] == (
        "Abra o produto pela entrada normal.",
        "Confirme o fluxo principal sem atalhos internos.",
    )
    assert "Fim (done)" in context.approve_effect
    assert "correção/revisão" in context.reject_effect
    assert context.limitations


def test_declared_context_overrides_fallback_and_keeps_existing_paths(
    tmp_path: Path,
) -> None:
    graph, stakeholder = _legacy_graph()
    artifact = tmp_path / "docs" / "decision.md"
    artifact.parent.mkdir()
    artifact.write_text("evidence\n", encoding="utf-8")
    stakeholder.decision_context = {
        "decision": "Liberar a entrega?",
        "why_now": "Todos os checks terminaram.",
        "review_paths": ["docs/decision.md", "docs/missing.md"],
        "checklist": ["Executar o caso A."],
        "limitations": ["Somente Linux."],
        "approve_effect": "Vai para o handoff.",
        "reject_effect": "Volta para o fix.",
    }

    context = build_decision_gate_context(
        graph=graph,
        node=stakeholder,
        state=SimpleNamespace(completed_nodes=[]),
        project_root=tmp_path,
    )

    assert context.decision == "Liberar a entrega?"
    assert context.why_now == "Todos os checks terminaram."
    assert context.review_paths == (str(artifact.resolve()),)
    assert context.checklist[0] == "Executar o caso A."
    assert context.limitations == ("Somente Linux.",)
    assert context.approve_effect == "Vai para o handoff."
    assert context.reject_effect == "Volta para o fix."


def test_product_acceptance_gate_gets_actionable_default_checklist(
    tmp_path: Path,
) -> None:
    graph, stakeholder = _legacy_graph()
    stakeholder.title = "Validação do Stakeholder"
    context = build_decision_gate_context(
        graph=graph,
        node=stakeholder,
        state=SimpleNamespace(completed_nodes=[]),
        project_root=tmp_path,
    )

    assert any("fluxo P0" in item for item in context.checklist)
    assert any("target obrigatório" in item for item in context.checklist)
    assert any("finding aberto" in item for item in context.checklist)


def test_human_gate_card_always_explains_the_decision() -> None:
    context = DecisionGateContext(
        decision="Aprovar o candidato?",
        why_now="A regressão terminou.",
        review_paths=("/tmp/report.md",),
        checklist=("Executar o fluxo principal.",),
        limitations=("Somente o escopo P0.",),
        approve_effect="Avança para handoff.",
        reject_effect="Retorna para correção.",
    )

    rendered = human_gate_card(
        "Aceite",
        url="http://127.0.0.1:8000",
        artifact="/tmp/SymDANFE.AppImage",
        context=context,
        approval_message_required=True,
    )

    for heading in (
        "DECISÃO",
        "POR QUE AGORA",
        "ONDE AVALIAR",
        "CHECKLIST DE DECISÃO",
        "LIMITES CONHECIDOS",
        "SE APROVAR",
        "SE REJEITAR",
    ):
        assert heading in rendered
    assert "http://127.0.0.1:8000" in rendered
    assert "Aplicativo desktop aberto: /tmp/SymDANFE.AppImage" in rendered
    assert "/tmp/report.md" in rendered
    assert 'ft approve "decisão/ressalvas"' in rendered
    assert 'ft reject "onde; passos; esperado; observado"' in rendered


def test_status_presents_existing_desktop_artifact_without_web_url(
    tmp_path: Path,
    capsys,
) -> None:
    process = tmp_path / "process.yml"
    process.write_text(
        """
nodes:
  - id: stakeholder
    type: human_gate
    title: Validar desktop
    next: done
  - id: done
    type: end
    title: Fim
""".lstrip(),
        encoding="utf-8",
    )
    artifact = tmp_path / "project" / "dist" / "Product.AppImage"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"appimage")
    (tmp_path / ".presented_artifact").write_text(
        "project/dist/Product.AppImage\n",
        encoding="utf-8",
    )
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state.yml",
        project_root=tmp_path,
    )
    runner.init_state()
    runner.state_mgr.set_pending_approval("stakeholder")
    capsys.readouterr()

    runner.status()
    rendered = capsys.readouterr().out

    assert f"Aplicativo apresentado: {artifact.resolve()}" in rendered
    assert "Aplicativo desktop aberto:" in rendered
    assert "project/dist/Product.AppImage" in rendered
    assert "Produto em execução: http" not in rendered


def test_graph_loader_preserves_decision_context(tmp_path: Path) -> None:
    process = tmp_path / "process.yml"
    process.write_text(
        """
nodes:
  - id: stakeholder
    type: human_gate
    title: Aceite
    decision_context:
      decision: Aprovar?
      checklist: [Executar o fluxo.]
    next: done
  - id: done
    type: end
    title: Fim
""".lstrip(),
        encoding="utf-8",
    )

    node = load_graph(process).get_node("stakeholder")
    assert node.decision_context == {
        "decision": "Aprovar?",
        "checklist": ["Executar o fluxo."],
    }


def test_process_validator_rejects_unsafe_decision_context() -> None:
    gate = Node(
        id="gate",
        type="human_gate",
        title="Gate",
        decision_context={
            "decision": "",
            "review_paths": ["../secret"],
            "unknown": "value",
        },
        next="done",
    )
    graph = ProcessGraph(
        [gate, Node(id="done", type="end", title="Fim")],
        {},
    )

    report = validate_process(graph)
    messages = "\n".join(issue.message for issue in report.errors)
    assert "campos desconhecidos" in messages
    assert "decision_context.decision" in messages
    assert "paths relativos e seguros" in messages


def test_status_shows_decision_packet_for_requires_approval(
    tmp_path: Path,
    capsys,
) -> None:
    process = tmp_path / "process.yml"
    process.write_text(
        """
id: approval_process
version: "1"
nodes:
  - id: report
    type: document
    title: Revisar relatório
    executor: python
    description: Confirme se o relatório representa a decisão.
    outputs: [docs/report.md]
    requires_approval: true
    next: done
  - id: done
    type: end
    title: Fim
""".lstrip(),
        encoding="utf-8",
    )
    report = tmp_path / "docs" / "report.md"
    report.parent.mkdir()
    report.write_text("ready\n", encoding="utf-8")
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state.yml",
        project_root=tmp_path,
    )
    runner.init_state()
    runner.state_mgr.set_pending_approval("report")
    capsys.readouterr()

    runner.status()
    output = capsys.readouterr().out

    assert "DECISION GATE · Revisar relatório" in output
    assert "POR QUE AGORA" in output
    assert "CHECKLIST DE DECISÃO" in output
    assert str(report.resolve()) in output
    assert "SE APROVAR" in output
    assert "SE REJEITAR" in output

    runner.graph.get_node("report").type = "human_gate"
    runner.status()
    pending_human_output = capsys.readouterr().out
    assert "Pacote de decisão e comandos disponíveis abaixo" in pending_human_output
    assert "para entrar no gate" not in pending_human_output
