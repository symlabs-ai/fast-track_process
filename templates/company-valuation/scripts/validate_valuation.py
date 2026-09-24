#!/usr/bin/env python3
"""Structural and arithmetic checks for the company-valuation template."""

from __future__ import annotations

import math
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

DOCS = Path("docs")
RESEARCH = DOCS / "research"
CONTRACT_V2 = {"buyer-profile.yml", "buyer-case.yml", "competitors-evidence.yml"}
UNIT_SCALES = {"units": 1, "thousands": 1_000, "millions": 1_000_000}
SCORE_WEIGHTS = {
    "encaixe_com_a_compradora": 25, "crescimento_recente": 15,
    "margem_e_estabilidade": 20, "sinais_de_produto_e_clientes": 15,
    "potencial_de_sinergia": 15, "risco_de_execucao": 10,
}
SECTIONS = (
    "Sumário Executivo", "Decisão de Triagem e Economia do Alvo",
    "Escopo e Data-Base", "Empresa e Modelo de Negócio",
    "Mercado e Tamanho", "Concorrência", "Concorrentes de Porte Similar", "Tendências e Perspectivas",
    "Desempenho Financeiro", "Metodologia e Premissas",
    "Resultado do Valuation", "Sensibilidade e Cenários", "Cenários de Integração, Margem e Retorno",
    "Perfil da Compradora", "Encaixe Estratégico e Sinergias",
    "Preço para a Compradora",
    "Riscos e Diligências Pendentes", "Fontes e Rastreabilidade",
)


def load(path: Path, errors: list[str]) -> dict:
    if not path.is_file():
        errors.append(f"{path}: arquivo ausente")
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{path}: YAML inválido: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path}: raiz deve ser mapping")
        return {}
    version = 2 if path.name in CONTRACT_V2 else 1
    if value.get("schema_version") != version:
        errors.append(f"{path}: schema_version deve ser {version}; consulte a migração no README")
    return value


def number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def text_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(nonempty(item) for item in value)


def reconciles(actual: object, expected: float) -> bool:
    return number(actual) and math.isclose(actual, expected, rel_tol=1e-4, abs_tol=1e-6)


def monetary_basis(data: dict, label: str, errors: list[str]) -> bool:
    valid = True
    if not re.fullmatch(r"[A-Z]{3}", str(data.get("currency", ""))):
        errors.append(f"{label}: currency deve ser código ISO de três letras")
        valid = False
    if data.get("unit") not in UNIT_SCALES:
        errors.append(f"{label}: unit não suportada; use units|thousands|millions ou registre lacuna sem calcular")
        valid = False
    return valid


def validate_channel(base: object, errors: list[str]) -> bool:
    label = "channel_base"
    if not isinstance(base, dict):
        errors.append(f"{label}: mapping obrigatório (contrato v2)")
        return False
    if base.get("status") == "unavailable":
        if base.get("size") is not None or not text_list(base.get("gaps")):
            errors.append(f"{label}: unavailable exige size null e gaps explícitos")
        return False
    start = len(errors)
    if base.get("status") != "available" or not number(base.get("size")) or base["size"] <= 0:
        errors.append(f"{label}: status available exige size positivo em membros")
    if not nonempty(base.get("population_definition")) or base.get("population_kind") not in {"active", "published"}:
        errors.append(f"{label}: defina a população e population_kind active|published")
    if not iso_date(base.get("as_of")) or not source_ok(base.get("source_reference")):
        errors.append(f"{label}: as_of e source_reference obrigatórios")
    if base.get("source_kind") not in {"stakeholder", "document", "public"} or base.get("verification") not in {"verified", "unverified"}:
        errors.append(f"{label}: origem e grau de verificação obrigatórios")
    if base.get("source_kind") == "stakeholder" and base.get("verification") != "unverified":
        errors.append(f"{label}: dado de stakeholder permanece unverified")
    return len(errors) == start


def validate_strategic_value_case(case: object, owner: dict, profile: dict, errors: list[str]) -> None:
    if not isinstance(case, dict):
        errors.append("buyer-case.yml: strategic_value_case obrigatório com status e insumos ou gaps")
        return
    outputs = (
        "next_year_growth_total", "next_year_revenue", "next_year_ebitda",
        "channel_extra_revenue", "equivalent_new_customers", "implied_partner_conversion",
        "payback_on_next_year_ebitda_years", "target_value_at_base_revenue",
        "value_created_net_of_purchase_at_base_revenue", "target_value_at_next_year_revenue",
        "value_created_net_of_purchase_at_next_year_revenue",
    )
    if case.get("status") in {"not_calculable", "not_applicable"}:
        if not text_list(case.get("gaps")) or any(case.get(key) is not None for key in outputs):
            errors.append("strategic_value_case: indisponível exige gaps e resultados null")
        return
    if case.get("status") != "calculated":
        errors.append("strategic_value_case: status inválido")
        return
    valid_basis = monetary_basis(case, "strategic_value_case", errors)
    if any(case.get(key) != owner.get(key) for key in ("currency", "unit")):
        errors.append("strategic_value_case: moeda/unidade devem coincidir com buyer-case")
    if not text_list(case.get("assumptions")) or not text_list(case.get("limitations")):
        errors.append("strategic_value_case: assumptions e limitations obrigatórias")
    base = case.get("channel_base")
    valid_channel = validate_channel(base, errors)
    if base != profile.get("channel_base"):
        errors.append("strategic_value_case: channel_base diverge do perfil da compradora")
    arpa_input = case.get("customer_arpa")
    if not isinstance(arpa_input, dict):
        errors.append("strategic_value_case: customer_arpa deve declarar value, currency, unit, period e source_reference")
        return
    valid_arpa = monetary_basis(arpa_input, "customer_arpa", errors)
    if arpa_input.get("currency") != case.get("currency") or arpa_input.get("period") != "year" or not source_ok(arpa_input.get("source_reference")):
        errors.append("customer_arpa: mesma moeda, period year e fonte obrigatórios; sem câmbio implícito")
    fields = (
        "acquisition_ev_assumed", "working_revenue_multiple", "target_revenue_base",
        "target_baseline_growth", "channel_extra_growth_pp", "next_year_growth_total",
        "next_year_revenue", "target_margin_assumed", "next_year_ebitda",
        "channel_extra_revenue", "customers_per_converted_member",
        "equivalent_new_customers", "implied_partner_conversion",
        "target_value_at_base_revenue", "value_created_net_of_purchase_at_base_revenue",
        "target_value_at_next_year_revenue", "value_created_net_of_purchase_at_next_year_revenue",
    )
    if any(not number(case.get(field)) for field in fields):
        errors.append("buyer-case.yml: strategic_value_case requer campos numéricos completos")
        return
    price = case["acquisition_ev_assumed"]
    multiple = case["working_revenue_multiple"]
    revenue = case["target_revenue_base"]
    growth = case["target_baseline_growth"] + case["channel_extra_growth_pp"]
    next_revenue = revenue * (1 + growth)
    next_ebitda = next_revenue * case["target_margin_assumed"]
    arpa = arpa_input.get("value")
    if not valid_basis or not valid_arpa or not valid_channel:
        errors.append("strategic_value_case: denominadores/unidades ausentes; registre not_calculable e gaps")
        return
    if not number(arpa) or min(price, multiple, revenue, arpa, case["customers_per_converted_member"]) <= 0 or growth <= -1 or case["channel_extra_growth_pp"] < 0 or not -1 <= case["target_margin_assumed"] <= 1:
        errors.append("buyer-case.yml: strategic_value_case contém base inválida; registre lacunas sem dividir")
        return
    expected = {
        "next_year_growth_total": growth,
        "next_year_revenue": next_revenue,
        "next_year_ebitda": next_ebitda,
        "channel_extra_revenue": revenue * case["channel_extra_growth_pp"],
        "target_value_at_base_revenue": revenue * multiple,
        "value_created_net_of_purchase_at_base_revenue": revenue * multiple - price,
        "target_value_at_next_year_revenue": next_revenue * multiple,
        "value_created_net_of_purchase_at_next_year_revenue": next_revenue * multiple - price,
    }
    expected["equivalent_new_customers"] = expected["channel_extra_revenue"] * UNIT_SCALES[case["unit"]] / (arpa * UNIT_SCALES[arpa_input["unit"]])
    expected["implied_partner_conversion"] = expected["equivalent_new_customers"] / (base["size"] * case["customers_per_converted_member"])
    if next_ebitda > 0:
        expected["payback_on_next_year_ebitda_years"] = price / next_ebitda
        if case.get("payback_status") != "calculated":
            errors.append("strategic_value_case: payback_status deve ser calculated")
    elif case.get("payback_status") != "non_positive_result" or case.get("payback_on_next_year_ebitda_years") is not None:
        errors.append("strategic_value_case: resultado não positivo exige payback null e status próprio")
    for field, value in expected.items():
        if not reconciles(case.get(field), value):
            errors.append(f"buyer-case.yml: strategic_value_case.{field} não concilia")


