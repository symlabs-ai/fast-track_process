"""Universal input contract declared by runnable templates."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Callable

import yaml


class InputPolicyError(ValueError):
    """Raised for an invalid policy or ambiguous run input."""


class InputRequiredError(InputPolicyError):
    """Raised when a template requires input and none was supplied."""


@dataclass(frozen=True)
class PreparedInput:
    """Input staged inside the new run worktree."""

    destination: Path
    text: str
    source: str


def _safe_destination(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise InputPolicyError("input_policy.destination deve ser path não vazio")
    if "\\" in raw:
        raise InputPolicyError("input_policy.destination deve usar separadores POSIX")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise InputPolicyError(
            "input_policy.destination deve ser path relativo seguro"
        )
    if path.parts[0] in {".ft", ".git"}:
        raise InputPolicyError(
            "input_policy.destination não pode escrever metadados internos"
        )
    return path.as_posix()


@dataclass(frozen=True)
class InputPolicy:
    """How a template accepts ``--input`` or ``--request`` for one run."""

    required: bool = False
    destination: str | None = None
    prompt: str | None = None

    def __post_init__(self) -> None:
        # ``InputPolicy`` is part of the public API, so direct construction must
        # enforce the same path-safety contract as YAML parsing.
        if not isinstance(self.required, bool):
            raise InputPolicyError("input_policy.required deve ser boolean")
        destination = _safe_destination(self.destination)
        if self.prompt is not None and (
            not isinstance(self.prompt, str) or not self.prompt.strip()
        ):
            raise InputPolicyError("input_policy.prompt deve ser string não vazia")
        prompt = self.prompt.strip() if isinstance(self.prompt, str) else None
        if self.required and destination is None:
            raise InputPolicyError("input_policy.required exige destination")
        if self.required and prompt is None:
            raise InputPolicyError("input_policy.required exige prompt")
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "prompt", prompt)

    @classmethod
    def from_mapping(cls, mapping: object) -> "InputPolicy":
        if mapping is None:
            return cls()
        if not isinstance(mapping, dict):
            raise InputPolicyError("input_policy deve ser mapping")
        unknown = set(mapping) - {"required", "destination", "prompt"}
        if unknown:
            fields = ", ".join(sorted(str(field) for field in unknown))
            raise InputPolicyError(f"campos desconhecidos em input_policy: {fields}")
        required = mapping.get("required", False)
        if not isinstance(required, bool):
            raise InputPolicyError("input_policy.required deve ser boolean")
        destination = mapping.get("destination")
        raw_prompt = mapping.get("prompt")
        if raw_prompt is not None and (
            not isinstance(raw_prompt, str) or not raw_prompt.strip()
        ):
            raise InputPolicyError("input_policy.prompt deve ser string não vazia")
        return cls(required=required, destination=destination, prompt=raw_prompt)

    def acquire(
        self,
        *,
        request: str | None = None,
        input_file: str | Path | None = None,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> tuple[str, str] | None:
        """Resolve exactly one input source without writing project files."""
        if request is not None and input_file is not None:
            raise InputPolicyError("informe --input ou --request, não ambos")

        if input_file is not None:
            source_path = Path(input_file)
            if not source_path.is_file():
                raise InputPolicyError(f"arquivo de input ausente: {source_path}")
            try:
                text = source_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise InputPolicyError(
                    f"não foi possível ler input {source_path}: {exc}"
                ) from exc
            source = str(source_path)
        elif request is not None:
            if not isinstance(request, str):
                raise InputPolicyError("--request deve ser texto")
            text = request
            source = "request"
        elif self.required:
            if prompt_fn is None:
                raise InputRequiredError("este template exige --input ou --request")
            text = prompt_fn(self.prompt or "Descreva a demanda")
            source = "interactive"
        else:
            return None

        if not text.strip():
            raise InputRequiredError("a entrada do template não pode ser vazia")
        if self.destination is None:
            raise InputPolicyError(
                "template recebeu input, mas não declara input_policy.destination"
            )
        return text, source

    def stage(
        self,
        worktree: str | Path,
        *,
        request: str | None = None,
        input_file: str | Path | None = None,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> PreparedInput | None:
        """Atomically write selected input only inside a run worktree."""
        acquired = self.acquire(
            request=request,
            input_file=input_file,
            prompt_fn=prompt_fn,
        )
        if acquired is None:
            return None
        text, source = acquired
        assert self.destination is not None

        root = Path(worktree).resolve()
        raw_root = Path(worktree)
        if raw_root.is_symlink() or not root.is_dir():
            raise InputPolicyError(f"worktree inválida: {worktree}")
        target = root.joinpath(*PurePosixPath(self.destination).parts)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise InputPolicyError("destino de input escapa da worktree") from exc

        current = target.parent
        while current != root:
            if current.is_symlink():
                raise InputPolicyError(
                    f"destino de input atravessa link simbólico: {current}"
                )
            parent = current.parent
            if parent == current:
                break
            current = parent
        if target.is_symlink():
            raise InputPolicyError(
                f"destino de input não pode ser link simbólico: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        original_mode = (
            stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        )
        temporary: Path | None = None
        try:
            fd, raw_temporary = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(raw_temporary)
            os.fchmod(fd, original_mode)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return PreparedInput(destination=target, text=text, source=source)


def load_input_policy(process_file: str | Path) -> InputPolicy:
    """Parse ``input_policy`` from one process graph."""
    path = Path(process_file)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise InputPolicyError(f"processo inválido em {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InputPolicyError(f"processo inválido em {path}: raiz deve ser mapping")
    return InputPolicy.from_mapping(payload.get("input_policy"))
