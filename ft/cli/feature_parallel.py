"""Orquestrador do ``ft feature --parallel``.

Recebe N demandas, planeja dependências (LLM declara áreas/depends_on; o
engine computa as waves deterministicamente) e executa cada feature como um
ciclo ``feature`` normal em worktree próprio — possivelmente com engines e
modelos diferentes — paralelizando dentro de cada wave.

Contratos com o resto do engine:
- setup dos ciclos é in-process e sequencial (git não aceita corrida);
- a execução longa é por subprocess ``ft continue --auto --cycle <nome>``
  com log por feature — o run para sozinho em human_gate, BLOCK ou fim;
- gates são apresentados inline neste terminal (PV-9: auto ≠ bypass);
- ao fim da wave, cada ciclo done é fechado com merge full em ordem estável;
  a wave seguinte nasce do HEAD já mergeado. Conflito de merge pausa o batch
  preservando worktree e branch (mesma garantia do ft close manual).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from ft.engine import feature_batch as fb
from ft.engine import paths

POLL_SECONDS = 5
RATE_LIMIT_RETRY_SECONDS = 60
MAX_RATE_LIMIT_RESPAWNS = 3

_TERMINAL = {"merged", "failed", "skipped"}
_EXTERNAL_CLOSE_CANDIDATES = {"setup", "running", "gate", "blocked", "done"}
_GIT_OBJECT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_RESUMABLE_BATCH_STATUSES = {"planned", "running", "paused"}


@dataclass(frozen=True)
class ParallelPolicy:
    """Process-owned limits for the shared ``ft feature --parallel`` runner."""

    planner_timeout_seconds: int | None
    rate_limit_respawns: int


_DEFAULT_PARALLEL_POLICY = ParallelPolicy(
    planner_timeout_seconds=None,
    rate_limit_respawns=MAX_RATE_LIMIT_RESPAWNS,
)
_HISTORICAL_PARALLEL_POLICIES = {
    # Preserve the contract of already-materialized tweak forks that predate
    # the declarative policy. New templates must declare their own values.
    "tweak": ParallelPolicy(planner_timeout_seconds=120, rate_limit_respawns=0),
}


def _parallel_policy_source(root: Path, template: str) -> Path | None:
    """Prefer the project-owned process and fall back to its global template."""
    local = paths.project_named_process_file(root, template)
    if local.is_symlink():
        raise ValueError(f"processo paralelo local não pode ser symlink: {local}")
    if local.is_file():
        return local

    global_process = _cli().engine_root() / "templates" / template / "process.yml"
    return global_process if global_process.is_file() else None


def _parallel_policy(root: Path, template: str) -> ParallelPolicy:
    """Load a template's declarative policy with backward-compatible defaults."""
    defaults = _HISTORICAL_PARALLEL_POLICIES.get(
        template, _DEFAULT_PARALLEL_POLICY
    )
    source = _parallel_policy_source(root, template)
    if source is None:
        return defaults
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"processo paralelo inválido em {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"processo paralelo inválido em {source}: raiz deve ser mapping")

    raw_policy = payload.get("parallel_policy")
    if raw_policy is None:
        return defaults
    if not isinstance(raw_policy, dict):
        raise ValueError(
            f"parallel_policy inválida em {source}: esperado mapping"
        )

    timeout = raw_policy.get(
        "planner_timeout_seconds", defaults.planner_timeout_seconds
    )
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0
    ):
        raise ValueError(
            f"parallel_policy.planner_timeout_seconds inválido em {source}: "
            "esperado inteiro positivo ou null"
        )

    respawns = raw_policy.get("rate_limit_respawns", defaults.rate_limit_respawns)
    if (
        isinstance(respawns, bool)
        or not isinstance(respawns, int)
        or respawns < 0
    ):
        raise ValueError(
            f"parallel_policy.rate_limit_respawns inválido em {source}: "
            "esperado inteiro não negativo"
        )
    return ParallelPolicy(
        planner_timeout_seconds=timeout,
        rate_limit_respawns=respawns,
    )


def _rate_limit_respawn_limit(batch: fb.FeatureBatch) -> int:
    """Resolve the selected process policy without forking the orchestrator."""
    return _parallel_policy(
        Path(batch.project_root), batch.template
    ).rate_limit_respawns


def _cli():
    from ft.cli import main as cli_main

    return cli_main


def _ui():
    from ft.engine import ui

    return ui


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def run_parallel_batch(args) -> None:
    """Entry point chamado por cmd_feature quando --parallel está presente."""
    cli = _cli()
    ui = _ui()
    sys.stdout.reconfigure(line_buffering=True)

    root = cli.find_project_root().resolve()

    resume = getattr(args, "resume", None)
    if resume:
        batch_id = resume if isinstance(resume, str) else fb.latest_batch_id(root)
        if not batch_id:
            print(ui.fail("Nenhum batch encontrado para retomar."))
            sys.exit(1)
        batch = fb.load_batch(root, str(batch_id))
        requested_template = getattr(args, "template", None)
        if requested_template and str(requested_template) != batch.template:
            raise ValueError(
                f"batch {batch.batch_id} usa o template '{batch.template}'; "
                f"--template {requested_template} não pode trocar o processo na retomada"
            )
        if batch.status == "done":
            print(ui.warn(f"Batch {batch.batch_id} já concluído."))
            return
        if batch.status not in _RESUMABLE_BATCH_STATUSES:
            print(
                ui.fail(
                    f"Batch {batch.batch_id} não pode ser retomado no estado "
                    f"'{batch.status}'. Inicie um novo batch."
                )
            )
            sys.exit(1)
        print(
            ui.header(
                f"Retomando batch {batch.batch_id} (wave {batch.current_wave + 1}/{len(batch.waves)})"
            )
        )
    else:
        batch = _plan_batch(args, root)
        if batch is None:
            return

    batch.status = "running"
    fb.save_batch(batch)
    _execute_batch(batch, args)