def source_ok(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        return bool(parsed.netloc and not parsed.username and not parsed.password)
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and path.is_file()


def iso_date(value: object) -> bool:
    try:
        date.fromisoformat(str(value))
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)))
    except ValueError:
        return False


def validate_screening(data: dict, errors: list[str]) -> None:
    """Shared scoring contract; ranking never repairs or regrades a case."""
    monetary_basis(data, "buyer-case.yml", errors)
    if data.get("schema_version") != 2:
        errors.append("buyer-case.yml: screening requer schema_version 2; migre explicitamente conforme README")
    screen = data.get("screening")
    if not isinstance(screen, dict):
        errors.append("buyer-case.yml: screening obrigatório")
        return
    score = screen.get("priority_score")
    if not number(score) or not 0 <= score <= 100:
        errors.append("screening: priority_score deve estar em 0..100")
    else:
        band = "A" if score >= 70 else "B" if score >= 50 else "C"
        if screen.get("priority_band") != band:
            errors.append(f"screening: priority_band deve ser {band}, sem texto ou universo presumido")
        expected = {"A": "priority", "B": "selective_shortlist", "C": "pass"}[band]
        if screen.get("decision") != expected and not nonempty(screen.get("decision_override_reason")):
            errors.append("screening: decisão divergente da classe exige decision_override_reason")
    if screen.get("decision") not in {"priority", "selective_shortlist", "pass"}:
        errors.append("screening: decision inválida")
    for field in ("decision_rationale", "near_term_action"):
        if not nonempty(screen.get(field)):
            errors.append(f"screening: {field} obrigatório")
    if not text_list(screen.get("decision_change_conditions")):
        errors.append("screening: decision_change_conditions deve ser lista de condições explícitas")
    entries = screen.get("scoring")
    if not isinstance(entries, list):
        errors.append("screening: scoring deve ser lista com os seis critérios")
        entries = []
    seen: list[str] = []
    total = 0.0
    for i, entry in enumerate(entries):
        label = f"screening.scoring[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: mapping obrigatório")
            continue
        criterion = entry.get("criterion")
        if not isinstance(criterion, str) or criterion not in SCORE_WEIGHTS or criterion in seen:
            errors.append(f"{label}: criterion desconhecido ou duplicado")
            continue
        seen.append(criterion)
        weight, grade, points = (entry.get(k) for k in ("weight", "grade_0_to_5", "weighted_points"))
        if not number(weight) or weight != SCORE_WEIGHTS[criterion]:
            errors.append(f"{label}: weight deve ser {SCORE_WEIGHTS[criterion]}")
        if not number(grade) or not 0 <= grade <= 5 or not number(points):
            errors.append(f"{label}: nota ou pontos inválidos")
        else:
            if not reconciles(points, SCORE_WEIGHTS[criterion] * grade / 5):
                errors.append(f"{label}: weighted_points não concilia")
            total += points
        if not nonempty(entry.get("rationale")):
            errors.append(f"{label}: rationale obrigatório por nota")
    if set(seen) != set(SCORE_WEIGHTS) or len(entries) != len(SCORE_WEIGHTS):
        errors.append("screening: exige exatamente os seis critérios prescritos, uma vez cada")
    if not reconciles(score, total):
        errors.append("screening: priority_score não concilia com os pontos")
    low, high, preferred = (screen.get(k) for k in ("screening_ev_low", "screening_ev_high", "preferred_ev_ceiling"))
    if low is None and high is None:
        if not nonempty(screen.get("ev_unavailable_reason")) or preferred is not None:
            errors.append("screening: EV ausente exige ev_unavailable_reason e preferred_ev_ceiling null")
    elif not number(low) or not number(high) or not 0 < low <= high:
        errors.append("screening: faixa de EV inválida")
    else:
        if not nonempty(screen.get("value_basis")):
            errors.append("screening: EV hipotético exige value_basis")
        if preferred is not None and (not number(preferred) or not low <= preferred <= high):
            errors.append("screening: preferred_ev_ceiling fora da faixa")


def payback(price: float, flows: list[float]) -> dict:
    """Undiscounted annual EBITDA proxy, uniform within each modeled year."""
    cumulative = 0.0
    for year, flow in enumerate(flows):
        if flow > 0 and cumulative + flow >= price:
            return {"status": "recovered", "years": year + (price - cumulative) / flow}
        cumulative += flow
    return {"status": "non_positive_result" if flows[-1] <= 0 else "beyond_horizon", "years": None}


