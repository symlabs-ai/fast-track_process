"""Contratos determinísticos do batch interno do ``mvp-builder-fast``.

O usuário fornece uma demanda em linguagem natural. O LLM pode decompor essa
demanda, mas não decide isolamento, waves ou integridade do runtime: essas
fronteiras pertencem ao engine e são cobertas aqui.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from ft.engine import builder_batch
from ft.engine.builder_batch import (
    BatchPlanError,
    compute_waves,
    load_runtime,
    paths_outside_ownership,
    save_runtime,
    validate_batch_plan,
)

REQUEST_TEXT = """\
WiFire Go — feedback da primeira etapa de validação

S07.
 a. Existem botões > nos cards que não levam para lugar nenhum.
 b. Ler Documento completo também não leva para lugar nenhum.
 c. O botão de aceite precisa ter semântica clara; se avançar, deve dizer Avançar.

S09.
 a. Permitir deve ser um switch que mostre se a permissão está ativa.
 b. Cada controle deve solicitar somente sua própria permissão.
 c. Continuar Depois deve ser Avançar.

S10. Explique claramente o que a pessoa está permitindo nessa tela.

S11.
 a. Banco vazio não pode mostrar 18 lugares.
 b. Os ícones > precisam navegar ou ser removidos.
 c. O botão final deve ser Concluir.

