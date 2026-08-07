"""Contract tests for the opt-in mvp-builder-fast process."""

from pathlib import Path

from ft.engine.graph import ProcessGraph, load_graph
from ft.engine.process_validator import validate_process
from ft.templates.catalog import TemplateCatalog


ROOT = Path(__file__).resolve().parents[2]
FAST_PROCESS = ROOT / "templates" / "mvp-builder-fast" / "process.yml"
BASE_PROCESS = ROOT / "templates" / "mvp-builder" / "process.yml"


def _new_product_path(graph: ProcessGraph) -> list[str]:
    """Follow the false branch used when no canonical project docs exist."""
    seen: list[str] = []
    node = graph.first_node()
    while node.id not in seen:
        seen.append(node.id)
        if node.type == "decision":
            branches = node.branches or {}
            target = branches.get("false", branches.get("_default"))
        else:
            target = node.next
        if not target:
            break
        node = graph.get_node(target)
    return seen


def _reachable(graph: ProcessGraph) -> set[str]:
    pending = [graph.first_node().id]
    found: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in found:
            continue
        found.add(node_id)
        node = graph.get_node(node_id)
        targets = [
            node.next,
            node.reject_next,
            (node.on_fail or {}).get("goto"),
            *(node.branches or {}).values(),
        ]
        pending.extend(target for target in targets if target)
    return found


def test_catalog_exposes_mvp_builder_fast() -> None:
    descriptor = TemplateCatalog(ROOT / "templates").get("mvp-builder-fast")

    assert descriptor.process_file == FAST_PROCESS
    assert descriptor.policy["template"] == "mvp-builder-fast"


def test_fast_graph_and_session_policy_are_valid() -> None:
    graph = load_graph(FAST_PROCESS)
    report = validate_process(graph)

    assert report.passed, [issue.message for issue in report.errors]
    assert graph.meta["id"] == "mvp_builder_fast"
    assert graph.meta["session_policy"] == {
        "mode": "sprint",
        "providers": ["claude", "codex"],
        "initial_plan": "internal",
        "parallel_strategy": "fork",
        "recovery": "rehydrate",
    }
    assert _reachable(graph) == set(graph.nodes)


def test_fast_path_reduces_llm_turns_and_preserves_human_gates() -> None:
    baseline = load_graph(BASE_PROCESS)
    fast = load_graph(FAST_PROCESS)
    baseline_path = _new_product_path(baseline)
    fast_path = _new_product_path(fast)
    baseline_turns = sum(
        baseline.get_node(node_id).executor.startswith("llm")
        for node_id in baseline_path
    )
    # Include the internal planning turn that precedes the graph.
    fast_turns = 1 + sum(
        fast.get_node(node_id).executor.startswith("llm") for node_id in fast_path
    )

    assert fast_turns <= 25
    # O helper agora percorre corretamente `_default` desde o route_mode; a
    # comparação anterior parava no primeiro decision e não media o caminho.
    assert fast_turns <= baseline_turns * 0.80
    # O gate de plano é determinístico; mockups têm aceite humano próprio.
    assert sum(node.type == "human_gate" for node in fast.nodes.values()) == 8
    assert sum(node.type == "human_gate" for node in baseline.nodes.values()) == 7


