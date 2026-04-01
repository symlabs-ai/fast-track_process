"""TDD Cycle Tracker — rastreia fases RED → GREEN → REFACTOR."""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class TDDPhase(Enum):
    RED = "RED"
    GREEN = "GREEN"
    REFACTOR = "REFACTOR"


_SEQUENCE = [TDDPhase.RED, TDDPhase.GREEN, TDDPhase.REFACTOR]


class TDDCycleTracker:
    def __init__(self, state_path: Path | str):
        self._path = Path(state_path)
        self._phase_index: int = 0
        self._red_complete: bool = False
        self._green_complete: bool = False
        self._refactor_complete: bool = False

    def current_phase(self) -> TDDPhase:
        return _SEQUENCE[self._phase_index]

    def advance_phase(self) -> None:
        if self._phase_index < len(_SEQUENCE) - 1:
            self._phase_index += 1

    def set_phase(self, phase: TDDPhase) -> None:
        idx = _SEQUENCE.index(phase)
        if idx > self._phase_index + 1 or (idx > 0 and self._phase_index == 0):
            raise ValueError(
                f"Não é possível pular para {phase.value} sem completar a sequência RED primeiro."
            )
        self._phase_index = idx

    def complete_red(self, *, tests_failed: bool) -> None:
        if not tests_failed:
            raise ValueError(
                "RED phase requer que os testes falhem (tests must fail) para ser marcada como completa."
            )
        self._red_complete = True

    def complete_green(self, *, tests_passed: bool) -> None:
        if not tests_passed:
            raise ValueError(
                "GREEN phase requer que os testes passem (tests must pass) para ser marcada como completa."
            )
        self._green_complete = True

    def complete_refactor(self) -> None:
        self._refactor_complete = True

    def is_cycle_complete(self) -> bool:
        return self._red_complete and self._green_complete and self._refactor_complete
