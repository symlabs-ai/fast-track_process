"""Contract checks for the acquisition valuation template."""

from __future__ import annotations

import subprocess
import sys
import runpy
from pathlib import Path

import yaml

from ft.engine.graph import load_graph
from ft.engine.process_validator import validate_process
from ft.templates.input_policy import InputPolicy, InputPolicyError


TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "company-valuation"
VALIDATOR = TEMPLATE / "scripts" / "validate_valuation.py"


def write_yaml(root: Path, name: str, data: dict) -> None:
    path = root / "docs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def validate(root: Path, stage: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), stage],
        cwd=root, text=True, capture_output=True, check=False,
    )


def test_template_is_valid_and_has_final_human_review() -> None:
    graph = load_graph(TEMPLATE / "process.yml")
    report = validate_process(graph)
    assert report.passed, [error.message for error in report.errors]
    review = next(node for node in graph.nodes.values() if node.id == "company-valuation.review")
    assert review.type == "human_gate"
    assert "docs/valuation-report.md" in review.decision_context["review_paths"]


def test_strategic_value_bridge_recalculates_growth_and_net_value() -> None:
    check = runpy.run_path(str(VALIDATOR))["validate_strategic_value_case"]
    case = {
        "acquisition_ev_assumed": 12, "working_revenue_multiple": 3,
        "target_revenue_base": 10, "target_baseline_growth": 0.05,
        "channel_extra_growth_pp": 0.10, "next_year_growth_total": 0.15,
        "next_year_revenue": 11.5, "target_margin_assumed": 0.20,
        "next_year_ebitda": 2.3, "payback_on_next_year_ebitda_years": 12 / 2.3,
        "software_houses_available": 100, "channel_extra_revenue": 1,
        "implied_target_customer_arpa_year": 10_000,
        "equivalent_new_customers": 100, "implied_partner_conversion": 1,
        "target_value_at_base_revenue": 30,
        "value_created_net_of_purchase_at_base_revenue": 18,
        "target_value_at_next_year_revenue": 34.5,
        "value_created_net_of_purchase_at_next_year_revenue": 22.5,
    }
    errors: list[str] = []
    check(case, errors)
    assert not errors
    case["value_created_net_of_purchase_at_next_year_revenue"] = 26
    check(case, errors)
    assert any("value_created_net_of_purchase_at_next_year_revenue" in error for error in errors)


def test_pdf_input_is_copied_and_referenced(tmp_path: Path) -> None:
    policy = InputPolicy(
        required=True,
        destination="docs/valuation-request.md",
        prompt="Fonte da empresa",
        pdf_destination="docs/valuation-source.pdf",
    )
    source = tmp_path / "briefing.pdf"
    payload = b"%PDF-1.7\nexample binary payload\n"
    source.write_bytes(payload)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    prepared = policy.stage(worktree, input_file=source)
    assert prepared is not None
    assert prepared.destination.read_text() == "Fonte de entrada: docs/valuation-source.pdf\n"
    assert (worktree / "docs" / "valuation-source.pdf").read_bytes() == payload

    source.write_bytes(b"not a PDF")
    try:
        policy.acquire(input_file=source)
    except InputPolicyError as error:
        assert "PDF inválido" in str(error)
    else:
        raise AssertionError("PDF inválido deveria ser recusado")


