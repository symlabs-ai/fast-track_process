"""Reusable, template-local expert prompts.

An expert is a Markdown file with YAML frontmatter stored under the selected
process bundle's ``experts/`` directory.  Keeping the definition beside the
process means copy-once materialization also pins the prompt used by a cycle.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

EXPERT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ExpertError(ValueError):
    """Raised when an expert definition is missing, unsafe, or malformed."""


@dataclass(frozen=True)
class Expert:
    """Validated expert identity and specialist prompt."""

    id: str
    name: str
    description: str
    version: str
    prompt: str
    path: Path
    digest: str
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


def validate_expert_id(value: object) -> str:
    """Return a safe canonical expert id suitable for a filename."""
    if not isinstance(value, str) or not EXPERT_ID_RE.fullmatch(value):
        raise ExpertError(f"id de expert inválido: {value!r}; use snake_case minúsculo")
    return value


def _frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ExpertError(f"expert sem frontmatter YAML: {path}")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ExpertError(f"frontmatter não encerrado em {path}") from exc

    try:
        raw = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        raise ExpertError(f"frontmatter inválido em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExpertError(f"frontmatter do expert deve ser mapping: {path}")

    prompt = "\n".join(lines[closing + 1 :]).strip()
    if not prompt:
        raise ExpertError(f"expert sem prompt após o frontmatter: {path}")
    return raw, prompt


def _required_text(metadata: dict[str, Any], field: str, path: Path) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ExpertError(
            f"frontmatter do expert exige {field} como texto não vazio: {path}"
        )
    return value.strip()


def load_expert(path: str | Path) -> Expert:
    """Parse and validate one expert Markdown file."""
    source = Path(path)
    if source.is_symlink():
        raise ExpertError(f"expert não pode ser link simbólico: {source}")
    if not source.is_file():
        raise ExpertError(f"expert não encontrado: {source}")

    try:
        content = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExpertError(f"não foi possível ler expert {source}: {exc}") from exc
    metadata, prompt = _frontmatter(content, source)

    expert_id = validate_expert_id(metadata.get("id"))
    if source.suffix != ".md" or source.stem != expert_id:
        raise ExpertError(
            f"id do expert deve coincidir com o arquivo {source.name!r}: "
            f"recebido {expert_id!r}"
        )
    name = _required_text(metadata, "name", source)
    description = _required_text(metadata, "description", source)

    raw_version = metadata.get("version")
    if isinstance(raw_version, bool) or not isinstance(raw_version, (int, str)):
        raise ExpertError(
            f"frontmatter do expert exige version inteira ou textual: {source}"
        )
    version = str(raw_version).strip()
    if not version or (isinstance(raw_version, int) and raw_version <= 0):
        raise ExpertError(f"version inválida no expert: {source}")

    raw_tags = metadata.get("tags", [])
    if not isinstance(raw_tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in raw_tags
    ):
        raise ExpertError(f"tags do expert devem ser uma lista de textos: {source}")
    tags = tuple(dict.fromkeys(tag.strip() for tag in raw_tags))

    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Expert(
        id=expert_id,
        name=name,
        description=description,
        version=version,
        prompt=prompt,
        path=source.resolve(),
        digest=digest,
        tags=tags,
        metadata=dict(metadata),
    )


def resolve_process_expert(
    process_file: str | Path,
    expert_id: object,
) -> Expert:
    """Resolve an expert strictly inside ``<process bundle>/experts``."""
    selected = validate_expert_id(expert_id)
    process_path = Path(process_file).resolve()
    experts_dir = process_path.parent / "experts"
    candidate = experts_dir / f"{selected}.md"

    if experts_dir.is_symlink() or candidate.is_symlink():
        raise ExpertError(
            f"catálogo de experts não pode conter links simbólicos: {candidate}"
        )
    try:
        candidate.resolve(strict=False).relative_to(experts_dir.resolve())
    except ValueError as exc:
        raise ExpertError(f"path inseguro para expert {selected!r}") from exc
    return load_expert(candidate)


def list_process_experts(process_file: str | Path) -> tuple[Expert, ...]:
    """Load the complete expert catalog bundled with one process."""
    process_path = Path(process_file).resolve()
    experts_dir = process_path.parent / "experts"
    if not experts_dir.exists():
        return ()
    if experts_dir.is_symlink() or not experts_dir.is_dir():
        raise ExpertError(f"catálogo de experts deve ser diretório real: {experts_dir}")

    experts: list[Expert] = []
    for candidate in sorted(experts_dir.iterdir()):
        if candidate.is_symlink():
            raise ExpertError(
                f"catálogo de experts não pode conter links simbólicos: {candidate}"
            )
        if candidate.is_file() and candidate.suffix == ".md":
            experts.append(load_expert(candidate))
    return tuple(experts)


def compose_expert_task(
    expert: Expert,
    task: str,
    *,
    runtime_context: str | None = None,
) -> str:
    """Combine specialist role and node task without expanding authority."""
    context_block = (
        f"\nCONTEXTO AUDITÁVEL DO NODE\n{runtime_context}\n" if runtime_context else ""
    )
    return f"""PERFIL DE EXPERT ATIVO
Id: {expert.id}
Nome: {expert.name}
Descrição: {expert.description}

INSTRUÇÕES DO EXPERT
{expert.prompt}
{context_block}

TAREFA DO NODE
{task}

PRECEDÊNCIA
O expert especializa como executar a tarefa, mas não amplia autoridade. Em
caso de conflito, prevalecem as regras de segurança da engine, o escopo de
escrita, a tarefa do node, seus contratos de saída e seus validadores.
"""
