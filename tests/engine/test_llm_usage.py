import json

from ft.engine.llm_usage import format_llm_usage_lines, summarize_llm_usage


def test_summarize_claude_log_dedupes_message_usage(tmp_path):
    logs = tmp_path / "state" / "llm_logs"
    logs.mkdir(parents=True)
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "model": "claude-fable-5",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
                "output_tokens": 4,
            },
            "content": [],
        },
    }
    (logs / "20260710-010101__node__run.log").write_text(
        "\n".join(
            [
                "## Output",
                json.dumps({"type": "system", "subtype": "init", "model": "claude-fable-5"}),
                json.dumps(event),
                json.dumps(event),
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_llm_usage(logs, default_engine="claude")

    totals = summary["totals"]
    assert totals["input_tokens"] == 10
    assert totals["cache_creation_input_tokens"] == 20
    assert totals["cache_read_input_tokens"] == 30
    assert totals["output_tokens"] == 4
    assert totals["total_all_tokens"] == 64
    assert totals["total_without_cache_read_tokens"] == 34
    assert summary["by_model"]["claude/claude-fable-5"]["events"] == 1


def test_summarize_codex_usage_with_default_model(tmp_path):
    logs = tmp_path / "state" / "llm_logs"
    logs.mkdir(parents=True)
    (logs / "20260710-010101__node__run.jsonl").write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 7,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 3},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_llm_usage(
        logs,
        default_engine="codex",
        default_model="gpt-5.5",
    )

    assert summary["totals"]["total_all_tokens"] == 12
    assert summary["by_model"]["codex/gpt-5.5"]["cached_input_tokens"] == 2
    assert summary["by_model"]["codex/gpt-5.5"]["reasoning_output_tokens"] == 3


def test_format_lines_are_explicit_about_missing_usage(tmp_path):
    logs = tmp_path / "state" / "llm_logs"
    logs.mkdir(parents=True)
    (logs / "20260710-010101__node__run.log").write_text("plain output\n", encoding="utf-8")

    lines = format_llm_usage_lines(summarize_llm_usage(logs, default_engine="opencode"))

    assert lines == ["  Tokens LLM delegado: indisponivel nos logs do ciclo"]
