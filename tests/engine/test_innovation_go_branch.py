"""Validação integral do ramo GO do processo innovation.

Cinco ciclos reais terminaram refuted antes do business case, deixando o ramo
business_case → go_nogo → prd → end sem cobertura de execução. Estes testes
exercitam a maquinaria real (graph do template, gates determinísticos,
validate_innovation.py, bypass de human gate e bypass_reject_when) com o LLM
substituído por fixtures — separando "a maquinaria funciona" de "alguma ideia
real vai passar".
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

from ft.engine.delegate import DelegateResult
from ft.engine.layout import ensure_project_layout, register_project_process
from ft.engine.runner import StepRunner

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "innovation"

BUSINESS_CASE_GO = """---
recommendation: go
estimated_effort: M
horizon: 90 dias
---
# Business Case — Fixture

## Oportunidade
Problema validado (EV-M01).

## Alternativas
Concorrente X cobra mais caro (EV-C01); não fazer nada mantém a dor.

## Custos e Esforço
Estimativa M, premissas declaradas.

## Riscos
Dependência de terceiros com severidade média.

## Critérios de Sucesso
### SC-01 — Adoção piloto
- Métrica: usuários ativos semanais
- Alvo: 50 usuários por 4 semanas seguidas
- Prazo: 90 dias após o piloto iniciar

### SC-02 — Conversão paga
- Métrica: conversão free→premium entre ativos maduros
- Alvo: >= 4%
- Prazo: 120 dias após o piloto iniciar

## Recomendação
Go: a evidência sustenta com riscos declarados.
"""

BUSINESS_CASE_NO_GO = BUSINESS_CASE_GO.replace(
    "recommendation: go", "recommendation: no_go"
).replace("Go: a evidência sustenta", "No-go: a evidência não sustenta")

PRD = """# PRD — Fixture

## User Stories
### US-01 — Capturar dado
- AC-01: dado um input válido, o registro aparece na listagem

### US-02 — Assinar premium
- AC-02: usuário ativo consegue assinar e o estado premium persiste

## Rastreabilidade
| SC-* | AC-* |
|---|---|
| SC-01 | AC-01 |
| SC-02 | AC-02 |

## Fora do Escopo
Cobertura nacional e apps nativos.
"""

HANDOFF = """---
next_process: mvp-builder-fast
---
# Handoff — Fixture

Seed: PRD, restrições e test data. SC-01/SC-02 são os critérios do piloto.
Pesquisa de 2026-07-29; re-checar mercado se o delivery começar após 8 semanas.
"""

POST_MORTEM = """# Post-mortem — Fixture

## Por que morreu
Recomendação no_go do business case, ancorada em EV-M01.

## O que reativaria
Evidência nova de demanda paga.

## Aproveitável
Dossiê de pesquisa e âncoras de custo.
"""

EVIDENCE = """schema_version: 1
lens: market
research_date: 2026-07-29
claims:
  - id: EV-M01
    statement: Afirmação de exemplo
    source: https://example.com/fonte
    date: 2026-07-20
    confidence: medium
    supports: [H-01]