# ---------------------------------------------------------------------------
# Planejamento
# ---------------------------------------------------------------------------


def _collect_demands(args) -> list[tuple[str, fb.EngineSpec | None]]:
    positional = [d for d in (getattr(args, "demand", None) or []) if str(d).strip()]
    input_file = getattr(args, "feature_input", None)
    if positional and input_file:
        raise ValueError("informe demandas posicionais ou --input FILE, não ambos")
    if input_file:
        source = Path(input_file).expanduser()
        if not source.is_absolute():
            source = Path.cwd() / source
        if not source.is_file():
            raise FileNotFoundError(f"arquivo de demandas não encontrado: {source}")
        return fb.split_input_demands(source.read_text(encoding="utf-8"))
    return [(str(demand), None) for demand in positional]


def _preflight(root: Path, args) -> None:
    """Mesmas garantias do ft feature avulso, mais exclusividade do batch."""
    cli = _cli()

    if (
        not paths.project_manifest(root).is_file()
        or cli.find_process_yaml(root) is None
    ):
        raise ValueError(
            "ft feature --parallel exige um projeto já inicializado; "
            "execute ft init <nome> --template <template> primeiro"
        )
    cli._warn_process_drift(root, str(getattr(args, "template", None) or "feature"))
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or head.returncode != 0:
        raise RuntimeError(
            "ft feature --parallel exige um repositório Git com commit inicial"
        )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if dirty.stdout.strip():
        raise RuntimeError(
            "commite as mudanças do checkout principal antes do batch:\n"
            + dirty.stdout.strip()
        )
    active = cli._check_active_run(root)
    if active and not getattr(args, "force", False):
        raise RuntimeError(
            f"já existe um ciclo ativo: {active}. Encerre-o (ft close/abort) "
            "antes de iniciar um batch paralelo, ou use --force"
        )


