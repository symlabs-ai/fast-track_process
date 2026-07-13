"""Batch de features paralelas — ft feature --parallel.

O stakeholder entrega N demandas de uma vez; um planner LLM declara as áreas
tocadas e as dependências entre elas; o ENGINE (nunca o LLM) computa as waves
deterministicamente: níveis topológicos por dependência, com guarda de overlap
de áreas dentro da mesma wave. Cada feature roda como um ciclo `feature`
normal em worktree próprio — possivelmente com engines/modelos diferentes — e
o orquestrador fecha e mergeia cada wave antes de liberar a seguinte.

O estado do batch vive em ``$FT_HOME/runtime/<projeto>/parallel/<batch-id>/``
(fora de worktrees/): o batch em si nunca aparece como ciclo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ft.engine import paths


class FeatureBatchError(ValueError):
    """Erro de parsing, plano ou estado de um batch de features."""


PLAN_SCHEMA_VERSION = 1
PLAN_FILENAME = "plan.yml"
BATCH_FILENAME = "batch.yml"

# Ciclo de vida de uma feature dentro do batch.
FEATURE_STATUSES = {
    "planned",  # aguardando a wave
    "setup",  # ciclo criado, ainda não executando
    "running",  # subprocess `ft continue --auto` em andamento
    "gate",  # pausada em human_gate aguardando o stakeholder
    "blocked",  # ciclo bloqueou (validators/retries esgotados)
    "done",  # ciclo terminou, aguardando close
    "merged",  # close + merge concluídos
    "failed",  # abandonada pelo stakeholder
    "skipped",  # dependência falhou — nunca executou
}

_FEATURE_ID_RE = re.compile(r"^F-[0-9]{2,}$")
_ENGINES = {"claude", "codex", "gemini", "opencode"}
_BACKLOG_ITEM_RE = re.compile(r"\bPB-(\d+)([A-Z]?)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Engines por feature
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineSpec:
    """Atribuição de executor por feature: engine[:model][@effort]."""

    engine: str
    model: str | None = None
    effort: str | None = None

    @property
    def label(self) -> str:
        parts = [self.engine]
        if self.model:
            parts.append(self.model)
        if self.effort:
            parts.append(f"@{self.effort}")
        return "/".join(parts[:2]) + (parts[2] if len(parts) > 2 else "")

    def to_dict(self) -> dict:
        return {"engine": self.engine, "model": self.model, "effort": self.effort}

    @staticmethod
    def from_dict(data: dict | None) -> "EngineSpec | None":
        if not isinstance(data, dict) or not data.get("engine"):
            return None
        return EngineSpec(
            engine=str(data["engine"]),
            model=data.get("model") or None,
            effort=data.get("effort") or None,
        )


def parse_engine_spec(raw: str) -> EngineSpec:
    """Parseia ``engine[:model][@effort]`` (ex.: ``codex:gpt-5.3@high``)."""
    text = raw.strip()
    if not text:
        raise FeatureBatchError("spec de engine vazia")
    effort = None
    if "@" in text:
        text, effort = text.rsplit("@", 1)
        effort = effort.strip() or None
    model = None
    if ":" in text:
        text, model = text.split(":", 1)
        model = model.strip() or None
    engine = text.strip().lower()
    if engine not in _ENGINES:
        raise FeatureBatchError(
            f"engine desconhecido: {engine!r} (aceitos: {', '.join(sorted(_ENGINES))})"
        )
    return EngineSpec(engine=engine, model=model, effort=effort)


def parse_engine_list(raw: str) -> list[EngineSpec]:
    """Parseia a lista de ``--engines`` separada por vírgula."""
    specs = [parse_engine_spec(item) for item in raw.split(",") if item.strip()]
    if not specs:
        raise FeatureBatchError("--engines precisa de ao menos uma spec")
    return specs


# ---------------------------------------------------------------------------
# Demandas
# ---------------------------------------------------------------------------


@dataclass
class BatchFeature:
    feature_id: str
    demand: str
    engine_spec: EngineSpec | None = None
    depends_on: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    cycle_name: str | None = None
    status: str = "planned"
    detail: str = ""
    reserved_backlog_item: str | None = None

    @property
    def slug(self) -> str:
        return slugify(self.demand)

    @property
    def title(self) -> str:
        first_line = self.demand.strip().splitlines()[0]
        return first_line[:72]

    def to_dict(self) -> dict:
        data = {
            "id": self.feature_id,
            "demand": self.demand,
            "engine": self.engine_spec.to_dict() if self.engine_spec else None,
            "depends_on": list(self.depends_on),
            "areas": list(self.areas),
            "cycle_name": self.cycle_name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.reserved_backlog_item:
            data["reserved_backlog_item"] = self.reserved_backlog_item
        return data

    @staticmethod
    def from_dict(data: dict) -> "BatchFeature":
        return BatchFeature(
            feature_id=str(data["id"]),
            demand=str(data["demand"]),
            engine_spec=EngineSpec.from_dict(data.get("engine")),
            depends_on=[str(item) for item in data.get("depends_on") or []],
            areas=[str(item) for item in data.get("areas") or []],
            cycle_name=data.get("cycle_name") or None,
            status=str(data.get("status") or "planned"),
            detail=str(data.get("detail") or ""),
            reserved_backlog_item=(
                str(data["reserved_backlog_item"]).upper()
                if data.get("reserved_backlog_item")
                else None
            ),
        )


def backlog_items(text: str) -> list[str]:
    """Retorna PBs normalizados na ordem em que aparecem em ``text``."""
    return [
        f"PB-{int(match.group(1)):03d}{match.group(2).upper()}"
        for match in _BACKLOG_ITEM_RE.finditer(text)
    ]


def explicit_backlog_item(demand: str) -> str | None:
    """PB explicitamente citado por uma demanda, quando inequívoco.

    Uma demanda que cita mais de um PB não identifica qual deles é seu item
    canônico. Nesse caso o batch falha antes do setup em vez de escolher um ID
    silenciosamente e orientar o discovery para o item errado.
    """
    unique = list(dict.fromkeys(backlog_items(demand)))
    if len(unique) > 1:
        raise FeatureBatchError(
            "demanda referencia mais de um PB; informe um único backlog item: "
            + ", ".join(unique)
        )
    return unique[0] if unique else None


def slugify(text: str, max_length: int = 24) -> str:
    """Slug estável para nomear ciclos: minúsculas, ascii, hífens."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text.strip().lower())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:max_length].rstrip("-") or "feature"