def validate_scenarios(data: dict, financial: dict, errors: list[str]) -> None:
    screen = data.get("screening")
    if not isinstance(screen, dict):
        return
    plan = screen.get("scenario_analysis")
    scenarios = screen.get("scenarios")
    selected = screen.get("summary_scenario_ids")
    if not isinstance(plan, dict) or not isinstance(scenarios, list):
        errors.append("screening: scenario_analysis e scenarios obrigatórios mesmo sem dados")
        return
    if not isinstance(selected, list) or any(not isinstance(s, str) for s in selected) or len(set(selected)) != len(selected):
        errors.append("screening: summary_scenario_ids deve listar IDs únicos")
        selected = []
    if plan.get("status") in {"not_calculable", "not_applicable"}:
        if not text_list(plan.get("gaps")) or not nonempty(plan.get("rationale")):
            errors.append("scenario_analysis: indisponibilidade exige gaps e rationale, mesmo com dados financeiros")
        if scenarios or selected:
            errors.append("scenario_analysis: indisponível exige scenarios=[] e summary_scenario_ids=[]")
        return
    if plan.get("status") != "calculated":
        errors.append("scenario_analysis: status deve ser calculated|not_calculable|not_applicable")
        return
    valid_basis = monetary_basis(plan, "scenario_analysis", errors)
    if any(plan.get(k) != data.get(k) for k in ("currency", "unit")):
        errors.append("scenario_analysis: moeda/unidade divergem de buyer-case")
    revenue, baseline = plan.get("revenue"), plan.get("baseline_ebitda")
    if not valid_basis or not number(revenue) or revenue <= 0 or not number(baseline):
        errors.append("scenario_analysis: receita positiva e EBITDA base exigidos; sem insumos use not_calculable com gaps")
        return
    periods = financial.get("periods", [])
    period = next((p for p in periods if isinstance(p, dict) and p.get("year") == plan.get("financial_year")), None) if isinstance(periods, list) else None
    if not period or not reconciles(period.get("revenue"), revenue) or not reconciles(period.get("ebitda"), baseline) or any(financial.get(k) != plan.get(k) for k in ("currency", "unit")):
        errors.append("scenario_analysis: bases/ano devem conciliar com financial-inputs; sem base declare lacuna")
    if not source_ok(plan.get("source_reference")) or not text_list(plan.get("assumptions")) or not text_list(plan.get("limitations")):
        errors.append("scenario_analysis: source_reference, assumptions e limitations obrigatórios")
    margins, prices = plan.get("margin_hypotheses"), plan.get("price_hypotheses")
    if not isinstance(margins, list) or len(margins) < 3 or any(not number(m) or not -1 <= m <= 1 for m in margins):
        errors.append("scenario_analysis: margin_hypotheses exige base, intermediária e meta explícitas (frações)")
        return
    if len(set(margins)) != len(margins) or not any(reconciles(m, baseline / revenue) for m in margins):
        errors.append("scenario_analysis: margens únicas devem incluir a margem atual")
    if not isinstance(prices, list) or not 2 <= len(prices) <= 3 or any(not number(p) or p <= 0 for p in prices):
        errors.append("scenario_analysis: price_hypotheses exige 2–3 preços hipotéticos positivos")
        return
    if len(set(prices)) != len(prices):
        errors.append("scenario_analysis: preços devem ser distintos")
    horizon = plan.get("horizon_years")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        errors.append("scenario_analysis: horizon_years deve ser inteiro positivo")
        return
    ids: dict[str, dict] = {}
    pairs: list[tuple[float, float]] = []
    for i, item in enumerate(scenarios):
        label = f"screening.scenarios[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: mapping obrigatório")
            continue
        sid = item.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"SC-\d{2,}", sid) or sid in ids:
            errors.append(f"{label}: id inválido ou duplicado")
        else:
            ids[sid] = item
        price, margin = item.get("price"), item.get("margin")
        if not number(price) or not number(margin) or price not in prices or margin not in margins:
            errors.append(f"{label}: preço/margem fora das hipóteses declaradas")
            continue
        pairs.append((price, margin))
        annual = revenue * margin
        if not reconciles(item.get("annual_result"), annual) or not reconciles(item.get("required_annual_improvement"), annual - baseline):
            errors.append(f"{label}: receita × margem ou melhoria versus base não concilia")
        ramp = item.get("ramp")
        if not isinstance(ramp, list) or len(ramp) != horizon or not nonempty(item.get("ramp_rationale")):
            errors.append(f"{label}: ramp deve cobrir cada ano do horizonte com ramp_rationale")
            continue
        flows = []
        for year, step in enumerate(ramp, 1):
            if not isinstance(step, dict) or step.get("year") != year or not number(step.get("margin")) or not -1 <= step["margin"] <= 1:
                errors.append(f"{label}: cronograma da rampa inválido no ano {year}")
                break
            flow = revenue * step["margin"]
            if not reconciles(step.get("annual_result"), flow):
                errors.append(f"{label}: annual_result da rampa não concilia no ano {year}")
            flows.append(flow)
        if len(flows) != horizon:
            continue
        if not reconciles(ramp[-1]["margin"], margin):
            errors.append(f"{label}: último ano da rampa deve atingir a margem do cenário")
        returns = item.get("returns")
        if not isinstance(returns, dict) or returns.get("basis") != "ebitda_proxy" or returns.get("formula") != "undiscounted_cumulative_annual_result" or not text_list(returns.get("limitations")):
            errors.append(f"{label}: returns exige basis ebitda_proxy, fórmula e limitações explícitas")
            continue
        for mode, mode_flows in (("without_ramp", [annual] * horizon), ("with_ramp", flows)):
            expected = payback(price, mode_flows)
            actual = returns.get(mode)
            if not isinstance(actual, dict) or actual.get("status") != expected["status"] or (actual.get("years") is not None if expected["years"] is None else not reconciles(actual.get("years"), expected["years"])):
                errors.append(f"{label}: returns.{mode} não concilia com os fluxos e o horizonte")
    if len(pairs) != len(set(pairs)) or set(pairs) != {(p, m) for p in prices for m in margins}:
        errors.append("screening: scenarios deve cobrir a matriz de preços × margens, sem pares duplicados")
    if not 2 <= len(selected) <= 3 or any(sid not in ids for sid in selected):
        errors.append("screening: sumário exige 2–3 summary_scenario_ids existentes")
    else:
        chosen = [ids[sid] for sid in selected]
        if len({s.get("price") for s in chosen if number(s.get("price"))}) < 2 or len({s.get("margin") for s in chosen if number(s.get("margin"))}) < 2:
            errors.append("screening: sumário deve contrastar preços e margens")


def references(value: object, known: set[str]) -> bool:
    return text_list(value) and all(item in known for item in value)


