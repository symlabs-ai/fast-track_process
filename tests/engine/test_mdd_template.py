"""Contract tests for the standalone MDD process."""

from pathlib import Path

from ft.engine.graph import load_graph
from ft.engine.process_validator import validate_process
from ft.project.lifecycle import project_role_for_template
from ft.templates.catalog import TemplateCatalog


ROOT = Path(__file__).resolve().parents[2]
MDD_PROCESS = ROOT / "templates" / "mdd" / "process.yml"


def test_catalog_exposes_standalone_mdd() -> None:
    descriptor = TemplateCatalog(ROOT / "templates").get("mdd")

    assert descriptor.process_file == MDD_PROCESS
    assert descriptor.policy["template"] == "mdd"
    assert descriptor.policy["project_role"] == "neutral"


def test_mdd_graph_is_complete_and_valid() -> None:
    graph = load_graph(MDD_PROCESS)
    report = validate_process(graph)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["version"] == "2.3.0"
    assert graph.first_node().id == "mdd.01.hipotese"
    assert list(graph.nodes) == [
        "mdd.01.hipotese",
        "mdd.01b.hipotese_gate",
        "mdd.02.vision",
        "mdd.02b.vision_gate",
        "mdd.03.prd",
        "mdd.03b.prd_gate",
        "mdd.04.definition_gate",
        "mdd.05.executive_summary",
        "mdd.06.pitch_deck",
        "mdd.07.site_proposal",
        "mdd.08.package_gate",
        "mdd.09.package_review",
        "mdd.09b.package_revision",
        "mdd.10.pitch_images",
        "mdd.11.site_prototype",
        "mdd.12.visual_gate",
        "mdd.13.visual_review",
        "mdd.13b.visual_revision",
        "mdd.14.handoff",
        "mdd.end",
    ]
    assert graph.get_node("mdd.01b.hipotese_gate").reject_next == (
        "mdd.01.hipotese"
    )
    assert graph.get_node("mdd.02b.vision_gate").reject_next == "mdd.02.vision"
    assert graph.get_node("mdd.03b.prd_gate").reject_next == "mdd.03.prd"
    assert graph.get_node("mdd.04.definition_gate").type == "gate"
    assert graph.get_node("mdd.08.package_gate").type == "gate"
    assert graph.get_node("mdd.09.package_review").reject_next == (
        "mdd.09b.package_revision"
    )
    assert graph.get_node("mdd.09b.package_revision").next == (
        "mdd.08.package_gate"
    )
    assert graph.get_node("mdd.12.visual_gate").type == "gate"
    assert graph.get_node("mdd.13.visual_review").reject_next == (
        "mdd.13b.visual_revision"
    )
    assert graph.get_node("mdd.13b.visual_revision").next == (
        "mdd.12.visual_gate"
    )
    assert graph.get_node("mdd.14.handoff").next == "mdd.end"


def test_mdd_prepares_builder_inputs_without_claiming_builder_ownership() -> None:
    graph = load_graph(MDD_PROCESS)
    policy = graph.meta["execution_policy"]

    assert project_role_for_template("mdd", policy) == (
        "neutral",
        ("building", "maintenance"),
    )
    assert graph.meta["input_policy"] == {
        "required": True,
        "destination": "docs/demanda.md",
        "prompt": "Descreva o problema, o público e o resultado esperado",
    }
    assert set(graph.meta["artifact_policy"]["canonical"]) == {
        "docs/demanda.md",
        "docs/hipotese.md",
        "docs/VISION.md",
        "docs/PRD.md",
        "docs/executive-summary.md",
        "docs/pitch-deck.md",
        "docs/site-proposal.md",
        "docs/pitch-deck-images/",
        "docs/site-prototype/",
        "docs/mdd-handoff.md",
    }
    assert graph.meta["close_policy"]["backlog"]["mode"] == "none"


def test_every_human_gate_has_an_explicit_decision_package() -> None:
    graph = load_graph(MDD_PROCESS)
    gates = [node for node in graph.nodes.values() if node.type == "human_gate"]

    assert len(gates) == 5
    for gate in gates:
        assert {
            "decision",
            "why_now",
            "review_paths",
            "checklist",
            "limitations",
            "approve_effect",
            "reject_effect",
        } <= set(gate.decision_context or {})


def test_mdd_derives_narrative_only_after_approved_definition() -> None:
    graph = load_graph(MDD_PROCESS)

    assert graph.get_node("mdd.01b.hipotese_gate").next == "mdd.02.vision"
    assert graph.get_node("mdd.02b.vision_gate").next == "mdd.03.prd"
    assert graph.get_node("mdd.03b.prd_gate").next == "mdd.04.definition_gate"
    assert graph.get_node("mdd.04.definition_gate").next == (
        "mdd.05.executive_summary"
    )
    assert graph.get_node("mdd.05.executive_summary").next == (
        "mdd.06.pitch_deck"
    )
    assert graph.get_node("mdd.06.pitch_deck").next == "mdd.07.site_proposal"
    assert graph.get_node("mdd.07.site_proposal").next == "mdd.08.package_gate"
    assert graph.get_node("mdd.08.package_gate").next == "mdd.09.package_review"
    assert graph.get_node("mdd.09.package_review").next == (
        "mdd.10.pitch_images"
    )
    assert graph.get_node("mdd.10.pitch_images").next == (
        "mdd.11.site_prototype"
    )
    assert graph.get_node("mdd.11.site_prototype").next == (
        "mdd.12.visual_gate"
    )
    assert graph.get_node("mdd.12.visual_gate").next == (
        "mdd.13.visual_review"
    )
    assert graph.get_node("mdd.13.visual_review").next == "mdd.14.handoff"


