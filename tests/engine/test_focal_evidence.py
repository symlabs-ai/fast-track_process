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

    result = validate_focal_approval(
        review_output=_review(
            """focal_evidence:
  coverage_complete: true
  finding_kind: ui_data
  evidence_level: physical_e2e
  data_origin: real_system
  mock_only: false
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