def validate_comparisons(data: dict, known: set[str], errors: list[str]) -> None:
    comparisons = data.get("comparisons")
    if not isinstance(comparisons, list):
        errors.append("competitors-evidence.yml: comparisons obrigatório no contrato v2")
        return
    if not comparisons:
        gap = data.get("comparison_gap")
        if not isinstance(gap, dict) or not nonempty(gap.get("limitation")) or not references(gap.get("evidence_ids"), known):
            errors.append("competitors-evidence.yml: sem comparáveis, documente comparison_gap com fontes consultadas")
    seen: set[str] = set()
    for i, entry in enumerate(comparisons):
        label = f"comparisons[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: mapping obrigatório")
            continue
        cid = entry.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"CMP-\d{2,}", cid) or cid in seen:
            errors.append(f"{label}: id inválido ou duplicado")
        else:
            seen.add(cid)
        if not nonempty(entry.get("name")) or entry.get("category") not in {"direct", "adjacent", "manual", "do_nothing", "transaction"}:
            errors.append(f"{label}: name/category obrigatórios")
        if entry.get("scale_class") not in {"similar", "larger", "smaller", "unknown", "not_applicable"} or not nonempty(entry.get("comparability_rationale")):
            errors.append(f"{label}: scale_class e comparability_rationale obrigatórios")
        for field in ("solution", "scale", "public_price", "history", "differences", "transaction_price", "valuation_multiple"):
            if field in {"transaction_price", "valuation_multiple"} and field not in entry:
                continue
            dim = entry.get(field)
            if not isinstance(dim, dict) or not references(dim.get("evidence_ids"), known):
                errors.append(f"{label}.{field}: dimensão e evidence_ids existentes obrigatórios")
                continue
            if dim.get("status") == "unavailable":
                if dim.get("value") is not None or not nonempty(dim.get("limitation")):
                    errors.append(f"{label}.{field}: N/D exige value null e limitation com fonte consultada")
                if field == "scale" and entry.get("scale_class") not in {"unknown", "not_applicable"}:
                    errors.append(f"{label}: porte ausente não permite equivalência de escala")
            elif dim.get("status") != "available" or not (nonempty(dim.get("value")) or number(dim.get("value"))):
                errors.append(f"{label}.{field}: dado disponível ou N/D fundamentado obrigatório")
            elif field == "scale" and (not nonempty(dim.get("metric")) or not nonempty(dim.get("period"))):
                errors.append(f"{label}.scale: metric e period obrigatórios")
            elif field in {"public_price", "transaction_price", "valuation_multiple"}:
                kind = {"public_price": "commercial", "transaction_price": "transaction", "valuation_multiple": "valuation_multiple"}[field]
                if dim.get("kind") != kind or not nonempty(dim.get("basis")):
                    errors.append(f"{label}.{field}: kind {kind} e basis obrigatórios, sem promover preço a múltiplo")


def intake(errors: list[str]) -> None:
    data = load(DOCS / "valuation-scope.yml", errors)
    for field in ("target_name", "currency", "unit", "basis_of_value", "jurisdiction"):
        if not str(data.get(field) or "").strip():
            errors.append(f"valuation-scope.yml: {field} obrigatório")
    if not iso_date(data.get("valuation_date")):
        errors.append("valuation-scope.yml: valuation_date deve ser YYYY-MM-DD válido")
    if not re.fullmatch(r"[A-Z]{3}", str(data.get("currency", ""))):
        errors.append("valuation-scope.yml: currency deve ser código ISO 4217 de três letras")
    monetary_basis(data, "valuation-scope.yml", errors)
    stake = data.get("stake_percent")
    if not number(stake) or not 0 < stake <= 100:
        errors.append("valuation-scope.yml: stake_percent deve estar em (0, 100]")
    if data.get("purpose") != "acquisition":
        errors.append("valuation-scope.yml: purpose deve ser acquisition")
    sources = data.get("source_documents")
    if not isinstance(sources, list):
        errors.append("valuation-scope.yml: source_documents deve ser lista")
    elif any(not source_ok(source) for source in sources):
        errors.append("valuation-scope.yml: source_documents contém path/URL inválido")
    if not isinstance(data.get("missing_documents"), list):
        errors.append("valuation-scope.yml: missing_documents deve ser lista")


def evidence(lens: str, errors: list[str]) -> set[str]:
    path = RESEARCH / f"{lens}-evidence.yml"
    data = load(path, errors)
    if data.get("lens") != lens:
        errors.append(f"{path}: lens deve ser {lens}")
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append(f"{path}: claims deve ser lista não vazia")
        return set()
    ids: set[str] = set()
    letter = {"market": "M", "competitors": "C", "buyer": "B"}[lens]
    for i, claim in enumerate(claims):
        label = f"{path}: claim[{i}]"
        if not isinstance(claim, dict):
            errors.append(f"{label}: deve ser mapping")
            continue
        cid = str(claim.get("id", ""))
        if not re.fullmatch(rf"EV-{letter}\d{{2,}}", cid) or cid in ids:
            errors.append(f"{label}: id inválido ou duplicado: {cid}")
        ids.add(cid)
        if not str(claim.get("statement") or "").strip():
            errors.append(f"{label}: statement vazio")
        source = claim.get("source")
        if not isinstance(source, str) or not source.startswith(("https://", "http://")) or not source_ok(source):
            errors.append(f"{label}: source deve ser URL consultável")
        if not iso_date(claim.get("date")):
            errors.append(f"{label}: date inválida")
        if claim.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"{label}: confidence inválida")
    md = RESEARCH / f"{lens}.md"
    if not md.is_file() or len(md.read_text(encoding="utf-8").strip()) < 100:
        errors.append(f"{md}: síntese ausente ou muito curta")
    if lens == "competitors":
        validate_comparisons(data, ids, errors)
    return ids