"""

HYPOTHESES = """## H-01 — Hipótese de exemplo sustentada
- Se verdadeira, esperamos encontrar: sinais X
- Se falsa, esperamos encontrar: sinais Y
"""

VALIDATION = """---
overall_verdict: supported
---
| H-* | verdict | evidências | racional |
|---|---|---|---|
| H-01 | supported | EV-M01 | evidência de exemplo |
"""


def _fake_delegate(scenario: str):
    """delegate_to_llm fake: escreve o fixture do node em vez de chamar LLM."""

    def fake(**kwargs):
        task = kwargs.get("task", "")
        root = Path(kwargs.get("project_root", "."))
        created: list[str] = []
        if "business-case.md" in task:
            content = BUSINESS_CASE_GO if scenario == "go" else BUSINESS_CASE_NO_GO
            (root / "docs/business-case.md").write_text(content, encoding="utf-8")
            created = ["docs/business-case.md"]
        elif "PRD.md" in task or "handoff" in task:
            (root / "docs/PRD.md").write_text(PRD, encoding="utf-8")
            (root / "docs/handoff.md").write_text(HANDOFF, encoding="utf-8")
            created = ["docs/PRD.md", "docs/handoff.md"]
        elif "post-mortem" in task:
            (root / "docs/post-mortem.md").write_text(POST_MORTEM, encoding="utf-8")
            created = ["docs/post-mortem.md"]
        return DelegateResult(
            success=True,
            output="DONE",
            files_created=created,
            files_modified=[],
        )

    return fake


def _runner(tmp_path: Path) -> StepRunner:
    root = tmp_path / "proj"
    root.mkdir()
    ensure_project_layout(
        root,
        defaults={"llm_engine": "claude", "llm_model": "opus", "llm_effort": "high"},
    )
    process_dir = root / ".ft" / "process" / "innovation"
    process_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE_DIR / "process.yml", process_dir / "process.yml")
    scripts_dir = process_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    shutil.copy(
        TEMPLATE_DIR / "scripts" / "validate_innovation.py",
        scripts_dir / "validate_innovation.py",
    )
    register_project_process(
        root,
        process_name="innovation",
        process_path=process_dir / "process.yml",
        template_id="innovation",
        entrypoint="run",
        set_default=True,
    )
    docs = root / "docs"
    (docs / "research").mkdir(parents=True, exist_ok=True)
    (docs / "hypotheses.md").write_text(HYPOTHESES, encoding="utf-8")
    (docs / "validation.md").write_text(VALIDATION, encoding="utf-8")
    (docs / "research-questions.md").write_text(
        "Não há perguntas pendentes.\n", encoding="utf-8"
    )
    for lens in ("market", "competitors", "feasibility"):
        (docs / "research" / f"{lens}-evidence.yml").write_text(
            EVIDENCE.replace("lens: market", f"lens: {lens}"), encoding="utf-8"
        )
        (docs / "research" / f"{lens}.md").write_text(
            "# Síntese\n" + ("conteúdo citando EV-M01. " * 30), encoding="utf-8"
        )

    runner = StepRunner(
        process_path=process_dir / "process.yml",
        state_path=root / "state" / "engine_state.yml",
        project_root=root,
    )
    runner.init_state()
    runner._bypass_human_gates = True
    # Posiciona o ciclo no início do ramo nunca exercitado.
    state = runner.state_mgr.load()
    state.current_node = "innovation.business_case"
    state.node_status = "ready"
    runner.state_mgr.save()
    return runner


def _drive(runner: StepRunner, max_steps: int = 12) -> list[str]:
    """Avança até end ou bloqueio, registrando o caminho percorrido."""
    visited: list[str] = []
    for _ in range(max_steps):
        state = runner.state_mgr.load()
        node = state.current_node
        if node is None:
            break
        visited.append(node)
        if runner.graph.get_node(node).type == "end":
            break
        runner.run(mode="step")
        new_state = runner.state_mgr.load()
        if new_state.current_node == node and new_state.node_status in (
            "blocked",
            "awaiting_approval",
        ):
            break
    return visited


def test_go_branch_reaches_end_with_prd_and_handoff(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with patch("ft.engine.runner.delegate_to_llm", _fake_delegate("go")):
        visited = _drive(runner)

    root = Path(runner.project_root)
    assert "innovation.business_case" in visited
    assert "innovation.go_nogo" in visited
    assert "innovation.prd" in visited
    assert "innovation.end" in visited, f"caminho: {visited}"
    assert "innovation.post_mortem" not in visited
    assert (root / "docs/PRD.md").is_file()
    assert (root / "docs/handoff.md").is_file()
    assert "SC-01" in (root / "docs/PRD.md").read_text(encoding="utf-8")


def test_no_go_recommendation_routes_bypass_to_post_mortem(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with patch("ft.engine.runner.delegate_to_llm", _fake_delegate("no_go")):
        visited = _drive(runner)

    root = Path(runner.project_root)
    assert "innovation.go_nogo" in visited
    assert "innovation.post_mortem" in visited, f"caminho: {visited}"
    assert "innovation.prd" not in visited
    assert "innovation.end" in visited, f"caminho: {visited}"
    assert (root / "docs/post-mortem.md").is_file()
    assert not (root / "docs/PRD.md").exists()