def _write_planner_context(
    root: Path, batch_directory: Path, features: list[fb.BatchFeature]
) -> None:
    """Contexto hermético para o planner — ele não navega o repositório."""
    context = batch_directory / "context"
    context.mkdir(parents=True, exist_ok=True)

    demands = "\n\n".join(
        f"## {feature.feature_id}\n{feature.demand}" for feature in features
    )
    (context / "demands.md").write_text(demands + "\n", encoding="utf-8")

    for name in ("FEATURES.md", "PROJECT_BACKLOG.md", "PRD.md", "api_contract.md"):
        source = root / "docs" / name
        if source.is_file():
            (context / name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )

    tree = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if tree.returncode == 0:
        lines = tree.stdout.strip().splitlines()[:600]
        (context / "tree.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _planner_task(features: list[fb.BatchFeature]) -> str:
    base = fb.build_planner_task(features)
    return base + """
Contexto disponível (somente leitura):
- context/demands.md — as demandas acima
- context/tree.txt — arquivos versionados do projeto
- context/FEATURES.md, context/PROJECT_BACKLOG.md, context/PRD.md,
  context/api_contract.md — quando existirem

Observação sobre áreas: declare apenas áreas de CÓDIGO (src/, scripts/,
tests/...). Docs canônicos (docs/FEATURES.md, docs/PROJECT_BACKLOG.md,
CHANGELOG.md) são compartilhados por todas as features por design e NÃO devem
aparecer em areas.
"""


def _run_planner(
    batch_directory: Path,
    features: list[fb.BatchFeature],
    *,
    llm_engine: str,
    llm_model: str | None,
    llm_effort: str | None,
    llm_timeout_seconds: int | None = None,
) -> dict:
    """Roda o planner e valida o plano; uma retentativa dentro do mesmo budget."""
    from ft.engine.delegate import delegate_to_llm

    plan_path = batch_directory / fb.PLAN_FILENAME
    task = _planner_task(features)
    feedback = ""
    deadline = (
        time.monotonic() + llm_timeout_seconds
        if llm_timeout_seconds is not None
        else None
    )
    for attempt in (1, 2):
        remaining_timeout = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            remaining_timeout = int(remaining)
        result = delegate_to_llm(
            task=task + feedback,
            project_root=str(batch_directory),
            allowed_paths=[fb.PLAN_FILENAME],
            llm_engine=llm_engine,
            llm_model=llm_model,
            llm_effort=llm_effort,
            llm_timeout_seconds=remaining_timeout,
            log_path=str(batch_directory / "logs" / f"planner_{attempt:02d}.log"),
        )
        if not result.success:
            feedback = "\n\nA tentativa anterior falhou; escreva plan.yml completo."
            continue
        if not plan_path.is_file():
            feedback = "\n\nVocê não escreveu plan.yml. Escreva-o agora."
            continue
        try:
            data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            feedback = f"\n\nplan.yml anterior tinha YAML inválido ({exc}). Reescreva."
            continue
        errors = fb.validate_plan(data, features)
        if not errors:
            return data
        feedback = "\n\nO plan.yml anterior falhou na validação:\n- " + "\n- ".join(
            errors
        )
    raise fb.FeatureBatchError(
        "planner não produziu um plan.yml válido dentro do budget/2 tentativas; "
        f"inspecione {batch_directory / 'logs'}"
    )


def _print_plan(batch: fb.FeatureBatch) -> None:
    ui = _ui()
    print()
    print(
        ui.header(
            f"Plano do batch {batch.batch_id} — {len(batch.features)} demanda(s), "
            f"{len(batch.waves)} wave(s)"
        )
    )
    for wave_index, wave in enumerate(batch.waves, start=1):
        print(f"\n  Wave {wave_index}:")
        for feature_id in wave:
            feature = batch.feature(feature_id)
            worker_engine = _worker_engine_spec(batch, feature)
            engine = worker_engine.label if worker_engine else "default"
            deps = f"  ← {', '.join(feature.depends_on)}" if feature.depends_on else ""
            print(f"    {feature.feature_id} [{engine}] {feature.title}{deps}")
            if feature.areas:
                print(f"      áreas: {', '.join(feature.areas)}")
    print()


def _plan_batch(args, root: Path) -> fb.FeatureBatch | None:
    cli = _cli()
    ui = _ui()

    demands = _collect_demands(args)
    engines_raw = getattr(args, "engines", None)
    engine_specs = fb.parse_engine_list(engines_raw) if engines_raw else None
    features = fb.build_features(demands, engine_specs)

    _preflight(root, args)

    # Policy, defaults e reserva do batch formam uma única leitura pinada do
    # processo. Depois de save_batch, o guard enxerga o template reservado e o
    # planner (LLM) roda fora do lock, sem pausar processos disjuntos.
    from ft.engine.layout import (
        _assert_no_exclusive_startup,
        _manifest_write_lock,
    )

    with _manifest_write_lock(root):
        _assert_no_exclusive_startup(root)
        template = cli.resolve_feature_template(getattr(args, "template", None))
        parallel_policy = _parallel_policy(root, template)

        manifest_engine, manifest_model, manifest_effort = (
            cli.manifest_llm_defaults(root)
        )
        requested_engine = cli.resolve_llm_engine(args)
        requested_model = cli.resolve_llm_model(args)
        requested_effort = cli.resolve_llm_effort(args)
        planner_engine = requested_engine or manifest_engine or "claude"
        manifest_is_compatible = (
            requested_engine is None or requested_engine == manifest_engine
        )
        planner_model = requested_model or (
            manifest_model if manifest_is_compatible else None
        )
        planner_effort = requested_effort or (
            manifest_effort if manifest_is_compatible else None
        )

        active_batch = fb.latest_active_batch(root)
        if active_batch is not None and not getattr(args, "force", False):
            raise RuntimeError(
                f"já existe um batch paralelo ativo: {active_batch.batch_id} "
                f"({active_batch.status}). Retome-o com --resume "
                "ou use --force para iniciar outro."
            )
        batch_id = fb.new_batch_id(root)
        batch = fb.FeatureBatch(
            batch_id=batch_id,
            project_root=str(root),
            template=template,
            features=features,
            waves=[],
            status="planning",
            max_parallel=max(1, int(getattr(args, "max_parallel", None) or 2)),
            planner_engine=planner_engine,
            planner_model=planner_model,
            planner_effort=planner_effort,
        )
        fb.save_batch(batch)

    batch_directory = fb.batch_dir(root, batch_id)
    (batch_directory / "logs").mkdir(parents=True, exist_ok=True)

    try:
        _write_planner_context(root, batch_directory, features)
        print(
            ui.info(
                f"Planejando {len(features)} features (planner: {planner_engine})…"
            )
        )
        plan = _run_planner(
            batch_directory,
            features,
            llm_engine=planner_engine,
            llm_model=planner_model,
            llm_effort=planner_effort,
            llm_timeout_seconds=parallel_policy.planner_timeout_seconds,
        )
        fb.apply_plan(plan, features)
        waves = fb.compute_waves(features)
    except BaseException:
        batch.status = "failed"
        fb.save_batch(batch)
        raise

    batch.waves = waves
    batch.status = "planned"
    fb.save_batch(batch)
    _print_plan(batch)

    if not getattr(args, "yes", False):
        try:
            answer = input("Executar este plano? [s/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt, OSError):
            answer = ""
        if answer not in {"s", "sim", "y", "yes"}:
            batch.status = "failed"
            fb.save_batch(batch)
            print(ui.warn("Plano recusado — batch descartado."))
            return None
    return batch


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def _engine_cli_flags(spec: fb.EngineSpec | None) -> list[str]:
    if spec is None:
        return []
    flags = [f"--{spec.engine}"]
    if spec.model:
        flags.append(spec.model)
    if spec.effort:
        flags.extend(["--effort", spec.effort])
    return flags


def _worker_engine_spec(
    batch: fb.FeatureBatch, feature: fb.BatchFeature
) -> fb.EngineSpec | None:
    """Resolve o executor efetivo preservado pelo plano do batch.

    Uma atribuição explícita da feature (arquivo de demandas ou ``--engines``)
    continua tendo precedência. Sem override, os workers reutilizam a seleção
    global que o batch já resolveu e persistiu para o planner, inclusive após
    ``--resume``.
    """
    if feature.engine_spec is not None:
        return feature.engine_spec
    if not batch.planner_engine:
        return None
    return fb.EngineSpec(
        engine=batch.planner_engine,
        model=batch.planner_model,
        effort=batch.planner_effort,
    )


def _cycle_state(root: Path, cycle_name: str) -> tuple[str, str]:
    """(current_node, node_status) do state do ciclo; ('?', 'missing') se ausente."""
    state_file = paths.worktrees_home(root) / cycle_name / "state" / "engine_state.yml"
    if not state_file.is_file():
        return "?", "missing"
    try:
        data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return "?", "unreadable"
    return str(data.get("current_node") or "?"), str(data.get("node_status") or "?")


def _cycle_delegation_is_orphaned(root: Path, cycle_name: str) -> bool:
    """True se o state diz delegated/validating mas ninguém está dirigindo o ciclo.

    O lock persistido registra o PID do runner. Worker morto no meio de uma
    chamada LLM deixa o state em ``delegated`` com um lock de PID morto — sem
    esta checagem o batch espera para sempre por um subprocesso que não existe.
    Um PID vivo significa um driver externo legítimo (ex.: ft continue manual).
    """
    state_file = paths.worktrees_home(root) / cycle_name / "state" / "engine_state.yml"
    try:
        data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    if str(data.get("node_status") or "") not in {"delegated", "validating"}:
        return False
    from ft.engine.state import lock_owner_is_alive

    return not lock_owner_is_alive(data.get("_lock"))


def _reconcile_external_idle_transition(
    root: Path,
    feature: fb.BatchFeature,
) -> bool:
    """Sincroniza uma feature sem subprocesso com o state do ciclo.

    O batch pode persistir ``running``, ``gate`` ou ``blocked`` antes de uma
    pausa ou de uma decisão feita por outro comando ``ft``. Sem um ``Popen``
    local, esses estados são apenas um cache: o state autoritativo pode já estar
    em outro gate, no próximo node ``ready``, bloqueado ou concluído. Um
    ``ready`` deliberado volta ao caminho normal de spawn, sem ser confundido
    com rate limit.
    """
    if feature.status not in {"running", "gate", "blocked"} or not feature.cycle_name:
        return False

    _node, cycle_status = _cycle_state(root, feature.cycle_name)
    target = {
        "ready": "setup",
        "awaiting_approval": "gate",
        "blocked": "blocked",
        "done": "done",
        "completed": "done",
    }.get(cycle_status)
    if target is None and cycle_status in {"delegated", "validating"}:
        # Delegação órfã (worker morto em voo, lock com PID morto): volta a
        # setup para o loop respawnar ft continue, que recupera a delegação.
        if _cycle_delegation_is_orphaned(root, feature.cycle_name):
            target = "setup"
    if target is None or target == feature.status:
        return False

    feature.status = target
    feature.detail = ""
    return True


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _externally_closed_cycle_was_merged(root: Path, cycle_name: str) -> bool:
    """Prova conservadoramente que um ciclo fechado fora do batch foi integrado.

    A mera ausência da worktree não basta: ``ft close --merge none`` também a
    remove. Exigimos o registro ``done`` no checkout principal e o commit de
    archive original alcançável por HEAD, com a cadeia ancorada no base_commit
    registrado pelo ciclo.
    """
    if not cycle_name or Path(cycle_name).name != cycle_name:
        return False
    if (paths.worktrees_home(root) / cycle_name).exists():
        return False

    archive = paths.project_cycle_dir(root, cycle_name) / "cycle.yml"
    if not archive.is_file():
        return False
    try:
        archive_bytes = archive.read_bytes()
        record = yaml.safe_load(archive_bytes) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(record, dict):
        return False
    if record.get("id") != cycle_name or record.get("status") != "done":
        return False
    git_record = record.get("git")
    if not isinstance(git_record, dict):
        return False
    base_commit = str(git_record.get("base_commit") or "")
    worktree_branch = str(git_record.get("worktree_branch") or "")
    if not _GIT_OBJECT_RE.fullmatch(base_commit) or not worktree_branch:
        return False

    relative = archive.relative_to(root).as_posix()
    history = subprocess.run(
        ["git", "log", "HEAD", "--format=%H%x09%s", "--", relative],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if history.returncode != 0:
        return False
    expected_subject = f"chore(ft): archive {cycle_name}"
    for line in history.stdout.splitlines():
        archive_commit, separator, subject = line.partition("\t")
        if not separator or subject != expected_subject:
            continue
        if not _GIT_OBJECT_RE.fullmatch(archive_commit):
            continue
        changed = subprocess.run(
            [
                "git",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                archive_commit,
                "--",
                relative,
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if changed.returncode != 0 or relative not in changed.stdout.splitlines():
            continue
        committed = subprocess.run(
            ["git", "show", f"{archive_commit}:{relative}"],
            cwd=root,
            capture_output=True,
        )
        if committed.returncode != 0 or committed.stdout != archive_bytes:
            continue
        if not _git_is_ancestor(root, base_commit, archive_commit):
            continue
        if not _git_is_ancestor(root, archive_commit, "HEAD"):
            continue
        return True
    return False


def _reconcile_externally_closed_cycles(
    batch: fb.FeatureBatch,
    wave_ids: list[str],
) -> list[str]:
    """Atualiza estados obsoletos do batch após um ``ft close`` externo."""
    root = Path(batch.project_root)
    reconciled: list[str] = []
    for feature_id in wave_ids:
        feature = batch.feature(feature_id)
        if feature.status not in _EXTERNAL_CLOSE_CANDIDATES or not feature.cycle_name:
            continue
        if not _externally_closed_cycle_was_merged(root, feature.cycle_name):
            continue
        feature.status = "merged"
        feature.detail = ""
        reconciled.append(feature_id)
    if reconciled:
        fb.save_batch(batch)
    return reconciled


def _reserve_wave_backlog_items(
    batch: fb.FeatureBatch,
    wave_ids: list[str],
) -> None:
    """Reserva PBs da wave atual sem alterar o checkout do produto.

    A leitura acontece imediatamente antes do setup da wave. Assim, cada wave
    parte do PROJECT_BACKLOG já atualizado pelos merges (ou pushes) anteriores,
    enquanto uma retomada reaproveita as reservas persistidas no batch.yml.
    Features legadas que já saíram de ``planned`` nunca são alteradas.
    """
    root = Path(batch.project_root)
    backlog_path = root / "docs" / "PROJECT_BACKLOG.md"
    backlog_text = (
        backlog_path.read_text(encoding="utf-8") if backlog_path.is_file() else ""
    )
    backlog_ids = fb.backlog_items(backlog_text)
    backlog_numbers = [
        int(item.removeprefix("PB-").rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        for item in backlog_ids
    ]
    next_number = max(backlog_numbers, default=0) + 1

    # IDs explícitos de qualquer wave e reservas já persistidas não podem ser
    # escolhidos para uma demanda nova desta wave.
    used = set(backlog_ids)
    for feature in batch.features:
        if feature.reserved_backlog_item:
            used.add(feature.reserved_backlog_item.upper())
        # Só fazemos a atribuição na wave atual, mas qualquer PB citado por
        # outra demanda do batch já está indisponível para alocação automática.
        # Isso também protege ciclos legados sem lhes acrescentar metadata.
        used.update(fb.backlog_items(feature.demand))

    planned = [
        batch.feature(feature_id)
        for feature_id in wave_ids
        if batch.feature(feature_id).status == "planned"
    ]
    for feature in planned:
        if feature.reserved_backlog_item:
            continue
        explicit = fb.explicit_backlog_item(feature.demand)
        if explicit:
            feature.reserved_backlog_item = explicit
            continue
        while f"PB-{next_number:03d}" in used:
            next_number += 1
        reservation = f"PB-{next_number:03d}"
        feature.reserved_backlog_item = reservation
        used.add(reservation)
        next_number += 1


def _batch_backlog_mode(batch: fb.FeatureBatch) -> str:
    """Read backlog governance from the selected process metadata.

    Prefer the project-owned fork after materialization.  Before the first
    setup, fall back to the global catalog entry.  Any missing or malformed
    metadata fails conservatively to the historical ``global`` mode, so legacy
    batches keep their reservations.
    """
    cli = _cli()
    root = Path(batch.project_root)
    candidates = [
        paths.project_named_process_file(root, batch.template),
        cli.engine_root() / "templates" / batch.template / "process.yml",
    ]
    for process_path in candidates:
        if not process_path.is_file() or process_path.is_symlink():
            continue
        try:
            payload = yaml.safe_load(process_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict):
            continue
        close_policy = payload.get("close_policy")
        if not isinstance(close_policy, dict):
            continue
        backlog_policy = close_policy.get("backlog")
        if not isinstance(backlog_policy, dict):
            continue
        mode = backlog_policy.get("mode")
        if isinstance(mode, str) and mode.strip():
            return mode.strip()
    return "global"


def _feature_request_text(feature: fb.BatchFeature) -> str:
    """Demanda original com metadata de reserva exclusiva do batch."""
    if not feature.reserved_backlog_item:
        return feature.demand
    return (
        "---\n"
        f"reserved_backlog_item: {feature.reserved_backlog_item}\n"
        "---\n\n"
        f"{feature.demand}"
    )


def _setup_feature_cycle(
    batch: fb.FeatureBatch, feature: fb.BatchFeature, args
) -> None:
    """Cria o ciclo da feature agora (worktree + state), sem executar nodes."""
    cli = _cli()
    root = Path(batch.project_root)

    number = cli._next_cycle_num(root)
    cycle_name = f"cycle-{number:02d}-{feature.feature_id.lower()}-{feature.slug}"

    namespace = argparse.Namespace(
        command="feature",
        process=None,
        verbose=bool(getattr(args, "verbose", False)),
        demand=_feature_request_text(feature),
        feature_input=None,
        template=batch.template,
        force=True,  # ciclos do batch coexistem por design
        cycle_name=cycle_name,
        bypass_human_gates=bool(getattr(args, "bypass_human_gates", False)),
        claude=None,
        codex=None,
        gemini=None,
        opencode=None,
        effort=None,
        _setup_only=True,
    )
    worker_engine = _worker_engine_spec(batch, feature)
    if worker_engine is not None:
        setattr(
            namespace, worker_engine.engine, worker_engine.model or True
        )
        namespace.effort = worker_engine.effort

    cli.cmd_feature(namespace)
    feature.cycle_name = cycle_name
    feature.status = "setup"
    feature.detail = ""


def _spawn(
    batch: fb.FeatureBatch, feature: fb.BatchFeature, args, *, command: list[str]
) -> subprocess.Popen:
    root = Path(batch.project_root)
    log_dir = fb.batch_dir(root, batch.batch_id) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{feature.feature_id}.log"
    log_handle = open(log_path, "a", encoding="utf-8")
    log_handle.write(f"\n──── {' '.join(command)}\n")
    log_handle.flush()
    return subprocess.Popen(
        [sys.executable, "-m", "ft.cli.main", *command],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _spawn_continue(
    batch: fb.FeatureBatch, feature: fb.BatchFeature, args
) -> subprocess.Popen:
    command = ["continue", "--auto", "--cycle", str(feature.cycle_name)]
    command += _engine_cli_flags(_worker_engine_spec(batch, feature))
    if getattr(args, "bypass_human_gates", False):
        command.append("--bypass-human-gates")
    return _spawn(batch, feature, args, command=command)


def _print_board(batch: fb.FeatureBatch, wave_ids: list[str]) -> None:
    ui = _ui()
    root = Path(batch.project_root)
    print()
    print(
        ui.header(
            f"[{batch.batch_id}] wave {batch.current_wave + 1}/{len(batch.waves)}"
        )
    )
    for feature_id in wave_ids:
        feature = batch.feature(feature_id)
        worker_engine = _worker_engine_spec(batch, feature)
        engine = worker_engine.label if worker_engine else "default"
        node = "-"
        if feature.cycle_name:
            node, _ = _cycle_state(root, feature.cycle_name)
        marker = {
            "running": "▶",
            "gate": "⏸",
            "blocked": "✗",
            "done": "✓",
            "merged": "✓✓",
            "failed": "☠",
            "skipped": "→",
            "setup": "…",
        }.get(feature.status, "·")
        detail = f" — {feature.detail}" if feature.detail else ""
        print(
            f"  {marker} {feature.feature_id} [{engine}] {feature.cycle_name or '(sem ciclo)'} "
            f"{node} {feature.status}{detail}"
        )


def _handle_gate(
    batch: fb.FeatureBatch, feature: fb.BatchFeature, args
) -> subprocess.Popen | None:
    """Gate inline: aprova/rejeita no terminal do orquestrador."""
    ui = _ui()
    root = Path(batch.project_root)
    node, _status = _cycle_state(root, str(feature.cycle_name))
    worktree = paths.worktrees_home(root) / str(feature.cycle_name)

    print()
    print(ui.header(f"Gate pendente — {feature.feature_id}: {feature.title}"))
    print(f"  Ciclo: {feature.cycle_name}")
    print(f"  Node:  {node}")
    print(f"  Worktree: {worktree}")
    print(
        f"  Log: {fb.batch_dir(root, batch.batch_id) / 'logs' / (feature.feature_id + '.log')}"
    )

    while True:
        try:
            answer = (
                input("  [a]provar / [r]ejeitar / [d]epois / [p]ausar batch: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt, OSError):
            answer = "p"
        if answer in {"a", "aprovar", "approve"}:
            command = ["approve"]
            if node == "feature.questions":
                try:
                    message = input("  Respostas/decisões para o discovery: ").strip()
                except (EOFError, KeyboardInterrupt, OSError):
                    message = ""
                if not message:
                    print(ui.warn("  Respostas/decisões obrigatórias neste gate."))
                    continue
                command.append(message)
            command += ["--auto", "--cycle", str(feature.cycle_name)]
            command += _engine_cli_flags(_worker_engine_spec(batch, feature))
            feature.status = "running"
            feature.detail = ""
            return _spawn(batch, feature, args, command=command)
        if answer in {"r", "rejeitar", "reject"}:
            try:
                reason = input("  Motivo da rejeição: ").strip()
            except (EOFError, KeyboardInterrupt, OSError):
                reason = ""
            if not reason:
                print(ui.warn("  Motivo obrigatório."))
                continue
            command = ["reject", reason, "--cycle", str(feature.cycle_name)]
            command += _engine_cli_flags(_worker_engine_spec(batch, feature))
            feature.status = "running"
            feature.detail = "rejeitado — retrabalhando"
            return _spawn(batch, feature, args, command=command)
        if answer in {"d", "depois"}:
            return None
        if answer in {"p", "pausar", "pause"}:
            raise _PauseBatch()
        print(ui.warn("  Opção inválida."))


def _handle_blocked(
    batch: fb.FeatureBatch, feature: fb.BatchFeature, args
) -> subprocess.Popen | None:
    ui = _ui()
    root = Path(batch.project_root)
    print()
    print(ui.fail(f"{feature.feature_id} bloqueou — ciclo {feature.cycle_name}"))
    print(
        f"  Log: {fb.batch_dir(root, batch.batch_id) / 'logs' / (feature.feature_id + '.log')}"
    )
    while True:
        try:
            answer = (
                input("  [r]etentar / [f]alhar feature / [p]ausar batch: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt, OSError):
            answer = "p"
        if answer in {"r", "retentar", "retry"}:
            command = ["retry", "--auto", "--cycle", str(feature.cycle_name)]
            command += _engine_cli_flags(_worker_engine_spec(batch, feature))
            feature.status = "running"
            feature.detail = "retry"
            return _spawn(batch, feature, args, command=command)
        if answer in {"f", "falhar", "fail"}:
            feature.status = "failed"
            feature.detail = "abandonada pelo stakeholder"
            return None
        if answer in {"p", "pausar", "pause"}:
            raise _PauseBatch()
        print(ui.warn("  Opção inválida."))


class _PauseBatch(Exception):
    """Stakeholder pediu pausa — estado persiste e o batch sai limpo."""


def _skip_orphans(batch: fb.FeatureBatch, wave_ids: list[str]) -> None:
    """Features cujas dependências falharam nunca executam."""
    for feature_id in wave_ids:
        feature = batch.feature(feature_id)
        if feature.status in _TERMINAL:
            continue
        broken = [
            dep
            for dep in feature.depends_on
            if batch.feature(dep).status in {"failed", "skipped"}
        ]
        if broken:
            feature.status = "skipped"
            feature.detail = f"dependências falharam: {', '.join(broken)}"


def _run_wave(batch: fb.FeatureBatch, args) -> None:
    """Executa a wave atual até todas as features saírem de execução.

    Levanta _PauseBatch quando o stakeholder pausa.
    """
    root = Path(batch.project_root)
    wave_ids = batch.waves[batch.current_wave]
    procs: dict[str, subprocess.Popen] = {}
    rate_respawns: dict[str, int] = {}
    next_spawn_at: dict[str, float] = {}
    last_board = ""

    def _board_signature() -> str:
        return "|".join(
            f"{fid}:{batch.feature(fid).status}:{_cycle_state(root, batch.feature(fid).cycle_name or '')[0]}"
            for fid in wave_ids
        )

    try:
        while True:
            # 1. Reap subprocesses que terminaram.
            for feature_id, proc in list(procs.items()):
                if proc.poll() is None:
                    continue
                del procs[feature_id]
                feature = batch.feature(feature_id)
                _node, status = _cycle_state(root, str(feature.cycle_name))
                if status in {"done", "completed"}:
                    feature.status = "done"
                    feature.detail = ""
                elif status == "awaiting_approval":
                    feature.status = "gate"
                    feature.detail = ""
                elif status == "blocked":
                    feature.status = "blocked"
                elif status == "ready":
                    # Rate limit: o run pausa preservando o node como ready.
                    count = rate_respawns.get(feature_id, 0) + 1
                    rate_respawns[feature_id] = count
                    respawn_limit = _rate_limit_respawn_limit(batch)
                    if count > respawn_limit:
                        feature.status = "blocked"
                        feature.detail = "rate limit persistente"
                    else:
                        feature.status = "setup"
                        feature.detail = (
                            f"rate limit — respawn {count}/{respawn_limit}"
                        )
                        next_spawn_at[feature_id] = (
                            time.monotonic() + RATE_LIMIT_RETRY_SECONDS
                        )
                elif status in {"delegated", "validating"}:
                    if _cycle_delegation_is_orphaned(
                        root, str(feature.cycle_name)
                    ):
                        feature.status = "setup"
                        feature.detail = "delegação órfã — retomando"
                    else:
                        # Outro `ft continue` venceu o claim e segue dirigindo
                        # o mesmo ciclo. O subprocesso local perder a corrida
                        # não transforma uma execução saudável em BLOCKED.
                        feature.status = "running"
                        feature.detail = "driver externo ativo"
                else:
                    feature.status = "blocked"
                    feature.detail = f"estado inesperado: {status}"
                fb.save_batch(batch)

            # 2. Gates e bloqueios — interação inline, um por vez.
            for feature_id in wave_ids:
                feature = batch.feature(feature_id)
                if (
                    feature_id not in procs
                    and _reconcile_external_idle_transition(root, feature)
                ):
                    fb.save_batch(batch)
                if feature.status == "gate":
                    proc = _handle_gate(batch, feature, args)
                    if proc is not None:
                        procs[feature_id] = proc
                    fb.save_batch(batch)
                elif feature.status == "blocked":
                    proc = _handle_blocked(batch, feature, args)
                    if proc is not None:
                        procs[feature_id] = proc
                    fb.save_batch(batch)

            # 3. Spawn respeitando max_parallel.
            for feature_id in wave_ids:
                feature = batch.feature(feature_id)
                if feature.status != "setup" or feature_id in procs:
                    continue
                if len(procs) >= batch.max_parallel:
                    break
                if time.monotonic() < next_spawn_at.get(feature_id, 0.0):
                    continue
                procs[feature_id] = _spawn_continue(batch, feature, args)
                feature.status = "running"
                fb.save_batch(batch)

            # 4. Board (só quando algo muda).
            signature = _board_signature()
            if signature != last_board:
                _print_board(batch, wave_ids)
                last_board = signature

            # 5. Fim da wave?
            statuses = {batch.feature(fid).status for fid in wave_ids}
            if statuses <= ({"done", "gate", "blocked"} | _TERMINAL) and not procs:
                if "gate" not in statuses and "blocked" not in statuses:
                    return
            time.sleep(POLL_SECONDS if procs else 0.2)
    except _PauseBatch:
        for proc in procs.values():
            proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise


def _close_wave(batch: fb.FeatureBatch, args) -> bool:
    """Fecha e mergeia os ciclos done da wave em ordem estável."""
    cli = _cli()
    ui = _ui()
    root = Path(batch.project_root)

    for feature_id in batch.waves[batch.current_wave]:
        feature = batch.feature(feature_id)
        if feature.status != "done":
            continue
        print(
            ui.info(
                f"Fechando {feature.feature_id} ({feature.cycle_name}) com merge full…"
            )
        )
        namespace = argparse.Namespace(
            command="close",
            process=None,
            verbose=bool(getattr(args, "verbose", False)),
            cycle=feature.cycle_name,
            merge=None,  # close_policy do template decide (feature: full)
            merge_paths=None,
            keep_worktree=False,
            force=False,
            claude=None,
            codex=None,
            gemini=None,
            opencode=None,
            effort=None,
        )
        try:
            cli.cmd_close(namespace)
        except SystemExit:
            pass
        worktree = paths.worktrees_home(root) / str(feature.cycle_name)
        if worktree.exists() and _finish_canonical_merge(batch, feature):
            # O primeiro close já arquivou/commitou o worker e deixou um merge
            # válido em andamento. Após a reconciliação determinística, uma
            # segunda chamada constata a branch integrada e faz apenas cleanup.
            try:
                cli.cmd_close(namespace)
            except SystemExit:
                pass
        if worktree.exists():
            feature.detail = "close/merge pendente — resolva e rode --resume"
            fb.save_batch(batch)
            print(
                ui.fail(
                    f"{feature.feature_id}: close não concluiu (worktree preservado)."
                )
            )
            print(
                ui.info(
                    f"Resolva (ex.: conflito de merge) e rode: ft close --cycle {feature.cycle_name} "
                    f"--merge full; depois ft feature --parallel --resume {batch.batch_id}"
                )
            )
            return False
        feature.status = "merged"
        feature.detail = ""
        fb.save_batch(batch)
    return True


def _finish_canonical_merge(
    batch: fb.FeatureBatch,
    feature: fb.BatchFeature,
) -> bool:
    """Finish a docs-only merge conflict without serializing product workers."""
    from ft.engine.canonical_merge import resolve_canonical_conflicts
    from ft.engine.git_ops import git_command_prefix, verify_hooks_from_process_meta
    from ft.engine.validators.artifacts import (
        features_catalog_valid,
        implemented_backlog_covered_by_features,
        project_backlog_valid,
    )

    root = Path(batch.project_root)
    if not root.is_dir():
        return False
    try:
        merge_head = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    if merge_head.returncode != 0:
        return False

    resolution = resolve_canonical_conflicts(root)
    if not resolution.success:
        print(
            _ui().warn(
                f"{feature.feature_id}: conflito não reconciliável automaticamente — "
                f"{resolution.error or 'motivo desconhecido'}"
            )
        )
        return False

    checks = (
        project_backlog_valid(project_root=str(root), min_items=1),
        features_catalog_valid(project_root=str(root)),
        implemented_backlog_covered_by_features(project_root=str(root)),
    )
    failed = [detail for passed, detail in checks if not passed]
    if failed:
        print(
            _ui().warn(
                f"{feature.feature_id}: documentos reconciliados não passaram "
                f"na validação — {'; '.join(failed)}"
            )
        )
        return False

    diff_check = subprocess.run(
        ["git", "diff", "--cached", "--check"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if diff_check.returncode != 0:
        print(
            _ui().warn(
                f"{feature.feature_id}: reconciliação contém whitespace inválido — "
                f"{diff_check.stdout.strip() or diff_check.stderr.strip()}"
            )
        )
        return False

    process_path = _parallel_policy_source(root, batch.template)
    process_meta: dict = {}
    if process_path is not None:
        try:
            loaded = yaml.safe_load(process_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                process_meta = loaded
        except (OSError, yaml.YAMLError):
            process_meta = {}
    verify_hooks = verify_hooks_from_process_meta(process_meta)
    command = [*git_command_prefix(verify_hooks), "commit"]
    if not verify_hooks:
        command.extend(["--no-verify", "--no-gpg-sign"])
    command.append("--no-edit")
    environment = os.environ.copy()
    environment["GIT_EDITOR"] = "true"
    committed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0:
        print(
            _ui().warn(
                f"{feature.feature_id}: não foi possível concluir o merge "
                f"reconciliado — {committed.stdout.strip() or committed.stderr.strip()}"
            )
        )
        return False

    print(
        _ui().success(
            f"{feature.feature_id}: conflito canônico reconciliado "
            f"({', '.join(resolution.resolved)})"
        )
    )
    return True


def _execute_batch(batch: fb.FeatureBatch, args) -> None:
    ui = _ui()

    while batch.current_wave < len(batch.waves):
        wave_ids = batch.waves[batch.current_wave]
        _reconcile_externally_closed_cycles(batch, wave_ids)
        _skip_orphans(batch, wave_ids)
        pending = [
            batch.feature(fid)
            for fid in wave_ids
            if batch.feature(fid).status not in _TERMINAL
        ]
        if not pending:
            batch.current_wave += 1
            fb.save_batch(batch)
            continue

        print()
        print(
            ui.header(
                f"Wave {batch.current_wave + 1}/{len(batch.waves)} — "
                f"{len(pending)} ciclo(s), max {batch.max_parallel} em paralelo"
            )
        )

        # Setup sequencial (git não aceita corrida na criação de worktrees).
        if _batch_backlog_mode(batch) != "none":
            _reserve_wave_backlog_items(batch, wave_ids)
        # A decisão/reserva precisa sobreviver mesmo se o setup for interrompido.
        fb.save_batch(batch)
        for feature in pending:
            if feature.status == "planned":
                _setup_feature_cycle(batch, feature, args)
                fb.save_batch(batch)

        try:
            _run_wave(batch, args)
        except _PauseBatch:
            batch.status = "paused"
            fb.save_batch(batch)
            print(
                ui.warn(
                    f"Batch pausado. Retome com: ft feature --parallel --resume {batch.batch_id}"
                )
            )
            return

        # Um "done" que regrediu para failed/skipped não bloqueia o close dos demais.
        if not _close_wave(batch, args):
            batch.status = "paused"
            fb.save_batch(batch)
            return

        batch.current_wave += 1
        fb.save_batch(batch)

    batch.status = "done"
    fb.save_batch(batch)

    print()
    print(ui.header(f"Batch {batch.batch_id} concluído"))
    for feature in batch.features:
        marker = "✓" if feature.status == "merged" else "✗"
        detail = f" — {feature.detail}" if feature.detail else ""
        print(
            f"  {marker} {feature.feature_id} {feature.title}: {feature.status}{detail}"
        )