def test_visual_brief_and_mockups_are_mandatory_and_model_pinned() -> None:
    graph = load_graph(FAST_PROCESS)

    route = graph.get_node("ft.start.route")
    assert route.type == "gate"
    assert route.next == "ft.hyper.00.ui_criteria_questions"
    assert route.outputs == ["docs/hipotese.md", "docs/PRD.md"]
    assert "docs/hipotese.md" in str(route.validators)
    assert "docs/PRD.md" in str(route.validators)

    brief_gate = graph.get_node("ft.hyper.00.ui_criteria_questions")
    assert brief_gate.type == "human_gate"
    assert brief_gate.approval_message_required is True
    assert brief_gate.next == "ft.hyper.00b.ui_brief"
    brief = graph.get_node("ft.hyper.00b.ui_brief")
    assert "docs/ui-brief.md" in brief.outputs
    assert ".ft/project.yml" in brief.outputs
    assert brief.next == "ft.start.surface.route"

    surface = graph.get_node("ft.start.surface.route")
    assert surface.condition == "project_validation_mode"
    assert surface.branches == {
        "disabled": "ft.start.backlog.route",
        "_default": "ft.start.ui_criteria.route",
    }

    mockups = graph.get_node("ft.plan.06.mockups")
    assert graph.get_node("ft.plan.05.test_data").next == mockups.id
    assert mockups.executor == "llm_codex"
    assert mockups.expert == "prototype_png_designer"
    assert mockups.expert_definition is not None
    assert mockups.expert_definition.version == "1"
    assert "Material Design 3" in mockups.expert_definition.prompt
    assert "M3 Expressive" in mockups.expert_definition.prompt
    assert "SwiftUI" in mockups.expert_definition.prompt
    assert "Liquid Glass" in mockups.expert_definition.prompt
    assert "Web" in mockups.expert_definition.prompt
    assert "$imagegen" in mockups.expert_definition.prompt
    assert "gpt-image-2" not in mockups.expert_definition.prompt
    assert mockups.llm_engine == "codex"
    assert mockups.llm_model == "gpt-5.6-sol"
    assert mockups.llm_effort == "max"
    assert mockups.codex_auth == "chatgpt"
    assert any("Logged in using ChatGPT" in command for command in mockups.env_setup)
    assert "docs/mockups/screen-map.yml" in mockups.outputs
    assert "docs/mockups/image-generation-receipt.yml" in mockups.outputs
    assert "docs/mockups/images/" in mockups.outputs
    assert not any(
        output.endswith((".html", ".css", ".js", ".svg"))
        for output in mockups.outputs
    )
    assert any("image_generation" in command for command in mockups.env_setup)
    assert "built-in \u0060image_gen\u0060" in mockups.prompt
    assert "Invoque explicitamente \u0060$imagegen\u0060" in mockups.prompt
    assert "gpt-image-2" not in mockups.prompt
    assert "imagegen" in str(mockups.validators)
    assert "gpt-image-2" not in str(mockups.validators)
    assert "\u0060ui-mockup\u0060" in mockups.prompt
    assert "É PROIBIDO criar HTML" in mockups.prompt
    assert "screenshot de página/app" in mockups.prompt
    assert "SNN.1, SNN.2" in mockups.prompt
    assert "uma chamada image_gen própria e um PNG próprio" in mockups.prompt
    assert "Nunca reúna múltiplas views em um único bitmap" in mockups.prompt
    assert "IDs de views devem seguir SNN ou SNN.1..N" in str(mockups.validators)
    assert "imagem relativa e nome do PNG devem usar o ID exato da view" in str(
        mockups.validators
    )
    assert "Crie HTML/CSS/JS code-native" not in mockups.prompt
    assert "renderize PNGs reais com Chromium/Playwright" not in mockups.prompt
    assert mockups.next == "ft.plan.06a.mockup_prd_review"

    review = graph.get_node("ft.plan.06a.mockup_prd_review")
    assert review.type == "review"
    assert review.llm_engine == "codex"
    assert review.llm_model == "gpt-5.6-sol"
    assert review.llm_effort == "max"
    assert review.write_scope == [
        "docs/mockups/prd-coherence-review.md",
        "docs/mockups/prd-coherence-review.yml",
    ]
    assert "abra o PNG declarado com `view_image` em resolução original" in review.prompt
    assert "Nunca infira TNN a partir de SNN" in review.prompt
    assert "Um relatório REJECTED também segue ao human gate" in review.prompt
    assert "validate_mockup_prd_review.py" in str(review.validators)
    assert review.next == "ft.plan.06b.mockup_gate"

    mockup_gate = graph.get_node("ft.plan.06b.mockup_gate")
    assert mockup_gate.type == "human_gate"
    assert mockup_gate.approval_message_required is True
    assert "docs/mockups/prd-coherence-review.md" in mockup_gate.decision_context["review_paths"]
    assert "docs/mockups/prd-coherence-review.yml" in mockup_gate.decision_context["review_paths"]
    assert mockup_gate.reject_next == mockups.id
    assert mockup_gate.next == "ft.plan.gate"
    assert "docs/mockups/" in graph.meta["artifact_policy"]["canonical"]
    assert "docs/mockups/" in graph.get_node("ft.frontend.01.build").prompt


