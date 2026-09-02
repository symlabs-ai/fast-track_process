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
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Importar um módulo irmão gravaria `__pycache__` dentro do bundle, em
# `.ft/process/<template>/scripts/`. Esse bytecode aparece como arquivo
# novo na árvore e é contado como mudança de quem trabalha no produto.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_feature as vf  # noqa: E402

CHECK_TIMEOUT = 120

# Marcação no contrato que dispensa um AC do controle negativo: a garantia já
# valia na baseline, então exigir que o check reprove lá seria exigir que ele
# minta. Fica em `docs/feature.md` de propósito — é o artefato que o
# stakeholder lê no gate humano. Uma isenção escondida no próprio check seria
# o buraco que o controle existe para fechar.
NON_REGRESSION_RE = re.compile(
    r"\((?:n[ãa]o[- ]regress[ãa]o|non[- ]regression)\)", re.I
)


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
    root: Path,
    path: Path,
    interpreter: str | None = None,
    env: dict[str, str] | None = None,
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
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout após {CHECK_TIMEOUT}s"
    except OSError as exc:
        return False, f"falha ao executar: {exc}"
    saida = " ".join(done.stdout.split())[:200] or "(sem saída)"
    return done.returncode == 0, f"exit {done.returncode}: {saida}"


def _non_regression_acs(root: Path) -> set[str]:
    """Os AC que o contrato declara como garantia preexistente."""
    text = vf._read(root, "docs/feature.md")
    content = vf._section(
        text,
        ("Critérios de Aceite", "Criterios de Aceite", "Acceptance Criteria"),
    )
    marcados: set[str] = set()
    for line in content.splitlines():
        if NON_REGRESSION_RE.search(line):
            marcados.update(m.group(0).upper() for m in vf.AC_RE.finditer(line))
    return marcados


def _baseline_commit(root: Path) -> str:
    try:
        payload = vf._read_yaml(root, vf.RECEIPT_BASELINE_PATH)
    except vf.FeatureValidationError as exc:
        _fail(str(exc))
    commit = str(payload.get("baseline_commit") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        _fail(
            f"{vf.RECEIPT_BASELINE_PATH}: baseline_commit ausente ou inválido. "
            "Rode novamente `validate_feature.py prepare-receipt-baseline` a "
            "partir do estado pré-implementação."
        )
    return commit


def _negative_control(
    root: Path, checks: dict[str, Path], interpreter: str
) -> tuple[list[str], list[str]]:
    """Prova que cada check fala da entrega, e não de algo que já era verdade.

    A cobertura diz que existe um arquivo por AC; ela não diz que o arquivo
    prova alguma coisa. Um `sys.exit(0)` satisfaz a cobertura e atesta o nada.
    O teste determinístico disso é o controle negativo: o check de um AC novo
    tem que **reprovar** no commit congelado por `feature.receipt_baseline` —
    o estado em que a garantia ainda não valia — e passar depois. Se ele passa
    lá, ele não distingue o produto entregue do produto anterior.

    Limite que não escondo: o controle é permissivo. Um check pode reprovar na
    baseline pelo motivo errado — um ImportError porque o produto não está
    instalado naquela árvore, por exemplo — e ainda assim contar como
    reprovado. Isso enfraquece a evidência, mas nunca deixa passar um check
    oco: o oco passa em qualquer estado, inclusive na baseline.

    O outro limite é ambiental. A árvore da baseline é uma worktree do commit,
    mas o interpretador é o do projeto: com instalação editável estrita, o
    finder do setuptools pode resolver o import para o código NOVO mesmo com o
    `PYTHONPATH` apontando para a baseline. Quando isso acontece o check passa
    na baseline e é reportado aqui — a mensagem nomeia as duas causas
    possíveis e o comando para reproduzir, porque distinguir uma da outra
    exige olhar o produto, não adivinhar.
    """
    isentos = _non_regression_acs(root)
    alvos = [ac for ac in checks if ac not in isentos]
    if not alvos:
        return (
            [],
            [
                "Controle negativo: nenhum AC sujeito — o contrato declara "
                f"todos como não-regressão ({', '.join(sorted(isentos))})."
            ],
        )

    commit = _baseline_commit(root)
    try:
        _, _, product_root = vf._load_baseline(root)
    except vf.FeatureValidationError:
        product_root = "."

    temp_parent = tempfile.mkdtemp(prefix="ft-baseline-")
    work = Path(temp_parent) / "baseline"
    try:
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(work), commit],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=120,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            saida = getattr(exc, "output", "") or ""
            _fail(
                f"controle negativo: não consegui materializar a baseline "
                f"{commit[:12]}: {exc} {' '.join(saida.split())[:200]}"
            )

        # Os checks vêm da árvore atual: é justamente o código deles que está
        # sob teste. Só o produto volta ao estado anterior.
        destino = work / _checks_dir(root).relative_to(root)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(destino, ignore_errors=True)
        shutil.copytree(_checks_dir(root), destino)

        env = dict(os.environ)
        raiz_produto = work if product_root == "." else work / product_root
        env["PYTHONPATH"] = os.pathsep.join(
            [str(raiz_produto)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )

        passaram: list[str] = []
        linhas: list[str] = []
        for ac in alvos:
            relativo = checks[ac].relative_to(root)
            passou, evidencia = _run_check(work, work / relativo, interpreter, env=env)
            linhas.append(
                f"| {ac} | {'FALHOU (esperado)' if not passou else 'PASSOU'} | "
                f"`{relativo.as_posix()}` — {evidencia} |"
            )
            print(
                f"{ac}: baseline {'reprova (ok)' if not passou else 'APROVA (oco?)'}"
                f" — {evidencia}"
            )
            if passou:
                passaram.append(ac)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(work)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        shutil.rmtree(temp_parent, ignore_errors=True)

    relatorio = [
        "",
        "## Controle Negativo",
        "",
        f"Cada check reexecutado na baseline `{commit[:12]}`. Um check que "
        "passa lá não distingue a entrega do estado anterior.",
        "",
        "| AC | Baseline | Evidência |",
        "| --- | --- | --- |",
        *linhas,
    ]
    if isentos:
        relatorio += [
            "",
            "Isentos por declaração de não-regressão no contrato: "
            + ", ".join(sorted(isentos))
            + ".",
        ]
    return passaram, relatorio


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

    interpretador, origem = _check_interpreter(root)
    print(f"interpretador dos checks: {interpretador} — {origem}")
    hollow, controle = _negative_control(root, checks, interpretador)
    if hollow:
        _fail(
            "controle negativo reprovou — estes checks passam na baseline, "
            "logo não provam a entrega: "
            + ", ".join(f"{ac} (`{_rel(root, checks[ac])}`)" for ac in hollow)
            + ". Ou o check não exercita a garantia do AC, ou o produto novo "
            "vazou para a árvore da baseline (instalação editável estrita). "
            "Reproduza com `git worktree add --detach /tmp/baseline "
            f"{_baseline_commit(root)[:12]}`, copie os checks para lá e rode-os."
        )

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
    corpo += controle
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
                "dedicado, nenhum órfão; controle negativo verde na baseline"
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
