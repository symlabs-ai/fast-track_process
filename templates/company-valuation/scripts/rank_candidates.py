#!/usr/bin/env python3
"""Compare screening decisions from completed company-valuation cycles."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from validate_valuation import cell, validate_screening


def read_case(path: Path) -> dict:
    case_path = path / "docs/buyer-case.yml" if path.is_dir() else path
    with case_path.open(encoding="utf-8") as stream:
        case = yaml.safe_load(stream)
    if not isinstance(case, dict) or not isinstance(case.get("screening"), dict):
        raise ValueError(f"{case_path}: screening ausente")
    errors: list[str] = []
    validate_screening(case, errors)
    if errors:
        raise ValueError(f"{case_path}: " + "; ".join(errors))
    screen = case["screening"]
    return {
        "name": str(case.get("target_name") or (path.name if path.is_dir() else path.parent.parent.name)),
        "source": str(case_path),
        "score": screen["priority_score"],
        "band": screen["priority_band"],
        "decision": screen["decision"],
        "currency": case["currency"],
        "unit": case["unit"],
        "ev_low": screen.get("screening_ev_low"),
        "ev_high": screen.get("screening_ev_high"),
        "action": screen["near_term_action"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", type=Path, help="projetos ou arquivos buyer-case.yml")
    args = parser.parse_args()
    try:
        rows = [read_case(path) for path in args.cases]
    except (ValueError, OSError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    rows.sort(key=lambda row: (-row["score"], row["name"].casefold()))
    multiple = len(rows) > 1
    if multiple:
        print(f"Coleção fornecida: {len(rows)} casos. Ordem por nota; moedas e escalas próprias por caso.\n")
    prefix = "| Ordem " if multiple else ""
    print(prefix + "| Alvo | Classe | Nota | Decisão | EV de triagem (moeda / unidade) | Próximo passo | Origem |")
    print(("| ---: " if multiple else "") + "| --- | --- | ---: | --- | --- | --- | --- |")
    for rank, row in enumerate(rows, 1):
        ev = (
            f'{row["ev_low"]:g}–{row["ev_high"]:g}'
            if isinstance(row["ev_low"], (int, float)) and isinstance(row["ev_high"], (int, float))
            else "N/D"
        )
        values = [row["name"], row["band"], f'{row["score"]:g}/100', row["decision"],
                  f'{ev} ({row["currency"]} / {row["unit"]})', row["action"], row["source"]]
        if multiple:
            values.insert(0, str(rank))
        print("| " + " | ".join(cell(value) for value in values) + " |")


if __name__ == "__main__":
    main()
