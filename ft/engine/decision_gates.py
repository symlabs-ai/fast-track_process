"""Human-decision context assembled from process metadata and live state.

A human gate is useful only when the stakeholder can understand the decision,
inspect the relevant product/evidence, and predict the effect of each answer.
This module builds that packet deterministically. Templates may enrich it with
``decision_context``; older/local process forks still receive a complete
fallback assembled from the graph and existing artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from ft.engine.graph import Node, ProcessGraph


@dataclass(frozen=True)
class DecisionGateContext:
    """Context required to make an informed approve/reject decision."""

    decision: str
    why_now: str
    review_paths: tuple[str, ...]
    checklist: tuple[str, ...]
    limitations: tuple[str, ...]
    approve_effect: str
    reject_effect: str


_PATH_KEYS = frozenset(
    {
        "backlog_path",
        "criteria_path",
        "evidence_root",
        "features_path",
        "matrix_path",
        "path",
        "report_path",
        "source_dir",
        "test_identity_path",
    }
)
_PRODUCT_ACCEPTANCE_REVIEW_PATHS = (
    "docs/stakeholder-review-guide.md",
    "docs/PRD.md",
    "docs/acceptance-report.md",
    "docs/visual-check-report.md",
    "docs/platform-validation-report.yml",
    "docs/fiscal-fidelity-report.md",
    "docs/native-cli-validation.md",
)
_PRODUCT_ACCEPTANCE_CHECKLIST = (
    "Abra o produto pela entrada apresentada para o target selecionado "
    "(URL para web, aplicativo/artefato para desktop ou app instalado no "
    "dispositivo) e complete o fluxo P0 usando somente menus e controles "
    "visíveis.",
    "Compare o comportamento e a aparência observados com o PRD, os critérios "
    "de UI e os relatórios de aceite e revisão visual.",
    "Confirme no relatório de plataforma que cada target obrigatório está em "
    "PASS e que não existe finding aberto.",
    "Não aprove se houver bug reproduzível, capacidade P0 órfã, evidência "
    "inacessível ou limitação relevante não declarada.",
)


def _configured_text(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _configured_list(config: Mapping[str, Any], key: str) -> list[str]:
    value = config.get(key)
    if not isinstance(value, list):
        return []
    return [
        normalized
        for item in value
        if isinstance(item, str) and (normalized := " ".join(item.split()))
    ]


def _description_checklist(description: str | None) -> list[str]:
    if not description:
        return [
            "Examine o produto e as evidências listadas antes de decidir.",
            "Confirme que o resultado observado corresponde ao escopo deste gate.",
        ]
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", description.strip())
        if sentence.strip()
    ]
    return sentences or [description.strip()]


def _candidate_path(root: Path, raw: str) -> Path | None:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not resolved.exists():
        return None
    return resolved


def _validator_path_values(value: object, key: str | None = None) -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            paths.extend(_validator_path_values(child, str(child_key)))
    elif isinstance(value, list):
        for child in value:
            paths.extend(_validator_path_values(child, key))
    elif isinstance(value, str) and key is not None:
        if key in _PATH_KEYS or key.endswith("_path") or key.endswith("_dir"):
            paths.append(value)
    return paths


def _recent_artifact_paths(
    graph: ProcessGraph,
    node: Node,
    completed_nodes: list[str],
) -> list[str]:
    candidates: list[str] = list(node.outputs)
    # Recent completed nodes are the best deterministic approximation of the
    # evidence chain that led to this gate. Inspect enough nodes to cross pure
    # validator gates and reach the build/review that produced the receipt.
    for node_id in reversed(completed_nodes[-8:]):
        previous = graph.nodes.get(node_id)
        if previous is None:
            continue
        candidates.extend(previous.outputs)
        candidates.extend(_validator_path_values(previous.validators))
    return candidates


def _review_paths(
    root: Path,
    priority_paths: list[str],
    derived_paths: list[str],
    *,
    limit: int = 8,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[Path] = set()

    def append(raw: str) -> None:
        resolved = _candidate_path(root, raw)
        if resolved is None or resolved in seen:
            return
        seen.add(resolved)
        result.append(str(resolved))

    for raw in priority_paths:
        append(raw)
        if len(result) >= limit:
            return tuple(result)
    # Concrete reports are more useful to a decision-maker than broad source
    # or evidence directories. Keep directories as a last-resort fallback.
    resolved_derived = [
        resolved
        for raw in derived_paths
        if (resolved := _candidate_path(root, raw)) is not None
    ]
    for resolved in [
        *[path for path in resolved_derived if path.is_file()],
        *[path for path in resolved_derived if path.is_dir()],
    ]:
        append(str(resolved))
        if len(result) >= limit:
            break
    return tuple(result)


def _is_product_acceptance_gate(node: Node) -> bool:
    label = f"{node.title} {node.description or ''}".casefold()
    return any(
        marker in label
        for marker in (
            "aceite final",
            "produto validado",
            "stakeholder",
            "validação final",
            "validacao final",
        )
    )


def _target_label(graph: ProcessGraph, node_id: str | None, fallback: str) -> str:
    if not node_id:
        return fallback
    target = graph.nodes.get(node_id)
    if target is None:
        return node_id
    return f"{target.title} ({target.id})"


def build_decision_gate_context(
    *,
    graph: ProcessGraph,
    node: Node,
    state: object,
    project_root: str | Path,
) -> DecisionGateContext:
    """Build a complete decision packet for any human gate.

    Missing optional template metadata never collapses the packet: every field
    has a useful graph/state-derived fallback, which keeps old materialized
    process forks understandable after an engine upgrade.
    """

    raw_config = node.decision_context
    config: Mapping[str, Any] = raw_config if isinstance(raw_config, Mapping) else {}
    completed = getattr(state, "completed_nodes", [])
    completed_nodes = [str(value) for value in completed] if isinstance(completed, list) else []

    last_completed = graph.nodes.get(completed_nodes[-1]) if completed_nodes else None
    next_label = _target_label(graph, node.next, "a próxima etapa")
    reject_label = _target_label(graph, node.reject_next, "a correção/revisão do gate")

    decision = _configured_text(config, "decision") or (
        f"Decidir se “{node.title}” pode avançar após revisar o produto "
        "e as evidências abaixo."
    )
    why_now = _configured_text(config, "why_now")
    if why_now is None:
        previous = (
            f"A etapa “{last_completed.title}” foi concluída. "
            if last_completed is not None
            else "As etapas anteriores chegaram a este checkpoint. "
        )
        why_now = (
            f"{previous}O processo está pausado antes de {next_label} e só "
            "avança com uma decisão humana explícita."
        )

    product_acceptance = _is_product_acceptance_gate(node)
    configured_checklist = _configured_list(config, "checklist")
    checklist = configured_checklist or (
        list(_PRODUCT_ACCEPTANCE_CHECKLIST)
        if product_acceptance
        else _description_checklist(node.description)
    )
    reproducible_feedback = (
        "Se encontrar problema, rejeite informando onde ocorreu, os passos, "
        "o resultado esperado e o observado."
    )
    if reproducible_feedback not in checklist:
        checklist.append(reproducible_feedback)

    limitations = _configured_list(config, "limitations") or [
        "A aprovação cobre somente o escopo e o candidato apresentados neste gate.",
        "Evidência ausente, inacessível ou ambígua é motivo para não aprovar.",
    ]

    approve_effect = _configured_text(config, "approve_effect") or (
        f"Registra a aprovação e avança para {next_label}."
    )
    reject_effect = _configured_text(config, "reject_effect") or (
        f"Registra o motivo e encaminha o ciclo para {reject_label}; o gate deve "
        "ser apresentado novamente depois da correção."
    )

    root = Path(project_root).resolve()
    priority_paths = _configured_list(config, "review_paths")
    if product_acceptance:
        priority_paths.extend(_PRODUCT_ACCEPTANCE_REVIEW_PATHS)
    review_paths = _review_paths(
        root,
        priority_paths,
        _recent_artifact_paths(graph, node, completed_nodes),
    )

    return DecisionGateContext(
        decision=decision,
        why_now=why_now,
        review_paths=review_paths,
        checklist=tuple(checklist),
        limitations=tuple(limitations),
        approve_effect=approve_effect,
        reject_effect=reject_effect,
    )
