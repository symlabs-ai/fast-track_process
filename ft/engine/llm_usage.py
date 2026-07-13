"""LLM token usage aggregation from cycle logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_COUNT_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_output_tokens",
)


def _blank_totals() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_output_tokens": 0,
        "events": 0,
        "files": 0,
    }


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    """Normalize common Claude/Codex/OpenAI token usage shapes."""
    out = _blank_totals()
    out["input_tokens"] = _safe_int(
        usage.get("input_tokens", usage.get("prompt_tokens", 0))
    )
    out["output_tokens"] = _safe_int(
        usage.get("output_tokens", usage.get("completion_tokens", 0))
    )
    out["cache_creation_input_tokens"] = _safe_int(
        usage.get("cache_creation_input_tokens", 0)
    )
    out["cache_read_input_tokens"] = _safe_int(
        usage.get("cache_read_input_tokens", 0)
    )
    out["total_tokens"] = _safe_int(usage.get("total_tokens", 0))
    out["cached_input_tokens"] = _safe_int(usage.get("cached_input_tokens", 0))
    out["reasoning_output_tokens"] = _safe_int(usage.get("reasoning_output_tokens", 0))

    details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        # OpenAI/Codex reporta cached_input como subconjunto de input_tokens.
        # Algumas versões usam o campo top-level, outras o details; se ambos
        # vierem presentes, representam a mesma grandeza e não devem somar.
        out["cached_input_tokens"] = max(
            out["cached_input_tokens"],
            _safe_int(details.get("cached_tokens", 0)),
        )
    details = usage.get("output_tokens_details")
    if isinstance(details, dict):
        out["reasoning_output_tokens"] += _safe_int(details.get("reasoning_tokens", 0))

    return out


def _total_all(tokens: dict[str, int]) -> int:
    additive = (
        tokens.get("input_tokens", 0)
        + tokens.get("cache_creation_input_tokens", 0)
        + tokens.get("cache_read_input_tokens", 0)
        + tokens.get("output_tokens", 0)
    )
    return additive or tokens.get("total_tokens", 0)


def _total_without_cache_read(tokens: dict[str, int]) -> int:
    input_tokens = tokens.get("input_tokens", 0)
    cached_input_tokens = min(
        input_tokens,
        max(0, tokens.get("cached_input_tokens", 0)),
    )
    additive = (
        input_tokens
        - cached_input_tokens
        + tokens.get("cache_creation_input_tokens", 0)
        + tokens.get("output_tokens", 0)
    )
    return additive or tokens.get("total_tokens", 0)


def _add_counts(dst: dict[str, int], usage: dict[str, int]) -> None:
    for key in _COUNT_KEYS:
        dst[key] += usage.get(key, 0)
    dst["events"] += 1


def _label(engine: str | None, model: str | None) -> str:
    engine = str(engine or "").strip()
    model = str(model or "").strip()
    if engine and model:
        return f"{engine}/{model}"
    return model or engine or "unknown"


def _iter_json_events(path: Path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _usage_from_event(event: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return usage, message_id, model from the most precise location in event."""
    message = event.get("message")
    if isinstance(message, dict):
        usage = message.get("usage")
        if isinstance(usage, dict):
            msg_id = message.get("id")
            return usage, str(msg_id) if msg_id else None, message.get("model")

    usage = event.get("usage")
    if isinstance(usage, dict):
        return usage, None, event.get("model")

    response = event.get("response")
    if isinstance(response, dict):
        usage = response.get("usage")
        if isinstance(usage, dict):
            return usage, None, response.get("model") or event.get("model")

    item = event.get("item")
    if isinstance(item, dict):
        usage = item.get("usage")
        if isinstance(usage, dict):
            return usage, None, item.get("model") or event.get("model")

    return None, None, None


def summarize_llm_usage(
    logs_dir: Path,
    default_engine: str | None = None,
    default_model: str | None = None,
) -> dict[str, Any]:
    """Summarize token counters emitted by LLM provider logs in a cycle."""
    summary: dict[str, Any] = {
        "by_model": {},
        "totals": _blank_totals(),
        "log_files": 0,
    }
    if not logs_dir.is_dir():
        return _finalize_summary(summary)

    files = sorted(
        [p for p in logs_dir.iterdir() if p.is_file() and p.suffix in {".log", ".jsonl"}],
        key=lambda p: p.stat().st_mtime,
    )
    summary["log_files"] = len(files)
    seen_message_ids: set[str] = set()

    for path in files:
        current_model = default_model
        file_touched_labels: set[str] = set()
        for event in _iter_json_events(path):
            if event.get("type") == "system" and event.get("subtype") == "init":
                current_model = event.get("model") or current_model
                continue

            usage, message_id, event_model = _usage_from_event(event)
            if not usage:
                continue
            if message_id:
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)

            model = event_model or current_model or default_model
            key = _label(default_engine, model)
            counts = _normalize_usage(usage)
            by_model = summary["by_model"].setdefault(key, _blank_totals())
            _add_counts(by_model, counts)
            _add_counts(summary["totals"], counts)
            file_touched_labels.add(key)

        for key in file_touched_labels:
            summary["by_model"][key]["files"] += 1
        if file_touched_labels:
            summary["totals"]["files"] += 1

    return _finalize_summary(summary)


def _finalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    for totals in [summary["totals"], *summary["by_model"].values()]:
        totals["total_all_tokens"] = _total_all(totals)
        totals["total_without_cache_read_tokens"] = _total_without_cache_read(totals)
    return summary


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def format_llm_usage_lines(summary: dict[str, Any], indent: str = "  ") -> list[str]:
    totals = summary.get("totals") or {}
    if not totals.get("events"):
        return [f"{indent}Tokens LLM delegado: indisponivel nos logs do ciclo"]

    lines = [
        (
            f"{indent}Tokens LLM delegado: "
            f"{_fmt_int(totals.get('total_all_tokens', 0))} total bruto; "
            f"{_fmt_int(totals.get('total_without_cache_read_tokens', 0))} sem cache"
        )
    ]
    for label, data in sorted((summary.get("by_model") or {}).items()):
        lines.append(
            f"{indent}  {label}: "
            f"in {_fmt_int(data.get('input_tokens', 0))} | "
            f"cache_write {_fmt_int(data.get('cache_creation_input_tokens', 0))} | "
            f"cache_read {_fmt_int(data.get('cache_read_input_tokens', 0))} | "
            f"cached_input {_fmt_int(data.get('cached_input_tokens', 0))} | "
            f"out {_fmt_int(data.get('output_tokens', 0))}"
        )
    return lines
