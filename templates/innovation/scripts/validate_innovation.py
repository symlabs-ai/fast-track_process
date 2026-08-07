#!/usr/bin/env python3
"""Validações determinísticas do processo innovation.

Uso: validate_innovation.py <stage>
Stages: intake | research | validation | business_case | prd | post_mortem

Cada stage lê artefatos em docs/ (CWD do run), verifica condições binárias e
sai com 0 (PASS) ou 1 (BLOCK) imprimindo os erros. Nenhum stage julga mérito —
apenas estrutura, rastreabilidade e auditabilidade.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

DOCS = Path("docs")
RESEARCH = DOCS / "research"
LENSES = ("market", "competitors", "feasibility")
CONFIDENCE_LEVELS = {"high", "medium", "low"}
VERDICTS = {"supported", "refuted", "inconclusive"}


def read_frontmatter(path: Path) -> tuple[dict, str]:
    """Retorna (frontmatter, texto completo). Frontmatter vazio se ausente/inválido."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, text
    return (data if isinstance(data, dict) else {}), text


def hypothesis_ids() -> list[str]:
    path = DOCS / "hypotheses.md"
    if not path.exists():
        return []
    return sorted(set(re.findall(r"\bH-\d{2}\b", path.read_text(encoding="utf-8"))))


def load_evidence(lens: str, errors: list[str]) -> list[dict]:
    path = RESEARCH / f"{lens}-evidence.yml"
    if not path.exists():
        errors.append(f"{path}: arquivo ausente")
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{path}: YAML inválido ({exc})")
        return []
    if not isinstance(data, dict):
        errors.append(f"{path}: raiz deve ser um mapping")
        return []
    if data.get("schema_version") != 1:
        errors.append(f"{path}: schema_version deve ser 1")
    if data.get("lens") != lens:
        errors.append(f"{path}: lens deve ser '{lens}'")
    research_date = str(data.get("research_date", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", research_date):
        errors.append(f"{path}: research_date deve ser YYYY-MM-DD")
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append(f"{path}: claims deve ser lista não vazia")
        return []
    return claims


def check_claims(lens: str, claims: list[dict], known_h: list[str], errors: list[str]) -> None:
    path = RESEARCH / f"{lens}-evidence.yml"
    seen_ids: set[str] = set()
    for i, claim in enumerate(claims):
        label = f"{path} claim[{i}]"
        if not isinstance(claim, dict):
            errors.append(f"{label}: deve ser mapping")
            continue
        cid = str(claim.get("id", ""))
        if not re.fullmatch(r"EV-[A-Z]\d{2,}", cid):
            errors.append(f"{label}: id '{cid}' fora do padrão EV-<letra><NN>")
        elif cid in seen_ids:
            errors.append(f"{label}: id duplicado '{cid}'")
        else:
            seen_ids.add(cid)
        if not str(claim.get("statement", "")).strip():
            errors.append(f"{label}: statement vazio")
        source = str(claim.get("source", ""))
        if not re.match(r"https?://\S+$", source):
            errors.append(f"{label}: source deve ser URL (http/https), obtido '{source or '<vazio>'}'")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(claim.get("date", ""))):
            errors.append(f"{label}: date deve ser YYYY-MM-DD")
        if claim.get("confidence") not in CONFIDENCE_LEVELS:
            errors.append(f"{label}: confidence deve ser high|medium|low")
        supports = claim.get("supports")
        if not isinstance(supports, list) or not supports:
            errors.append(f"{label}: supports deve ser lista não vazia de H-*")
        else:
            for h in supports:
                if str(h) not in known_h:
                    errors.append(f"{label}: supports referencia '{h}' inexistente em hypotheses.md")


def all_evidence_ids() -> set[str]:
    ids: set[str] = set()
    for lens in LENSES:
        path = RESEARCH / f"{lens}-evidence.yml"
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for claim in data.get("claims") or []:
            if isinstance(claim, dict) and claim.get("id"):
                ids.add(str(claim["id"]))
    return ids


def success_criteria_ids() -> list[str]:
    path = DOCS / "business-case.md"
    if not path.exists():
        return []
    return sorted(set(re.findall(r"\bSC-\d{2}\b", path.read_text(encoding="utf-8"))))


# --- stages -----------------------------------------------------------------


def stage_intake() -> list[str]:
    errors: list[str] = []
    fm, _ = read_frontmatter(DOCS / "idea.md")
    if fm.get("kind") not in {"product", "process", "mixed"}:
        errors.append("docs/idea.md: frontmatter kind deve ser product|process|mixed")
    if not str(fm.get("summary", "")).strip():
        errors.append("docs/idea.md: frontmatter summary vazio")
    hypotheses = hypothesis_ids()
    if not hypotheses:
        errors.append("docs/hypotheses.md: nenhuma hipótese H-NN encontrada")
    text = (DOCS / "hypotheses.md").read_text(encoding="utf-8") if (DOCS / "hypotheses.md").exists() else ""
    for h in hypotheses:
        if not re.search(rf"^##\s+{h}\s+—", text, re.MULTILINE):
            errors.append(f"docs/hypotheses.md: {h} sem heading '## {h} — <afirmação>'")
    return errors


def stage_research() -> list[str]:
    errors: list[str] = []
    known_h = hypothesis_ids()
    if not known_h:
        return ["docs/hypotheses.md: ausente ou sem hipóteses — rode o intake antes"]
    supported_h: set[str] = set()
    for lens in LENSES:
        md = RESEARCH / f"{lens}.md"
        if not md.exists() or len(md.read_text(encoding="utf-8").strip()) < 200:
            errors.append(f"{md}: ausente ou curto demais para ser uma síntese real")
        claims = load_evidence(lens, errors)
        check_claims(lens, claims, known_h, errors)
        for claim in claims:
            if isinstance(claim, dict):
                for h in claim.get("supports") or []:
                    supported_h.add(str(h))
    uncovered = [h for h in known_h if h not in supported_h]
    if uncovered:
        errors.append(
            "hipóteses sem nenhum claim associado: "
            + ", ".join(uncovered)
            + " — se não há evidência, registre isso no .md e marque a lacuna,"
            " mas ao menos um claim (mesmo contrário ou low) deve tocá-las"
        )
    return errors


def stage_validation() -> list[str]:
    errors: list[str] = []
    fm, text = read_frontmatter(DOCS / "validation.md")
    if fm.get("overall_verdict") not in VERDICTS:
        errors.append("docs/validation.md: overall_verdict deve ser supported|refuted|inconclusive")
    known_h = hypothesis_ids()
    known_ev = all_evidence_ids()
    for h in known_h:
        row = re.search(rf"^\|\s*{h}\s*\|(.+)$", text, re.MULTILINE)
        if not row:
            errors.append(f"docs/validation.md: sem linha de tabela para {h}")
            continue
        rest = row.group(1)
        verdict = next((v for v in VERDICTS if re.search(rf"\b{v}\b", rest)), None)
        if not verdict:
            errors.append(f"docs/validation.md: linha de {h} sem verdict supported|refuted|inconclusive")
        cited = re.findall(r"\bEV-[A-Z]\d{2,}\b", rest)
        if verdict in {"supported", "refuted"} and not cited:
            errors.append(f"docs/validation.md: {h} com verdict '{verdict}' mas sem EV-* citado")
        for ev in cited:
            if ev not in known_ev:
                errors.append(f"docs/validation.md: {h} cita '{ev}' inexistente nos evidence.yml")
    questions = DOCS / "research-questions.md"
    if fm.get("overall_verdict") == "inconclusive":
        qtext = questions.read_text(encoding="utf-8") if questions.exists() else ""
        if not re.search(r"^\s*(\d+)[.)]\s+\S", qtext, re.MULTILINE):
            errors.append("docs/research-questions.md: verdict inconclusive exige perguntas numeradas")
    return errors


def stage_business_case() -> list[str]:
    errors: list[str] = []
    fm, text = read_frontmatter(DOCS / "business-case.md")
    if fm.get("recommendation") not in {"go", "no_go"}:
        errors.append("docs/business-case.md: recommendation deve ser go|no_go")
    if str(fm.get("estimated_effort", "")).strip() not in {"P", "M", "G"}:
        errors.append("docs/business-case.md: estimated_effort deve ser P|M|G")
    if not str(fm.get("horizon", "")).strip():
        errors.append("docs/business-case.md: horizon vazio")
    scs = success_criteria_ids()
    if not scs:
        errors.append("docs/business-case.md: nenhum critério SC-NN encontrado")
    for sc in scs:
        block = re.search(rf"^###\s+{sc}\b(.*?)(?=^###\s|\Z)", text, re.MULTILINE | re.DOTALL)
        if not block:
            errors.append(f"docs/business-case.md: {sc} sem bloco '### {sc} — ...'")
            continue
        body = block.group(1)
        for field in ("Métrica", "Alvo", "Prazo"):
            if not re.search(rf"-\s*{field}:\s*\S", body):
                errors.append(f"docs/business-case.md: {sc} sem campo '{field}:' preenchido")
    return errors


def stage_prd() -> list[str]:
    errors: list[str] = []
    prd_path = DOCS / "PRD.md"
    if not prd_path.exists():
        return ["docs/PRD.md: ausente"]
    prd = prd_path.read_text(encoding="utf-8")
    if not re.search(r"\bUS-\d{2}\b", prd):
        errors.append("docs/PRD.md: nenhuma user story US-NN encontrada")
    if not re.search(r"\bAC-\d{2}\b", prd):
        errors.append("docs/PRD.md: nenhum critério de aceite AC-NN encontrado")
    scs = success_criteria_ids()
    if not scs:
        errors.append("docs/business-case.md: sem SC-* — o PRD não tem o que rastrear")
    for sc in scs:
        row = re.search(rf"^\|\s*{sc}\s*\|(.+)$", prd, re.MULTILINE)
        if not row or not re.search(r"\bAC-\d{2}\b", row.group(1)):
            errors.append(f"docs/PRD.md: Rastreabilidade sem linha '{sc} | AC-*' — todo SC exige ao menos um AC")

    handoff_path = DOCS / "handoff.md"
    handoff_fm, handoff = read_frontmatter(handoff_path)
    next_process = handoff_fm.get("next_process")
    delivery_process = handoff_fm.get("delivery_process")
    process_sequence = handoff_fm.get("process_sequence")
    valid_sequences = {
        ("mdd", "mvp-builder-fast"): ["mdd", "mvp-builder-fast"],
        ("feature-fast", "feature-fast"): ["feature-fast"],
    }
    expected_sequence = valid_sequences.get((next_process, delivery_process))
    if expected_sequence is None:
        errors.append(
            "docs/handoff.md: use next_process/delivery_process "
            "mdd→mvp-builder-fast para produto novo ou feature-fast→feature-fast "
            "para produto entregue"
        )
    elif process_sequence != expected_sequence:
        errors.append(
            "docs/handoff.md: process_sequence deve ser "
            + str(expected_sequence)
        )
    if handoff_fm.get("delivery_readiness") != "planning_required":
        errors.append("docs/handoff.md: delivery_readiness deve ser planning_required")
    if not isinstance(handoff_fm.get("implementation_authorized"), bool):
        errors.append("docs/handoff.md: implementation_authorized deve ser true|false")
    for section in ("Estado para o delivery", "Inventário durável"):
        if not re.search(rf"^##\s+{re.escape(section)}\s*$", handoff, re.MULTILINE):
            errors.append(f"docs/handoff.md: seção '## {section}' ausente")
    for required in (
        "docs/PROJECT_BACKLOG.md",
        ".ft/project.yml",
        ".ft/cycles/<cycle-id>/",
        "building",
        "BLOCKED",
        "process_sequence",
    ):
        if required not in handoff:
            errors.append(f"docs/handoff.md: Estado/Inventário deve citar '{required}'")
    if "docs/research/" in handoff:
        errors.append(
            "docs/handoff.md: não publique docs/research/ como path durável; "
            "use .ft/cycles/<cycle-id>/research/..."
        )
    return errors


def stage_post_mortem() -> list[str]:
    errors: list[str] = []
    path = DOCS / "post-mortem.md"
    if not path.exists():
        return ["docs/post-mortem.md: ausente"]
    text = path.read_text(encoding="utf-8")
    known_ev = all_evidence_ids()
    cited = set(re.findall(r"\bEV-[A-Z]\d{2,}\b", text))
    if known_ev and not cited:
        errors.append("docs/post-mortem.md: 'Por que morreu' deve citar ao menos um EV-* real")
    for ev in cited:
        if ev not in known_ev:
            errors.append(f"docs/post-mortem.md: cita '{ev}' inexistente nos evidence.yml")
    return errors


STAGES = {
    "intake": stage_intake,
    "research": stage_research,
    "validation": stage_validation,
    "business_case": stage_business_case,
    "prd": stage_prd,
    "post_mortem": stage_post_mortem,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in STAGES:
        print(f"uso: validate_innovation.py <{'|'.join(STAGES)}>")
        return 2
    errors = STAGES[sys.argv[1]]()
    if errors:
        print(f"BLOCK ({sys.argv[1]}): {len(errors)} problema(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"PASS ({sys.argv[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
