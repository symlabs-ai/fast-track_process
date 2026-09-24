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
    if value.get("schema_version") != 1:
        errors.append(f"{path}: schema_version deve ser 1")
    return value


def number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_strategic_value_case(case: object, errors: list[str]) -> None:
    if not isinstance(case, dict):
        errors.append("buyer-case.yml: strategic_value_case deve ser mapping")
        return
    fields = (
        "acquisition_ev_assumed", "working_revenue_multiple", "target_revenue_base",
        "target_baseline_growth", "channel_extra_growth_pp", "next_year_growth_total",
        "next_year_revenue", "target_margin_assumed", "next_year_ebitda",
        "payback_on_next_year_ebitda_years", "software_houses_available",
        "channel_extra_revenue", "implied_target_customer_arpa_year",
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
    partner_count = case["software_houses_available"]
    arpa = case["implied_target_customer_arpa_year"]
    if min(price, multiple, revenue, next_ebitda, partner_count, arpa) <= 0:
        errors.append("buyer-case.yml: strategic_value_case contém base não positiva")
        return
    expected = {
        "next_year_growth_total": growth,
        "next_year_revenue": next_revenue,
        "next_year_ebitda": next_ebitda,
        "payback_on_next_year_ebitda_years": price / next_ebitda,
        "channel_extra_revenue": revenue * case["channel_extra_growth_pp"],
        "target_value_at_base_revenue": revenue * multiple,
        "value_created_net_of_purchase_at_base_revenue": revenue * multiple - price,
        "target_value_at_next_year_revenue": next_revenue * multiple,
        "value_created_net_of_purchase_at_next_year_revenue": next_revenue * multiple - price,
    }
    expected["equivalent_new_customers"] = expected["channel_extra_revenue"] * 1_000_000 / arpa
    expected["implied_partner_conversion"] = expected["equivalent_new_customers"] / partner_count
    for field, value in expected.items():
        if not math.isclose(case[field], value, rel_tol=0.001, abs_tol=0.02):
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


def intake(errors: list[str]) -> None:
    data = load(DOCS / "valuation-scope.yml", errors)
    for field in ("target_name", "currency", "unit", "basis_of_value", "jurisdiction"):
        if not str(data.get(field) or "").strip():
            errors.append(f"valuation-scope.yml: {field} obrigatório")
    if not iso_date(data.get("valuation_date")):
        errors.append("valuation-scope.yml: valuation_date deve ser YYYY-MM-DD válido")
    if not re.fullmatch(r"[A-Z]{3}", str(data.get("currency", ""))):
        errors.append("valuation-scope.yml: currency deve ser código ISO 4217 de três letras")
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
    screening = data.get("screening")
    if not isinstance(screening, dict):
        errors.append("buyer-case.yml: screening de triagem obrigatório")
    else:
        if screening.get("decision") not in {"priority", "selective_shortlist", "pass"}:
            errors.append("buyer-case.yml: screening.decision inválida")
        score = screening.get("priority_score")
        entries = screening.get("scoring")
        if not number(score) or not 0 <= score <= 100 or not isinstance(entries, list) or not entries:
            errors.append("buyer-case.yml: screening requer nota 0..100 e scoring")
        else:
            weights = 0.0
            points = 0.0
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    errors.append(f"buyer-case.yml: screening.scoring[{i}] inválido")
                    continue
                weight, grade, weighted = (entry.get(k) for k in ("weight", "grade_0_to_5", "weighted_points"))
                if not all(number(v) for v in (weight, grade, weighted)) or not 0 <= grade <= 5 or weight < 0:
                    errors.append(f"buyer-case.yml: screening.scoring[{i}] contém pesos/notas inválidos")
                    continue
                if not math.isclose(weight * grade / 5, weighted, abs_tol=0.01):
                    errors.append(f"buyer-case.yml: screening.scoring[{i}] não concilia")
                weights += weight
                points += weighted
            if not math.isclose(weights, 100, abs_tol=0.01) or not math.isclose(points, score, abs_tol=0.01):
                errors.append("buyer-case.yml: screening pesos ou nota final não conciliam")
        low, high = screening.get("screening_ev_low"), screening.get("screening_ev_high")
        if low is not None or high is not None:
            if not number(low) or not number(high) or not 0 < low <= high:
                errors.append("buyer-case.yml: faixa de EV de triagem inválida")
            preferred = screening.get("preferred_ev_ceiling")
            if preferred is not None and (not number(preferred) or not number(low) or not number(high) or not low <= preferred <= high):
                errors.append("buyer-case.yml: preço preferencial fora da faixa de triagem")
        revenue = screening.get("current_revenue")
        current = screening.get("current_ebitda_proxy")
        margin = screening.get("target_ebitda_margin")
        target_ebitda = screening.get("target_ebitda_proxy")
        improvement = screening.get("required_annual_improvement")
        if all(number(v) for v in (revenue, current, margin, target_ebitda, improvement)):
            if not math.isclose(revenue * margin, target_ebitda, abs_tol=0.02) or not math.isclose(target_ebitda - current, improvement, abs_tol=0.02):
                errors.append("buyer-case.yml: cenário de margem não concilia")
        if screening.get("strategic_value_case") is not None:
            validate_strategic_value_case(screening["strategic_value_case"], errors)
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
    if model_data.get("status") == "insufficient_data" and "valor formal não estimável" not in content.lower():
        errors.append(f"{path}: diferencie valor formal indisponível de faixa de triagem")
    if model_data.get("status") == "indicative_ev" and ("dívida líquida" not in content.lower() or "preço da participação não" not in content.lower()):
        errors.append(f"{path}: EV indicativo exige ressalva de dívida líquida e preço da participação")
    case = load(DOCS / "buyer-case.yml", errors)
    if case.get("status") == "insufficient_data" and "preço exato das quotas" not in content.lower():
        errors.append(f"{path}: diferencie faixa de EV de triagem do preço exato das quotas")
    if "decisão agora" not in content.lower():
        errors.append(f"{path}: informe a decisão de triagem no sumário")


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
    if len(sys.argv) != 2 or sys.argv[1] not in STAGES:
        print("uso: validate_valuation.py <" + "|".join(STAGES) + ">")
        return 2
    errors: list[str] = []
    STAGES[sys.argv[1]](errors)
    if errors:
        print(f"BLOCK ({sys.argv[1]}):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"PASS ({sys.argv[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