def test_headless_route_has_no_visual_or_http_delivery_dependency() -> None:
    graph = load_graph(FAST_PROCESS)

    canonical = graph.meta["artifact_policy"]["canonical"]
    cycle = graph.meta["artifact_policy"]["cycle"]
    assert "docs/acceptance-result.json" in canonical
    assert "docs/acceptance-result.json" not in cycle

    route = graph.get_node("ft.plan.surface.route")
    assert route.condition == "project_validation_mode"
    assert route.branches == {
        "disabled": "ft.headless.plan.01.contract",
        "_default": "ft.plan.03.api_contract",
    }

    contract = graph.get_node("ft.headless.plan.01.contract")
    assert "library_contract_complete" in str(contract.validators)
    assert contract.next == "ft.headless.plan.gate"
    assert "API PÚBLICA PROGRAMÁTICA" in contract.prompt
    assert "não como API HTTP" in contract.prompt

    build = graph.get_node("ft.headless.01.build")
    assert "src/" in build.outputs
    assert "tests/" in build.outputs
    assert "Makefile" in build.outputs
    assert ".github/workflows/" in build.outputs
    assert ".github/workflows" in build.write_scope
    assert "src/frontend/" not in build.outputs
    assert "make verify" in str(build.validators)
    assert "make build" in str(build.validators)
    assert "make smoke" in str(build.validators)
    assert "projeto novo permanece" in build.prompt
    assert "0.0.1" in build.prompt

    review = graph.get_node("ft.headless.02.review")
    assert review.type == "review"
    assert "review_outcome_valid" in str(review.validators)
    assert "claims[].evidence" in review.prompt
    assert "path relativo existente" in review.prompt
    assert "bump não" in review.prompt
    assert review.on_fail["goto"] == "ft.headless.03.fix"
    fix = graph.get_node("ft.headless.03.fix")
    assert fix.fix_review == review.id
    assert ".github/workflows/" in fix.outputs
    assert ".github/workflows" in fix.write_scope
    assert "sem deploy, publicação" in fix.prompt
    assert ".github/workflows/quality.yml" in fix.prompt
    assert "fallback local determinístico" in fix.prompt

    acceptance = graph.get_node("ft.headless.05.acceptance")
    assert acceptance.type == "review"
    assert "docs/acceptance-result.json" in acceptance.outputs
    assert "browser" in acceptance.prompt
    assert "claims[].evidence" in acceptance.prompt
    assert "path relativo existente" in acceptance.prompt
    assert "versão do pacote continua igual ao baseline" in acceptance.prompt
    assert acceptance.next == "ft.headless.06.stakeholder"
    assert graph.get_node("ft.headless.06.stakeholder").next == (
        "ft.handoff.01.retro"
    )


def test_parallel_flag_routes_to_one_internal_builder_batch() -> None:
    graph = load_graph(FAST_PROCESS)
    route = graph.first_node()

    assert route.id == "ft.start.route_mode"
    assert route.condition == "run_route"
    assert route.branches == {
        "validation": "ft.batch.01.plan",
        "_default": "ft.start.route",
    }
    assert graph.get_node("ft.batch.03.foundation").type == "build"
    assert graph.get_node("ft.batch.03.foundation").allow_pre_seed is True
    assert graph.get_node("ft.batch.04.execute").type == "batch"
    assert graph.get_node("ft.batch.04.execute").next == "ft.batch.05.review"
    assert graph.get_node("ft.batch.05.review").on_fail["automatic"] is True
    assert graph.get_node("ft.batch.01.plan").no_pre_seed is False
    assert graph.get_node("ft.batch.01.plan").next == "ft.batch.03.foundation"
    assert graph.get_node("ft.batch.06.fix").next == "ft.batch.06b.fix_review"
    assert graph.get_node("ft.batch.06.fix").fix_review == "ft.batch.06b.fix_review"
    assert graph.get_node("ft.batch.06b.fix_review").next == "ft.batch.07.verify"
    assert graph.get_node("ft.batch.06b.fix_review").on_fail["automatic"] is True
    assert graph.get_node("ft.batch.07.verify").next == "ft.batch.07a.platform_route"
    assert graph.get_node("ft.batch.07a.platform_route").condition == (
        "validation_profiles_active"
    )
    assert graph.get_node("ft.batch.07b.platform_validation").next == (
        "ft.batch.08.acceptance"
    )
    assert graph.get_node("ft.batch.08.acceptance").reject_next == "ft.batch.06.fix"
    assert graph.get_node("ft.batch.09.reconcile").next == "ft.end"
    assert graph.meta["batch_policy"]["min_lanes"] == 2
    assert graph.meta["batch_policy"]["default_max_parallel"] == 2