S12. Use o nome da conta autenticada; nunca mostre Marina para Rodrigo.
"""


REQUIREMENTS = [
    {
        "id": "R-001",
        "text": "S07.a — chevrons sem destino devem navegar ou ser removidos.",
    },
    {
        "id": "R-002",
        "text": "S07.b — documentos completos precisam abrir uma tela real.",
    },
    {
        "id": "R-003",
        "text": "S07.c — aceite e avanço precisam de semântica explícita.",
    },
    {
        "id": "R-004",
        "text": "S09.a — cada permissão usa switch com estado observável.",
    },
    {
        "id": "R-005",
        "text": "S09.b — uma ação solicita somente a própria permissão.",
    },
    {
        "id": "R-006",
        "text": "S09.c — o CTA de saída deve se chamar Avançar.",
    },
    {
        "id": "R-007",
        "text": "S10 — explicar em linguagem simples o efeito das preferências.",
    },
    {
        "id": "R-008",
        "text": "S11.a — a quantidade de lugares vem do catálogo real.",
    },
    {
        "id": "R-009",
        "text": "S11.b — chevrons sem ação devem navegar ou ser removidos.",
    },
    {
        "id": "R-010",
        "text": "S11.c — o CTA final deve se chamar Concluir.",
    },
    {
        "id": "R-011",
        "text": "S12 — a saudação usa o nome da conta autenticada.",
    },
]


POLICY = {
    "min_lanes": 2,
    "max_lanes": 8,
    "max_acceptance_criteria_per_lane": 6,
    "protected_paths": [
        ".git",
        ".ft",
        "state",
        "docs/PROJECT_BACKLOG.md",
        "docs/FEATURES.md",
        "mockup/sample1/rt",
        "docs/screenshots",
    ],
    "evidence_root": "docs/batches/onboarding-s07-s12",
}


def _request_sha256(text: str = REQUEST_TEXT) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_payload() -> dict:
    return {
        "schema_version": 1,
        "request_sha256": _request_sha256(),
        "requirements": deepcopy(REQUIREMENTS),
        "foundation": {
            "goal": (
                "Extrair os contratos e componentes compartilhados de S07–S12 "
                "sem alterar comportamento."
            ),
            "acceptance_criteria": [
                "A baseline Android compila antes do fan-out.",
                "As quatro lanes possuem ownership disjunto.",
                "Agregadores compartilhados ficam fora das lanes.",
            ],
            "areas": [
                (
                    "project/android/app/src/main/java/com/wifire/go/ui/catalog/"
                    "CanonicalScreen.kt"
                ),
                (
                    "project/android/app/src/main/java/com/wifire/go/ui/catalog/"
                    "CanonicalContent.kt"
                ),
                (
                    "project/android/app/src/main/java/com/wifire/go/catalog/"
                    "ScreenAction.kt"
                ),
            ],
        },
        "lanes": [
            {
                "id": "L-01",
                "title": "S07 — termos",
                "goal": "Corrigir ações, documentos e avanço de S07.",
                "backlog_items": ["PB-027"],
                "requirements": ["R-001", "R-002", "R-003"],
                "acceptance_criteria": [
                    "Não existe affordance habilitada sem efeito.",
                    "Os dois documentos abrem e retornam preservando estado.",
                    "O CTA possui efeito e rótulo coerentes.",
                ],
                "areas": [
                    (
                        "project/android/app/src/main/java/com/wifire/go/ui/"
                        "onboarding/terms"
                    ),
                    (
                        "project/android/app/src/androidTest/java/com/wifire/go/"
                        "onboarding/terms"
                    ),
                ],
                "depends_on": [],
            },
            {
                "id": "L-02",
                "title": "S09 — permissões",
                "goal": "Isolar solicitação e estado de cada permissão.",
                "backlog_items": ["PB-028"],
                "requirements": ["R-004", "R-005", "R-006"],
                "acceptance_criteria": [
                    "Cada switch mostra o estado de sua categoria.",
                    "Nenhuma ação solicita todas as permissões.",
                    "O CTA Avançar mantém o modo limitado disponível.",
                ],
                "areas": [
                    (
                        "project/android/app/src/main/java/com/wifire/go/ui/"
                        "onboarding/permissions"
                    ),
                    (
                        "project/android/app/src/main/java/com/wifire/go/platform/"
                        "onboarding/permissions"
                    ),
                ],
                "depends_on": [],
            },
            {
                "id": "L-03",
                "title": "S10 — preferências",
                "goal": "Tornar as preferências compreensíveis e persistentes.",
                "backlog_items": ["PB-029"],
                "requirements": ["R-007"],
                "acceptance_criteria": [
                    "A tela explica o efeito de cada política.",
                    "As escolhas persistem e restauram.",
                ],
                "areas": [
                    (
                        "project/android/app/src/main/java/com/wifire/go/ui/"
                        "onboarding/preferences"
                    ),
                    (
                        "project/android/app/src/main/java/com/wifire/go/data/"
                        "onboarding/preferences"
                    ),
                ],
                "depends_on": [],
            },
            {
                "id": "L-04",
                "title": "S11/S12 — conclusão e início",
                "goal": "Usar conta e catálogo reais na conclusão e na home.",
                "backlog_items": ["PB-030"],
                "requirements": ["R-008", "R-009", "R-010", "R-011"],
                "acceptance_criteria": [
                    "Catálogo vazio mostra zero lugares e nenhum dado fictício.",
                    "Todo chevron possui ação ou é removido.",
                    "Concluir encerra o onboarding antes de abrir S12.",
                    "A saudação usa o displayName autenticado.",
                ],
                "areas": [
                    (
                        "project/android/app/src/main/java/com/wifire/go/ui/"
                        "onboarding/completion"
                    ),
                    (
                        "project/android/app/src/main/java/com/wifire/go/data/"
                        "onboarding/completion"
                    ),
                ],
                "depends_on": [],
            },
        ],
    }


def _validated(payload: dict | None = None):
    return validate_batch_plan(
        payload or _valid_payload(),
        REQUEST_TEXT,
        POLICY,
    )


def _lane(plan, lane_id: str):
    return next(lane for lane in plan.lanes if lane.id == lane_id)


def test_valid_plan_binds_exact_request_and_covers_all_11_requirements_once() -> None:
    plan = _validated()

    assert plan.request_sha256 == _request_sha256()
    assert len(plan.requirements) == 11
    assert plan.requirement_texts["R-005"] == REQUIREMENTS[4]["text"]
    coverage = Counter(
        requirement for lane in plan.lanes for requirement in lane.requirements
    )
    assert coverage == Counter({f"R-{index:03d}": 1 for index in range(1, 12)})
    assert [lane.id for lane in plan.lanes] == ["L-01", "L-02", "L-03", "L-04"]
    assert _lane(plan, "L-04").areas == (
        ("project/android/app/src/main/java/com/wifire/go/ui/onboarding/completion"),
        ("project/android/app/src/main/java/com/wifire/go/data/onboarding/completion"),
    )


def test_plan_hash_rejects_same_plan_for_changed_natural_language_request() -> None:
    payload = _valid_payload()

    with pytest.raises(BatchPlanError, match=r"(?i)(request_sha256|hash)"):
        validate_batch_plan(
            payload,
            REQUEST_TEXT + "\nS13. Esta é outra demanda.",
            POLICY,
        )


@pytest.mark.parametrize("mode", ["missing", "duplicated"])
def test_requirement_coverage_must_be_exact(mode: str) -> None:
    payload = _valid_payload()
    if mode == "missing":
        payload["lanes"][-1]["requirements"].remove("R-011")
    else:
        payload["lanes"][1]["requirements"].append("R-001")

    with pytest.raises(BatchPlanError, match=r"(?i)(cobertura|coverage|requirement)"):
        _validated(payload)


@pytest.mark.parametrize(
    "unsafe_area",
    [
        "/etc/passwd",
        "../fora-do-repo",
        ".",
        ".git/config",
        ".ft/state/batch.yml",
        "state/llm_execution_plan.yml",
        "docs/PROJECT_BACKLOG.md",
        "mockup/sample1/rt/07-termos.png",
        "docs/screenshots/xiaomi/07-termos.png",
    ],
)
def test_plan_rejects_unsafe_or_protected_lane_areas(unsafe_area: str) -> None:
    payload = _valid_payload()
    payload["lanes"][0]["areas"] = [unsafe_area]

    with pytest.raises(BatchPlanError, match=r"(?i)(area|path|proteg|safe)"):
        _validated(payload)


@pytest.mark.parametrize(
    ("lane_id", "depends_on"),
    [
        ("L-02", ["L-99"]),
        ("L-02", ["L-02"]),
    ],
)
def test_dependencies_must_reference_another_known_lane(
    lane_id: str,
    depends_on: list[str],
) -> None:
    payload = _valid_payload()
    next(lane for lane in payload["lanes"] if lane["id"] == lane_id)["depends_on"] = (
        depends_on
    )

    with pytest.raises(BatchPlanError, match=r"(?i)(depend|lane)"):
        _validated(payload)


def test_dependency_cycle_is_rejected_before_execution() -> None:
    payload = _valid_payload()
    payload["lanes"][0]["depends_on"] = ["L-02"]
    payload["lanes"][1]["depends_on"] = ["L-01"]

    with pytest.raises(BatchPlanError, match=r"(?i)(cicl|cycle|depend)"):
        _validated(payload)


def test_llm_is_not_allowed_to_declare_waves() -> None:
    payload = _valid_payload()
    payload["waves"] = [["L-01", "L-02"], ["L-03", "L-04"]]

    with pytest.raises(BatchPlanError, match=r"(?i)waves"):
        _validated(payload)


def test_overlapping_ancestor_paths_are_split_into_stable_waves() -> None:
    payload = _valid_payload()
    payload["lanes"][0]["areas"] = ["project/android/ui/onboarding"]
    payload["lanes"][1]["areas"] = ["project/android/ui/onboarding/permissions"]
    payload["lanes"][2]["areas"] = ["project/backend"]
    payload["lanes"][3]["areas"] = ["project/android/ui/home"]
    plan = _validated(payload)

    waves = compute_waves(plan.lanes, max_parallel=4)

    assert [list(wave) for wave in waves] == [
        ["L-01", "L-03", "L-04"],
        ["L-02"],
    ]


def test_waves_honor_dependencies_and_parallel_cap_deterministically() -> None:
    payload = _valid_payload()
    payload["lanes"][3]["depends_on"] = ["L-02"]
    plan = _validated(payload)

    first_run = compute_waves(plan.lanes, max_parallel=2)
    second_run = compute_waves(plan.lanes, max_parallel=2)

    assert [list(wave) for wave in first_run] == [
        ["L-01", "L-02"],
        ["L-03", "L-04"],
    ]
    assert [list(wave) for wave in second_run] == [
        ["L-01", "L-02"],
        ["L-03", "L-04"],
    ]


def test_actual_changes_must_stay_in_lane_ownership_or_own_evidence() -> None:
    changed = [
        (
            "project/android/app/src/main/java/com/wifire/go/ui/"
            "onboarding/terms/TermsScreen.kt"
        ),
        (
            "project/android/app/src/androidTest/java/com/wifire/go/"
            "onboarding/terms/TermsScreenTest.kt"
        ),
        "docs/batches/onboarding-s07-s12/L-01/report.md",
        "project/android/app/src/main/java/com/wifire/go/ui/CanonicalScreen.kt",
        "docs/batches/onboarding-s07-s12/L-02/report.md",
    ]
    outside = paths_outside_ownership(
        changed,
        allowed=[
            ("project/android/app/src/main/java/com/wifire/go/ui/onboarding/terms"),
            ("project/android/app/src/androidTest/java/com/wifire/go/onboarding/terms"),
        ],
        evidence_root=POLICY["evidence_root"],
        lane_id="L-01",
    )

    assert set(outside) == {
        "project/android/app/src/main/java/com/wifire/go/ui/CanonicalScreen.kt",
        "docs/batches/onboarding-s07-s12/L-02/report.md",
    }


def test_ownership_comparison_is_component_aware_not_string_prefix_based() -> None:
    outside = paths_outside_ownership(
        [
            "project/android/onboarding/terms/Terms.kt",
            "project/android/onboarding/terms-evil/Escape.kt",
            "../project/android/onboarding/terms/Escape.kt",
            "/project/android/onboarding/terms/Escape.kt",
        ],
        allowed=["project/android/onboarding/terms"],
        evidence_root=POLICY["evidence_root"],
        lane_id="L-01",
    )

    assert set(outside) == {
        "project/android/onboarding/terms-evil/Escape.kt",
        "../project/android/onboarding/terms/Escape.kt",
        "/project/android/onboarding/terms/Escape.kt",
    }


def test_runtime_roundtrip_is_written_with_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state" / "builder-batch.yml"
    runtime = {
        "schema_version": 1,
        "request_sha256": _request_sha256(),
        "status": "running",
        "current_wave": 0,
        "lanes": {
            "L-01": {"status": "done"},
            "L-02": {"status": "running"},
        },
    }
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        assert source_path != target_path
        assert source_path.is_file()
        assert not target_path.exists()
        replacements.append((source_path, target_path))
        real_replace(source, target)

    monkeypatch.setattr(builder_batch.os, "replace", recording_replace)

    save_runtime(path, runtime)

    assert replacements and replacements[-1][1] == path
    assert load_runtime(path) == runtime
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_failed_atomic_replace_preserves_previous_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "builder-batch.yml"
    original = {"schema_version": 1, "status": "planned"}
    save_runtime(path, original)

    def fail_replace(_source, _target):
        raise OSError("replace interrompido")

    monkeypatch.setattr(builder_batch.os, "replace", fail_replace)

    with pytest.raises(BatchPlanError, match="replace interrompido"):
        save_runtime(path, {"schema_version": 1, "status": "running"})

    assert load_runtime(path) == original
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


@pytest.mark.parametrize(
    "content",
    [
        "schema_version: [\n",
        "- isto\n- nao\n- e\n- mapping\n",
    ],
)
def test_runtime_load_fails_closed_for_malformed_or_non_mapping_state(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "builder-batch.yml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(BatchPlanError, match=r"(?i)(runtime|yaml|mapping|vazio)"):
        load_runtime(path)
