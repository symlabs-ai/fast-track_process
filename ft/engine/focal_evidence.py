"""Fail-closed evidence contract for approvals after a focal correction.

The contract does not decide whether a product is correct.  It prevents a
reviewer from approving a stakeholder finding with evidence that cannot prove
the claim (for example, a mocked component test for data that must travel from
the real backend to a physical screen).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import unicodedata

import yaml


@dataclass(frozen=True)
class FocalEvidenceValidation:
    passed: bool
    reason: str = ""


_ANCHORS: dict[str, tuple[str, ...]] = {
    "telefone": ("telefone", "phone", "celular"),
    "e-mail": ("email", "e-mail"),
    "nome": ("nome", "name", "displayname", "display_name"),
    "sobrenome": ("sobrenome", "surname", "lastname", "last_name"),
    "foto": ("foto", "photo", "avatar"),
    "senha": ("senha", "password"),
    "permissões": ("permissao", "permissoes", "permission", "permissions"),
}
_UI_MARKERS = (
    "tela",
    "screen",
    "ui",
    "visual",
    "render",
    "exibir",
    "exibido",
    "aparece",
    "campo",
    "perfil",
)
_DATA_MARKERS = (
    "dado",
    "dados",
    "data",
    "cadastro",
    "cadastrado",
    "persist",
    "backend",
    "fastapi",
    "conta",
    "account",
)
_REAL_DATA_ORIGINS = {"real_system", "public_interface", "real_backend"}
_PHYSICAL_LEVELS = {"physical_e2e", "device_e2e"}
_VISUAL_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".xml"}


FOCAL_EVIDENCE_INSTRUCTIONS = """\
CONTRATO GLOBAL DE FIDELIDADE DA AUDITORIA FOCAL

Uma aprovação precisa terminar com `VERDICT: APPROVED` e incluir exatamente um
bloco YAML `focal_evidence`. `VERDICT: APPROVED` sem esse recibo será rejeitado
pela engine. Use esta estrutura:

```yaml
focal_evidence:
  coverage_complete: true
  finding_kind: ui_data | ui_visual | behavior | technical
  evidence_level: physical_e2e | device_e2e | integration | unit
  data_origin: real_system | public_interface | real_backend | local_product
  mock_only: false
  journey:
    - etapa realmente executada
  visual_evidence:
    - path/repo-local.png
  artifact:
    path: path/repo-local/current.apk
    sha256: hash do arquivo corrente
    observed_sha256: hash do artefato realmente instalado/observado
    evidence: path/repo-local/installed-artifact-hash.txt
  claims:
    - requirement: trecho verificável do finding
      expected: resultado exigido
      observed: resultado realmente observado
      status: PASS
      evidence:
        - path/repo-local.log
```