def test_macro_nodes_keep_deterministic_checkpoints() -> None:
    graph = load_graph(FAST_PROCESS)

    foundation = graph.get_node("ft.plan.00.foundation_full")
    assert set(foundation.outputs) >= {
        "docs/PROJECT_BACKLOG.md",
        "docs/FEATURES.md",
        "docs/task_list.md",
        "docs/TECH_STACK.md",
    }
    assert foundation.next == "ft.plan.02b.tech_gate"

    tech_stack = graph.get_node("ft.plan.02.tech_stack")
    assert tech_stack.no_pre_seed is True

    frontend = graph.get_node("ft.frontend.01.build")
    assert "src/frontend/" in frontend.outputs
    assert "project/" not in FAST_PROCESS.read_text(encoding="utf-8")
    assert frontend.next == "ft.frontend.03.prd_review"
    visual_review = graph.get_node("ft.frontend.03.prd_review")
    visual_gate = graph.get_node("gate.frontend")
    assert visual_review.type == "review"
    assert visual_review.no_pre_seed is True
    assert visual_review.next == "gate.frontend"
    assert visual_gate.next == "ft.tdd.01.red"
    assert "ui_criteria_coverage" not in str(visual_review.validators)
    assert "ui_criteria_coverage" not in str(visual_gate.validators)

    assert graph.get_node("ft.tdd.01.red").next == "ft.tdd.02.green"
    assert graph.get_node("ft.tdd.02.green").next == "ft.tdd.03.refactor"
    assert graph.get_node("ft.tdd.03.refactor").next == "gate.tdd"
    assert graph.get_node("gate.tdd").next == "ft.frontend.04.integrated_review"
    assert (
        graph.get_node("ft.frontend.04.integrated_review").next
        == "gate.frontend_integrated"
    )
    assert graph.get_node("ft.frontend.04.integrated_review").no_pre_seed is True
    assert graph.get_node("gate.frontend_integrated").next == "ft.delivery.01.entrypoint"

    assert graph.get_node("ft.handoff.02.backlog_update").executor == "python"
    assert graph.get_node("ft.handoff.02b.features_update").executor == "python"


def test_builder_scope_is_product_and_software_engineering_only() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.meta["product_engineering_policy"] == {
        "focus": "product_and_software_engineering",
        "includes": [
            "product_behavior_and_ux",
            "architecture_code_data_and_apis",
            "technical_security_quality_and_reliability",
            "packaging_installation_and_technical_maintenance",
        ],
        "excludes": [
            "legal_and_contractual_approval",
            "marketing_sales_and_promotional_sites",
            "market_research_recruiting_and_commercial_metrics",
            "revenue_renewal_and_business_validation",
        ],
        "external_items": {
            "disposition": "pending_downstream_team",
            "block_builder_delivery": False,
        },
    }

    scoped_planning_nodes = (
        "ft.batch.01.plan",
        "ft.plan.00.foundation_full",
        "ft.plan.00.foundation_features",
        "ft.plan.00.foundation_existing",
    )
    for node_id in scoped_planning_nodes:
        prompt = " ".join(graph.get_node(node_id).prompt.casefold().split())
        assert "engenharia de software" in prompt
        assert "equipe posterior" in prompt
        assert "não" in prompt and "bloqueio" in prompt

    entry = graph.get_node("ft.start.route")
    assert "template mdd" in entry.description