def test_approved_package_generates_visual_assets_with_sol_max() -> None:
    graph = load_graph(MDD_PROCESS)

    textual_pitch = graph.get_node("mdd.06.pitch_deck")
    assert "proposta de valor a stakeholders" in textual_pitch.prompt
    assert "não é relatório de progresso" in textual_pitch.prompt
    assert "Slide 04 — Proposta de Valor" in textual_pitch.prompt
    assert "Slide 09 — Impacto Esperado" in textual_pitch.prompt
    assert "Slide 12 — Convite ao Stakeholder" in textual_pitch.prompt
    assert "Slide 09 — Evidências e Estado Atual" not in textual_pitch.prompt

    site_proposal = graph.get_node("mdd.07.site_proposal")
    assert "uma única proposta comercial" in site_proposal.prompt
    assert "dirigida ao usuário final" in site_proposal.prompt
    assert "como uma oferta pronta" in site_proposal.prompt
    assert "A copy pública não pode mencionar projeto" in site_proposal.prompt
    assert "Arquitetura Comercial da Home" in site_proposal.prompt
    assert "Contrato do Protótipo Visual" in site_proposal.prompt
    assert "Alternativas de Direção" not in str(site_proposal.validators)

    pitch = graph.get_node("mdd.10.pitch_images")
    assert pitch.executor == "llm_codex"
    assert pitch.llm_engine == "codex"
    assert pitch.llm_model == "gpt-5.6-sol"
    assert pitch.llm_effort == "max"
    assert any("image_generation" in command for command in pitch.env_setup)
    assert "EXATAMENTE uma chamada de geração por slide" in pitch.prompt
    assert "12 chamadas independentes" in pitch.prompt
    assert "built-in `image_gen`" in pitch.prompt
    assert "productivity-visual" in pitch.prompt
    assert "slide-01.png" in pitch.prompt
    assert "slide-12.png" in pitch.prompt
    assert "nunca um status report" in pitch.prompt
    assert "nenhuma sigla técnica" in pitch.prompt
    assert "institucional pode aparecer sem explicação" in pitch.prompt
    assert "termo por extenso seguido da sigla" in pitch.prompt
    assert "cada slide exige bitmap próprio" in str(pitch.validators)

    site = graph.get_node("mdd.11.site_prototype")
    assert site.executor == "llm_codex"
    assert site.llm_engine == "codex"
    assert site.llm_model == "gpt-5.6-sol"
    assert site.llm_effort == "max"
    assert any("image_generation" in command for command in site.env_setup)
    assert "built-in `image_gen`" in site.prompt
    assert "ui-mockup" in site.prompt
    assert "uma única imagem PNG vertical e comprida" in site.prompt
    assert "docs/site-prototype/home-full-page.png" in site.prompt
    assert "home comercial" in site.prompt
    assert "apresentar o produto como oferta" in site.prompt
    assert "A copy visível não pode mencionar projeto" in site.prompt
    assert "landing page comercial contemporânea" in site.prompt
    assert "nenhuma sigla técnica" in site.prompt
    assert "institucional pode aparecer sem explicação" in site.prompt
    assert "termo por extenso seguido da sigla" in site.prompt
    assert "protótipo deve ser PNG vertical e comprido" in str(site.validators)

    review = graph.get_node("mdd.13.visual_review")
    assert review.type == "human_gate"
    assert review.approval_message_required is True
    assert review.reject_next == "mdd.13b.visual_revision"
    assert "docs/pitch-deck-images/" in review.outputs
    assert "docs/site-prototype/" in review.outputs
    assert any("sigla técnica ou institucional" in item for item in review.decision_context["checklist"])
    assert any("usuário final como produto" in item for item in review.decision_context["checklist"])


def test_handoff_inventories_every_approved_mdd_artifact() -> None:
    graph = load_graph(MDD_PROCESS)
    handoff = graph.get_node("mdd.14.handoff")
    contract = str(handoff.validators)

    for path in (
        "docs/hipotese.md",
        "docs/VISION.md",
        "docs/PRD.md",
        "docs/executive-summary.md",
        "docs/pitch-deck.md",
        "docs/site-proposal.md",
        "docs/pitch-deck-images/",
        "docs/site-prototype/",
    ):
        assert path in handoff.prompt
        assert path in contract
