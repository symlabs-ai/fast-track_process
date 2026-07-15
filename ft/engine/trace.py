"""Crash-tolerant execution traces for Fast Track cycles.

The engine state is intentionally a compact snapshot.  Historical attempts,
validator timings and provider calls live in this append-only journal instead
of making ``engine_state.yml`` grow without bound.  A small derived JSON report
is safe to archive with the cycle; the raw journal remains runtime-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4


TRACE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _parse_utc(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_value(value: Any) -> Any:
    """Return a deterministic JSON-safe value without inventing metrics."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, raw = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(raw)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class TraceSpan:
    """One in-process span whose start has already been journaled."""

    recorder: "TraceRecorder"
    span_id: str
    started_monotonic_ns: int
    finished: bool = False

    def finish(
        self,
        *,
        status: str = "ok",
        result: str | None = None,
        metrics: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if self.finished:
            return
        duration_ms = max(
            0,
            (time.monotonic_ns() - self.started_monotonic_ns) // 1_000_000,
        )
        self.recorder._append(
            {
                "event": "span_end",
                "event_id": uuid4().hex,
                "run_id": self.recorder.run_id,
                "span_id": self.span_id,
                "ended_at": _utc_text(),
                "duration_ms": duration_ms,
                "duration_source": "monotonic",
                "status": str(status),
                "result": result,
                "metrics": {
                    str(key): _json_value(value)
                    for key, value in (metrics or {}).items()
                },
                "attributes": {
                    str(key): _json_value(value)
                    for key, value in (attributes or {}).items()
                },
            }
        )
        self.finished = True

    def __enter__(self) -> "TraceSpan":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        self.finish(
            status="error" if exc_type else "ok",
            result=type(exc).__name__ if exc is not None else None,
        )
        return False


@dataclass
class TraceRecorder:
    """Append spans to one cycle-local journal with process-safe writes."""

    path: Path
    run_id: str
    _thread_lock: threading.RLock = field(default_factory=threading.RLock)
    _ordinals: dict[tuple[str, str], int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.run_id = str(self.run_id)
        self._load_ordinals()

    @classmethod
    def for_state_path(cls, state_path: str | Path, run_id: str) -> "TraceRecorder":
        state = Path(state_path)
        return cls(state.parent / "trace" / "events.jsonl", run_id)

    def _load_ordinals(self) -> None:
        for event in read_trace_events(self.path):
            if event.get("event") != "span_start":
                continue
            category = str(event.get("category") or "")
            node_id = str(event.get("node_id") or "")
            ordinal = event.get("ordinal")
            if not category or not node_id or not isinstance(ordinal, int):
                continue
            key = (category, node_id)
            self._ordinals[key] = max(self._ordinals.get(key, 0), ordinal)

    def next_ordinal(self, category: str, node_id: str) -> int:
        with self._thread_lock:
            key = (str(category), str(node_id))
            ordinal = self._ordinals.get(key, 0) + 1
            self._ordinals[key] = ordinal
            return ordinal

    def begin_span(
        self,
        *,
        category: str,
        name: str,
        node_id: str | None = None,
        parent_span_id: str | None = None,
        attempt_id: str | None = None,
        invocation_id: str | None = None,
        ordinal: int | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> TraceSpan:
        started_monotonic_ns = time.monotonic_ns()
        span_id = uuid4().hex
        self._append(
            {
                "event": "span_start",
                "event_id": uuid4().hex,
                "run_id": self.run_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "category": str(category),
                "name": str(name),
                "node_id": node_id,
                "attempt_id": attempt_id,
                "invocation_id": invocation_id,
                "ordinal": ordinal,
                "started_at": _utc_text(),
                "attributes": {
                    str(key): _json_value(value)
                    for key, value in (attributes or {}).items()
                },
            }
        )
        return TraceSpan(self, span_id, started_monotonic_ns)

    def finish_open_span(
        self,
        span_id: str,
        *,
        status: str,
        result: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> bool:
        """Finish a span opened by another process (for human waits/recovery)."""
        events = read_trace_events(self.path)
        start = next(
            (
                event
                for event in reversed(events)
                if event.get("event") == "span_start"
                and event.get("span_id") == span_id
            ),
            None,
        )
        if start is None:
            return False
        if any(
            event.get("event") == "span_end" and event.get("span_id") == span_id
            for event in events
        ):
            return False
        started_at = _parse_utc(start.get("started_at"))
        ended_at = _utc_now()
        duration_ms = (
            max(0, int((ended_at - started_at).total_seconds() * 1000))
            if started_at is not None
            else None
        )
        self._append(
            {
                "event": "span_end",
                "event_id": uuid4().hex,
                "run_id": self.run_id,
                "span_id": span_id,
                "ended_at": _utc_text(ended_at),
                "duration_ms": duration_ms,
                "duration_source": "utc",
                "status": str(status),
                "result": result,
                "metrics": {},
                "attributes": {
                    str(key): _json_value(value)
                    for key, value in (attributes or {}).items()
                },
            }
        )
        return True

    def open_span_ids(
        self,
        *,
        category: str | None = None,
        node_id: str | None = None,
    ) -> tuple[str, ...]:
        events = read_trace_events(self.path)
        starts: dict[str, Mapping[str, Any]] = {}
        finished: set[str] = set()
        for event in events:
            span_id = event.get("span_id")
            if not isinstance(span_id, str):
                continue
            if event.get("event") == "span_start":
                starts[span_id] = event
            elif event.get("event") == "span_end":
                finished.add(span_id)
        return tuple(
            span_id
            for span_id, event in starts.items()
            if span_id not in finished
            and (category is None or event.get("category") == category)
            and (node_id is None or event.get("node_id") == node_id)
        )

    def finish_open_spans(
        self,
        *,
        category: str | None = None,
        node_id: str | None = None,
        status: str = "interrupted",
        result: str | None = None,
    ) -> int:
        span_ids = self.open_span_ids(category=category, node_id=node_id)
        return sum(
            1
            for span_id in span_ids
            if self.finish_open_span(span_id, status=status, result=result)
        )

    def _append(self, event: Mapping[str, Any]) -> None:
        payload = {
            "schema_version": TRACE_SCHEMA_VERSION,
            **{str(key): _json_value(value) for key, value in event.items()},
        }
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


def read_trace_events(path: str | Path) -> list[dict[str, Any]]:
    trace = Path(path)
    if not trace.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            # A final partial line can survive SIGKILL.  Earlier complete
            # events remain authoritative and the report marks open spans.
            continue
        if isinstance(payload, dict) and payload.get("schema_version") == TRACE_SCHEMA_VERSION:
            events.append(payload)
    return events


def _usage_from_log(path: Path) -> dict[str, int | None]:
    values: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "turns": None,
    }
    if not path.is_file():
        return values
    totals = {key: 0 for key in values if key != "turns"}
    seen_any = {key: False for key in totals}
    turns = 0
    seen_messages: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        turns += 1
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            if message_id in seen_messages:
                continue
            seen_messages.add(message_id)
        usage = message.get("usage") or event.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        aliases = {
            "input_tokens": ("input_tokens", "prompt_tokens"),
            "output_tokens": ("output_tokens", "completion_tokens"),
            "cache_read_tokens": ("cache_read_input_tokens", "cache_read_tokens"),
            "cache_write_tokens": (
                "cache_creation_input_tokens",
                "cache_write_input_tokens",
                "cache_write_tokens",
            ),
        }
        for target, candidates in aliases.items():
            for candidate in candidates:
                value = usage.get(candidate)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[target] += value
                    seen_any[target] = True
                    break
    for key, total in totals.items():
        values[key] = total if seen_any[key] else None
    values["turns"] = turns if turns else None
    return values


def _span_rows(
    events: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime,
    log_root: Path | None,
) -> list[dict[str, Any]]:
    starts: dict[str, Mapping[str, Any]] = {}
    ends: dict[str, Mapping[str, Any]] = {}
    for event in events:
        span_id = event.get("span_id")
        if not isinstance(span_id, str):
            continue
        if event.get("event") == "span_start":
            starts[span_id] = event
        elif event.get("event") == "span_end":
            ends[span_id] = event

    rows: list[dict[str, Any]] = []
    for span_id, start in sorted(
        starts.items(), key=lambda item: str(item[1].get("started_at") or "")
    ):
        end = ends.get(span_id)
        started_at = _parse_utc(start.get("started_at"))
        ended_at = _parse_utc(end.get("ended_at")) if end else generated_at
        duration_ms = end.get("duration_ms") if end else None
        if not isinstance(duration_ms, int):
            duration_ms = (
                max(0, int((ended_at - started_at).total_seconds() * 1000))
                if started_at is not None and ended_at is not None
                else None
            )
        attributes = dict(start.get("attributes") or {})
        if end and isinstance(end.get("attributes"), dict):
            attributes.update(end["attributes"])
        metrics = dict(end.get("metrics") or {}) if end else {}
        if start.get("category") == "llm":
            raw_log = attributes.get("log_path")
            if isinstance(raw_log, str) and log_root is not None:
                candidate = Path(raw_log)
                if not candidate.is_absolute():
                    candidate = log_root / candidate
                for key, value in _usage_from_log(candidate).items():
                    metrics.setdefault(key, value)
        rows.append(
            {
                "span_id": span_id,
                "parent_span_id": start.get("parent_span_id"),
                "category": start.get("category"),
                "name": start.get("name"),
                "node_id": start.get("node_id"),
                "attempt_id": start.get("attempt_id"),
                "invocation_id": start.get("invocation_id"),
                "ordinal": start.get("ordinal"),
                "started_at": start.get("started_at"),
                "ended_at": end.get("ended_at") if end else None,
                "duration_ms": duration_ms,
                "duration_source": (
                    end.get("duration_source") if end else "utc_open"
                ),
                "status": end.get("status") if end else "open",
                "result": end.get("result") if end else None,
                "attributes": attributes,
                "metrics": metrics,
            }
        )
    return rows


def build_run_report(
    trace_path: str | Path,
    *,
    run_id: str | None = None,
    generated_at: datetime | None = None,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    generated = generated_at or _utc_now()
    events = read_trace_events(trace_path)
    rows = _span_rows(
        events,
        generated_at=generated,
        log_root=Path(log_root) if log_root is not None else None,
    )
    starts = [_parse_utc(row.get("started_at")) for row in rows]
    starts = [value for value in starts if value is not None]
    ended = [_parse_utc(row.get("ended_at")) for row in rows]
    ended = [value for value in ended if value is not None]
    wall_start = min(starts) if starts else None
    has_open = any(row["status"] == "open" for row in rows)
    wall_end = generated if has_open else (max(ended) if ended else generated)
    wall_ms = (
        max(0, int((wall_end - wall_start).total_seconds() * 1000))
        if wall_start is not None
        else None
    )
    categories = ("llm", "validator", "human", "queue", "close")
    active_ms = {
        category: sum(
            int(row["duration_ms"])
            for row in rows
            if row.get("category") == category
            and isinstance(row.get("duration_ms"), int)
        )
        for category in categories
    }

    llm_rows = [row for row in rows if row.get("category") == "llm"]

    def token_total(key: str) -> int | None:
        values = [
            row.get("metrics", {}).get(key)
            for row in llm_rows
            if isinstance(row.get("metrics"), dict)
        ]
        present = [value for value in values if isinstance(value, int)]
        return sum(present) if present else None

    selected_run_id = run_id or next(
        (str(event.get("run_id")) for event in events if event.get("run_id")),
        None,
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "generated_at": _utc_text(generated),
        "wall": {
            "started_at": _utc_text(wall_start) if wall_start else None,
            "ended_at": _utc_text(wall_end) if wall_start else None,
            "duration_ms": wall_ms,
            "status": "active" if has_open else "closed",
        },
        "active_time_ms": active_ms,
        "llm": {
            "calls": len(llm_rows),
            "input_tokens": token_total("input_tokens"),
            "output_tokens": token_total("output_tokens"),
            "cache_read_tokens": token_total("cache_read_tokens"),
            "cache_write_tokens": token_total("cache_write_tokens"),
        },
        "spans": rows,
    }


def write_run_report(
    trace_path: str | Path,
    destination: str | Path,
    *,
    run_id: str | None = None,
    generated_at: datetime | None = None,
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    report = build_run_report(
        trace_path,
        run_id=run_id,
        generated_at=generated_at,
        log_root=log_root,
    )
    _atomic_json(Path(destination), report)
    return report