Liste uma claim para cada campo, dado, ação ou estado citado no finding. Para
dado que atravessa camadas e aparece em UI, mock, fixture ou teste isolado não
provam aceite: execute a jornada pela interface pública/fonte real, abra a tela
no dispositivo físico, compare esperado e observado campo a campo e preserve
captura/dump repo-local. Se isso não for possível ou qualquer claim falhar,
emita `VERDICT: REJECTED`; uma rejeição não precisa fabricar recibo de PASS.
Quando o finding citar APK ou hash, `artifact.path` deve apontar para o
candidato corrente e seus dois hashes devem coincidir com o SHA-256 real desse
arquivo; preserve também a saída sanitizada que mediu o artefato instalado.
Nunca grave dado pessoal bruto: masque valores mantendo a comparabilidade.
Token, senha ou credencial de sessão não podem aparecer em argumento de
processo, log ou artefato de evidência; reutilize o estado autenticado seguro
do dispositivo ou um mecanismo interno que não exteriorize o segredo.
"""


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _explicit_verdict(review_output: str) -> str | None:
    """Return the last explicit verdict outside fenced evidence blocks.

    Claim fields such as ``status: PASS`` are deliberately ignored.  Focal
    evidence is commonly emitted before the final verdict and must never make
    an approval look like a rejection (or vice versa).
    """

    verdict: str | None = None
    inside_fence = False
    for line in review_output.splitlines():
        if line.lstrip().startswith("```"):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            continue

        normalized = _normalized(line).strip("#>*- `")
        labeled = re.match(
            r"^(?:verdict|veredicto|resultado|result|parecer)\s*[:=-]\s*(.+)$",
            normalized,
        )
        candidate = labeled.group(1).strip() if labeled else normalized
        if candidate in {"approved", "approved with notes"}:
            verdict = "approved"
        elif candidate in {
            "rejected",
            "blocked",
            "incomplete",
            "incompleto",
            "iterate",
        }:
            verdict = "rejected"
    return verdict


def _evidence_records(review_output: str) -> list[dict[str, object]]:
    blocks = re.findall(
        r"```(?:yaml|yml)\s*\n(.*?)```",
        review_output,
        flags=re.IGNORECASE | re.DOTALL,
    )
    records: list[dict[str, object]] = []
    for block in blocks:
        try:
            payload = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("focal_evidence"), dict):
            records.append(payload["focal_evidence"])
    return records


def _safe_existing_path(project_root: Path, raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip())
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = project_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def _required_anchors(finding_context: str) -> dict[str, tuple[str, ...]]:
    context = _normalized(finding_context)
    required: dict[str, tuple[str, ...]] = {}
    for canonical, aliases in _ANCHORS.items():
        if any(re.search(rf"(?<!\w){re.escape(_normalized(alias))}(?!\w)", context) for alias in aliases):
            required[canonical] = aliases
    return required


def _requires_real_ui_data(finding_context: str) -> bool:
    context = _normalized(finding_context)
    has_ui = bool(re.search(r"(?<!\w)s\d{2}(?!\w)", context)) or any(
        marker in context for marker in _UI_MARKERS
    )
    has_data = bool(_required_anchors(finding_context)) or any(
        marker in context for marker in _DATA_MARKERS
    )
    return has_ui and has_data


def _requires_artifact_identity(finding_context: str) -> bool:
    context = _normalized(finding_context)
    return bool(re.search(r"(?<!\w)apk(?!\w)", context)) or any(
        marker in context for marker in ("sha256", "sha-256", "hash do apk")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def validate_focal_approval(
    *,
    review_output: str,
    finding_context: str,
    project_root: str | Path,
) -> FocalEvidenceValidation:
    """Validate evidence fidelity only when a focal reviewer approves.

    A rejection remains fail-safe and needs no approval receipt.  An approval
    must carry a structured, repository-verifiable claim matrix.  Findings
    about real data rendered in UI additionally require a physical end-to-end
    journey and cannot be proved by mocks or component fixtures.
    """

    verdict = _explicit_verdict(review_output)
    if verdict == "rejected":
        return FocalEvidenceValidation(True)
    if verdict != "approved":
        return FocalEvidenceValidation(
            False,
            "auditoria focal sem VERDICT explícito",
        )

    records = _evidence_records(review_output)
    if not records:
        return FocalEvidenceValidation(
            False,
            "aprovação focal sem bloco YAML focal_evidence",
        )
    if len(records) != 1:
        return FocalEvidenceValidation(
            False,
            "aprovação focal deve conter exatamente um bloco focal_evidence",
        )
    record = records[0]
    if record.get("coverage_complete") is not True:
        return FocalEvidenceValidation(
            False,
            "aprovação focal não declarou coverage_complete: true",
        )
    if record.get("mock_only") is not False:
        return FocalEvidenceValidation(
            False,
            "evidência mock-only/fixture não pode aprovar um finding focal",
        )

    claims = record.get("claims")
    if not isinstance(claims, list) or not claims:
        return FocalEvidenceValidation(False, "matriz focal de claims está vazia")

    root = Path(project_root)
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            return FocalEvidenceValidation(False, f"claim focal {index} inválido")
        missing = [
            field
            for field in ("requirement", "expected", "observed", "status", "evidence")
            if not claim.get(field)
        ]
        if missing:
            return FocalEvidenceValidation(
                False,
                f"claim focal {index} sem {', '.join(missing)}",
            )
        if _normalized(claim.get("status")) != "pass":
            return FocalEvidenceValidation(
                False,
                f"claim focal {index} não está PASS",
            )
        evidence_paths = _list_of_strings(claim.get("evidence"))
        if not evidence_paths or not all(
            _safe_existing_path(root, path) is not None for path in evidence_paths
        ):
            return FocalEvidenceValidation(
                False,
                f"claim focal {index} não possui evidência repo-local existente",
            )

    if not _requires_real_ui_data(finding_context):
        return FocalEvidenceValidation(True)

    if _normalized(record.get("finding_kind")) != "ui_data":
        return FocalEvidenceValidation(
            False,
            "finding de dados visíveis deve declarar finding_kind: ui_data",
        )
    if _normalized(record.get("evidence_level")) not in _PHYSICAL_LEVELS:
        return FocalEvidenceValidation(
            False,
            "finding de dados visíveis exige evidence_level: physical_e2e",
        )
    if _normalized(record.get("data_origin")) not in _REAL_DATA_ORIGINS:
        return FocalEvidenceValidation(
            False,
            "finding de dados visíveis exige origem real/public_interface, não fixture",
        )
    journey = _list_of_strings(record.get("journey"))
    if len(journey) < 3:
        return FocalEvidenceValidation(
            False,
            "finding de dados visíveis exige jornada real com ao menos três etapas",
        )

    visual_paths = _list_of_strings(record.get("visual_evidence"))
    resolved_visual = [_safe_existing_path(root, path) for path in visual_paths]
    if (
        not resolved_visual
        or any(path is None for path in resolved_visual)
        or not any(path.suffix.casefold() in _VISUAL_SUFFIXES for path in resolved_visual if path)
    ):
        return FocalEvidenceValidation(
            False,
            "finding de dados visíveis exige captura/dump físico repo-local",
        )

    required_anchors = _required_anchors(finding_context)
    claim_requirements = [
        _normalized(claim.get("requirement"))
        for claim in claims
        if isinstance(claim, dict)
    ]
    anchor_claims: dict[str, set[int]] = {}
    for canonical, aliases in required_anchors.items():
        matching_claims = {
            index
            for index, requirement in enumerate(claim_requirements)
            if any(
                re.search(
                    rf"(?<!\w){re.escape(_normalized(alias))}(?!\w)",
                    requirement,
                )
                for alias in aliases
            )
        }
        if not matching_claims:
            return FocalEvidenceValidation(
                False,
                f"matriz focal não cobre o campo citado: {canonical}",
            )
        anchor_claims[canonical] = matching_claims

    def has_distinct_claims(
        remaining: list[set[int]],
        used: set[int],
    ) -> bool:
        if not remaining:
            return True
        options = min(remaining, key=len)
        tail = list(remaining)
        tail.remove(options)
        return any(
            has_distinct_claims(tail, used | {claim_index})
            for claim_index in options - used
        )

    if not has_distinct_claims(list(anchor_claims.values()), set()):
        return FocalEvidenceValidation(
            False,
            "cada campo citado no finding exige uma claim separada",
        )

    if _requires_artifact_identity(finding_context):
        artifact = record.get("artifact")
        if not isinstance(artifact, dict):
            return FocalEvidenceValidation(
                False,
                "finding de APK/hash exige identidade do artefato corrente",
            )
        artifact_path = _safe_existing_path(root, artifact.get("path"))
        evidence_path = _safe_existing_path(root, artifact.get("evidence"))
        declared = _normalized(artifact.get("sha256")).removeprefix("sha256:")
        observed = _normalized(artifact.get("observed_sha256")).removeprefix(
            "sha256:"
        )
        if artifact_path is None or evidence_path is None:
            return FocalEvidenceValidation(
                False,
                "identidade do APK exige artefato e evidência repo-local existentes",
            )
        if not re.fullmatch(r"[0-9a-f]{64}", declared) or not re.fullmatch(
            r"[0-9a-f]{64}", observed
        ):
            return FocalEvidenceValidation(False, "hash de APK inválido ou ausente")
        actual = _sha256(artifact_path)
        if declared != actual or observed != actual:
            return FocalEvidenceValidation(
                False,
                "APK local, declarado e observado não possuem o mesmo SHA-256",
            )
        try:
            evidence_text = evidence_path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return FocalEvidenceValidation(False, "evidência do hash do APK ilegível")
        if observed not in _normalized(evidence_text):
            return FocalEvidenceValidation(
                False,
                "evidência física não registra o hash observado do APK",
            )

    required_screens = {
        match.upper()
        for match in re.findall(r"(?i)(?<!\w)s\d{2}(?!\w)", finding_context)
    }
    record_text = str(record).upper()
    missing_screens = sorted(screen for screen in required_screens if screen not in record_text)
    if missing_screens:
        return FocalEvidenceValidation(
            False,
            "matriz focal não cobre tela(s): " + ", ".join(missing_screens),
        )

    return FocalEvidenceValidation(True)
