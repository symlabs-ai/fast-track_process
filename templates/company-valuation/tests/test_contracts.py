"""Synthetic regression cases for evolution EV-01 through EV-05."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_valuation as validation
import render_valuation_pdf as renderer

SOURCE = "https://sources.example/fixture"


def case_fixture() -> dict:
    screen = {
        "decision": "selective_shortlist", "priority_score": 60, "priority_band": "B",
        "decision_rationale": "Tese plausível, condicionada à margem.",
        "near_term_action": "Solicitar composição de custos.",
        "decision_change_conditions": ["Confirmar margem e dívida líquida."],
        "scoring": [{"criterion": key, "weight": weight, "grade_0_to_5": 3,
                     "weighted_points": weight * .6, "rationale": "Evidência parcial para este critério."}
                    for key, weight in validation.SCORE_WEIGHTS.items()],
        "screening_ev_low": 5, "screening_ev_high": 8, "preferred_ev_ceiling": 5,
        "value_basis": "EV hipotético de 100% para sensibilidade.",
        "scenario_analysis": {
            "status": "calculated", "currency": "USD", "unit": "thousands", "financial_year": 2025,
            "revenue": 100, "baseline_ebitda": 2, "source_reference": SOURCE,
            "assumptions": ["Receita constante; margens e preços hipotéticos."],
            "limitations": ["Exclui impostos, giro, investimentos e integração."],
            "horizon_years": 5, "margin_hypotheses": [.02, .1, .2], "price_hypotheses": [5, 8],
        },
        "scenarios": [], "summary_scenario_ids": ["SC-01", "SC-02", "SC-06"],
        "strategic_value_case": {"status": "not_calculable", "gaps": ["Canal sem denominador comprovado."]},
    }
    for price in (5, 8):
        for margin in (.02, .1, .2):
            annual = 100 * margin
            second = (2 + annual) / 2
            # The fixed fixture recovers in at most four years.
            ramp_return = 1 + (price - 2) / second if price <= 2 + second else 2 + (price - 2 - second) / annual
            screen["scenarios"].append({
                "id": f"SC-{len(screen['scenarios']) + 1:02}", "price": price, "margin": margin,
                "annual_result": annual, "required_annual_improvement": annual - 2,
                "ramp_rationale": "Base no primeiro ano, transição no segundo e meta no terceiro.",
                "ramp": [{"year": i, "margin": result / 100, "annual_result": result}
                         for i, result in enumerate((2, second, annual, annual, annual), 1)],
                "returns": {"basis": "ebitda_proxy", "formula": "undiscounted_cumulative_annual_result",
                            "limitations": ["Resultado distribuído uniformemente em cada ano; sem desconto."],
                            "without_ramp": {"status": "recovered", "years": price / annual},
                            "with_ramp": {"status": "recovered", "years": ramp_return}},
            })
    return {
        "schema_version": 2, "target_name": "Alvo sintético", "currency": "USD", "unit": "thousands",
        "status": "insufficient_data", "strategic_fit": "medium", "fit_rationale": "Complementaridade a investigar.",
        "fit_evidence": ["EV-B01"], "gaps": ["Dívida líquida ausente."], "synergies": [],
        "recommendation": "proceed_to_diligence", "price_status": "unavailable", "screening": screen,
    }


def financial_fixture() -> dict:
    return {"schema_version": 1, "currency": "USD", "unit": "thousands", "status": "partial",
            "periods": [{"year": 2025, "revenue": 100, "ebitda": 2, "free_cash_flow": None,
                         "source": SOURCE, "page_or_section": "Exemplo sintético"}], "net_debt": None}


def comparisons_fixture() -> dict:
    available = lambda value: {"status": "available", "value": value, "evidence_ids": ["EV-C01"]}
    return {"schema_version": 2, "lens": "competitors", "claims": [claim("C")], "comparisons": [{
        "id": "CMP-01", "name": "Alternativa sintética", "category": "direct", "scale_class": "similar",
        "comparability_rationale": "Mesma ordem de grandeza de equipe; receita não publicada.",
        "solution": available("Serviço equivalente"),
        "scale": {**available("10–20"), "metric": "pessoas", "period": "2025"},
        "public_price": {"status": "unavailable", "value": None,
                         "limitation": "Preço sob consulta no catálogo.", "evidence_ids": ["EV-C01"]},
        "history": available("Operação desde 2020"), "differences": available("Escopo contratual distinto"),
    }]}


def claim(letter: str) -> dict:
    return {"id": f"EV-{letter}01", "statement": "Afirmação sintética para teste, sem dado de mercado.",
            "source": SOURCE, "date": "2026-01-01", "confidence": "low"}


def strategic_fixture(unit: str, arpa_unit: str = "units") -> dict:
    scale = validation.UNIT_SCALES[unit]
    arpa_scale = validation.UNIT_SCALES[arpa_unit]
    return {
        "status": "calculated", "currency": "EUR", "unit": unit,
        "assumptions": ["Múltiplo e crescimento hipotéticos."], "limitations": ["Não inclui dívida ou custos."],
        "acquisition_ev_assumed": 500_000 / scale, "working_revenue_multiple": 2,
        "target_revenue_base": 1_000_000 / scale, "target_baseline_growth": .05, "channel_extra_growth_pp": .1,
        "next_year_growth_total": .15, "next_year_revenue": 1_150_000 / scale,
        "target_margin_assumed": .2, "next_year_ebitda": 230_000 / scale,
        "payback_on_next_year_ebitda_years": 500_000 / 230_000, "payback_status": "calculated",
        "channel_base": {"status": "available", "size": 500, "population_definition": "Distribuidores de insumos",
                         "population_kind": "active", "as_of": "2026-01-01", "source_reference": SOURCE,
                         "source_kind": "stakeholder", "verification": "unverified"},
        "customers_per_converted_member": 1,
        "customer_arpa": {"value": 1000 / arpa_scale, "currency": "EUR", "unit": arpa_unit,
                          "period": "year", "source_reference": SOURCE},
        "channel_extra_revenue": 100_000 / scale, "equivalent_new_customers": 100, "implied_partner_conversion": .2,
        "target_value_at_base_revenue": 2_000_000 / scale,
        "value_created_net_of_purchase_at_base_revenue": 1_500_000 / scale,
        "target_value_at_next_year_revenue": 2_300_000 / scale,
        "value_created_net_of_purchase_at_next_year_revenue": 1_800_000 / scale,
    }


def model_fixture() -> dict:
    return {"schema_version": 1, "currency": "USD", "unit": "thousands", "status": "insufficient_data",
            "methods": [], "assumptions": ["Premissas insuficientes para valor formal."],
            "limitations": ["Dívida não informada."], "enterprise_range_low": None, "enterprise_range_high": None,
            "equity_range_low": None, "equity_range_high": None}


def report_fixture(case: dict, model: dict, competitors: dict) -> str:
    sections = []
    for section in validation.SECTIONS:
        if section == "Sumário Executivo":
            body = validation.decision_block(case, model)
        elif section == "Concorrentes de Porte Similar":
            body = validation.comparison_table(competitors)
        else:
            body = "| Item | Evidência |\n| --- | --- |\n| Exemplo sintético | EV-M01 EV-C01 EV-B01 |"
        sections.append(f"## {section}\n\n{body}")
    return "# Relatório sintético\n\n" + "\n\n".join(sections) + "\n"


def write_fixture(root: Path, qualitative: bool = False, channel: bool = False) -> tuple[dict, dict, dict]:
    docs = root / "docs"
    (docs / "research").mkdir(parents=True, exist_ok=True)
    case, model, competitors = case_fixture(), model_fixture(), comparisons_fixture()
    financial = financial_fixture()
    profile = {"schema_version": 2, "buyer_name": "Compradora sintética", "website": SOURCE,
               "currency": "USD", "unit": "thousands", "valuation_basis": "unknown",
               "financial_source_kind": "none", "financial_verification": "unknown",
               "strategic_priorities": [], "missing_data": ["Canal ausente."],
               "channel_base": {"status": "unavailable", "size": None, "gaps": ["Canal ausente."]}}
    if qualitative:
        screen = case["screening"]
        screen.update({"screening_ev_low": None, "screening_ev_high": None, "preferred_ev_ceiling": None,
                       "ev_unavailable_reason": "Sem dados financeiros.", "scenarios": [], "summary_scenario_ids": [],
                       "scenario_analysis": {"status": "not_calculable", "rationale": "Sem base para projetar.",
                                             "gaps": ["Receita e EBITDA não informados."]}})
        financial.update({"periods": [], "status": "insufficient"})
    if channel:
        strategy = strategic_fixture("thousands")
        strategy["currency"] = strategy["customer_arpa"]["currency"] = "USD"
        case["screening"]["strategic_value_case"] = strategy
        profile["channel_base"] = strategy["channel_base"]
    scope = {"schema_version": 1, "target_name": "Alvo sintético", "valuation_date": "2026-01-01",
             "currency": "USD", "unit": "thousands", "stake_percent": 100, "purpose": "acquisition",
             "basis_of_value": "hipótese de aquisição", "jurisdiction": "definida pela entrada",
             "source_documents": [], "missing_documents": ["Dívida líquida"]}
    files = {"buyer-case.yml": case, "valuation-model.yml": model, "financial-inputs.yml": financial,
             "valuation-scope.yml": scope, "buyer-profile.yml": profile}
    for lens, letter in (("market", "M"), ("buyer", "B"), ("competitors", "C")):
        files[f"research/{lens}-evidence.yml"] = competitors if lens == "competitors" else {
            "schema_version": 1, "lens": lens, "claims": [claim(letter)]}
        (docs / f"research/{lens}.md").write_text("Síntese sintética para teste, sem informação de mercado. " * 4)
    for path, data in files.items():
        (docs / path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    (docs / "financial-diligence.md").write_text("Diligência sintética com lacunas explícitas. " * 8)
    (docs / "valuation-report.md").write_text(report_fixture(case, model, competitors))
    return case, model, competitors


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = case_fixture()
        self.financial = financial_fixture()
        self.competitors = comparisons_fixture()
        root = Path(os.environ.get("VALUATION_TEST_ROOT", "report/valuation-tests")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def scoring_errors(self, case: dict) -> list[str]:
        errors = []
        validation.validate_screening(case, errors)
        return errors

    def scenario_errors(self, case: dict) -> list[str]:
        errors = []
        validation.validate_scenarios(case, self.financial, errors)
        return errors

    def comparison_errors(self, data: dict) -> list[str]:
        errors = []
        validation.validate_comparisons(data, {"EV-C01"}, errors)
        return errors

    def test_scoring_accepts_prescribed_ruler(self):
        self.assertEqual(self.scoring_errors(self.case), [])

    def test_scoring_rejects_duplicates_missing_swapped_weights_and_unexplained_grades(self):
        changes = [
            lambda s: s["scoring"].append(copy.deepcopy(s["scoring"][0])),
            lambda s: s["scoring"].pop(),
            lambda s: (s["scoring"][0].update(weight=15), s["scoring"][1].update(weight=25)),
            lambda s: s["scoring"][0].pop("rationale"),
            lambda s: s.update(priority_band="A"),
            lambda s: s.update(priority_band="B — entre 20 candidatos"),
        ]
        for change in changes:
            with self.subTest(change=change):
                case = copy.deepcopy(self.case)
                change(case["screening"])
                self.assertTrue(self.scoring_errors(case))

    def test_divergent_decision_requires_explicit_reason(self):
        self.case["screening"]["decision"] = "pass"
        self.assertTrue(self.scoring_errors(self.case))
        self.case["screening"]["decision_override_reason"] = "Risco impeditivo ainda não refletido na classe."
        self.assertEqual(self.scoring_errors(self.case), [])

    def test_ramp_recomputed_and_distinct_from_stabilized_result(self):
        self.assertEqual(self.scenario_errors(self.case), [])
        item = self.case["screening"]["scenarios"][2]
        self.assertAlmostEqual(item["returns"]["with_ramp"]["years"], 1 + 3 / 11)
        item["returns"]["with_ramp"]["years"] = .25
        self.assertTrue(self.scenario_errors(self.case))

    def test_missing_matrix_result_and_basis_are_rejected(self):
        changes = [
            lambda s: s.pop("scenarios"),
            lambda s: s["scenarios"].pop(),
            lambda s: s["scenarios"][0].update(annual_result=999),
            lambda s: s["scenarios"][0].update(required_annual_improvement=999),
            lambda s: s["scenarios"][0]["ramp"][0].update(annual_result=999),
            lambda s: s["scenario_analysis"].update(baseline_ebitda=999),
            lambda s: s["scenarios"][0].pop("returns"),
            lambda s: s.update(summary_scenario_ids=["SC-01"]),
        ]
        for change in changes:
            with self.subTest(change=change):
                case = copy.deepcopy(self.case)
                change(case["screening"])
                self.assertTrue(self.scenario_errors(case))

    def test_zero_negative_and_beyond_horizon_returns(self):
        for flows, status in (([0, 0], "non_positive_result"), ([-1, -2], "non_positive_result"),
                              ([1, 2], "beyond_horizon")):
            self.assertEqual(validation.payback(5, flows), {"status": status, "years": None})
        self.assertEqual(validation.payback(5, [-2, 4, 6]), {"status": "recovered", "years": 2.5})

    def test_unavailable_scenarios_need_gaps_and_reason(self):
        self.case["screening"].update(scenarios=[], summary_scenario_ids=[],
                                     scenario_analysis={"status": "not_applicable"})
        self.assertTrue(self.scenario_errors(self.case))
        self.case["screening"]["scenario_analysis"].update(gaps=["Base sem EBITDA conciliável."], rationale="Receita isolada não define margem base.")
        self.assertEqual(self.scenario_errors(self.case), [])

    def test_comparisons_accept_documented_unavailable_price(self):
        self.assertEqual(self.comparison_errors(self.competitors), [])

    def test_comparisons_reject_missing_dimensions_unknown_references_and_false_equivalence(self):
        changes = [lambda e: e.pop("scale"), lambda e: e.pop("public_price"),
                   lambda e: e["public_price"].update(evidence_ids=["EV-C99"]),
                   lambda e: e.update(comparability_rationale=""),
                   lambda e: e["scale"].update(status="unavailable", value=None, limitation="Sem porte divulgado."),
                   lambda e: e["public_price"].update(status="available", value=3, kind="valuation_multiple", basis="EV/receita")]
        for change in changes:
            with self.subTest(change=change):
                data = copy.deepcopy(self.competitors)
                change(data["comparisons"][0])
                self.assertTrue(self.comparison_errors(data))

    def test_absence_of_similar_peers_is_allowed_with_traceable_limits(self):
        self.competitors.update(comparisons=[], comparison_gap={"limitation": "Não foram encontrados pares.", "evidence_ids": ["EV-C01"]})
        self.assertEqual(self.comparison_errors(self.competitors), [])

    def test_scale_conversion_accepts_other_sector_and_all_scales(self):
        for unit in validation.UNIT_SCALES:
            for arpa_unit in validation.UNIT_SCALES:
                with self.subTest(unit=unit, arpa_unit=arpa_unit):
                    strategy = strategic_fixture(unit, arpa_unit)
                    errors = []
                    validation.validate_strategic_value_case(strategy, strategy, {"channel_base": strategy["channel_base"]}, errors)
                    self.assertEqual(errors, [])
                    self.assertEqual(strategy["equivalent_new_customers"], 100)
                    self.assertEqual(strategy["implied_partner_conversion"], .2)

    def test_channel_rejects_missing_denominators_units_and_currency_mismatch(self):
        changes = [lambda s: s["channel_base"].update(size=None), lambda s: s["channel_base"].update(size=0),
                   lambda s: s["customer_arpa"].update(unit="billions"),
                   lambda s: s["customer_arpa"].update(currency="USD"),
                   lambda s: s.update(customers_per_converted_member=0),
                   lambda s: s.update(equivalent_new_customers=101),
                   lambda s: s["channel_base"].update(verification="verified")]
        for change in changes:
            with self.subTest(change=change):
                strategy = strategic_fixture("millions")
                change(strategy)
                errors = []
                validation.validate_strategic_value_case(strategy, strategy, {"channel_base": strategy["channel_base"]}, errors)
                self.assertTrue(errors)

    def test_summary_rejects_empty_body_only_decision_score_and_missing_scenario(self):
        model = model_fixture()
        content = report_fixture(self.case, model, self.competitors)
        errors = []
        validation.validate_report_blocks(content, self.case, model, self.competitors, errors)
        self.assertEqual(errors, [])
        summary = validation.decision_block(self.case, model)
        malformed = [content.replace(summary, ""), content.replace(summary, "") + "\n" + summary,
                     content.replace("Nota: 60/100", "Nota: 61/100"),
                     "\n".join(line for line in content.splitlines() if "SC-06" not in line)]
        for text in malformed:
            errors = []
            validation.validate_report_blocks(text, self.case, model, self.competitors, errors)
            self.assertTrue(errors)

    def test_report_preserves_comparison_scale_and_references(self):
        content = report_fixture(self.case, model_fixture(), self.competitors)
        for text in (content.replace("similar:", "larger:"), content.replace("| EV-C01 |", "| EV-C99 |")):
            errors = []
            validation.validate_report_blocks(text, self.case, model_fixture(), self.competitors, errors)
            self.assertTrue(errors)

    def test_all_cli_stages_and_qualitative_case(self):
        for qualitative in (False, True):
            write_fixture(self.root, qualitative=qualitative)
            for stage in (*validation.STAGES, "report_blocks"):
                with self.subTest(qualitative=qualitative, stage=stage):
                    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "validate_valuation.py"), stage], cwd=self.root, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ranking_1_3_21_cases_and_per_case_currency(self):
        paths = []
        for i in range(21):
            case = copy.deepcopy(self.case)
            score = 50 + i
            case["screening"].update(priority_score=score, priority_band="A" if score >= 70 else "B", decision="priority" if score >= 70 else "selective_shortlist")
            for entry in case["screening"]["scoring"]:
                entry.update(grade_0_to_5=score / 20, weighted_points=entry["weight"] * score / 100)
            case.update(target_name=f"Alvo {i:02}", currency="EUR" if i % 2 else "USD", unit="units" if i % 2 else "thousands")
            path = self.root / f"case-{i:02}.yml"
            path.write_text(yaml.safe_dump(case, allow_unicode=True))
            paths.append(str(path))
        for count in (1, 3, 21):
            result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "rank_candidates.py"), *paths[:count]], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("USD / thousands", result.stdout)
            self.assertIn("case-00.yml", result.stdout)
            if count == 1:
                self.assertNotIn("Ordem", result.stdout)
                self.assertNotIn("Coleção", result.stdout)
            else:
                self.assertIn(f"Coleção fornecida: {count}", result.stdout)
                self.assertIn("EUR / units", result.stdout)
                self.assertLess(result.stdout.index(f"Alvo {count - 1:02}"), result.stdout.index("Alvo 00"))
        invalid = copy.deepcopy(self.case)
        invalid["screening"]["scoring"][0]["weight"] = 99
        Path(paths[0]).write_text(yaml.safe_dump(invalid))
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "rank_candidates.py"), paths[0]], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("weight", result.stderr)

    def test_legacy_contract_is_rejected_without_changing_file(self):
        self.case["schema_version"] = 1
        self.assertTrue(self.scoring_errors(self.case))

    def test_html_places_summary_before_supporting_charts(self):
        write_fixture(self.root)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            html = renderer.build_html()
        finally:
            os.chdir(previous)
        summary = html.index('<section class="decision-summary">')
        self.assertLess(html.index('<section class="cover">'), summary)
        self.assertLess(summary, html.index('<div class="chart-card">'))
        self.assertEqual(html.count("Sumário Executivo"), 1)


if __name__ == "__main__":
    unittest.main()
