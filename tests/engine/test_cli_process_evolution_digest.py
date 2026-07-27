"""Digest guard coverage for the intentional end-of-cycle process evolution."""

from ft.cli.main import _completed_process_evolution_allows_digest_change


def _completed_state() -> dict:
    return {
        "current_node": None,
        "node_status": "done",
        "completed_nodes": [
            "ft.handoff.05.process_evolve",
            "ft.end",
        ],
        "gate_log": {
            "ft.handoff.05.process_evolve": "PASS",
            "ft.end": "PASS",
        },
    }


def test_completed_process_evolution_allows_final_digest_change():
    assert _completed_process_evolution_allows_digest_change(_completed_state())


def test_active_cycle_never_allows_process_digest_change():
    state = _completed_state()
    state["current_node"] = "ft.handoff.05.process_evolve"
    state["node_status"] = "delegated"

    assert not _completed_process_evolution_allows_digest_change(state)


def test_completed_cycle_requires_successful_process_evolution():
    state = _completed_state()
    state["gate_log"]["ft.handoff.05.process_evolve"] = "BLOCKED"

    assert not _completed_process_evolution_allows_digest_change(state)


def test_completed_cycle_requires_end_node_evidence():
    state = _completed_state()
    state["completed_nodes"].remove("ft.end")

    assert not _completed_process_evolution_allows_digest_change(state)