def buyer(errors: list[str]) -> None:
    data = load(DOCS / "buyer-profile.yml", errors)
    scope = load(DOCS / "valuation-scope.yml", errors)
    for field in ("buyer_name", "website"):
        if not str(data.get(field) or "").strip():
            errors.append(f"buyer-profile.yml: {field} obrigatório")
    if not source_ok(data.get("website")) or not str(data.get("website", "")).startswith(("http://", "https://")):
        errors.append("buyer-profile.yml: website deve ser URL")
    for field in ("currency", "unit"):
        if data.get(field) != scope.get(field):
            errors.append(f"buyer-profile.yml: {field} diverge do escopo")
    year = data.get("fiscal_year")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2200):
        errors.append("buyer-profile.yml: fiscal_year inválido")
    for field in ("forecast_revenue", "indicative_valuation_low", "indicative_valuation_high", "available_cash", "debt_capacity", "acquisition_budget"):
        if data.get(field) is not None and (not number(data[field]) or data[field] < 0):
            errors.append(f"buyer-profile.yml: {field} deve ser não negativo ou null")
    for field in ("ebitda_margin", "yoy_growth"):
        value = data.get(field)
        if value is not None and (not number(value) or not -1 < value <= 1):
            errors.append(f"buyer-profile.yml: {field} deve ser fração entre -1 e 1 ou null")
    low, high = data.get("indicative_valuation_low"), data.get("indicative_valuation_high")
    if (low is None) != (high is None) or (number(low) and number(high) and low > high):
        errors.append("buyer-profile.yml: faixa indicativa incompleta ou invertida")
    if data.get("valuation_basis") not in {"equity", "enterprise", "unknown"}:
        errors.append("buyer-profile.yml: valuation_basis inválido")
    source_kind = data.get("financial_source_kind")
    if source_kind not in {"stakeholder", "document", "public", "none"}:
        errors.append("buyer-profile.yml: financial_source_kind inválido")
    populated = any(data.get(field) is not None for field in ("forecast_revenue", "ebitda_margin", "yoy_growth", "indicative_valuation_low"))
    if populated and (source_kind == "none" or not source_ok(data.get("financial_source_reference"))):
        errors.append("buyer-profile.yml: indicadores exigem origem rastreável")
    if source_kind == "stakeholder" and data.get("financial_verification") != "unverified":
        errors.append("buyer-profile.yml: números do stakeholder devem permanecer unverified")
    if data.get("financial_verification") not in {"verified", "unverified", "unknown"}:
        errors.append("buyer-profile.yml: financial_verification inválido")
    if any(data.get(field) is not None for field in ("available_cash", "debt_capacity", "acquisition_budget")) and not source_ok(data.get("funding_source")):
        errors.append("buyer-profile.yml: funding exige fonte própria")
    for field in ("strategic_priorities", "missing_data"):
        if not isinstance(data.get(field), list):
            errors.append(f"buyer-profile.yml: {field} deve ser lista")
    validate_channel(data.get("channel_base"), errors)
    evidence("buyer", errors)


def financials(errors: list[str]) -> None:
    data = load(DOCS / "financial-inputs.yml", errors)
    scope = load(DOCS / "valuation-scope.yml", errors)
    if data.get("currency") != scope.get("currency"):
        errors.append("financial-inputs.yml: moeda diverge do escopo")
    if data.get("unit") != scope.get("unit"):
        errors.append("financial-inputs.yml: unidade diverge do escopo")
    if data.get("status") not in {"sufficient", "partial", "insufficient"}:
        errors.append("financial-inputs.yml: status inválido")
    periods = data.get("periods")
    if not isinstance(periods, list):
        errors.append("financial-inputs.yml: periods deve ser lista")
        periods = []
    years: set[int] = set()
    for i, period in enumerate(periods):
        label = f"financial-inputs.yml: periods[{i}]"
        if not isinstance(period, dict):
            errors.append(f"{label}: deve ser mapping")
            continue
        year = period.get("year")
        if not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2200 or year in years:
            errors.append(f"{label}: year inválido ou duplicado")
        else:
            years.add(year)
        for field in ("revenue", "ebitda", "free_cash_flow"):
            if period.get(field) is not None and not number(period[field]):
                errors.append(f"{label}: {field} deve ser número ou null")
        if not source_ok(period.get("source")) or not str(period.get("page_or_section") or "").strip():
            errors.append(f"{label}: source e page_or_section obrigatórios")
    debt = data.get("net_debt")
    if debt is not None and (not number(debt) or not source_ok(data.get("net_debt_source"))):
        errors.append("financial-inputs.yml: net_debt requer número e fonte válida")
    if data.get("status") == "sufficient" and (not periods or not number(debt)):
        errors.append("financial-inputs.yml: sufficient exige histórico e dívida líquida")
    if data.get("status") == "partial" and (not periods or not any(number(p.get("revenue")) or number(p.get("ebitda")) for p in periods if isinstance(p, dict))):
        errors.append("financial-inputs.yml: partial exige histórico com receita ou EBITDA")
    md = DOCS / "financial-diligence.md"
    if not md.is_file() or len(md.read_text(encoding="utf-8").strip()) < 200:
        errors.append("financial-diligence.md: análise ausente ou muito curta")


def model(errors: list[str]) -> None:
    data = load(DOCS / "valuation-model.yml", errors)
    scope = load(DOCS / "valuation-scope.yml", errors)
    if data.get("currency") != scope.get("currency"):
        errors.append("valuation-model.yml: moeda diverge do escopo")
    if data.get("unit") != scope.get("unit"):
        errors.append("valuation-model.yml: unidade diverge do escopo")
    status = data.get("status")
    methods = data.get("methods")
    if status not in {"valued", "indicative_ev", "insufficient_data"} or not isinstance(methods, list):
        errors.append("valuation-model.yml: status ou methods inválido")
        return
    for field in ("assumptions", "limitations"):
        if not isinstance(data.get(field), list) or not data[field]:
            errors.append(f"valuation-model.yml: {field} deve ser lista não vazia")
    low, high = data.get("equity_range_low"), data.get("equity_range_high")
    ev_low, ev_high = data.get("enterprise_range_low"), data.get("enterprise_range_high")
    if status == "insufficient_data":
        if methods or any(value is not None for value in (low, high, ev_low, ev_high)):
            errors.append("valuation-model.yml: dados insuficientes exigem methods=[] e faixas null")
        return
    financial = load(DOCS / "financial-inputs.yml", errors)
    if status == "valued" and financial.get("status") != "sufficient":
        errors.append("valuation-model.yml: valued exige financial-inputs sufficient")
    if status == "indicative_ev" and financial.get("status") not in {"partial", "sufficient"}:
        errors.append("valuation-model.yml: indicative_ev exige dados financeiros parciais")
    if not methods:
        errors.append("valuation-model.yml: estimativa exige ao menos um método")
        return
    if status == "valued" and (not number(low) or not number(high) or low > high):
        errors.append("valuation-model.yml: valued exige faixa de equity numérica ordenada")
        return
    if status == "indicative_ev" and (low is not None or high is not None):
        errors.append("valuation-model.yml: indicative_ev exige faixa de equity null")
    if status == "indicative_ev" and (not number(ev_low) or not number(ev_high) or ev_low > ev_high):
        errors.append("valuation-model.yml: indicative_ev exige faixa de EV numérica ordenada")
        return
    seen: set[str] = set()
    values: list[float] = []
    enterprise_values: list[float] = []
    known_evidence = set()
    for lens in ("market", "competitors"):
        data_path = RESEARCH / f"{lens}-evidence.yml"
        if data_path.is_file():
            data = load(data_path, errors)
            known_evidence.update(str(c.get("id")) for c in data.get("claims", []) if isinstance(c, dict))
    for i, method in enumerate(methods):
        label = f"valuation-model.yml: methods[{i}]"
        if not isinstance(method, dict):
            errors.append(f"{label}: deve ser mapping")
            continue
        name = method.get("name")
        if name not in {"dcf", "market_multiples", "adjusted_net_assets"} or name in seen:
            errors.append(f"{label}: name inválido ou duplicado")
        seen.add(name)
        equity = method.get("equity_value")
        if status == "valued":
            if not number(equity):
                errors.append(f"{label}: equity_value inválido")
                continue
            values.append(equity)
        elif equity is not None:
            errors.append(f"{label}: indicative_ev exige equity_value null")
        if not str(method.get("rationale") or "").strip():
            errors.append(f"{label}: rationale vazio")
        sources = method.get("source_ids")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{label}: source_ids obrigatório")
        else:
            for source in sources:
                if source not in known_evidence and not source_ok(source):
                    errors.append(f"{label}: source_ids contém evidência/path inexistente: {source}")
        ev, debt = method.get("enterprise_value"), method.get("net_debt")
        if number(ev):
            enterprise_values.append(ev)
        if name != "adjusted_net_assets":
            if not number(ev):
                errors.append(f"{label}: EV numérico obrigatório")
                continue
            if status == "valued" and not number(debt):
                errors.append(f"{label}: dívida líquida numérica obrigatória")
                continue
            if status == "indicative_ev" and debt is not None:
                errors.append(f"{label}: indicative_ev exige net_debt null")
            if status == "valued" and not math.isclose(ev - debt, equity, rel_tol=1e-4, abs_tol=0.02):
                errors.append(f"{label}: equity_value não concilia com EV - dívida")
            if name == "market_multiples":
                metric, multiple = method.get("metric_value"), method.get("multiple")
                if not str(method.get("metric") or "").strip() or not number(metric) or not number(multiple) or multiple <= 0:
                    errors.append(f"{label}: métrica e múltiplo inválidos")
                elif not math.isclose(metric * multiple, ev, rel_tol=1e-4, abs_tol=0.02):
                    errors.append(f"{label}: EV não concilia com métrica × múltiplo")
            if name == "dcf":
                r, g, flows = method.get("discount_rate"), method.get("terminal_growth_rate"), method.get("forecast_fcf")
                if not number(r) or not number(g) or not 0 < r <= 1 or not -1 < g < r or not isinstance(flows, list) or not flows or any(not number(f) for f in flows):
                    errors.append(f"{label}: premissas DCF inválidas")
                elif method.get("year_count") != len(flows):
                    errors.append(f"{label}: year_count difere dos fluxos")
                else:
                    calculated = sum(f / (1 + r) ** t for t, f in enumerate(flows, 1))
                    calculated += flows[-1] * (1 + g) / (r - g) / (1 + r) ** len(flows)
                    if not math.isclose(calculated, ev, rel_tol=1e-4, abs_tol=0.02):
                        errors.append(f"{label}: EV não concilia com DCF")
    if status == "indicative_ev" and not enterprise_values:
        errors.append("valuation-model.yml: indicative_ev exige método com EV")
    if values and (min(values) < low - 0.02 or max(values) > high + 0.02):
        errors.append("valuation-model.yml: faixa não cobre os valores dos métodos")
    if enterprise_values and number(ev_low) and number(ev_high) and (min(enterprise_values) < ev_low - 0.02 or max(enterprise_values) > ev_high + 0.02):
        errors.append("valuation-model.yml: faixa de EV não cobre os métodos")