def split_input_demands(text: str) -> list[tuple[str, EngineSpec | None]]:
    """Divide um arquivo de demandas em features individuais.

    Formatos aceitos:
    - Seções ``## título`` — o corpo da seção é a demanda.
    - Blocos separados por linhas ``---``.
    Uma linha inicial ``engine: codex:gpt-5.3@high`` dentro da seção atribui
    o executor daquela feature.
    """
    body = text.strip()
    if not body:
        raise FeatureBatchError("arquivo de demandas vazio")

    blocks: list[str]
    if re.search(r"^##\s+", body, flags=re.MULTILINE):
        parts = re.split(r"^##\s+", body, flags=re.MULTILINE)
        blocks = [part.strip() for part in parts[1:] if part.strip()]
    else:
        blocks = [
            part.strip()
            for part in re.split(r"^---\s*$", body, flags=re.MULTILINE)
            if part.strip()
        ]
    if not blocks:
        raise FeatureBatchError("nenhuma demanda encontrada no arquivo")

    demands: list[tuple[str, EngineSpec | None]] = []
    for block in blocks:
        spec: EngineSpec | None = None
        content: list[str] = []
        for line in block.splitlines():
            match = re.match(r"^engine:\s*(\S.*)$", line.strip(), flags=re.IGNORECASE)
            if match and spec is None:
                spec = parse_engine_spec(match.group(1))
                continue
            content.append(line)
        demand = "\n".join(content).strip()
        if demand:
            demands.append((demand, spec))
    if not demands:
        raise FeatureBatchError("nenhuma demanda não-vazia no arquivo")
    return demands


def build_features(
    demands: list[tuple[str, EngineSpec | None]],
    engine_specs: list[EngineSpec] | None = None,
) -> list[BatchFeature]:
    """Cria as features com ids F-NN e engines atribuídos.

    Prioridade do executor: engine declarado na própria demanda >
    ``--engines`` em round-robin > default do projeto (spec None).
    """
    if len(demands) < 2:
        raise FeatureBatchError("--parallel exige ao menos 2 demandas")
    features: list[BatchFeature] = []
    for index, (demand, own_spec) in enumerate(demands):
        assigned = own_spec
        if assigned is None and engine_specs:
            assigned = engine_specs[index % len(engine_specs)]
        features.append(
            BatchFeature(
                feature_id=f"F-{index + 1:02d}",
                demand=demand,
                engine_spec=assigned,
            )
        )
    return features


# ---------------------------------------------------------------------------
# Plano (LLM declara, engine valida)
# ---------------------------------------------------------------------------


