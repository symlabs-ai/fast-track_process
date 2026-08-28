"""Deriva candidatos a melhoria de processo a partir do custo medido.

O `process_evolve` pede ao LLM que enxergue melhorias no próprio ciclo; esta
derivação faz o contrário: parte dos números que o trace já registrou e
descreve o que eles implicam, sem julgamento. Um achado daqui é reproduzível —
outra pessoa, com o mesmo relatório, chega ao mesmo item.

Nada aqui aplica mudança. A decisão de aplicar, promover ao catálogo global ou
descartar é sempre de quem conduz o ciclo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ft.engine.cycle_analysis import CycleAnalysis

# Limiares deliberadamente frouxos: a intenção é levantar o que MERECE olhar,
# não encher o relatório. Um ciclo saudável não deve produzir achado nenhum.
FIRST_PASS_FLOOR = 0.80
REWORK_CEILING = 0.25
LOOP_EXECUTION_FLOOR = 2


@dataclass
class ImprovementCandidate:
    """Um achado derivado de métrica, pronto para virar PI-NNN."""

    kind: str
    title: str
    rationale: str
    evidence: list[dict[str, str]] = field(default_factory=list)
    # Sugestão de classificação; quem decide é o operador.
    suggested_classification: str = "local"

    def as_record(self, improvement_id: str) -> dict:
        """Converte para o schema de docs/process-improvements.yml."""
        return {
            "id": improvement_id,
            "title": self.title,
            "rationale": self.rationale,
            "classification": self.suggested_classification,
            "evidence": list(self.evidence),
            "criteria": {
                # Derivação automática não pode afirmar critérios globais: quem
                # promove ao catálogo precisa verificá-los deliberadamente.
                "domain_independent": False,
                "no_product_identifiers": False,
                "configurable": False,
                "verified_in_cycle": False,
                "backward_compatible": False,
            },
            "change": {
                "applied_locally": False,
                "summary": "Derivado da telemetria do ciclo; nenhuma mudança aplicada.",
                "paths": [],
            },
        }


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def derive_candidates(analysis: CycleAnalysis) -> list[ImprovementCandidate]:
    """Traduz as métricas do ciclo em achados acionáveis, sem aplicar nada."""
    candidates: list[ImprovementCandidate] = []

    if not analysis.nodes:
        return candidates

    # 1. Loops caros: nodes que repetiram, com o custo que isso teve.
    for node in analysis.loops():
        if node.executions < LOOP_EXECUTION_FLOOR:
            continue
        rounds = analysis.on_fail_rounds.get(node.id)
        cost = (
            f"{node.output_tokens:,} tokens de saída"
            if node.output_tokens
            else f"{node.duration_ms / 1000:.0f}s"
        )
        detail = (
            f"{node.executions} execuções, {node.reexecutions} reexecução(ões), "
            f"{node.failures} resultado(s) de falha, {cost}"
        )
        if rounds:
            detail += f"; {rounds} rodada(s) de on_fail"
        candidates.append(
            ImprovementCandidate(
                kind="loop",
                title=f"Node {node.id} repetiu {node.executions}x no ciclo",
                rationale=(
                    "Reexecução custa uma chamada LLM inteira. Vale verificar se "
                    "o node pode falhar mais cedo num gate determinístico, se o "
                    "prompt define o contrato de saída com precisão, ou se o "
                    "escopo do node é grande demais para convergir de primeira."
                ),
                evidence=[{"source": f"trace:{node.id}", "detail": detail}],
            )
        )

    # 2. Camadas de validação que nunca reprovaram: custo que não compra risco.
    for node in analysis.silent_layers():
        cost = (
            f"{node.output_tokens:,} tokens de saída"
            if node.output_tokens
            else f"{node.duration_ms / 1000:.0f}s"
        )
        candidates.append(
            ImprovementCandidate(
                kind="silent_layer",
                title=f"Camada {node.id} não reprovou nada neste ciclo",
                rationale=(
                    "Uma camada de validação que só confirma o que outra já "
                    "garantiu é candidata a virar gate determinístico ou a sair. "
                    "Um único ciclo não prova redundância — confirme em outros "
                    "ciclos antes de remover."
                ),
                evidence=[
                    {
                        "source": f"trace:{node.id}",
                        "detail": f"{node.executions} execução(ões), 0 reprovações, {cost}",
                    }
                ],
            )
        )

    # 3. Saúde geral do ciclo.
    if analysis.first_pass_rate < FIRST_PASS_FLOOR:
        candidates.append(
            ImprovementCandidate(
                kind="first_pass",
                title=f"First-pass de {_pct(analysis.first_pass_rate)} neste ciclo",
                rationale=(
                    "Menos de "
                    f"{_pct(FIRST_PASS_FLOOR)} dos nodes passaram sem repetir. "
                    "Antes de mexer no processo, verifique se as repetições vieram "
                    "de defeito do produto ou de fricção da ferramenta: só a "
                    "segunda causa justifica mudar o processo."
                ),
                evidence=[
                    {
                        "source": "trace:resumo",
                        "detail": (
                            f"{analysis.total_executions} execuções, "
                            f"{analysis.total_reexecutions} reexecuções, "
                            f"retrabalho {_pct(analysis.rework_ratio)}"
                        ),
                    }
                ],
            )
        )
    elif analysis.rework_ratio > REWORK_CEILING:
        candidates.append(
            ImprovementCandidate(
                kind="rework",
                title=f"Retrabalho de {_pct(analysis.rework_ratio)} das execuções",
                rationale=(
                    "Boa parte do ciclo foi refazer trabalho já feito. Identifique "
                    "o node que concentra as repetições e trate a causa dele."
                ),
                evidence=[
                    {
                        "source": "trace:resumo",
                        "detail": (
                            f"{analysis.total_reexecutions} de "
                            f"{analysis.total_executions} execuções foram repetição"
                        ),
                    }
                ],
            )
        )

    return candidates


def next_improvement_ids(existing: list[str], count: int) -> list[str]:
    """Gera IDs PI-NNN sem colidir com os já registrados no ciclo."""
    used = set()
    for raw in existing:
        text = str(raw or "")
        if text.startswith("PI-") and text[3:].isdigit():
            used.add(int(text[3:]))
    ids: list[str] = []
    cursor = 1
    while len(ids) < count:
        while cursor in used:
            cursor += 1
        ids.append(f"PI-{cursor:03d}")
        used.add(cursor)
    return ids
