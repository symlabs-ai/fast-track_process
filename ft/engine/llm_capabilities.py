"""Discover model and reasoning capabilities from installed LLM CLIs.

The discovery is intentionally read-only and uncached.  Consumers such as the
FT UI can call :func:`discover_llm_capabilities` on every load and receive a
JSON-serialisable snapshot of what the locally installed CLIs advertise.

No capability is guessed when a command fails or its output cannot be parsed:
that agent is returned as unavailable with an empty model list and a structured
error (fail closed).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 5.0
MAX_DISCOVERY_TIMEOUT_SECONDS = 10.0
MAX_DISCOVERY_OUTPUT_CHARS = 5_000_000

_AGENTS: tuple[tuple[str, str, tuple[str, ...], Callable[[str], dict[str, object]]], ...] = (
    ("claude", "Claude", ("claude", "--help"), lambda output: _parse_claude_help(output)),
    ("codex", "Codex", ("codex", "debug", "models"), lambda output: _parse_codex_catalog(output)),
    (
        "opencode",
        "OpenCode",
        ("opencode", "models", "--verbose"),
        lambda output: _parse_opencode_models(output),
    ),
)

_OPTION_LINE = re.compile(r"^\s*(?:-[A-Za-z],\s*)?--(?P<name>[A-Za-z][\w-]*)\b")
_SIMPLE_CHOICE_LIST = re.compile(
    r"\((?:choices?:\s*)?(?P<choices>[a-z][\w-]*(?:\s*,\s*[a-z][\w-]*)+)\)",
    re.IGNORECASE,
)
_CLAUDE_MODEL_ID = re.compile(r"\bclaude-[a-z0-9][a-z0-9.-]*(?:-[a-z0-9][a-z0-9.-]*)*\b", re.IGNORECASE)
_QUOTED_VALUE = re.compile(r"['\"]([a-z0-9][a-z0-9._/-]*)['\"]", re.IGNORECASE)


def discover_llm_capabilities(
    *,
    timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    cwd: str | Path | None = None,
) -> dict[str, object]:
    """Probe every supported CLI and return a fresh, JSON-ready snapshot.

    The three probes run concurrently so the total load time is bounded by the
    slowest CLI rather than the sum of their timeouts.  ``timeout_seconds`` is
    applied separately by ``subprocess.run`` to each command and capped to keep
    a UI request bounded even if a caller supplies an excessive value.
    """

    timeout = _bounded_timeout(timeout_seconds)
    working_directory = str(cwd) if cwd is not None else None
    agents_by_id: dict[str, dict[str, object]] = {}

    with ThreadPoolExecutor(max_workers=len(_AGENTS), thread_name_prefix="ft-capabilities") as executor:
        futures = {
            executor.submit(
                _probe_agent,
                agent_id,
                label,
                command,
                parser,
                timeout,
                working_directory,
            ): agent_id
            for agent_id, label, command, parser in _AGENTS
        }
        for future in as_completed(futures):
            agent_id = futures[future]
            try:
                agents_by_id[agent_id] = future.result()
            except Exception as exc:  # pragma: no cover - final containment boundary
                agents_by_id[agent_id] = _unavailable_agent(
                    agent_id,
                    _agent_label(agent_id),
                    "probe_error",
                    f"Capability probe failed: {type(exc).__name__}",
                )

    agents = [agents_by_id[agent_id] for agent_id, *_ in _AGENTS]
    top_level_errors: list[dict[str, str]] = []
    model_defaults: dict[str, str | None] = {}
    effort_defaults: dict[str, dict[str, str | None]] = {}

    for agent in agents:
        agent_id = str(agent["id"])
        default_model = agent.get("default_model")
        model_defaults[agent_id] = str(default_model) if default_model is not None else None
        effort_defaults[agent_id] = {
            str(model["id"]): (
                str(model["default_effort"])
                if model.get("default_effort") is not None
                else None
            )
            for model in agent.get("models", [])
            if isinstance(model, dict) and model.get("id")
        }
        for error in agent.get("errors", []):
            if isinstance(error, dict):
                top_level_errors.append({"agent": agent_id, **{str(k): str(v) for k, v in error.items()}})

    return {
        "source": "real_provider_probe",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "available": any(bool(agent.get("available")) for agent in agents),
        "agents": agents,
        "defaults": {
            "agent": None,
            "models": model_defaults,
            "efforts": effort_defaults,
        },
        "errors": top_level_errors,
    }


def _bounded_timeout(value: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_DISCOVERY_TIMEOUT_SECONDS
    if timeout <= 0:
        timeout = DEFAULT_DISCOVERY_TIMEOUT_SECONDS
    return min(timeout, MAX_DISCOVERY_TIMEOUT_SECONDS)


def _agent_label(agent_id: str) -> str:
    for known_id, label, *_ in _AGENTS:
        if known_id == agent_id:
            return label
    return agent_id


def _probe_agent(
    agent_id: str,
    label: str,
    command: tuple[str, ...],
    parser: Callable[[str], dict[str, object]],
    timeout: float,
    cwd: str | None,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError:
        return _unavailable_agent(agent_id, label, "not_installed", f"{command[0]} CLI is not installed")
    except subprocess.TimeoutExpired:
        return _unavailable_agent(
            agent_id,
            label,
            "timeout",
            f"{' '.join(command)} exceeded the {timeout:g}s discovery timeout",
        )
    except OSError as exc:
        return _unavailable_agent(
            agent_id,
            label,
            "execution_error",
            f"Could not execute {command[0]}: {type(exc).__name__}",
        )

    if completed.returncode != 0:
        detail = _safe_error_detail(completed.stderr or completed.stdout)
        message = f"{' '.join(command)} exited with status {completed.returncode}"
        if detail:
            message = f"{message}: {detail}"
        return _unavailable_agent(agent_id, label, "command_failed", message)

    # Help output may be emitted to stderr by some CLI frameworks.  Catalog
    # commands must remain parseable even when harmless notices use stderr.
    output = completed.stdout
    if agent_id == "claude" and completed.stderr:
        output = f"{output}\n{completed.stderr}"
    if len(output) > MAX_DISCOVERY_OUTPUT_CHARS:
        return _unavailable_agent(
            agent_id,
            label,
            "output_too_large",
            f"{command[0]} capability output exceeded {MAX_DISCOVERY_OUTPUT_CHARS} characters",
        )

    try:
        parsed = parser(output)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _unavailable_agent(
            agent_id,
            label,
            "invalid_output",
            f"Could not parse {command[0]} capabilities: {type(exc).__name__}",
        )

    models = parsed.get("models")
    if not isinstance(models, list) or not models:
        return _unavailable_agent(
            agent_id,
            label,
            "no_models",
            f"{command[0]} did not advertise any usable models",
        )

    default_model = parsed.get("default_model")
    model_ids = {str(model.get("id")) for model in models if isinstance(model, dict)}
    if default_model not in model_ids:
        default_model = None

    return {
        "id": agent_id,
        "label": label,
        "available": True,
        "models": models,
        "default_model": default_model,
        "reason": None,
        "errors": [],
    }


def _unavailable_agent(
    agent_id: str,
    label: str,
    code: str,
    message: str,
) -> dict[str, object]:
    return {
        "id": agent_id,
        "label": label,
        "available": False,
        "models": [],
        "default_model": None,
        "reason": message,
        "errors": [{"code": code, "message": message}],
    }


def _safe_error_detail(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if not output:
        return ""
    return " ".join(str(output).split())[:300]


def _option_description(help_text: str, option: str) -> str:
    wanted = option.removeprefix("--")
    lines: list[str] = []
    collecting = False
    for line in help_text.splitlines():
        match = _OPTION_LINE.match(line)
        if collecting and match:
            break
        if match and match.group("name") == wanted:
            collecting = True
        if collecting:
            lines.append(line.strip())
    return " ".join(lines)


def _choice_list(description: str) -> list[str]:
    match = _SIMPLE_CHOICE_LIST.search(description)
    if not match:
        return []
    return _unique(
        choice.strip().lower()
        for choice in match.group("choices").split(",")
        if choice.strip()
    )


def _parse_claude_help(output: str) -> dict[str, object]:
    if not isinstance(output, str) or not output.strip():
        raise ValueError("empty Claude help")

    model_description = _option_description(output, "--model")
    effort_description = _option_description(output, "--effort")
    if not model_description:
        raise ValueError("Claude help has no --model option")

    full_ids = _unique(value.lower() for value in _CLAUDE_MODEL_ID.findall(model_description))
    quoted = _unique(value.lower() for value in _QUOTED_VALUE.findall(model_description))
    aliases = [value for value in quoted if not value.startswith("claude-")]

    selected_ids: list[str] = []
    consumed_full_ids: set[str] = set()
    alias_to_id: dict[str, str] = {}
    for alias in aliases:
        matches = [
            full_id
            for full_id in full_ids
            if re.search(rf"(?:^|[-./]){re.escape(alias)}(?:$|[-./])", full_id)
        ]
        model_id = matches[0] if len(matches) == 1 else alias
        alias_to_id[alias] = model_id
        selected_ids.append(model_id)
        if model_id in full_ids:
            consumed_full_ids.add(model_id)
    selected_ids.extend(full_id for full_id in full_ids if full_id not in consumed_full_ids)
    selected_ids = _unique(selected_ids)
    if not selected_ids:
        raise ValueError("Claude help has no model values")

    efforts = _choice_list(effort_description)
    default_effort = _extract_default(effort_description)
    if default_effort not in efforts:
        default_effort = None

    default_model = _extract_default(model_description)
    default_model = alias_to_id.get(default_model or "", default_model)
    if default_model not in selected_ids:
        default_model = None

    return {
        "models": [
            _model(model_id, _humanize_model_id(model_id), efforts, default_effort)
            for model_id in selected_ids
        ],
        "default_model": default_model,
    }


def _parse_codex_catalog(output: str) -> dict[str, object]:
    if not isinstance(output, str) or not output.strip():
        raise ValueError("empty Codex catalog")
    payload = json.loads(output)
    if isinstance(payload, dict):
        raw_models = payload.get("models")
    else:
        raw_models = payload
    if not isinstance(raw_models, list):
        raise ValueError("Codex catalog has no models list")

    normalized_with_priority: list[tuple[float, int, dict[str, object], bool]] = []
    for index, raw_model in enumerate(raw_models):
        if not isinstance(raw_model, dict):
            continue
        visibility = str(raw_model.get("visibility", "list")).lower()
        if visibility in {"hide", "hidden"}:
            continue
        model_id = _nonempty_string(raw_model.get("slug") or raw_model.get("id"))
        if not model_id:
            continue
        label = _nonempty_string(raw_model.get("display_name") or raw_model.get("name"))
        levels = raw_model.get("supported_reasoning_levels", [])
        efforts: list[str] = []
        if isinstance(levels, list):
            for level in levels:
                effort = level.get("effort") if isinstance(level, dict) else level
                normalized_effort = _nonempty_string(effort)
                if normalized_effort:
                    efforts.append(normalized_effort.lower())
        efforts = _unique(efforts)
        default_effort = _nonempty_string(
            raw_model.get("default_reasoning_level") or raw_model.get("default_effort")
        )
        if default_effort:
            default_effort = default_effort.lower()
        if default_effort not in efforts:
            default_effort = None
        try:
            priority = float(raw_model.get("priority", index))
        except (TypeError, ValueError):
            priority = float(index)
        is_default = raw_model.get("is_default") is True or raw_model.get("default") is True
        normalized_with_priority.append(
            (
                priority,
                index,
                _model(model_id, label or _humanize_model_id(model_id), efforts, default_effort),
                is_default,
            )
        )

    normalized_with_priority.sort(key=lambda item: (item[0], item[1]))
    models = [item[2] for item in normalized_with_priority]
    model_ids = {str(model["id"]) for model in models}

    default_model: str | None = None
    if isinstance(payload, dict):
        default_model = _nonempty_string(payload.get("default_model"))
    if default_model not in model_ids:
        default_model = next(
            (str(model["id"]) for _, _, model, is_default in normalized_with_priority if is_default),
            None,
        )

    return {"models": models, "default_model": default_model}


def _parse_opencode_models(output: str) -> dict[str, object]:
    if not isinstance(output, str) or not output.strip():
        raise ValueError("empty OpenCode model output")

    models: list[dict[str, object]] = []
    explicit_default: str | None = None
    for metadata in _json_objects(output):
        provider_id = _nonempty_string(metadata.get("providerID") or metadata.get("provider_id"))
        raw_id = _nonempty_string(metadata.get("id"))
        if not provider_id or not raw_id:
            continue
        status = _nonempty_string(metadata.get("status"))
        if status and status.lower() not in {"active", "available"}:
            continue

        model_id = raw_id if raw_id.startswith(f"{provider_id}/") else f"{provider_id}/{raw_id}"
        variants = metadata.get("variants", {})
        efforts: list[str] = []
        if isinstance(variants, dict):
            for variant_name, variant in variants.items():
                if not isinstance(variant, dict):
                    continue
                effort = variant.get("reasoningEffort") or variant.get("reasoning_effort")
                normalized_effort = _nonempty_string(effort)
                if normalized_effort:
                    efforts.append(normalized_effort.lower())
                elif variant.get("reasoning") is True:
                    normalized_name = _nonempty_string(variant_name)
                    if normalized_name:
                        efforts.append(normalized_name.lower())
        efforts = _unique(efforts)
        default_effort = _nonempty_string(
            metadata.get("defaultReasoningEffort")
            or metadata.get("default_reasoning_effort")
            or metadata.get("defaultVariant")
        )
        if default_effort:
            default_effort = default_effort.lower()
        if default_effort not in efforts:
            default_effort = None

        label = _nonempty_string(metadata.get("name")) or _humanize_model_id(model_id)
        models.append(_model(model_id, label, efforts, default_effort))
        if metadata.get("default") is True or metadata.get("isDefault") is True:
            explicit_default = model_id

    models = _deduplicate_models(models)
    if explicit_default not in {str(model["id"]) for model in models}:
        explicit_default = None
    return {"models": models, "default_model": explicit_default}


def _json_objects(output: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, object]] = []
    position = 0
    while position < len(output):
        start = output.find("{", position)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(output[start:])
        except json.JSONDecodeError:
            position = start + 1
            continue
        position = start + consumed
        if isinstance(value, dict):
            objects.append(value)
    return objects


def _model(
    model_id: str,
    label: str,
    efforts: list[str],
    default_effort: str | None,
) -> dict[str, object]:
    return {
        "id": model_id,
        "label": label,
        "available": True,
        "reason": None,
        "efforts": list(efforts) or None,
        "default_effort": default_effort,
    }


def _deduplicate_models(models: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for model in models:
        model_id = str(model["id"])
        if model_id not in seen:
            result.append(model)
            seen.add(model_id)
    return result


def _extract_default(description: str) -> str | None:
    match = re.search(r"\(default:\s*['\"]?([a-z0-9][a-z0-9._/-]*)", description, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _humanize_model_id(model_id: str) -> str:
    name = model_id.rsplit("/", 1)[-1]
    name = re.sub(r"^claude-", "", name, flags=re.IGNORECASE)
    acronyms = {"gpt": "GPT", "glm": "GLM", "oss": "OSS", "ai": "AI"}
    return " ".join(acronyms.get(token.lower(), token.capitalize()) for token in name.split("-") if token)


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