def buyer_case(errors: list[str]) -> None:
    data = load(DOCS / "buyer-case.yml", errors)
    scope = load(DOCS / "valuation-scope.yml", errors)
    target = load(DOCS / "valuation-model.yml", errors)
    profile = load(DOCS / "buyer-profile.yml", errors)
    for field in ("currency", "unit"):
        if data.get(field) != scope.get(field):
            errors.append(f"buyer-case.yml: {field} diverge do escopo")
    if data.get("status") not in {"quantified", "insufficient_data"}:
        errors.append("buyer-case.yml: status inválido")
    if data.get("strategic_fit") not in {"high", "medium", "low", "unknown"}:
        errors.append("buyer-case.yml: strategic_fit inválido")
    if not str(data.get("fit_rationale") or "").strip():
        errors.append("buyer-case.yml: fit_rationale obrigatório")
    if not isinstance(data.get("gaps"), list):
        errors.append("buyer-case.yml: gaps deve ser lista")
    known_ids: set[str] = set()
    for lens in ("market", "competitors", "buyer"):
        path = RESEARCH / f"{lens}-evidence.yml"
        if path.is_file():
            evidence_data = load(path, errors)
            known_ids.update(str(c.get("id")) for c in evidence_data.get("claims", []) if isinstance(c, dict))
    fit_evidence = data.get("fit_evidence")
    if not isinstance(fit_evidence, list) or not fit_evidence or any(item not in known_ids for item in fit_evidence):
        errors.append("buyer-case.yml: fit_evidence deve citar EV-* existentes")
    synergies = data.get("synergies")
    if not isinstance(synergies, list):
        errors.append("buyer-case.yml: synergies deve ser lista")
        synergies = []
    total_pv = 0.0
    for i, item in enumerate(synergies):
        label = f"buyer-case.yml: synergies[{i}]"
        if not isinstance(item, dict) or not str(item.get("name") or "").strip() or item.get("kind") not in {"revenue", "cost", "capability"}:
            errors.append(f"{label}: name/kind inválidos")
            continue
        flows, rate, probability, pv = (item.get(key) for key in ("forecast_incremental_fcf", "discount_rate", "realization_probability", "pv"))
        if not isinstance(flows, list) or not flows or any(not number(value) for value in flows) or not number(rate) or not 0 < rate <= 1 or not number(probability) or not 0 <= probability <= 1 or not number(pv):
            errors.append(f"{label}: fluxos, desconto, probabilidade ou PV inválidos")
            continue
        expected = probability * sum(value / (1 + rate) ** year for year, value in enumerate(flows, 1))
        if not math.isclose(expected, pv, rel_tol=1e-4, abs_tol=0.02):
            errors.append(f"{label}: PV não concilia com fluxos, taxa e probabilidade")
        total_pv += pv
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids or any(value not in known_ids for value in ids):
            errors.append(f"{label}: evidence_ids deve citar EV-* existentes")
    if data.get("recommendation") not in {"proceed_to_diligence", "reconsider", "insufficient_data"}:
        errors.append("buyer-case.yml: recommendation inválida")
    validate_screening(data, errors)
    financial = load(DOCS / "financial-inputs.yml", errors)
    validate_scenarios(data, financial, errors)
    screening = data.get("screening")
    if isinstance(screening, dict):
        validate_strategic_value_case(screening.get("strategic_value_case"), data, profile, errors)
    if data.get("status") == "insufficient_data":
        for field in ("total_synergy_pv", "target_stake_equity_low", "target_stake_equity_high", "buyer_economic_ceiling_low", "buyer_economic_ceiling_high", "actionable_price_ceiling", "proposed_price_low", "proposed_price_high"):
            if data.get(field) is not None:
                errors.append(f"buyer-case.yml: {field} deve ser null com dados insuficientes")
        if data.get("price_status") != "unavailable":
            errors.append("buyer-case.yml: price_status deve ser unavailable com dados insuficientes")
        return
    if target.get("status") != "valued" or not number(scope.get("stake_percent")):
        errors.append("buyer-case.yml: quantified exige valuation do alvo e participação válidos")
        return
    for field in ("integration_cost_pv", "dissynergy_pv", "transaction_cost_pv"):
        if not number(data.get(field)) or data[field] < 0:
            errors.append(f"buyer-case.yml: {field} deve ser custo não negativo")
            return
    expected_synergy = total_pv - sum(data[field] for field in ("integration_cost_pv", "dissynergy_pv", "transaction_cost_pv"))
    expected_low = target["equity_range_low"] * scope["stake_percent"] / 100
    expected_high = target["equity_range_high"] * scope["stake_percent"] / 100
    expected = {
        "total_synergy_pv": expected_synergy,
        "target_stake_equity_low": expected_low,
        "target_stake_equity_high": expected_high,
        "buyer_economic_ceiling_low": expected_low + expected_synergy,
        "buyer_economic_ceiling_high": expected_high + expected_synergy,
    }
    for field, value in expected.items():
        if not number(data.get(field)) or not math.isclose(data[field], value, rel_tol=1e-4, abs_tol=0.02):
            errors.append(f"buyer-case.yml: {field} não concilia")
    funding = data.get("funding_capacity")
    available_budget = profile.get("acquisition_budget")
    cash, debt = profile.get("available_cash"), profile.get("debt_capacity")
    justified_funding = available_budget if number(available_budget) else cash + debt if number(cash) and number(debt) else None
    if funding is not None and (not number(funding) or not number(justified_funding) or not math.isclose(funding, justified_funding, rel_tol=1e-4, abs_tol=0.02) or not source_ok(profile.get("funding_source"))):
        errors.append("buyer-case.yml: funding_capacity sem conciliação/fonte no perfil")
    ceiling = data.get("actionable_price_ceiling")
    if funding is None:
        if ceiling is not None:
            errors.append("buyer-case.yml: teto executável exige funding comprovado")
    elif not number(ceiling) or not math.isclose(ceiling, min(expected["buyer_economic_ceiling_high"], funding), rel_tol=1e-4, abs_tol=0.02):
        errors.append("buyer-case.yml: actionable_price_ceiling não concilia")
    price_status = data.get("price_status")
    proposed_low, proposed_high = data.get("proposed_price_low"), data.get("proposed_price_high")
    if price_status not in {"indicative", "actionable", "unavailable"}:
        errors.append("buyer-case.yml: price_status inválido")
    elif price_status == "unavailable":
        if proposed_low is not None or proposed_high is not None:
            errors.append("buyer-case.yml: preço indisponível exige faixa null")
    elif not number(proposed_low) or not number(proposed_high) or proposed_low > proposed_high or not str(data.get("price_basis") or "").strip():
        errors.append("buyer-case.yml: faixa de preço e price_basis obrigatórios")
    elif proposed_high > expected["buyer_economic_ceiling_high"] + 0.02:
        errors.append("buyer-case.yml: preço proposto supera teto econômico")
    elif price_status == "actionable" and (not number(ceiling) or proposed_high > ceiling + 0.02):
        errors.append("buyer-case.yml: preço acionável exige funding e deve caber no teto")
    elif price_status == "indicative" and number(ceiling):
        errors.append("buyer-case.yml: com funding comprovado use actionable ou unavailable")


