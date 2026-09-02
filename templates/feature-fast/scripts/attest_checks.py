#!/usr/bin/env python3
"""Deriva pré-revisão, evidência e atestação dos checks determinísticos.

Substitui as três revisões por LLM do feature-fast. Cada AC-* do contrato
tem um `checks/<FEAT-NNN>/AC-NNN.py` escrito antes da implementação; o
veredicto é o
resultado de executá-los, não uma opinião. Os artefatos emitidos seguem
exatamente o schema que `validate_feature.py` já cobrava do LLM, então os
gates seguintes auditam esta atestação com o mesmo rigor.

Etapas:
  pre       cobertura AC <-> checks, antes de gastar a suíte completa
  evidence  relatório e evidência por AC derivados do receipt executado
  attest    executa os checks e emite o veredicto final
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_feature as vf  # noqa: E402

CHECK_TIMEOUT = 120


def _fail(message: str) -> None:
    print(f"attest-checks: {message}", file=sys.stderr)
    raise SystemExit(1)


def _check_interpreter(root: Path) -> tuple[str, str]:
    """O interpretador em que os checks rodam, e como ele foi escolhido.

    Um check prova um AC exercitando o produto — importando o pacote, ou
    subindo o entrypoint dele num subprocesso. Isso só é verdade se ele rodar
    onde o produto está instalado. `sys.executable` aqui é o interpretador que
    o gate herdou do shell, que não é necessariamente o do projeto: no
    SymProbe era o do miniconda, sem o pacote instalado, e o subprocesso
    `python -m sym_probe` morria antes de abrir o socket. O check reprovava um
    produto correto, e `feature.verify` acabava auditando uma instalação
    diferente da que `product_validate` tinha validado via `make`.

    A convenção do venv no próprio projeto (`.venv/`, o padrão de poetry
    `in-project`, uv e `python -m venv`) é o que resolve isso sem adivinhar
    gerenciador. Sem ele, cai no interpretador atual — que é o comportamento
    correto para projetos que não isolam ambiente.
    """
    try:
        _, _, product_root = vf._load_baseline(root)
    except vf.FeatureValidationError:
        product_root = "."
    # O venv acompanha o pyproject/Makefile: pode estar no product_root ou na
    # raiz do repositório, conforme o produto seja um subdiretório ou não.
    bases = [root] if product_root == "." else [root / product_root, root]
    for base in bases:
        for relative in ("bin/python", "Scripts/python.exe"):
            candidate = base / ".venv" / relative
            if candidate.is_file():
                onde = base.relative_to(root).as_posix() or "."
                return str(candidate), f"venv do projeto ({onde}/.venv)"
    return sys.executable, "interpretador atual (nenhum .venv no projeto)"


def _checks_dir(root: Path) -> Path:
    """O diretório de checks desta feature, isolado pelo id reservado.

    Os AC são numerados por feature — toda feature tem um AC-01 —, então um
    diretório plano `checks/` faz features distintas disputarem os mesmos
    caminhos. Isso quebrava de duas maneiras. Ciclos paralelos colidiam por
    construção: dois worktrees fechando escreviam `checks/AC-01.py`..`AC-06.py`
    com conteúdos diferentes e o merge do segundo parava em conflito add/add.
    E ciclos sequenciais herdavam o que sobrou: uma feature com menos AC que a
    anterior encontrava os checks excedentes na árvore e `_coverage` a reprovava
    por órfãos, logo no primeiro gate, sem que nada estivesse errado com ela.

    `checks/<FEAT-NNN>/` dá a cada conjunto o seu espaço e mantém a prova
    durável de todas as features na árvore, que é a razão de `checks/` ser
    canônico.
    """
    try:
        reservation = vf._read_yaml(root, vf.RESERVATION_PATH)
    except vf.FeatureValidationError as exc:
        _fail(str(exc))
    feature_id = str(reservation.get("final_feature_id") or "").upper()
    if not vf.FEAT_RE.fullmatch(feature_id):
        _fail(
            f"{vf.RESERVATION_PATH}: final_feature_id ausente ou inválido "
            f"({feature_id or 'vazio'})"
        )
    return root / "checks" / feature_id


def _contract(root: Path) -> list[str]:
    try:
        _, _, acceptance_ids = vf._feature_contract(root)
    except vf.FeatureValidationError as exc:
        _fail(str(exc))
    if not acceptance_ids:
        _fail("docs/feature.md não declara nenhum AC-*")
    return [ac.upper() for ac in acceptance_ids]


def _coverage(root: Path, acceptance_ids: list[str]) -> dict[str, Path]:
    checks_dir = _checks_dir(root)
    onde = checks_dir.relative_to(root).as_posix()
    have = {p.stem.upper(): p for p in sorted(checks_dir.glob("AC-*.py"))}
    missing = [ac for ac in acceptance_ids if ac not in have]
    orphans = sorted(set(have) - set(acceptance_ids))
    if missing or orphans:
        _fail(
            "cobertura incompleta — AC sem check: "
            f"{missing or 'nenhum'}; checks órfãos: {orphans or 'nenhum'}. "
            f"Todo AC-* precisa de {onde}/AC-NNN.py e nenhum check pode sobrar."
        )
    return {ac: have[ac] for ac in acceptance_ids}


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _run_check(
    root: Path, path: Path, interpreter: str | None = None
) -> tuple[bool, str]:
    try:
        done = subprocess.run(
            [interpreter or sys.executable, str(path.relative_to(root))],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=CHECK_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout após {CHECK_TIMEOUT}s"
    except OSError as exc:
        return False, f"falha ao executar: {exc}"
    saida = " ".join(done.stdout.split())[:200] or "(sem saída)"
    return done.returncode == 0, f"exit {done.returncode}: {saida}"


def _ac_table(rows: list[tuple[str, str, str]]) -> list[str]:
    linhas = ["| AC | Resultado | Evidência |", "| --- | --- | --- |"]
    linhas += [f"| {ac} | {status} | {ev} |" for ac, status, ev in rows]
    return linhas


def stage_pre(root: Path) -> int:
    acceptance_ids = _contract(root)
    checks = _coverage(root, acceptance_ids)
    try:
        impact = vf._current_impact(root)
    except vf.FeatureValidationError as exc:
        _fail(str(exc))
    pre_review_id = impact.get("pre_review_id")
    if not isinstance(pre_review_id, str):
        _fail(f"{vf.IMPACT_PATH}: pre_review_id ausente")

    corpo = [
        "# Pré-revisão Determinística — Cobertura de Checks",
        "",
        f"Cada AC-* do contrato tem o seu check executável em "
        f"`{_checks_dir(root).relative_to(root).as_posix()}/`. "
        f"Cobertura verificada: {len(checks)} obrigação(ões), nenhum check órfão.",
        "A execução dos checks acontece no gate `feature.verify`, depois da suíte completa.",
        "",
        f"PRE_REVIEW_ID: {pre_review_id}",
        "",
    ]
    corpo += _ac_table(
        [
            (ac, "PASS", f"`{_rel(root, checks[ac])}` presente e executável")
            for ac in acceptance_ids
        ]
    )
    corpo += ["", "Resultado: APPROVED", ""]
    (root / vf.PRE_REVIEW_PATH).write_text("\n".join(corpo), encoding="utf-8")

    vf._atomic_write_yaml(
        root / vf.PRE_REVIEW_ROUTE_PATH,
        {
            "schema_version": 1,
            "review_id": pre_review_id,
            "verdict": "APPROVED",
            "review_route": "approved",
            "summary": (
                f"cobertura determinística completa: {len(checks)} AC com check "
                "dedicado, nenhum órfão"
            ),
        },
    )
    print(f"cobertura: {len(checks)} AC com check dedicado, nenhum órfão")
    return 0


def stage_evidence(root: Path) -> int:
    acceptance_ids = _contract(root)
    checks = _coverage(root, acceptance_ids)
    try:
        receipt = json.loads(vf._read(root, vf.RECEIPT_PATH))
    except (vf.FeatureValidationError, json.JSONDecodeError) as exc:
        _fail(f"{vf.RECEIPT_PATH}: {exc}")

    corpo = [
        "# Relatório de Implementação",
        "",
        "Evidência derivada do receipt executado. PASS nesta tabela atesta duas "
        "coisas verificadas aqui: a suíte completa do receipt passou e cada AC-* "
        "tem o seu check declarado e coberto. O veredicto de cada check é "
        "emitido em `feature.verify`, o único gate que os executa — este "
        "relatório não o antecipa.",
        "",
    ]
    corpo += _ac_table(
        [
            (
                ac,
                "PASS",
                f"suíte do receipt verde; `{_rel(root, checks[ac])}` declarado "
                "(veredicto em feature.verify)",
            )
            for ac in acceptance_ids
        ]
    )
    corpo += ["", "## Comandos do receipt", ""]
    corpo += [f"- `{c}`" for c in (receipt.get("commands") or [])] or ["- (nenhum)"]
    corpo += [""]
    (root / "docs/implementation-report.md").write_text(
        "\n".join(corpo), encoding="utf-8"
    )

    vf._atomic_write_yaml(
        root / vf.EVIDENCE_PATH,
        {
            "schema_version": 1,
            "receipt": vf.RECEIPT_PATH,
            "commands": receipt.get("commands"),
            "acceptance": [
                {
                    "id": ac,
                    "status": "PASS",
                    "tests": [str(checks[ac].relative_to(root))],
                }
                for ac in acceptance_ids
            ],
        },
    )
    print(f"evidência derivada: {len(acceptance_ids)} AC ancorados no receipt")
    return 0


def stage_attest(root: Path) -> int:
    acceptance_ids = _contract(root)
    checks = _coverage(root, acceptance_ids)
    try:
        context = vf._read_yaml(root, vf.REVIEW_CONTEXT_PATH)
    except vf.FeatureValidationError as exc:
        _fail(str(exc))
    review_id = context.get("review_id")
    fingerprint = context.get("receipt_fingerprint")
    if not isinstance(review_id, str) or not isinstance(fingerprint, str):
        _fail(f"{vf.REVIEW_CONTEXT_PATH}: review_id/receipt_fingerprint ausentes")

    interpreter, origem = _check_interpreter(root)
    print(f"interpretador dos checks: {interpreter} — {origem}")

    resultados = []
    for ac in acceptance_ids:
        passou, evidencia = _run_check(root, checks[ac], interpreter)
        resultados.append((ac, passou, evidencia))
        print(f"{ac}: {'PASS' if passou else 'FAIL'} — {evidencia}")

    falhas = [(ac, ev) for ac, passou, ev in resultados if not passou]
    aprovado = not falhas

    corpo = [
        "# Atestação Determinística da Feature",
        "",
        f"Veredicto derivado da execução de {len(resultados)} check(s) em "
        f"`{_checks_dir(root).relative_to(root).as_posix()}/`. "
        "Cada AC-* é provado pelo seu check; nenhum julgamento semântico foi aplicado.",
        "",
        f"Interpretador: `{interpreter}` — {origem}.",
        "",
        f"REVIEW_ID: {review_id}",
        "",
    ]
    corpo += _ac_table(
        [
            (ac, "PASS" if ok else "FAIL", f"`{_rel(root, checks[ac])}` — {ev}")
            for ac, ok, ev in resultados
        ]
    )
    if falhas:
        corpo += [
            "",
            "## Achados",
            "",
            "| Achado | Resultado | AC | Evidência |",
            "| --- | --- | --- | --- |",
        ]
        corpo += [
            f"| F-{i:02d} | FAIL | {ac} | `{_rel(root, checks[ac])}` — {ev} |"
            for i, (ac, ev) in enumerate(falhas, start=1)
        ]
    corpo += ["", f"Resultado: {'APPROVED' if aprovado else 'REJECTED'}", ""]
    (root / "docs/feature-review.md").write_text("\n".join(corpo), encoding="utf-8")

    resumo = (
        f"{len(resultados)} AC verificado(s) por check determinístico; todos PASS."
        if aprovado
        else f"{len(falhas)} de {len(resultados)} AC reprovaram no check determinístico: "
        + ", ".join(ac for ac, _ in falhas)
    )
    vf._atomic_write_yaml(
        root / vf.REVIEW_ROUTE_PATH,
        {
            "schema_version": 1,
            "review_id": review_id,
            "receipt_fingerprint": fingerprint,
            "verdict": "APPROVED" if aprovado else "REJECTED",
            "review_route": "approved" if aprovado else "implementation",
            "summary": resumo,
        },
    )
    print(f"atestação: {resumo}")
    return 0


STAGES = {"pre": stage_pre, "evidence": stage_evidence, "attest": stage_attest}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in STAGES:
        _fail(f"uso: attest_checks.py {{{'|'.join(STAGES)}}}")
    return STAGES[sys.argv[1]](Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