def build_planner_task(features: list[BatchFeature]) -> str:
    """Prompt do planner: declarar áreas e dependências — nunca as waves."""
    demands_block = "\n\n".join(
        f"### {feature.feature_id}\n{feature.demand}" for feature in features
    )
    ids = ", ".join(feature.feature_id for feature in features)
    return f"""Você é o planner de um batch de features paralelas.

Analise as demandas abaixo e o código/docs do projeto (leia docs/FEATURES.md,
docs/PROJECT_BACKLOG.md e a estrutura de src/ se existirem) e escreva o
arquivo plan.yml declarando, para CADA feature:

- as áreas do repositório que ela provavelmente tocará (paths relativos,
  ex.: src/api/, src/ui/settings/, docs/) — seja específico; áreas largas
  demais (src/) impedem paralelização;
- de quais outras features ela depende (depends_on) — apenas dependências
  REAIS: B depende de A quando B precisa do código/contrato que A cria.

NÃO agrupe em waves — isso é responsabilidade determinística do engine.

## Demandas

{demands_block}

## Formato OBRIGATÓRIO de plan.yml

schema_version: {PLAN_SCHEMA_VERSION}
features:
  - id: F-01
    areas:
      - src/exemplo/
    depends_on: []
    rationale: uma linha justificando áreas e dependências

Regras:
- Um item por feature, ids exatamente: {ids}.
- depends_on só pode referenciar esses ids (sem ciclos).
- Escreva SOMENTE o arquivo plan.yml. Ao final diga DONE.
"""