def cell(value: object) -> str:
    return " ".join(str(value).replace("|", "/").split())


def numeric(value: object) -> str:
    return f"{value:.6g}" if number(value) else "N/D"


def table(headers: list[str], rows: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(cell(v) for v in row) + " |" for row in (headers, ["---"] * len(headers), *rows))


def summary_parts(case: dict, model_data: dict) -> tuple[list[str], list[str], list[list[str]]]:
    screen = case["screening"]
    lines = [
        f"Decisão agora: {screen['decision']}. Classe: {screen['priority_band']}. Nota: {numeric(screen['priority_score'])}/100.",
        "Motivo: " + cell(screen["decision_rationale"]),
        "Próximo passo: " + cell(screen["near_term_action"]),
        "Muda a decisão: " + cell("; ".join(screen["decision_change_conditions"])),
    ]
    if nonempty(screen.get("decision_override_reason")):
        lines.append("Exceção à classe: " + cell(screen["decision_override_reason"]))
    money_basis = f"{case['currency']} {case['unit']}"
    low, high = screen.get("screening_ev_low"), screen.get("screening_ev_high")
    if number(low) and number(high):
        lines.append(f"EV hipotético ({money_basis}): {numeric(low)} a {numeric(high)}; preferencial: {numeric(screen.get('preferred_ev_ceiling'))}. Base: {cell(screen['value_basis'])}")
    else:
        lines.append("EV hipotético: N/D — " + cell(screen["ev_unavailable_reason"]))
    formal = {
        "insufficient_data": "valor formal não estimável com os dados disponíveis",
        "indicative_ev": "EV preliminar; dívida líquida pendente; preço da participação não estimável",
        "valued": "equity estimado; faixa e premissas em Resultado do Valuation",
    }[model_data["status"]]
    lines.append(f"Valor formal: {formal}. Triagem não é preço exato das quotas nem oferta autorizada.")
    plan = screen["scenario_analysis"]
    rows: list[list[str]] = []
    headers = ["Cenário / margem", "Preço", "EBITDA base / meta", "Melhoria anual", "Retorno sem / com rampa (anos)"]
    if plan["status"] == "calculated":
        lines.append(f"Cenários hipotéticos ({money_basis}); EBITDA como proxy, sem desconto; horizonte: {plan['horizon_years']} anos.")
        lines.append("Limitações dos cenários: " + cell("; ".join(plan["limitations"])))
        by_id = {item["id"]: item for item in screen["scenarios"]}
        for sid in screen["summary_scenario_ids"]:
            item = by_id[sid]
            def return_text(mode: str) -> str:
                result = item["returns"][mode]
                return numeric(result["years"]) if result["status"] == "recovered" else {"non_positive_result": "resultado não positivo", "beyond_horizon": "além do horizonte"}[result["status"]]
            rows.append([
                f"{sid} / {numeric(item['margin'] * 100)}%", numeric(item["price"]),
                f"{numeric(plan['baseline_ebitda'])} / {numeric(item['annual_result'])}",
                numeric(item["required_annual_improvement"]),
                f"{return_text('without_ramp')} / {return_text('with_ramp')}",
            ])
    else:
        lines.append("Cenários: N/D — " + cell(plan["rationale"]) + "; lacunas: " + cell("; ".join(plan["gaps"])))
    strategy = screen["strategic_value_case"]
    if strategy["status"] == "calculated":
        lines.append(
            f"Canal hipotético ({money_basis}): receita adicional {numeric(strategy['channel_extra_revenue'])}; "
            f"clientes equivalentes {numeric(strategy['equivalent_new_customers'])}; conversão {numeric(strategy['implied_partner_conversion'] * 100)}%; "
            f"valor ilustrativo líquido do preço {numeric(strategy['value_created_net_of_purchase_at_next_year_revenue'])}, "
            f"sob múltiplo de receita {numeric(strategy['working_revenue_multiple'])}x. Não é reavaliação da compradora."
        )
    else:
        lines.append("Canal / valor incremental: N/D — " + cell("; ".join(strategy["gaps"])))
    return lines, headers, rows


