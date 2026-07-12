"""Live, provenance-aware LLM selection for active engine cycles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from ft.engine import paths
from ft.engine.layout import read_manifest


@dataclass(frozen=True)
class LLMSelection:
    """One atomic engine/model/effort snapshot for a delegated call."""

    engine: str
    model: str | None
    effort: str | None


def normalize_llm_effort(value: Any) -> str | None:
    """Normalize sentinels that select the provider's native default."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.lower() == "default":
        return None
    return normalized


@dataclass(frozen=True)
class LiveLLMSettings:
    """Resolve layered settings while retaining command-override provenance."""

    defaults_root: Path
    cycle_root: Path
    engine_override: str | None
    model_override: str | None
    effort_override_set: bool
    effort_override: str | None
    engine_fallback: str | None
    model_fallback: str | None
    effort_fallback: str | None

    @classmethod
    def from_inputs(
        cls,
        *,
        defaults_root: str | Path,
        cycle_root: str | Path,
        llm_engine: str | None,
        llm_model: str | None,
        llm_effort: str | None,
        engine_is_override: bool,
        model_is_override: bool,
        effort_is_override: bool,
    ) -> LiveLLMSettings:
        engine = llm_engine.lower().strip() if llm_engine else None
        model = llm_model.strip() if llm_model else None
        effort = normalize_llm_effort(llm_effort)
        return cls(
            defaults_root=Path(defaults_root).resolve(),
            cycle_root=Path(cycle_root).resolve(),
            engine_override=engine if engine_is_override else None,
            model_override=model if model_is_override else None,
            effort_override_set=effort_is_override,
            effort_override=effort if effort_is_override else None,
            engine_fallback=engine if not engine_is_override else None,
            model_fallback=model if not model_is_override else None,
            effort_fallback=effort if not effort_is_override else None,
        )

    @property
    def has_command_override(self) -> bool:
        return bool(
            self.engine_override
            or self.model_override
            or self.effort_override_set
        )

    def read_live_defaults(self) -> dict[str, Any]:
        manifest_path = paths.project_manifest(self.defaults_root)
        if not manifest_path.is_file():
            return {}
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"manifest de defaults LLM inválido: {manifest_path}") from exc
        if not isinstance(manifest, dict):
            raise ValueError(
                f"manifest de defaults LLM inválido: raiz deve ser mapping em {manifest_path}"
            )
        defaults = manifest.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ValueError(
                f"manifest de defaults LLM inválido: defaults deve ser mapping em {manifest_path}"
            )
        revision = manifest.get("llm_defaults_revision")
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool) or revision < 0
        ):
            raise ValueError(
                "manifest de defaults LLM inválido: "
                f"llm_defaults_revision deve ser inteiro >= 0 em {manifest_path}"
            )
        snapshot = dict(defaults)
        snapshot["__ft_llm_defaults_revision"] = revision
        return snapshot

    @staticmethod
    def defaults_digest(defaults: dict[str, Any]) -> str:
        relevant = {
            "llm_engine": str(defaults.get("llm_engine") or "").strip().lower() or None,
            "llm_model": str(defaults.get("llm_model") or "").strip() or None,
            "llm_effort": normalize_llm_effort(defaults.get("llm_effort")),
            "revision": defaults.get("__ft_llm_defaults_revision"),
        }
        payload = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def recorded_digest(self, state: Any | None) -> str | None:
        recorded = getattr(state, "llm_defaults_digest", None) if state is not None else None
        if recorded:
            return str(recorded)
        if state is None:
            return None
        if self.cycle_root != self.defaults_root:
            local_manifest = read_manifest(self.cycle_root)
            local_defaults = local_manifest.get("defaults", {})
            if isinstance(local_defaults, dict):
                local_snapshot = dict(local_defaults)
                local_snapshot["__ft_llm_defaults_revision"] = local_manifest.get(
                    "llm_defaults_revision"
                )
                return self.defaults_digest(local_snapshot)
        return self.defaults_digest(
            {
                "llm_engine": getattr(state, "llm_engine", None),
                "llm_model": getattr(state, "llm_model", None),
                "llm_effort": getattr(state, "llm_effort", None),
            }
        )

    def resolve(
        self,
        state: Any | None = None,
        node: Any | None = None,
        *,
        manifest_defaults: dict[str, Any] | None = None,
        manifest_is_active: bool | None = None,
    ) -> LLMSelection:
        defaults = self.read_live_defaults() if manifest_defaults is None else manifest_defaults
        current_digest = self.defaults_digest(defaults)
        if manifest_is_active is None:
            manifest_is_active = (
                state is None or current_digest != self.recorded_digest(state)
            )

        unset = object()
        engine = "claude"
        model: str | None = None
        effort: str | None = None

        def overlay(
            *,
            layer_engine: object = unset,
            layer_model: object = unset,
            layer_effort: object = unset,
        ) -> None:
            nonlocal engine, model, effort
            if layer_engine is not unset:
                next_engine = str(layer_engine or "").strip().lower() or "claude"
                if next_engine != engine:
                    engine, model, effort = next_engine, None, None
            if layer_model is not unset:
                next_model = str(layer_model or "").strip() or None
                if next_model != model:
                    model, effort = next_model, None
            if layer_effort is not unset:
                effort = normalize_llm_effort(layer_effort)

        env_engine = os.environ.get("FT_LLM_ENGINE")
        env_model = os.environ.get("FT_LLM_MODEL")
        env_effort = os.environ.get("FT_LLM_EFFORT")
        overlay(
            layer_engine=env_engine if env_engine else unset,
            layer_model=env_model if env_model else unset,
            layer_effort=env_effort if env_effort is not None else unset,
        )
        overlay(
            layer_engine=self.engine_fallback or unset,
            layer_model=self.model_fallback or unset,
            layer_effort=self.effort_fallback or unset,
        )
        state_engine = getattr(state, "llm_engine", None) if state is not None else None
        if state_engine:
            overlay(
                layer_engine=state_engine,
                layer_model=getattr(state, "llm_model", None),
                layer_effort=getattr(state, "llm_effort", None),
            )
        if manifest_is_active:
            manifest_engine = defaults.get("llm_engine", unset)
            manifest_model = defaults.get("llm_model", unset)
            complete = manifest_engine is not unset and manifest_model is not unset
            overlay(
                layer_engine=manifest_engine,
                layer_model=manifest_model,
                layer_effort=(
                    defaults["llm_effort"]
                    if "llm_effort" in defaults
                    else (None if complete else unset)
                ),
            )
        overlay(
            layer_engine=self.engine_override or unset,
            layer_model=self.model_override or unset,
            layer_effort=self.effort_override if self.effort_override_set else unset,
        )
        if node is not None:
            node_effort = getattr(node, "llm_effort", None)
            overlay(
                layer_engine=getattr(node, "llm_engine", None) or unset,
                layer_model=getattr(node, "llm_model", None) or unset,
                layer_effort=node_effort if node_effort is not None else unset,
            )
        return LLMSelection(engine, model, effort)