def test_site_or_briefing_remains_text_input(tmp_path: Path) -> None:
    policy = InputPolicy(
        required=True, destination="docs/valuation-request.md",
        prompt="Fonte da empresa", pdf_destination="docs/valuation-source.pdf",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    policy.stage(worktree, request="https://empresa.example")
    assert (worktree / "docs" / "valuation-request.md").read_text() == "https://empresa.example"
    briefing = tmp_path / "briefing.md"
    briefing.write_text("Avaliar aquisição da empresa X.\n", encoding="utf-8")
    policy.stage(worktree, input_file=briefing)
    assert (worktree / "docs" / "valuation-request.md").read_text() == briefing.read_text()
    assert not (worktree / "docs" / "valuation-source.pdf").exists()


def test_model_recalculates_dcf_and_rejects_unsupported_value(tmp_path: Path) -> None:
    write_yaml(tmp_path, "valuation-scope.yml", {
        "schema_version": 1, "target_name": "Exemplo", "valuation_date": "2026-09-24",
        "currency": "BRL", "unit": "millions", "stake_percent": 100, "purpose": "acquisition",
        "basis_of_value": "market", "jurisdiction": "BR", "source_documents": [],
        "missing_documents": [],
    })
    source = tmp_path / "docs" / "financial-source.md"
    source.write_text("Exemplo de fonte", encoding="utf-8")
    write_yaml(tmp_path, "financial-inputs.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions", "status": "sufficient",
        "periods": [{"year": 2025, "revenue": 100, "ebitda": 20,
                     "free_cash_flow": 10, "source": "docs/financial-source.md",
                     "page_or_section": "p. 1"}],
        "net_debt": 5, "net_debt_source": "docs/financial-source.md",
    })
    enterprise_value = 10 / 1.1 + (10 * 1.02 / (0.1 - 0.02)) / 1.1
    model = {
        "schema_version": 1, "currency": "BRL", "unit": "millions", "status": "valued",
        "equity_range_low": enterprise_value - 5,
        "equity_range_high": enterprise_value - 5,
        "assumptions": ["cenários conservador, base e otimista a revisar"],
        "limitations": ["dados ilustrativos"],
        "methods": [{
            "name": "dcf", "enterprise_value": enterprise_value, "net_debt": 5,
            "equity_value": enterprise_value - 5, "discount_rate": 0.1,
            "terminal_growth_rate": 0.02, "forecast_fcf": [10],
            "year_count": 1, "rationale": "fluxo ilustrativo",
            "source_ids": ["docs/financial-source.md"],
        }],
    }
    write_yaml(tmp_path, "valuation-model.yml", model)
    assert validate(tmp_path, "model").returncode == 0

    model["methods"][0]["enterprise_value"] += 10
    write_yaml(tmp_path, "valuation-model.yml", model)
    result = validate(tmp_path, "model")
    assert result.returncode == 1
    assert "não concilia" in result.stdout


def test_insufficient_data_cannot_include_invented_range(tmp_path: Path) -> None:
    write_yaml(tmp_path, "valuation-scope.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
    })
    model = {
        "schema_version": 1, "currency": "BRL", "unit": "millions", "status": "insufficient_data",
        "methods": [], "equity_range_low": None, "equity_range_high": None,
        "assumptions": ["dados ausentes"], "limitations": ["sem balanço"],
    }
    write_yaml(tmp_path, "valuation-model.yml", model)
    assert validate(tmp_path, "model").returncode == 0
    model["equity_range_low"] = 100
    write_yaml(tmp_path, "valuation-model.yml", model)
    assert validate(tmp_path, "model").returncode == 1


def test_teaser_can_support_preliminary_ev_without_equity_price(tmp_path: Path) -> None:
    write_yaml(tmp_path, "valuation-scope.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
    })
    source = tmp_path / "docs" / "teaser.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n")
    write_yaml(tmp_path, "financial-inputs.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
        "status": "partial", "periods": [{"year": 2025, "revenue": 10.9,
            "ebitda": 0.436, "free_cash_flow": None,
            "source": "docs/teaser.pdf", "page_or_section": "p. 1"}],
        "net_debt": None,
    })
    write_yaml(tmp_path, "valuation-model.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
        "status": "indicative_ev", "enterprise_range_low": 20,
        "enterprise_range_high": 30, "equity_range_low": None,
        "equity_range_high": None, "assumptions": ["múltiplo ilustrativo"],
        "limitations": ["dívida líquida ausente"],
        "methods": [{"name": "market_multiples", "metric": "revenue",
            "metric_value": 10.9, "multiple": 2.5,
            "enterprise_value": 27.25, "net_debt": None,
            "equity_value": None, "rationale": "teste aritmético",
            "source_ids": ["docs/teaser.pdf"]}],
    })
    assert validate(tmp_path, "model").returncode == 0
    model_path = tmp_path / "docs" / "valuation-model.yml"
    data = yaml.safe_load(model_path.read_text())
    data["equity_range_low"] = 24
    write_yaml(tmp_path, "valuation-model.yml", data)
    assert "equity null" in validate(tmp_path, "model").stdout


