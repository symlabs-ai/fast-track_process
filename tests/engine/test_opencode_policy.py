"""Regression tests for provider-neutral OpenCode execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ft.engine.delegate import DelegateResult
from ft.engine.runner import StepRunner
from ft.providers.opencode_policy import opencode_deny_edit_tools_enabled


def _runner(tmp_path: Path) -> StepRunner:
    process = tmp_path / "process.yml"
    process.write_text(
        """\
id: neutral-opencode
version: "1.0.0"
title: Neutral OpenCode
nodes:
  - id: ft.tdd.01.red
    type: test_red
    title: Testes RED
    executor: llm_coder
    description: Escreva testes RED para uma calculadora de órbitas lunares.
    outputs:
      - project/tests/test_orbits.py
    next: end
  - id: end
    type: end
    title: End
""",
        encoding="utf-8",
    )
    runner = StepRunner(
        process_path=process,
        state_path=tmp_path / "state" / "engine_state.yml",
        project_root=tmp_path,
        llm_engine="opencode",
    )
    runner.init_state()
    return runner


def test_former_direct_bundle_node_delegates_the_project_task(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    node = runner.graph.get_node("ft.tdd.01.red")

    with (
        patch.dict(
            "os.environ",
            {
                "FT_OPENCODE_COMPACT_BUNDLES": "1",
                "FT_OPENCODE_DETERMINISTIC_FALLBACKS": "1",
            },
        ),
        patch("ft.engine.runner.delegate_to_llm") as delegate,
    ):
        delegate.return_value = DelegateResult(True, "DONE", [], [])
        runner._run_llm_step(node)

    assert delegate.call_count == 1
    prompt = delegate.call_args.kwargs["task"]
    assert "calculadora de órbitas lunares" in prompt
    for forbidden in ("create_client", "list_clients", "Neon Stack", "ServiceMate"):
        assert forbidden not in prompt
    assert runner.state_mgr.load().current_node == "end"


def test_engine_package_contains_no_product_specific_opencode_generator() -> None:
    package = Path(__file__).resolve().parents[2] / "ft"
    forbidden = (
        "Neon Stack",
        "ServiceMate",
        "arena-board",
        "create_client",
        "list_clients",
        "total_pendente",
    )
    offenders: dict[str, list[str]] = {}
    for source in package.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        matches = [term for term in forbidden if term in text]
        if matches:
            offenders[source.relative_to(package).as_posix()] = matches
    assert offenders == {}
    assert not (package / "providers" / "opencode_fallbacks.py").exists()


def test_opencode_tool_policy_is_transport_only_and_overridable(monkeypatch) -> None:
    monkeypatch.delenv("FT_OPENCODE_DENY_EDIT_TOOLS", raising=False)
    assert opencode_deny_edit_tools_enabled() is True
    monkeypatch.setenv("FT_OPENCODE_DENY_EDIT_TOOLS", "0")
    assert opencode_deny_edit_tools_enabled() is False
