from __future__ import annotations

from datetime import datetime, timezone

from ft.engine.llm_activity import (
    activity_digest,
    activity_log_path,
    append_activity,
    recent_activity_timestamps,
)


def test_activity_sidecar_timestamps_lines_without_copying_content(tmp_path):
    log = tmp_path / "provider.jsonl"
    log.write_text('{"secret":"molhoespecial"}\n', encoding="utf-8")

    append_activity(log, log.read_text(encoding="utf-8"), source="stream")

    sidecar = activity_log_path(log)
    raw = sidecar.read_text(encoding="utf-8")
    assert "molhoespecial" not in raw
    timestamps = recent_activity_timestamps(log)
    assert activity_digest('{"secret":"molhoespecial"}') in timestamps
    assert timestamps[activity_digest('{"secret":"molhoespecial"}')].tzinfo == timezone.utc


def test_activity_reader_keeps_latest_timestamp_for_a_repeated_line(tmp_path):
    log = tmp_path / "provider.log"
    line = "same progress event"
    sidecar = activity_log_path(log)
    sidecar.write_text(
        "\n".join(
            [
                (
                    '{"line_sha256":"'
                    + activity_digest(line)
                    + '","observed_at":"2026-07-29T12:00:00+00:00",'
                    '"schema_version":1,"source":"stream"}'
                ),
                (
                    '{"line_sha256":"'
                    + activity_digest(line)
                    + '","observed_at":"2026-07-29T12:05:00+00:00",'
                    '"schema_version":1,"source":"stream"}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    timestamps = recent_activity_timestamps(log)

    assert timestamps[activity_digest(line)] == datetime(
        2026,
        7,
        29,
        12,
        5,
        tzinfo=timezone.utc,
    )