def test_handoff_is_human_readable_contextual_and_engineering_only() -> None:
    graph = load_graph(FAST_PROCESS)
    prd_next = graph.get_node("ft.handoff.02.prd_rewrite")
    critical = graph.get_node("ft.handoff.03.critical_analysis")
    handoff = graph.get_node("ft.handoff.04.plano_voo")
    gate = graph.get_node("ft.handoff.gate")

    assert "somente evolução do produto e da engenharia" in prd_next.prompt
    assert "Analise somente o software" in critical.prompt
    assert "responsabilidades pertencem a outra equipe" in critical.prompt
    handoff_prompt = " ".join(handoff.prompt.casefold().split())
    assert "documento principal de decisão" in handoff_prompt
    assert "consequência de não fazer ou adiar" in handoff_prompt
    assert "escopo obrigatório deste handoff" in handoff_prompt
    assert "preserve esses assuntos como pendentes para a equipe" in handoff_prompt

    validator_contract = str(handoff.validators)
    for section in (
        "Decisão solicitada",
        "Resumo em linguagem simples",
        "Escopo deste handoff de engenharia",
        "Funcionalidades incompletas e impacto",
        "Opções e consequências",
        "O que a aprovação autoriza",
        "O que a aprovação não autoriza",
        "Fora do escopo desta equipe",
        "Anexo técnico",
    ):
        assert section in validator_contract

    context = gate.decision_context
    assert {
        "decision",
        "why_now",
        "review_paths",
        "checklist",
        "limitations",
        "approve_effect",
        "reject_effect",
    } <= set(context)
    assert any("sem conhecer códigos internos" in item for item in context["checklist"])
    assert any("não fazer ou adiar" in item for item in context["checklist"])
    assert gate.reject_next == handoff.id


def test_navigation_reachability_is_gated_before_user_delivery() -> None:
    graph = load_graph(FAST_PROCESS)

    batch_plan = graph.get_node("ft.batch.01.plan")
    assert "docs/mvp-batch-navigation-contract.yml" in batch_plan.outputs
    assert "navigation_contract_valid" in str(batch_plan.validators)

    batch_review = graph.get_node("ft.batch.05.review")
    assert "docs/mvp-batch-navigation-reachability.yml" in batch_review.outputs
    assert "navigation_reachability" in str(batch_review.validators)
    assert "'require_approved': False" in str(batch_review.validators)

    fix_review = graph.get_node("ft.batch.06b.fix_review")
    assert "navigation_reachability" in str(fix_review.validators)
    assert "'require_approved': True" in str(fix_review.validators)
    assert "navigation_reachability" in str(
        graph.get_node("ft.batch.07.verify").validators
    )

    planning = graph.get_node("ft.plan.04.ui_criteria")
    assert "docs/navigation-contract.yml" in planning.outputs
    assert "navigation_contract_valid" in str(planning.validators)

    e2e = graph.get_node("ft.e2e.01.browser")
    assert "docs/navigation-reachability.yml" in e2e.outputs
    assert "navigation_reachability" in str(e2e.validators)
    assert "navigation_reachability" in str(graph.get_node("gate.e2e").validators)


