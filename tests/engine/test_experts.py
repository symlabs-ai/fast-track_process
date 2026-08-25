from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ft.cli import main as cli_main
from ft.engine.experts import (
    ExpertError,
    compose_expert_task,
    load_expert,
    resolve_process_expert,
)
from ft.engine.graph import load_graph
from ft.engine.process_validator import validate_process
from ft.engine.runner import build_task_prompt
from ft.engine.validators.artifacts import expert_review_report_valid
from ft.project import bootstrap_project

EXPERT = """---
id: code_reviewer
name: Code Reviewer
description: Revisa código com foco em regressões.
version: 1
tags: [engineering, quality]
---
Comece pelo diff real e reporte apenas findings verificáveis.
"""


def _write_expert(bundle: Path, content: str = EXPERT) -> Path:
    target = bundle / "experts" / "code_reviewer.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _write_process(bundle: Path, *, expert: str = "code_reviewer") -> Path:
    process = bundle / "process.yml"
    process.write_text(
        "id: example\n"
        "version: 1.0.0\n"
        "nodes:\n"
        "  - id: review\n"
        "    type: review\n"
        "    title: Revisar\n"
        "    executor: codex\n"
        f"    expert: {expert}\n"
        "    outputs: [docs/review.md]\n"
        "    next: end\n"
        "  - id: end\n"
        "    type: end\n"
        "    title: Fim\n",
        encoding="utf-8",
    )
    return process


def test_load_expert_parses_frontmatter_body_and_digest(tmp_path):
    source = _write_expert(tmp_path)

    expert = load_expert(source)

    assert expert.id == "code_reviewer"
    assert expert.name == "Code Reviewer"
    assert expert.version == "1"
    assert expert.tags == ("engineering", "quality")
    assert "diff real" in expert.prompt
    assert expert.digest.startswith("sha256:")


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("code_reviewer.md", "sem frontmatter", "sem frontmatter"),
        (
            "other.md",
            EXPERT,
            "deve coincidir com o arquivo",
        ),
        (
            "code_reviewer.md",
            EXPERT.replace("version: 1", "version: false"),
            "exige version",
        ),
        (
            "code_reviewer.md",
            EXPERT.replace(
                "Comece pelo diff real e reporte apenas findings verificáveis.",
                "",
            ),
            "sem prompt",
        ),
    ],
)
def test_load_expert_rejects_invalid_contract(tmp_path, filename, content, message):
    source = tmp_path / filename
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ExpertError, match=message):
        load_expert(source)


def test_resolve_process_expert_is_bundle_local(tmp_path):
    process = tmp_path / "process.yml"
    process.write_text("nodes: []\n", encoding="utf-8")
    source = _write_expert(tmp_path)

    assert resolve_process_expert(process, "code_reviewer").path == source.resolve()

    with pytest.raises(ExpertError, match="snake_case"):
        resolve_process_expert(process, "../code_reviewer")


def test_graph_resolves_expert_and_prompt_composition(tmp_path):
    _write_expert(tmp_path)
    process = _write_process(tmp_path)

    graph = load_graph(process)
    node = graph.get_node("review")
    prompt = build_task_prompt(node, {})

    assert node.expert == "code_reviewer"
    assert node.expert_definition is not None
    assert "PERFIL DE EXPERT ATIVO" in prompt
    assert "Comece pelo diff real" in prompt
    assert "TAREFA DO NODE" in prompt
    assert "EXPERT REVIEW: Revisar" in prompt
    assert validate_process(graph).passed


def test_graph_fails_closed_when_expert_is_missing(tmp_path):
    process = _write_process(tmp_path)

    with pytest.raises(ExpertError, match="não encontrado"):
        load_graph(process)


def test_expert_cannot_be_attached_to_non_llm_node(tmp_path):
    _write_expert(tmp_path)
    process = _write_process(tmp_path)
    content = process.read_text(encoding="utf-8").replace(
        "executor: codex", "executor: python"
    )
    process.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="não possui executor LLM"):
        load_graph(process)