def test_buyer_case_separates_economic_and_financeable_price(tmp_path: Path) -> None:
    write_yaml(tmp_path, "valuation-scope.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
        "stake_percent": 100,
    })
    write_yaml(tmp_path, "valuation-model.yml", {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
        "status": "valued", "equity_range_low": 100, "equity_range_high": 120,
    })
    (tmp_path / "docs" / "buyer-briefing.yml").write_text("Stakeholder data\n")
    write_yaml(tmp_path, "buyer-profile.yml", {
        "schema_version": 1, "buyer_name": "TecnoSpeed",
        "website": "https://tecnospeed.com.br/", "currency": "BRL",
        "unit": "millions", "fiscal_year": 2026,
        "forecast_revenue": 69, "ebitda_margin": 0.25, "yoy_growth": 0.15,
        "indicative_valuation_low": 180, "indicative_valuation_high": 210,
        "valuation_basis": "unknown", "financial_source_kind": "stakeholder",
        "financial_source_reference": "docs/buyer-briefing.yml",
        "financial_verification": "unverified", "available_cash": None,
        "debt_capacity": None, "acquisition_budget": None,
        "funding_source": None, "strategic_priorities": [], "missing_data": [],
    })
    write_yaml(tmp_path, "research/buyer-evidence.yml", {
        "schema_version": 1, "lens": "buyer", "claims": [{
            "id": "EV-B01", "statement": "Oferece APIs financeiras",
            "source": "https://tecnospeed.com.br/plugbank/",
            "date": "2026-09-24", "confidence": "high",
        }],
    })
    (tmp_path / "docs" / "research" / "buyer.md").write_text(
        "# Perfil público\n\n" + "APIs financeiras e canais para software houses. " * 4,
        encoding="utf-8",
    )
    assert validate(tmp_path, "buyer").returncode == 0
    profile_path = tmp_path / "docs" / "buyer-profile.yml"
    profile = yaml.safe_load(profile_path.read_text())
    profile["financial_verification"] = "verified"
    write_yaml(tmp_path, "buyer-profile.yml", profile)
    assert "unverified" in validate(tmp_path, "buyer").stdout
    profile["financial_verification"] = "unverified"
    write_yaml(tmp_path, "buyer-profile.yml", profile)
    case = {
        "schema_version": 1, "currency": "BRL", "unit": "millions",
        "status": "quantified", "strategic_fit": "medium",
        "fit_rationale": "Possível venda cruzada a investigar",
        "fit_evidence": ["EV-B01"], "gaps": ["Base de clientes do alvo"],
        "synergies": [{
            "name": "Venda cruzada", "kind": "revenue",
            "forecast_incremental_fcf": [11], "discount_rate": 0.1,
            "realization_probability": 0.5, "pv": 5,
            "evidence_ids": ["EV-B01"],
        }],
        "integration_cost_pv": 2, "dissynergy_pv": 1,
        "transaction_cost_pv": 1, "total_synergy_pv": 1,
        "target_stake_equity_low": 100, "target_stake_equity_high": 120,
        "buyer_economic_ceiling_low": 101,
        "buyer_economic_ceiling_high": 121,
        "funding_capacity": None, "actionable_price_ceiling": None,
        "proposed_price_low": 103, "proposed_price_high": 115,
        "price_status": "indicative", "price_basis": "Abaixo do teto econômico",
        "recommendation": "proceed_to_diligence",
        "screening": {
            "decision": "priority", "priority_score": 80,
            "scoring": [{"criterion": "encaixe", "weight": 100,
                         "grade_0_to_5": 4, "weighted_points": 80}],
            "screening_ev_low": None, "screening_ev_high": None,
        },
    }
    write_yaml(tmp_path, "buyer-case.yml", case)
    assert validate(tmp_path, "buyer_case").returncode == 0

    case["actionable_price_ceiling"] = 121
    write_yaml(tmp_path, "buyer-case.yml", case)
    result = validate(tmp_path, "buyer_case")
    assert result.returncode == 1
    assert "funding comprovado" in result.stdout

    profile = yaml.safe_load(profile_path.read_text())
    profile["acquisition_budget"] = 110
    profile["funding_source"] = "docs/buyer-briefing.yml"
    write_yaml(tmp_path, "buyer-profile.yml", profile)
    case["funding_capacity"] = 110
    case["actionable_price_ceiling"] = 110
    case["proposed_price_high"] = 108
    case["price_status"] = "actionable"
    write_yaml(tmp_path, "buyer-case.yml", case)
    assert validate(tmp_path, "buyer_case").returncode == 0