def validate_plan(data: object, features: list[BatchFeature]) -> list[str]:
    """Valida o plan.yml do planner contra as demandas. Retorna erros."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["plan.yml deve ser um mapping YAML"]
    if data.get("schema_version") != PLAN_SCHEMA_VERSION:
        errors.append(f"schema_version deve ser {PLAN_SCHEMA_VERSION}")
    entries = data.get("features")
    if not isinstance(entries, list):
        return errors + ["features deve ser uma lista"]

    expected = {feature.feature_id for feature in features}
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        prefix = f"features[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} deve ser um mapping")
            continue
        feature_id = entry.get("id")
        if not isinstance(feature_id, str) or not _FEATURE_ID_RE.fullmatch(feature_id):
            errors.append(f"{prefix}.id deve usar F-NN")
            continue
        if feature_id not in expected:
            errors.append(f"{feature_id} não corresponde a nenhuma demanda")
            continue
        if feature_id in seen:
            errors.append(f"id duplicado: {feature_id}")
            continue
        seen.add(feature_id)

        areas = entry.get("areas")
        if (
            not isinstance(areas, list)
            or not areas
            or not all(
                isinstance(area, str)
                and area.strip()
                and not Path(area).is_absolute()
                and ".." not in Path(area).parts
                for area in areas
            )
        ):
            errors.append(f"{feature_id}.areas exige paths relativos não-vazios")

        depends = entry.get("depends_on", [])
        if not isinstance(depends, list) or not all(
            isinstance(dep, str) for dep in depends
        ):
            errors.append(f"{feature_id}.depends_on deve ser lista de ids")
        else:
            unknown = [dep for dep in depends if dep not in expected]
            if unknown:
                errors.append(f"{feature_id}.depends_on desconhecidos: {unknown}")
            if feature_id in depends:
                errors.append(f"{feature_id} não pode depender de si mesma")

    missing = expected - seen
    if missing:
        errors.append(f"features ausentes do plano: {', '.join(sorted(missing))}")
    return errors


def apply_plan(data: dict, features: list[BatchFeature]) -> None:
    """Copia áreas e dependências validadas do plano para as features."""
    by_id = {feature.feature_id: feature for feature in features}
    for entry in data.get("features", []):
        feature = by_id[str(entry["id"])]
        feature.areas = [str(area).strip() for area in entry.get("areas", [])]
        feature.depends_on = sorted(str(dep) for dep in entry.get("depends_on", []))


# ---------------------------------------------------------------------------
# Waves — determinístico, responsabilidade do engine
# ---------------------------------------------------------------------------


def _areas_overlap(a: str, b: str) -> bool:
    """Duas áreas conflitam quando uma é prefixo da outra (ou iguais)."""
    left = a.strip().strip("/")
    right = b.strip().strip("/")
    if not left or not right:
        return True
    left_parts = left.split("/")
    right_parts = right.split("/")
    shorter, longer = sorted((left_parts, right_parts), key=len)
    return longer[: len(shorter)] == shorter


def features_conflict(a: BatchFeature, b: BatchFeature) -> bool:
    return any(
        _areas_overlap(area_a, area_b) for area_a in a.areas for area_b in b.areas
    )


def compute_waves(features: list[BatchFeature]) -> list[list[str]]:
    """Níveis topológicos por depends_on + guarda de overlap de áreas.

    Dentro de um nível, duas features que compartilham área não podem rodar
    juntas (mergeariam a mesma região): a de id maior desce para uma sub-wave
    seguinte. Ordem estável por id — o resultado é reproduzível.
    """
    by_id = {feature.feature_id: feature for feature in features}
    remaining_deps = {
        feature.feature_id: set(feature.depends_on) for feature in features
    }
    placed: set[str] = set()
    waves: list[list[str]] = []

    while len(placed) < len(features):
        level = sorted(
            feature_id
            for feature_id, deps in remaining_deps.items()
            if feature_id not in placed and deps <= placed
        )
        if not level:
            unresolved = sorted(set(remaining_deps) - placed)
            raise FeatureBatchError(
                f"dependência cíclica entre features: {', '.join(unresolved)}"
            )
        # Guarda de overlap: aloca gulosamente em sub-waves sem conflito.
        pending = list(level)
        while pending:
            wave: list[str] = []
            leftover: list[str] = []
            for feature_id in pending:
                feature = by_id[feature_id]
                if any(features_conflict(feature, by_id[member]) for member in wave):
                    leftover.append(feature_id)
                else:
                    wave.append(feature_id)
            waves.append(wave)
            placed.update(wave)
            pending = leftover

    return waves


# ---------------------------------------------------------------------------
# Estado do batch
# ---------------------------------------------------------------------------


@dataclass
class FeatureBatch:
    batch_id: str
    project_root: str
    template: str
    features: list[BatchFeature]
    waves: list[list[str]]
    current_wave: int = 0
    status: str = "planned"  # planned | running | paused | done | failed
    max_parallel: int = 2

    def feature(self, feature_id: str) -> BatchFeature:
        for feature in self.features:
            if feature.feature_id == feature_id:
                return feature
        raise FeatureBatchError(f"feature desconhecida: {feature_id}")

    def wave_features(self, wave_index: int) -> list[BatchFeature]:
        return [self.feature(feature_id) for feature_id in self.waves[wave_index]]

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "project_root": self.project_root,
            "template": self.template,
            "status": self.status,
            "current_wave": self.current_wave,
            "max_parallel": self.max_parallel,
            "waves": [list(wave) for wave in self.waves],
            "features": [feature.to_dict() for feature in self.features],
        }

    @staticmethod
    def from_dict(data: dict) -> "FeatureBatch":
        return FeatureBatch(
            batch_id=str(data["batch_id"]),
            project_root=str(data["project_root"]),
            template=str(data.get("template") or "feature"),
            features=[BatchFeature.from_dict(item) for item in data["features"]],
            waves=[[str(fid) for fid in wave] for wave in data["waves"]],
            current_wave=int(data.get("current_wave") or 0),
            status=str(data.get("status") or "planned"),
            max_parallel=int(data.get("max_parallel") or 2),
        )


def parallel_home(project_root: str | Path) -> Path:
    """Batches de features paralelas — runtime, nunca worktrees/."""
    return paths.runtime_home(project_root) / "parallel"


def batch_dir(project_root: str | Path, batch_id: str) -> Path:
    if not batch_id or Path(batch_id).name != batch_id:
        raise FeatureBatchError(f"batch_id inválido: {batch_id!r}")
    return parallel_home(project_root) / batch_id


def new_batch_id(project_root: str | Path) -> str:
    home = parallel_home(project_root)
    existing = []
    if home.is_dir():
        for item in home.iterdir():
            if item.is_dir() and item.name.startswith("batch-"):
                suffix = item.name[len("batch-") :]
                if suffix.isdigit():
                    existing.append(int(suffix))
    return f"batch-{(max(existing) + 1 if existing else 1):02d}"


def save_batch(batch: FeatureBatch) -> Path:
    directory = batch_dir(batch.project_root, batch.batch_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / BATCH_FILENAME
    target.write_text(
        yaml.safe_dump(batch.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def load_batch(project_root: str | Path, batch_id: str) -> FeatureBatch:
    target = batch_dir(project_root, batch_id) / BATCH_FILENAME
    if not target.is_file():
        raise FeatureBatchError(f"batch não encontrado: {batch_id}")
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise FeatureBatchError(f"batch.yml inválido em {target}: {exc}") from exc
    return FeatureBatch.from_dict(data)


def latest_batch_id(project_root: str | Path) -> str | None:
    home = parallel_home(project_root)
    if not home.is_dir():
        return None
    candidates = [
        item.name
        for item in home.iterdir()
        if item.is_dir() and (item / BATCH_FILENAME).is_file()
    ]
    if not candidates:
        return None
    return max(candidates)