def decision_block(case: dict, model_data: dict) -> str:
    lines, headers, rows = summary_parts(case, model_data)
    return "\n\n".join(lines + ([table(headers, rows)] if rows else []))


def summary_pdf_fragments(case: dict, model_data: dict) -> list[str]:
    lines, headers, rows = summary_parts(case, model_data)
    return ["Sumário Executivo", *lines, *(headers if rows else []), *(value for row in rows for value in row)]


def comparison_rows(data: dict) -> list[list[str]]:
    rows = []
    for entry in data["comparisons"]:
        def dimension(name: str) -> str:
            dim = entry[name]
            if dim["status"] == "unavailable":
                return "N/D: " + cell(dim["limitation"])
            value = cell(dim["value"])
            if name == "scale":
                value += f" {cell(dim['metric'])} ({cell(dim['period'])})"
            if name == "public_price":
                value += " — commercial; " + cell(dim["basis"])
            return value
        ids = sorted({cid for name in ("solution", "scale", "public_price", "history", "differences") for cid in entry[name]["evidence_ids"]})
        rows.append([
            f"{entry['id']} — {cell(entry['name'])} ({entry['category']})", dimension("solution"),
            f"{entry['scale_class']}: {dimension('scale')}; {cell(entry['comparability_rationale'])}",
            dimension("public_price"), dimension("history"), dimension("differences"), ", ".join(ids),
        ])
    if not rows:
        gap = data["comparison_gap"]
        rows.append(["N/D", "N/D", "unknown: " + cell(gap["limitation"]), "N/D", "N/D", "N/D", ", ".join(gap["evidence_ids"])])
    return rows


def comparison_table(data: dict) -> str:
    return table(["Alternativa / categoria", "Solução", "Porte / comparabilidade", "Preço comercial / base", "Histórico", "Diferenças", "Fontes"], comparison_rows(data))


def section_body(content: str, section: str) -> str:
    match = re.search(rf"^## {re.escape(section)}[^\S\n]*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def validate_report_blocks(content: str, case: dict, model_data: dict, competitors: dict, errors: list[str]) -> None:
    summary = section_body(content, "Sumário Executivo")
    normalize = lambda value: " ".join(value.split())
    if normalize(summary) != normalize(decision_block(case, model_data)):
        errors.append("valuation-report.md: Sumário Executivo deve reproduzir o bloco decisório do YAML (use report_blocks)")
    comparison = section_body(content, "Concorrentes de Porte Similar")
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in comparison.splitlines() if line.strip().startswith("|")]
    for row in comparison_rows(competitors):
        if [cell(value) for value in row] not in rows:
            errors.append(f"valuation-report.md: comparação não concilia em porte/dimensões/fontes: {row[0]}")


def report(errors: list[str]) -> None:
    path = DOCS / "valuation-report.md"
    if not path.is_file():
        errors.append(f"{path}: ausente")
        return
    content = path.read_text(encoding="utf-8")
    for section in SECTIONS:
        if not re.search(rf"^## {re.escape(section)}\s*$", content, re.MULTILINE):
            errors.append(f"{path}: seção ausente: {section}")
    market_ids = evidence("market", errors)
    competitor_ids = evidence("competitors", errors)
    buyer_ids = evidence("buyer", errors)
    cited = set(re.findall(r"\bEV-[MCB]\d{2,}\b", content))
    if not cited & market_ids or not cited & competitor_ids or not cited & buyer_ids:
        errors.append(f"{path}: cite evidências de mercado, concorrência e compradora")
    for cid in cited - market_ids - competitor_ids - buyer_ids:
        errors.append(f"{path}: evidência inexistente: {cid}")
    for section in ("Decisão de Triagem e Economia do Alvo", "Mercado e Tamanho", "Concorrência", "Concorrentes de Porte Similar", "Tendências e Perspectivas", "Desempenho Financeiro", "Resultado do Valuation", "Cenários de Integração, Margem e Retorno", "Perfil da Compradora", "Encaixe Estratégico e Sinergias", "Preço para a Compradora"):
        block = re.search(rf"^## {re.escape(section)}\s*$(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
        if block and "|" not in block.group(1):
            errors.append(f"{path}: {section} requer tabela")
    model_data = load(DOCS / "valuation-model.yml", errors)
    model(errors)
    if model_data.get("status") == "insufficient_data" and "valor formal não estimável" not in content.lower():
        errors.append(f"{path}: diferencie valor formal indisponível de faixa de triagem")
    if model_data.get("status") == "indicative_ev" and ("dívida líquida" not in content.lower() or "preço da participação não" not in content.lower()):
        errors.append(f"{path}: EV indicativo exige ressalva de dívida líquida e preço da participação")
    case = load(DOCS / "buyer-case.yml", errors)
    if case.get("status") == "insufficient_data" and "preço exato das quotas" not in content.lower():
        errors.append(f"{path}: diferencie faixa de EV de triagem do preço exato das quotas")
    buyer_case(errors)
    competitors = load(RESEARCH / "competitors-evidence.yml", errors)
    if not errors:
        validate_report_blocks(content, case, model_data, competitors, errors)


STAGES = {
    "intake": intake,
    "market": lambda errors: evidence("market", errors),
    "buyer": buyer,
    "competitors": lambda errors: evidence("competitors", errors),
    "financials": financials,
    "model": model,
    "buyer_case": buyer_case,
    "report": report,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {*STAGES, "report_blocks"}:
        print("uso: validate_valuation.py <" + "|".join(STAGES) + "|report_blocks>")
        return 2
    errors: list[str] = []
    if sys.argv[1] == "report_blocks":
        model(errors)
        buyer_case(errors)
        evidence("competitors", errors)
    else:
        STAGES[sys.argv[1]](errors)
    if errors:
        print(f"BLOCK ({sys.argv[1]}):")
        for error in errors:
            print(f"  - {error}")
        return 1
    if sys.argv[1] == "report_blocks":
        case = load(DOCS / "buyer-case.yml", errors)
        model_data = load(DOCS / "valuation-model.yml", errors)
        competitors = load(RESEARCH / "competitors-evidence.yml", errors)
        print("## Sumário Executivo\n\n" + decision_block(case, model_data))
        print("\n## Concorrentes de Porte Similar\n\n" + comparison_table(competitors))
    else:
        print(f"PASS ({sys.argv[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
