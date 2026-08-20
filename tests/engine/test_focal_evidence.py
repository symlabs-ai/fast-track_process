import hashlib
from pathlib import Path

from ft.engine.focal_evidence import validate_focal_approval


UI_DATA_FINDING = (
    "Na tela S44 os dados reais da conta não aparecem: o telefone cadastrado "
    "e o e-mail retornado pelo FastAPI precisam ser exibidos."
)


def _review(block: str) -> str:
    return f"VERDICT: APPROVED\n\n```yaml\n{block}\n```\n"


def _review_with_verdict_at_end(block: str) -> str:
    return f"```yaml\n{block}\n```\n\nVERDICT: APPROVED\n"


def _agent_identity_receipt(root: Path) -> None:
    receipt = root / "docs" / "agent-test-identity.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        '{"identity_ref":"agent_e2e_01","environment":"staging",'
        '"seed_status":"ready","seeded":true,"idempotent":true,'
        '"resettable":true,"journey_ready":true,'
        '"credentials_source":"secret_store",'
        '"secret_values_recorded":false}\n',
        encoding="utf-8",
    )


def test_ui_data_approval_rejects_mock_only_component_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "docs" / "s44-component.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"component screenshot")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: component
  data_origin: fixture
  mock_only: true
  journey: [render mocked S44]
  visual_evidence: [docs/s44-component.png]
  claims:
    - requirement: telefone e e-mail aparecem na S44
      expected: dados cadastrados visíveis
      observed: fixture renderizada
      status: PASS
      evidence: [docs/s44-component.png]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "mock" in result.reason.casefold()


