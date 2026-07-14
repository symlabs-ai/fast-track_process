"""Regressão: delegação morta sem veredito (died) é retentada automaticamente."""

from __future__ import annotations

from unittest.mock import patch

from ft.engine.delegate import DelegateResult
from ft.engine.runner import StepRunner


def _result(*, success: bool, died: bool = False, rate_limited: bool = False,
            output: str = "x") -> DelegateResult:
    return DelegateResult(
        success=success,
        output=output,
        files_created=[],
        files_modified=[],
        rate_limited=rate_limited,
        died=died,
    )


def _retry(results):
    calls = []

    def fake_delegate(**kwargs):
        calls.append(kwargs)
        return results[min(len(calls) - 1, len(results) - 1)]

    runner = object.__new__(StepRunner)  # helper não usa estado da instância
    with patch("ft.engine.runner.delegate_to_llm", side_effect=fake_delegate):
        final = StepRunner._delegate_with_stream_retry(runner, task="t")
    return final, len(calls)


def test_died_delegation_is_retried_until_success():
    final, calls = _retry([
        _result(success=False, died=True),
        _result(success=True),
    ])
    assert final.success is True
    assert calls == 2


def test_died_delegation_gives_up_after_max_retries():
    final, calls = _retry([_result(success=False, died=True)])
    assert final.success is False
    assert final.died is True
    assert calls == 1 + StepRunner._MAX_STREAM_RETRIES


def test_content_failure_is_not_retried():
    final, calls = _retry([_result(success=False, died=False, output="BLOCKED: x")])
    assert final.success is False
    assert calls == 1


def test_rate_limit_is_not_retried_here():
    final, calls = _retry([
        _result(success=False, died=False, rate_limited=True),
    ])
    assert final.rate_limited is True
    assert calls == 1


def test_success_returns_immediately():
    final, calls = _retry([_result(success=True)])
    assert final.success is True
    assert calls == 1
