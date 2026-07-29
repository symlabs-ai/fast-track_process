"""Timestamp sidecars for provider logs without rewriting their native format."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import IO


ACTIVITY_SCHEMA_VERSION = 1
ACTIVITY_SUFFIX = ".activity"
_ACTIVITY_TAIL_BYTES = 1_048_576


def activity_log_path(log_path: str | Path) -> Path:
    """Return the non-provider sidecar ignored by token/log parsers."""
    return Path(f"{Path(log_path)}{ACTIVITY_SUFFIX}")


def activity_digest(line: str) -> str:
    """Hash one normalized provider line so the sidecar never copies secrets."""
    normalized = line.strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def activity_record(
    line: str,
    *,
    source: str = "stream",
    observed_at: datetime | None = None,
) -> str | None:
    """Encode one timestamped activity event, or ``None`` for a blank line."""
    normalized = line.strip()
    if not normalized:
        return None
    timestamp = observed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    payload = {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "observed_at": timestamp.astimezone(timezone.utc).isoformat(),
        "source": source,
        "line_sha256": activity_digest(normalized),
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"


def write_activity(
    handle: IO[str],
    line: str,
    *,
    source: str = "stream",
    observed_at: datetime | None = None,
) -> bool:
    """Append one event to an already-open sidecar."""
    record = activity_record(line, source=source, observed_at=observed_at)
    if record is None:
        return False
    handle.write(record)
    handle.flush()
    return True


def append_activity(
    log_path: str | Path,
    text: str,
    *,
    source: str = "engine",
) -> None:
    """Timestamp the non-empty lines of an occasional engine append."""
    path = activity_log_path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in text.splitlines():
            write_activity(handle, line, source=source)


def recent_activity_timestamps(
    log_path: str | Path,
) -> dict[str, datetime]:
    """Map recent provider-line hashes to their exact latest observation time."""
    path = activity_log_path(log_path)
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            read_size = min(size, _ACTIVITY_TAIL_BYTES)
            stream.seek(-read_size, 2)
            raw_tail = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    lines = raw_tail.splitlines()
    if size > read_size and lines:
        lines = lines[1:]
    timestamps: dict[str, datetime] = {}
    for line in lines:
        try:
            payload = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != ACTIVITY_SCHEMA_VERSION
            or not isinstance(payload.get("line_sha256"), str)
            or not isinstance(payload.get("observed_at"), str)
        ):
            continue
        try:
            timestamp = datetime.fromisoformat(
                payload["observed_at"].replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamps[payload["line_sha256"]] = timestamp.astimezone(timezone.utc)
    return timestamps