def test_compose_expert_task_keeps_engine_precedence(tmp_path):
    expert = load_expert(_write_expert(tmp_path))

    prompt = compose_expert_task(expert, "Gere docs/review.md")

    assert "não amplia autoridade" in prompt
    assert "escopo de\nescrita" in prompt
    assert prompt.index("INSTRUÇÕES DO EXPERT") < prompt.index("TAREFA DO NODE")
    assert "Digest:" not in prompt
    assert "Versão:" not in prompt


def test_review_expert_receives_exact_cycle_baseline(tmp_path):
    _write_expert(tmp_path)
    process = _write_process(tmp_path)
    node = load_graph(process).get_node("review")
    baseline = "a" * 40

    prompt = build_task_prompt(node, {"base_commit": baseline})

    assert "CONTEXTO AUDITÁVEL DO NODE" in prompt
    assert f"Baseline exata do ciclo: {baseline}" in prompt
    assert f"git diff --name-status {baseline} --" in prompt


def test_review_expert_refuses_to_invent_missing_baseline(tmp_path):
    _write_expert(tmp_path)
    process = _write_process(tmp_path)
    node = load_graph(process).get_node("review")

    prompt = build_task_prompt(node, {})

    assert "Baseline exata do ciclo: INDISPONÍVEL" in prompt
    assert "Não invente um hash" in prompt


def _review_report(verdict: str) -> str:
    findings = (
        "Nenhum finding bloqueante."
        if verdict == "APPROVED"
        else "F-001: comportamento diverge do critério AC-01."
        if verdict == "REJECTED"
        else "Nenhum finding conclusivo; revisão interrompida."
    )
    baseline = "b" * 40 if verdict != "BLOCKED" else "INDISPONÍVEL"
    limitation = (
        "Risco residual baixo; integração externa não aplicável."
        if verdict != "BLOCKED"
        else "Evidência de execução ausente porque o toolchain está indisponível."
    )
    return f"""## Veredito

VERDICT: {verdict}

## Baseline e escopo

Baseline: `{baseline}`. Escopo: `src/app.py` e AC-01.

## Findings bloqueantes

{findings}

## Evidências executadas

- Fonte: `src/app.py:10`; resultado observado: PASS na inspeção; relação com o PRD: AC-01.

## Limitações e riscos residuais

{limitation}

## Notas não bloqueantes

Nenhuma.
"""


@pytest.mark.parametrize("verdict", ["APPROVED", "REJECTED", "BLOCKED"])
def test_expert_review_report_accepts_complete_contract(tmp_path, verdict):
    report = tmp_path / "review.md"
    report.write_text(_review_report(verdict), encoding="utf-8")

    passed, detail = expert_review_report_valid("review.md", project_root=tmp_path)

    assert passed, detail
    assert f"verdict={verdict}" in detail


def test_expert_review_report_rejects_approval_without_evidence(tmp_path):
    report = tmp_path / "review.md"
    content = _review_report("APPROVED").replace(
        "- Fonte: `src/app.py:10`; resultado observado: PASS na inspeção; relação com o PRD: AC-01.",
        "Nenhuma evidência.",
    )
    report.write_text(content, encoding="utf-8")

    passed, detail = expert_review_report_valid("review.md", project_root=tmp_path)

    assert not passed
    assert "evidencias devem registrar" in detail


def test_expert_review_report_rejects_approval_with_blocking_finding(tmp_path):
    report = tmp_path / "review.md"
    content = _review_report("APPROVED").replace(
        "Nenhum finding bloqueante.",
        "F-001: perda de dados reproduzida.",
    )
    report.write_text(content, encoding="utf-8")

    passed, detail = expert_review_report_valid("review.md", project_root=tmp_path)

    assert not passed
    assert "Nenhum finding bloqueante" in detail


def test_experts_cli_lists_global_template_catalog(
    tmp_path,
    monkeypatch,
    capsys,
):
    project = tmp_path / "project"
    bootstrap_project(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        sys,
        "argv",
        ["ft", "experts", "--template", "mvp-builder-fast", "--json"],
    )

    cli_main.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["template"] == "mvp-builder-fast"
    assert payload["origin"] == "global"
    assert {expert["id"] for expert in payload["experts"]} == {
        "prototype_png_designer",
    }