def test_visual_delivery_contract_is_platform_aware() -> None:
    graph = load_graph(FAST_PROCESS)

    route = graph.get_node("ft.plan.surface.route")
    assert "plataforma nativa, web ou desktop" in route.description
    assert "frontend/browser" not in route.description

    surface = graph.get_node("ft.frontend.01.build")
    surface_contract = f"{surface.prompt}\n{surface.validators}"
    assert "src/Makefile" in surface.outputs
    assert "make -C src surface-build" in surface_contract
    assert "Android nativo" in surface.prompt
    assert "src/frontend/package.json" not in surface_contract
    assert "npm " not in surface_contract
    assert "<form" in surface.prompt and "não presuma" in surface.prompt

    review = graph.get_node("ft.frontend.04.integrated_review")
    gate = graph.get_node("gate.frontend_integrated")
    assert "source_dir': 'src/frontend'" in str(review.validators)
    assert "source_dir': 'src/frontend'" in str(gate.validators)
    assert "src/frontend/src" not in str(review.validators)
    assert "API e a persistência reais" in review.prompt

    delivery = graph.get_node("ft.delivery.01.entrypoint")
    delivery_contract = f"{delivery.prompt}\n{delivery.validators}"
    for target in ("surface-build", "smoke", "acceptance", "e2e", "run"):
        assert target in delivery_contract
    assert "make -s url" not in delivery_contract
    assert "somente" in delivery.prompt and "superfície web" in delivery.prompt

    smoke = graph.get_node("ft.smoke.01.run")
    assert "make -C src smoke" in f"{smoke.prompt}\n{smoke.validators}"
    assert "urlopen" not in str(smoke.validators)
    assert ".smoke_url" not in str(smoke.env_setup)

    acceptance = graph.get_node("ft.acceptance.01.cli")
    assert "make -C src acceptance" in str(acceptance.validators)
    assert "'create'" not in str(acceptance.validators)

    e2e = graph.get_node("ft.e2e.01.browser")
    e2e_contract = f"{e2e.prompt}\n{e2e.validators}"
    assert "instrumentação/UIAutomator" in e2e.prompt
    assert "XCUITest" in e2e.prompt
    assert "Playwright no web" in e2e.prompt
    assert "make -C src e2e" in e2e_contract
    assert "docs/e2e-result.json" in e2e.outputs
    assert "docs/e2e-result.json" in graph.meta["artifact_policy"]["cycle"]
    assert "test_navigation.py" not in e2e_contract
    assert "len(shots) >= 9" not in e2e_contract
    assert not e2e.env_setup

    visual = graph.get_node("ft.final.01.visual_check")
    visual_contract = f"{visual.prompt}\n{visual.validators}"
    assert "source_dir': 'src/frontend'" in str(visual.validators)
    assert "len(shots) >= 9" not in visual_contract
    assert "'create'" not in str(visual.validators)


def test_navigation_contract_is_domain_neutral_and_profiles_are_generic() -> None:
    text = FAST_PROCESS.read_text(encoding="utf-8").casefold()

    for forbidden in ("wconnect", "wifire", "xiaomi", "s01", "86 telas"):
        assert forbidden not in text
    for profile in ("android", "ios", "web", "desktop"):
        assert profile in text


def test_stakeholder_gate_presents_the_selected_platform_not_always_web() -> None:
    graph = load_graph(FAST_PROCESS)
    delivery = graph.get_node("ft.delivery.01.entrypoint")
    stakeholder = graph.get_node("ft.final.02.stakeholder")
    stakeholder_fix = graph.get_node("ft.final.03.stakeholder_fix")

    assert "AppImage" in delivery.prompt
    assert ".presented_artifact" in delivery.prompt
    assert "nunca use o servidor web como fallback" in delivery.prompt
    checklist = stakeholder.decision_context["checklist"]
    assert any("URL somente para web" in item for item in checklist)
    assert any("AppImage/aplicativo nativo" in item for item in checklist)
    assert all("pela URL apresentada" not in item for item in checklist)
    assert stakeholder_fix.next == "ft.platform.01.validate"
    assert stakeholder_fix.fix_review == "ft.platform.01.validate"


