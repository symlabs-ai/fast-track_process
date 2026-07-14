from __future__ import annotations

from pathlib import Path

import pytest

from ft.templates import (
    InputPolicy,
    InputPolicyError,
    InputRequiredError,
    load_input_policy,
)


def test_required_policy_accepts_request_file_or_interactive_input(tmp_path: Path) -> None:
    policy = InputPolicy.from_mapping(
        {
            "required": True,
            "destination": "docs/feature-request.md",
            "prompt": "Descreva a feature",
        }
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    request = policy.stage(worktree, request="Busca por telefone\n")
    assert request is not None
    assert request.source == "request"
    assert request.destination == worktree / "docs/feature-request.md"
    assert request.destination.read_text(encoding="utf-8") == "Busca por telefone\n"

    source = tmp_path / "bug.md"
    source.write_text("Corrigir duplicação\n", encoding="utf-8")
    from_file = policy.stage(worktree, input_file=source)
    assert from_file is not None
    assert from_file.source == str(source)
    assert from_file.destination.read_text(encoding="utf-8") == "Corrigir duplicação\n"

    seen: list[str] = []
    interactive = policy.stage(
        worktree,
        prompt_fn=lambda prompt: seen.append(prompt) or "Ajustar botão",
    )
    assert interactive is not None
    assert interactive.source == "interactive"
    assert seen == ["Descreva a feature"]


def test_input_sources_are_exclusive_and_required_input_cannot_be_empty(tmp_path: Path) -> None:
    policy = InputPolicy(
        required=True,
        destination="docs/request.md",
        prompt="Demanda",
    )
    source = tmp_path / "request.md"
    source.write_text("arquivo\n", encoding="utf-8")

    with pytest.raises(InputPolicyError, match="não ambos"):
        policy.acquire(request="texto", input_file=source)
    with pytest.raises(InputRequiredError, match="exige"):
        policy.acquire()
    with pytest.raises(InputRequiredError, match="não pode ser vazia"):
        policy.acquire(request="  \n")
    with pytest.raises(InputRequiredError, match="não pode ser vazia"):
        policy.acquire(prompt_fn=lambda _prompt: "")


def test_optional_policy_without_input_does_nothing(tmp_path: Path) -> None:
    policy = InputPolicy.from_mapping(
        {
            "required": False,
            "destination": "docs/demanda.md",
            "prompt": "Demanda",
        }
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    assert policy.stage(worktree) is None
    assert not (worktree / "docs").exists()


@pytest.mark.parametrize(
    "mapping, message",
    [
        ({"required": "yes"}, "boolean"),
        ({"required": True, "destination": "docs/request.md"}, "exige prompt"),
        ({"required": True, "prompt": "x"}, "exige destination"),
        ({"destination": "/tmp/request"}, "relativo seguro"),
        ({"destination": "../request"}, "relativo seguro"),
        ({"destination": ".ft/state.yml"}, "metadados internos"),
        ({"destination": "docs\\request.md"}, "separadores POSIX"),
        ({"extra": True}, "campos desconhecidos"),
    ],
)
def test_invalid_policies_fail_closed(mapping: dict, message: str) -> None:
    with pytest.raises(InputPolicyError, match=message):
        InputPolicy.from_mapping(mapping)


def test_stage_rejects_symlinked_destination(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (worktree / "docs").symlink_to(outside, target_is_directory=True)
    policy = InputPolicy(
        required=True,
        destination="docs/request.md",
        prompt="Demanda",
    )

    with pytest.raises(InputPolicyError, match="link simbólico"):
        policy.stage(worktree, request="não escapar")

    assert not (outside / "request.md").exists()


def test_direct_policy_construction_cannot_bypass_destination_validation() -> None:
    with pytest.raises(InputPolicyError, match="relativo seguro"):
        InputPolicy(
            required=True,
            destination="../../outside.md",
            prompt="Demanda",
        )


def test_load_input_policy_from_process(tmp_path: Path) -> None:
    process = tmp_path / "process.yml"
    process.write_text(
        """id: feature
input_policy:
  required: true
  destination: docs/feature-request.md
  prompt: Descreva a feature
nodes: []
""",
        encoding="utf-8",
    )

    policy = load_input_policy(process)

    assert policy.required is True
    assert policy.destination == "docs/feature-request.md"
    assert policy.prompt == "Descreva a feature"