def test_mock_only_approval_is_rejected_when_verdict_follows_claim_status(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "docs" / "s44-component.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"component screenshot")

    result = validate_focal_approval(
        review_output=_review_with_verdict_at_end(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: unit
  data_origin: local_product
  mock_only: true
  journey: [render mocked S44]
  visual_evidence: [docs/s44-component.png]
  claims:
    - requirement: telefone e e-mail aparecem na S44
      expected: dados cadastrados visíveis
      observed: fixture renderizada
      status: PASS
      evidence: [docs/s44-component.png]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "mock" in result.reason.casefold()


def test_visual_finding_ignores_incidental_data_policy_language(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "docs" / "s19-modal.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"modal screenshot")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_visual
  evidence_level: physical_e2e
  data_origin: local_product
  mock_only: false
  journey: [abrir S19 e comparar o diálogo]
  visual_evidence: [docs/s19-modal.png]
  claims:
    - requirement: S19 renderiza diálogo central sobre a superfície de origem
      expected: diálogo central com backdrop
      observed: diálogo central com backdrop
      status: PASS
      evidence: [docs/s19-modal.png]"""
        ),
        finding_context=(
            "S19 deve renderizar como diálogo central. Dados determinísticos "
            "podem apoiar a renderização, mas não comprovam backend ou persistência."
        ),
        project_root=tmp_path,
    )

    assert result.passed, result.reason


def test_http_html_smoke_is_not_reclassified_as_real_ui_data(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "docs" / "smoke-report.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("GET /: HTTP 200, text/html, non-empty body\n", encoding="utf-8")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: behavior
  evidence_level: integration
  data_origin: local_product
  mock_only: false
  journey: [start isolated backend, execute GET /, inspect status and HTML body]
  visual_evidence: []
  claims:
    - requirement: GET / returns non-empty HTML with a 2xx status
      expected: HTTP 2xx and non-empty HTML
      observed: HTTP 200 and non-empty text/html body
      status: PASS
      evidence: [docs/smoke-report.md]"""
        ),
        finding_context=(
            "Corrija o smoke no backend: GET / deve retornar HTML não vazio com "
            "status 2xx, preservando /health e as rotas da API. Não rode a "
            "suíte completa durante a correção focal."
        ),
        project_root=tmp_path,
    )

    assert result.passed, result.reason


def test_named_ui_data_anchor_cannot_be_reclassified_as_visual(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "docs" / "s44-phone.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"phone screenshot")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_visual
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  journey: [abrir S44 e observar o telefone]
  visual_evidence: [docs/s44-phone.png]
  claims:
    - requirement: telefone aparece na S44
      expected: telefone visível
      observed: telefone visível
      status: PASS
      evidence: [docs/s44-phone.png]"""
        ),
        finding_context="Na tela S44, o telefone cadastrado deve aparecer.",
        project_root=tmp_path,
    )

    assert not result.passed
    assert "finding_kind: ui_data" in result.reason


def test_ui_data_approval_rejects_incomplete_field_coverage(tmp_path: Path) -> None:
    evidence = tmp_path / "docs" / "s44-device.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"physical screenshot")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  journey: [load account through public API, navigate to S44, compare rendered values]
  visual_evidence: [docs/s44-device.png]
  claims:
    - requirement: e-mail retornado aparece na S44
      expected: e-mail mascarado visível
      observed: e-mail mascarado visível
      status: PASS
      evidence: [docs/s44-device.png]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "telefone" in result.reason.casefold()


def test_ui_data_approval_accepts_complete_real_physical_journey(tmp_path: Path) -> None:
    screenshot = tmp_path / "docs" / "s44-device.png"
    dump = tmp_path / "docs" / "s44-device.xml"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"physical screenshot")
    dump.write_text("<hierarchy />", encoding="utf-8")
    _agent_identity_receipt(tmp_path)

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  test_identity:
    kind: dedicated_agent
    identity_ref: agent_e2e_01
    environment: staging
    seeded: true
    idempotent: true
    resettable: true
    journey_ready: true
    credentials_source: secret_store
    evidence: docs/agent-test-identity.json
  journey:
    - load the authenticated account through the public FastAPI contract
    - navigate through the installed app to S44
    - compare phone and e-mail from the source of truth with rendered values
  visual_evidence: [docs/s44-device.png, docs/s44-device.xml]
  claims:
    - requirement: telefone cadastrado aparece na S44
      expected: telefone mascarado visível
      observed: telefone mascarado visível
      status: PASS
      evidence: [docs/s44-device.png, docs/s44-device.xml]
    - requirement: e-mail retornado pelo FastAPI aparece na S44
      expected: e-mail mascarado visível
      observed: e-mail mascarado visível
      status: PASS
      evidence: [docs/s44-device.png, docs/s44-device.xml]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert result.passed, result.reason


def test_authenticated_ui_data_approval_requires_seeded_agent_identity(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "docs" / "s44-device.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"physical screenshot")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  journey:
    - authenticate a real account through the public API
    - navigate through the installed app to S44
    - compare phone and e-mail with the rendered values
  visual_evidence: [docs/s44-device.png]
  claims:
    - requirement: telefone cadastrado aparece na S44
      expected: telefone mascarado visível
      observed: telefone mascarado visível
      status: PASS
      evidence: [docs/s44-device.png]
    - requirement: e-mail retornado pelo FastAPI aparece na S44
      expected: e-mail mascarado visível
      observed: e-mail mascarado visível
      status: PASS
      evidence: [docs/s44-device.png]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "usuário dedicado do agente" in result.reason


def test_authenticated_ui_data_approval_rejects_sensitive_seed_receipt(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "docs" / "s44-device.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"physical screenshot")
    receipt = tmp_path / "docs" / "agent-test-identity.json"
    receipt.write_text(
        '{"identity_ref":"agent_e2e_01","environment":"staging",'
        '"seed_status":"ready","seeded":true,"idempotent":true,'
        '"resettable":true,"journey_ready":true,'
        '"credentials_source":"secret_store","secret_values_recorded":false,'
        '"bootstrap":{"password":"forbidden"}}',
        encoding="utf-8",
    )

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  test_identity:
    kind: dedicated_agent
    identity_ref: agent_e2e_01
    environment: staging
    seeded: true
    idempotent: true
    resettable: true
    journey_ready: true
    credentials_source: secret_store
    evidence: docs/agent-test-identity.json
  journey: [seed agent account, authenticate through public API, render S44]
  visual_evidence: [docs/s44-device.png]
  claims:
    - requirement: telefone cadastrado aparece na S44
      expected: telefone mascarado visível
      observed: telefone mascarado visível
      status: PASS
      evidence: [docs/s44-device.png]
    - requirement: e-mail retornado pelo FastAPI aparece na S44
      expected: e-mail mascarado visível
      observed: e-mail mascarado visível
      status: PASS
      evidence: [docs/s44-device.png]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "campo sensível" in result.reason


def test_permission_group_accepts_four_distinct_specific_claims(tmp_path: Path) -> None:
    screenshot = tmp_path / "docs" / "s44-device.png"
    permission_evidence = tmp_path / "docs" / "permissions.txt"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"physical screenshot")
    permission_evidence.write_text("four device permission states checked\n")
    _agent_identity_receipt(tmp_path)

    review_output = _review(
        """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_backend
  mock_only: false
  test_identity:
    kind: dedicated_agent
    identity_ref: agent_e2e_01
    environment: staging
    seeded: true
    idempotent: true
    resettable: true
    journey_ready: true
    credentials_source: secret_store
    evidence: docs/agent-test-identity.json
  journey: [seed agent account, authenticate through public API, render S44]
  visual_evidence: [docs/s44-device.png]
  claims:
    - requirement: telefone real em S44
      expected: visível
      observed: visível
      status: PASS
      evidence: [docs/s44-device.png]
    - requirement: e-mail real em S44
      expected: visível
      observed: visível
      status: PASS
      evidence: [docs/s44-device.png]
    - requirement: localização aproximada reflete o gateway Android
      expected: estado correspondente
      observed: estado correspondente
      status: PASS
      evidence: [docs/permissions.txt]
    - requirement: dispositivos Wi-Fi próximos refletem o gateway Android
      expected: estado correspondente
      observed: estado correspondente
      status: PASS
      evidence: [docs/permissions.txt]
    - requirement: notificações refletem o gateway Android
      expected: estado correspondente
      observed: estado correspondente
      status: PASS
      evidence: [docs/permissions.txt]
    - requirement: localização em segundo plano reflete o gateway Android
      expected: estado correspondente
      observed: estado correspondente
      status: PASS
      evidence: [docs/permissions.txt]"""
    )
    result = validate_focal_approval(
        review_output=review_output,
        finding_context=(
            "Na tela S44, telefone, e-mail e as quatro permissões devem refletir "
            "os dados reais e o gateway Android."
        ),
        project_root=tmp_path,
    )

    assert result.passed, result.reason

    complete_set_result = validate_focal_approval(
        review_output=review_output,
        finding_context=(
            "Na tela S44, telefone, e-mail e o conjunto completo de permissões "
            "devem refletir os dados reais e o gateway Android."
        ),
        project_root=tmp_path,
    )
    assert complete_set_result.passed, complete_set_result.reason


def test_rejected_review_never_needs_approval_evidence(tmp_path: Path) -> None:
    result = validate_focal_approval(
        review_output="VERDICT: REJECTED\nTelefone ausente na S44.",
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert result.passed


def test_review_without_explicit_verdict_is_fail_closed(tmp_path: Path) -> None:
    result = validate_focal_approval(
        review_output="A inspeção parece correta, mas não emitiu parecer final.",
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "verdict" in result.reason.casefold()


def test_approval_rejects_multiple_focal_evidence_receipts(tmp_path: Path) -> None:
    block = """focal_evidence:
  coverage_complete: true
  mock_only: false
  claims:
    - requirement: telefone na S44
      expected: visível
      observed: visível
      status: PASS
      evidence: [docs/device.png]"""
    evidence = tmp_path / "docs" / "device.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"device")

    result = validate_focal_approval(
        review_output=(
            f"VERDICT: APPROVED\n```yaml\n{block}\n```\n"
            f"```yaml\n{block}\n```\n"
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "exatamente um" in result.reason.casefold()


def test_real_ui_data_requires_one_claim_per_cited_field(tmp_path: Path) -> None:
    screenshot = tmp_path / "docs" / "s44-device.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"physical screenshot")

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  journey: [load real account, navigate to S44, compare rendered fields]
  visual_evidence: [docs/s44-device.png]
  claims:
    - requirement: telefone e e-mail aparecem na S44
      expected: ambos visíveis
      observed: ambos visíveis
      status: PASS
      evidence: [docs/s44-device.png]
    - requirement: tela S44 aberta no aparelho
      expected: tela física
      observed: tela física
      status: PASS
      evidence: [docs/s44-device.png]"""
        ),
        finding_context=UI_DATA_FINDING,
        project_root=tmp_path,
    )

    assert not result.passed
    assert "claim separada" in result.reason.casefold()


def test_apk_finding_rejects_hash_not_observed_on_device(tmp_path: Path) -> None:
    apk = tmp_path / "build" / "app.apk"
    screenshot = tmp_path / "docs" / "s44.png"
    hash_evidence = tmp_path / "docs" / "installed-hash.txt"
    apk.parent.mkdir(parents=True)
    screenshot.parent.mkdir(parents=True)
    apk.write_bytes(b"current candidate")
    screenshot.write_bytes(b"physical screenshot")
    current_hash = hashlib.sha256(apk.read_bytes()).hexdigest()
    other_hash = "f" * 64
    hash_evidence.write_text(f"installed_sha256={other_hash}\n", encoding="utf-8")

    result = validate_focal_approval(
        review_output=_review(
            f"""focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  journey: [load real account, install current APK, navigate to S44]
  visual_evidence: [docs/s44.png]
  artifact:
    path: build/app.apk
    sha256: {current_hash}
    observed_sha256: {other_hash}
    evidence: docs/installed-hash.txt
  claims:
    - requirement: telefone cadastrado aparece na S44
      expected: telefone visível
      observed: telefone visível
      status: PASS
      evidence: [docs/s44.png]
    - requirement: e-mail retornado aparece na S44
      expected: e-mail visível
      observed: e-mail visível
      status: PASS
      evidence: [docs/s44.png]"""
        ),
        finding_context=f"{UI_DATA_FINDING} APK físico sha256 corrente.",
        project_root=tmp_path,
    )

    assert not result.passed
    assert "mesmo sha-256" in result.reason.casefold()


def test_apk_finding_accepts_matching_local_and_observed_hash(tmp_path: Path) -> None:
    apk = tmp_path / "build" / "app.apk"
    screenshot = tmp_path / "docs" / "s44.png"
    hash_evidence = tmp_path / "docs" / "installed-hash.txt"
    apk.parent.mkdir(parents=True)
    screenshot.parent.mkdir(parents=True)
    apk.write_bytes(b"current candidate")
    screenshot.write_bytes(b"physical screenshot")
    current_hash = hashlib.sha256(apk.read_bytes()).hexdigest()
    hash_evidence.write_text(f"installed_sha256={current_hash}\n", encoding="utf-8")
    _agent_identity_receipt(tmp_path)

    result = validate_focal_approval(
        review_output=_review(
            f"""focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
  test_identity:
    kind: dedicated_agent
    identity_ref: agent_e2e_01
    environment: staging
    seeded: true
    idempotent: true
    resettable: true
    journey_ready: true
    credentials_source: secret_store
    evidence: docs/agent-test-identity.json
  journey: [load real account, install current APK, navigate to S44]
  visual_evidence: [docs/s44.png]
  artifact:
    path: build/app.apk
    sha256: {current_hash}
    observed_sha256: {current_hash}
    evidence: docs/installed-hash.txt
  claims:
    - requirement: telefone cadastrado aparece na S44
      expected: telefone visível
      observed: telefone visível
      status: PASS
      evidence: [docs/s44.png]
    - requirement: e-mail retornado aparece na S44
      expected: e-mail visível
      observed: e-mail visível
      status: PASS
      evidence: [docs/s44.png]"""
        ),
        finding_context=f"{UI_DATA_FINDING} APK físico sha256 corrente.",
        project_root=tmp_path,
    )

    assert result.passed, result.reason


def test_headless_approval_does_not_inherit_physical_ui_requirements(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "docs" / "headless-regression.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("25/25 programmatic checks passed\n", encoding="utf-8")
    review_output = _review(
        """focal_evidence:
  coverage_complete: true
  finding_kind: technical
  evidence_level: integration
  data_origin: local_product
  mock_only: false
  journey: [execute public SDK contract, inspect sanitized result]
  visual_evidence: []
  claims:
    - requirement: headless SDK contract
      expected: all programmatic checks pass
      observed: 25/25 checks passed
      status: PASS
      evidence: [docs/headless-regression.txt]"""
    )
    misleading_meta_context = (
        "EVIDENCE_FIDELITY_REJECTED: finding de dados visíveis deve declarar "
        "finding_kind: ui_data. Confirme evidência visual quando o finding for de UI."
    )

    ui_result = validate_focal_approval(
        review_output=review_output,
        finding_context=misleading_meta_context,
        project_root=tmp_path,
    )
    headless_result = validate_focal_approval(
        review_output=review_output,
        finding_context=misleading_meta_context,
        project_root=tmp_path,
        ui_validation_enabled=False,
    )

    assert not ui_result.passed
    assert headless_result.passed, headless_result.reason

    wrong_kind_result = validate_focal_approval(
        review_output=review_output.replace(
            "finding_kind: technical",
            "finding_kind: ui_data",
        ),
        finding_context=misleading_meta_context,
        project_root=tmp_path,
        ui_validation_enabled=False,
    )
    assert not wrong_kind_result.passed
    assert "headless" in wrong_kind_result.reason
