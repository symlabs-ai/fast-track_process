#!/usr/bin/env python3
"""Compare screening decisions from completed company-valuation cycles."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def read_case(path: Path) -> dict:
    case_path = path / "docs/buyer-case.yml" if path.is_dir() else path
    with case_path.open(encoding="utf-8") as stream:
        case = yaml.safe_load(stream)
    if not isinstance(case, dict) or not isinstance(case.get("screening"), dict):
        raise ValueError(f"{case_path}: screening ausente")
    screen = case["screening"]
    score = screen.get("priority_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError(f"{case_path}: priority_score inválido")
    decision = screen.get("decision")
    if decision not in {"priority", "selective_shortlist", "pass"}:
        raise ValueError(f"{case_path}: decision inválida")
    return {
        "name": str(case.get("target_name") or (path.name if path.is_dir() else path.parent.parent.name)),
        "score": score,
        "decision": decision,
        "ev_low": screen.get("screening_ev_low"),
        "ev_high": screen.get("screening_ev_high"),
        "action": str(screen.get("near_term_action", "")).split(".")[0].strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", type=Path, help="projetos ou arquivos buyer-case.yml")
    args = parser.parse_args()
    rows = [read_case(path) for path in args.cases]
    rows.sort(key=lambda row: (-row["score"], row["name"].casefold()))
    print("| Ordem | Alvo | Nota | Decisão | EV de triagem (R$ mi) | Próximo passo |")
    print("| ---: | --- | ---: | --- | --- | --- |")
    for rank, row in enumerate(rows, 1):
        ev = (
            f'{row["ev_low"]:g}–{row["ev_high"]:g}'
            if isinstance(row["ev_low"], (int, float)) and isinstance(row["ev_high"], (int, float))
            else "N/D"
        )
        action = row["action"].replace("|", "/")
        print(f'| {rank} | {row["name"]} | {row["score"]:g}/100 | {row["decision"]} | {ev} | {action} |')


if __name__ == "__main__":
    main()
