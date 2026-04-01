"""Gatekeeper — avalia critérios e retorna PASS ou BLOCK."""

from __future__ import annotations

from enum import Enum
from typing import Any


class GatekeeperResult(Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class Gatekeeper:
    def evaluate(self, criteria: dict[str, Any], *, all_passed: bool) -> GatekeeperResult:
        if all_passed:
            return GatekeeperResult.PASS
        return GatekeeperResult.BLOCK

    def evaluate_with_reason(
        self,
        criteria: dict[str, Any],
        *,
        all_passed: bool,
        failure_detail: str = "",
    ) -> tuple[GatekeeperResult, str]:
        result = self.evaluate(criteria, all_passed=all_passed)
        if result == GatekeeperResult.BLOCK:
            reason = failure_detail or "critérios não atendidos"
            return result, reason
        return result, ""