def test_composable_platform_validation_is_gated_in_both_routes() -> None:
    graph = load_graph(FAST_PROCESS)

    assert graph.meta["validation_policy"] == {
        "registry": "builtin_v1",
        "project_contract_path": ".ft/project.yml",
        "matrix_path": "docs/validation-matrix.yml",
        "report_path": "docs/platform-validation-report.yml",
        "evidence_root": "docs/evidence/platform-validation",
    }
    canonical = graph.meta["artifact_policy"]["canonical"]
    cycle = graph.meta["artifact_policy"]["cycle"]
    durable_validation_outputs = {
        "docs/test-identity.json",
        "docs/validation-matrix.yml",
        "docs/platform-validation-report.yml",
        "docs/evidence/platform-validation/",
    }
    assert durable_validation_outputs <= set(canonical)
    assert durable_validation_outputs.isdisjoint(cycle)

    batch = graph.get_node("ft.batch.07b.platform_validation")
    assert "ft validation-matrix ." in batch.env_setup
    assert "platform_validation_report" in str(batch.validators)
    assert "mockup_watermark" in batch.prompt
    assert "Overlay acrescentado pelo teste/captura não conta" in batch.prompt
    assert "artifact_install_reuse" in batch.prompt
    assert "adb shell am instrument" in batch.prompt
    batch_prompt = " ".join(batch.prompt.split())
    assert "Se apenas um pacote divergir, instale somente ele" in batch_prompt
    assert "uma única sessão multipacote" in batch_prompt
    assert "Um tap injetado não prova aceite" in batch.prompt
    assert "uma única autorização no checkpoint físico final" in batch.prompt
    assert "nunca use o aparelho físico interativo como loop de fix" in " ".join(
        batch.prompt.split()
    )
    assert batch.on_fail["goto"] == "ft.batch.06.fix"

    route = graph.get_node("ft.platform.00.route")
    assert route.condition == "validation_profiles_active"
    assert route.branches == {
        "true": "ft.platform.01.validate",
        "false": "ft.final.01.visual_check",
    }
    validation = graph.get_node("ft.platform.01.validate")
    assert "platform_validation_report" in str(validation.validators)
    assert "mockup_watermark" in validation.prompt
    assert "discovered_screen_count" in validation.prompt
    assert "artifact_install_reuse" in validation.prompt
    assert "adb shell am instrument" in validation.prompt
    validation_prompt = " ".join(validation.prompt.split())
    assert "Se apenas um pacote divergir, instale somente ele" in validation_prompt
    assert "uma única sessão multipacote" in validation_prompt
    assert "uma única autorização no checkpoint físico final" in validation.prompt
    fix = graph.get_node("ft.platform.02.fix")
    assert fix.fix_review == "ft.platform.01.validate"
    assert fix.next == "ft.platform.01.validate"
    assert "platform_validation_ready" in str(
        graph.get_node("gate.visual_check").validators
    )


def test_batch_review_uses_scope_bound_structured_receipts() -> None:
    graph = load_graph(FAST_PROCESS)

    review = graph.get_node("ft.batch.05.review")
    assert "docs/mvp-batch-review.yml" in review.outputs
    assert "docs/test-identity.json" in review.write_scope
    assert "review_outcome_valid" in str(review.validators)

    fix_review = graph.get_node("ft.batch.06b.fix_review")
    assert "docs/mvp-batch-fix-review.yml" in fix_review.outputs
    assert "review_outcome_valid" in str(fix_review.validators)
    assert "'require_approved': True" in str(fix_review.validators)

    verify = graph.get_node("ft.batch.07.verify")
    assert "review_chain_approved" in str(verify.validators)


def test_focal_review_persists_separate_physical_evidence_across_retries() -> None:
    graph = load_graph(FAST_PROCESS)
    evidence_dir = "docs/mvp-batch-fix-evidence/"

    review = graph.get_node("ft.batch.06b.fix_review")
    assert evidence_dir in graph.meta["artifact_policy"]["cycle"]
    assert evidence_dir in review.outputs
    assert evidence_dir in review.write_scope
    assert review.preserve_outputs_on_reentry is True
    assert "arquivo separado" in review.prompt
    assert "Auto-referenciar o Markdown" in review.prompt
    assert "Nunca grave certificado" in review.prompt
    assert "senha" in review.prompt


def test_batch_freezes_public_contract_and_acceptance_reports_non_p0_skip() -> None:
    graph = load_graph(FAST_PROCESS)

    protected = graph.meta["batch_policy"]["protected_paths"]
    assert "docs/api_contract.md" in protected
    plan = graph.get_node("ft.batch.01.plan")
    assert "antes do fan-out" in plan.prompt
    assert "api_contract_complete" in str(plan.validators)
    fix = graph.get_node("ft.batch.06.fix")
    assert "docs/api_contract.md" in fix.write_scope
    assert "atomicamente" in fix.prompt

    acceptance = graph.get_node("ft.acceptance.01.cli")
    gate = graph.get_node("gate.acceptance")
    acceptance_commands = "\n".join(
        str(spec.get("command_succeeds", "")) for spec in acceptance.validators
    )
    gate_commands = "\n".join(
        str(spec.get("command_succeeds", "")) for spec in gate.validators
    )
    assert "d.get('skip')>=0" in acceptance_commands
    assert "d.get('skip')>=0" in gate_commands
    assert "SKIP é permitido somente fora do P0" in gate.description
