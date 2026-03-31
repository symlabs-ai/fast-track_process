# Fast Track — Summary for Agents

> Resumo compacto para LLMs. Leia isto para entender o Fast Track em < 30 segundos.

## O que é

ForgeProcess: **19 steps, 5 symbiotas, 1 PRD → 1 SPEC**.
Para solo dev + AI. Sem BDD Gherkin e sem cerimônia de squad, mas com sprints técnicas por dependência.

`ft_manager` orquestra tudo. `ft_gatekeeper` valida gates (PASS/BLOCK). `ft_acceptance` projeta cenários de aceitação. `ft_coach` e `forge_coder` executam quando delegados.

## CLI (ft.py)

Validação determinística: `ft <cmd>`

- `init --check` — bootstrap (ft_manager, antes de tudo)
- `validate state` — estado válido? (ft_manager + ft_gatekeeper, após cada update)
- `validate gate <id>` — pre-flight mecânico (ft_gatekeeper, antes de cada gate)
- `validate artifacts` — artefatos existem? (ft_manager, antes do handoff)
- `validate integration` — mock audit, dead code, wiring (ft_gatekeeper, antes do gate.audit)
- `tokens snapshot --step <id>` — token tracking (ft_manager, momentos-chave)

BLOCK em qualquer comando = parar e resolver.

## Flow

```
[ft_manager inicia]
  |
  +--> stakeholder entregou PRD? --> SIM --> [hyper-mode]
  |                                            ft_coach absorve PRD
  |                                            gera PRD.md + TASK_LIST + questionário
  |                                            stakeholder responde questionário
  |                                            ft_coach incorpora respostas
  |                                            --> ft_manager valida PRD --> go/no-go
  |
  +--> NÃO --> [normal-mode]
  v
ft.mdd.01.hipotese -> ft.mdd.02.prd -> ft.mdd.03.validacao
  |                              [ft_gatekeeper: gate.prd]
  | rejected -> END                          |
  v                                    approved
ft.plan.01.task_list
  [ft_gatekeeper: gate.task_list → stakeholder aprova prioridades]
  |
  v
ft.plan.02.tech_stack (forge_coder propõe) → stakeholder revisa/aprova
  |
  v
ft.plan.03.diagrams (class / components / database / architecture)
  |
  v
ft_sprint_prepare (alinha current_sprint)
  |
  v
decisao_paralelo: parallel_mode true + >= 3 tasks na sprint atual + forge_coder recomendou?
  |
  +--> PARALLEL PATH:
  |     ft_parallel_fanout (worktrees + slots)
  |     -> ft_parallel_wait (aguardar slots done)
  |     -> ft_parallel_fanin (merge --no-ff + pytest + cleanup)
  |     -> more_tasks_in_sprint? -> decisao_paralelo / done? -> sprint_preflight
  |
  +--> SEQUENTIAL PATH:
LOOP[
  ft.tdd.01.selecao -> ft.tdd.02.red -> ft.tdd.03.green (suite completa obrigatória)
  -> ft.delivery.01.self_review (expandido, 10 itens) -> ft.delivery.02.refactor -> ft.delivery.03.commit
  [ft_gatekeeper: gate.delivery — cobertura >= 85%]
  -> more_tasks_in_sprint? -> decisao_paralelo / done? -> sprint_preflight
]
  -> ft_preflight_sprint_gates
  -> ft_sprint_expert_gate (/ask fast-track, salva sprint-review-sprint-XX.md)
  -> sprint_status == fixing? -> LOOP da mesma sprint
  -> next_sprint? -> ft_sprint_advance -> decisao_paralelo
  -> sprints_done? -> ft_preflight_gates
  -> ft.smoke.01.cli_run (GATE — processo real, PTY real, sem mocks, output documentado)
  -> ft.e2e.01.cli_validation (GATE — unit + smoke)
  -> interface_type != cli_only? -> ft.acceptance.01.scenario_design (ft_acceptance projeta cenários por track)
  -> ft.acceptance.02.interface_validation (GATE — cenários × interface real)
  -> [ft_manager decide modo]
     interactive: apresenta ao stakeholder -> feedback / MVP / autonomous
     autonomous:  valida internamente -> prossegue até MVP -> apresenta stakeholder
  -> ft.feedback.01.retro_note
  -> continue? -> ft.plan.01
  -> complete? -> ft.audit.01.forgebase (GATE — auditoria Pulse, logging, Clean/Hex)
  -> ft.handoff.01.specs (gerar SPEC.md) -> END [maintenance_mode: true]
```

## Step IDs (18 total)

| ID | Executor | Orquestrado por |
|----|----------|-----------------|
| ft.mdd.01.hipotese | ft_coach | ft_manager |
| ft.mdd.02.prd | ft_coach | ft_manager |
| ft.mdd.03.validacao | ft_coach | ft_manager |
| ft.plan.01.task_list | ft_coach | ft_manager |
| ft.plan.02.tech_stack | forge_coder | ft_manager |
| ft.plan.03.diagrams | forge_coder | ft_manager |
| ft.tdd.01.selecao | forge_coder | ft_manager |
| ft.tdd.02.red | forge_coder | ft_manager |
| ft.tdd.03.green | forge_coder | ft_manager |
| ft.delivery.01.self_review | forge_coder | ft_manager |
| ft.delivery.02.refactor | forge_coder | ft_manager |
| ft.delivery.03.commit | forge_coder | ft_manager |
| ft.smoke.01.cli_run | forge_coder | ft_manager |
| ft.e2e.01.cli_validation | forge_coder | ft_manager |
| ft.acceptance.01.scenario_design | ft_acceptance | ft_manager |
| ft.acceptance.02.interface_validation | forge_coder | ft_manager |
| ft.feedback.01.retro_note | ft_coach | ft_manager |
| ft.audit.01.forgebase | forge_coder | ft_manager |
| ft.handoff.01.specs | ft_coach | ft_manager |

## Artefatos

| Artefato | Path | Criado em |
|----------|------|-----------|
| Hipótese | project/docs/hipotese.md | ft.mdd.01.hipotese |
| PRD | project/docs/PRD.md | ft.mdd.02.prd |
| Task List | project/docs/TASK_LIST.md | ft.plan.01.task_list |
| Sprint Review | project/docs/sprint-review-sprint-XX.md | Sprint Expert Gate |
| Tech Stack | project/docs/tech_stack.md | ft.plan.02.tech_stack |
| Diagramas | project/docs/diagrams/ | ft.plan.03.diagrams |
| Código | src/ | ft.tdd.03.green |
| Testes | tests/ | ft.tdd.02.red |
| ForgePulse Spec | forgepulse.value_tracks.yml | ft.plan.02.tech_stack |
| Pulse Snapshot | artifacts/pulse_snapshot.json | ft.smoke.01.cli_run |
| Acceptance Report | project/docs/acceptance-cycle-XX.md | ft.acceptance.01.interface_validation |
| Acceptance Tests | tests/acceptance/cycle-XX/ | ft.acceptance.01.interface_validation |
| Retro | project/docs/retro-cycle-XX.md | ft.feedback.01.retro_note |
| ForgeBase Audit | project/docs/forgebase-audit.md | ft.audit.01.forgebase |
| Token Metrics | project/docs/metrics.yml | ft_manager (snapshots ao longo do processo) |
| SPEC | project/docs/SPEC.md | ft.handoff.01.specs |
| Changelog | CHANGELOG.md | ft.handoff.01.specs |
| Backlog | BACKLOG.md | ft.handoff.01.specs |

## Regras Críticas

### Tier 1 — Invioláveis (violação = processo corrompido)

> Estas regras nunca podem ser ignoradas. Violá-las invalida o ciclo inteiro.

1. **Sequência de gates é inviolável** — Sprint Expert Gate → Smoke → E2E CLI → Acceptance (condicional) → Feedback. Nenhum gate pode ser pulado.
2. **N/A não é resultado válido de gate** — Cada item do checklist do ft_gatekeeper é ✅ ou ❌. "Não aplicável", "N/A" ou "não implementado" = ❌ BLOCK.
3. **ft_gatekeeper é independente** — Separação de responsabilidades: ft_manager orquestra, ft_gatekeeper bloqueia. O mesmo agente que orquestra não valida os gates.
4. **gate.delivery tem enforcement por task** — Cada task `done` DEVE ter `gate.delivery: PASS` registrado no `gate_log` do `ft_state.yml`.
5. **TDD Red-Green** — Teste falhando antes de código. Sempre. Suite completa verde no green.
6. **`mvp_status: demonstravel` exige smoke PASSOU** — nunca declarar com base em unit tests.
7. **Smoke gate é obrigatório** — Ciclo não avança sem produto real executado e output documentado.
8. **Step IDs devem ser válidos** — ft_manager só grava em `completed_steps` IDs que existam em FAST_TRACK_IDS.md. IDs inventados corrompem o estado. Validável via `ft.py validate state`.
9. **Artefatos em paths canônicos** — smoke-cycle-XX.md, acceptance-cycle-XX.md, sprint-review-sprint-XX.md e forgebase-audit.md devem estar em `project/docs/`. Validável via `ft.py validate artifacts`.
10. **PRD é fonte única** — Sem documentos satélite.

### Tier 2 — Defaults do processo (deriváveis do YAML, mas importantes)

> Regras que definem como o processo opera normalmente. Deriváveis da spec mas listadas aqui para referência rápida.

11. **Sprint é a unidade de avanço dentro do ciclo** — TDD/Delivery opera sprint a sprint; tasks só podem ser selecionadas na `current_sprint`.
12. **Sprint Expert Gate é obrigatório** — Toda sprint termina com `/ask fast-track`, report salvo em `project/docs/sprint-review-sprint-XX.md` e correção integral das recomendações antes da próxima sprint.
13. **E2E CLI gate é obrigatório** — Ciclo não fecha sem `run-all.sh` passando (unit + smoke).
14. **ACs substituem BDD** — Given/When/Then dentro do PRD, sem .feature files.
15. **ft_gatekeeper valida gates binários** — Cada checkpoint formal delega ao ft_gatekeeper (PASS/BLOCK). O Sprint Expert Gate é revisão externa complementar, não substitui o gatekeeper.
16. **Modo autônomo não dispensa critérios** — ft_manager valida internamente com os mesmos padrões.
17. **SPEC.md é obrigatório ao encerrar** — MVP concluído sem SPEC.md gerado não está realmente encerrado.
18. **SPEC.md reflete o entregue, não o planejado** — features não implementadas vão para "fora do escopo".
19. **Value Tracks são obrigatórios** — PRD deve ter 2-5 Value Tracks com KPIs. Cada US mapeada para pelo menos 1 track.
20. **Observabilidade via ForgeBase Pulse** — todo UseCase passa por `UseCaseRunner`. Smoke gate gera `pulse_snapshot.json` com `mapping_source: "spec"`. Nunca inventar telemetria própria.
21. **Cobertura mínima 85%** — Arquivos alterados devem ter >= 85% de cobertura (desejável 90%). Validado no self-review com `--cov`.
22. **Self-review expandido** — 10 itens em 3 grupos: segurança/higiene, qualidade de código, arquitetura Clean/Hex + ForgeBase.
23. **Refactor é step formal** — Após self-review, antes do commit. No-op documentado se nada a refatorar.
24. **Decisão de ciclo é contextual, não genérica** — ft_manager analisa critérios de MVP antes de oferecer opções. "Encerrar MVP" só é opção primária quando critérios estão atendidos.
25. **Skip de tasks requer aprovação** — Tasks P0 nunca podem ser puladas. Todo skip registrado no TASK_LIST.md com motivo e quem aprovou.
26. **Prioridades e sequência de sprints requerem aprovação do stakeholder** — Após gate.task_list PASS, ft_manager apresenta prioridades e agrupamento incremental ao stakeholder.
27. **Progresso visível** — forge_coder exibe progress report ao iniciar/concluir cada task. ft_manager exibe resumo por sprint e por ciclo.
28. **Auditoria ForgeBase é obrigatória antes do handoff** — MVP não é entregue sem auditoria passando.

### Tier 3 — Contextuais (só aplicam quando feature/modo está ativo)

> Regras que só entram em vigor em cenários específicos. Ignoráveis quando o contexto não se aplica.

29. **Acceptance gate é condicional** — Obrigatório quando `interface_type` != `cli_only`. Cada AC do PRD testado contra a interface real.
30. **Acceptance tests devem ser reais** — Testes que fazem grep em arquivos ou passam sem servidor rodando NÃO são testes de aceitação válidos. Requer interação real (HTTP requests, Playwright, Chrome automation).
31. **Execução final do acceptance no ambiente do cliente** — Build de produção, Playwright headed, PWA exige HTTPS. 100% dos ACs cobertos.
32. **Paralelização é opt-in e limitada à sprint atual** — `parallel_mode: true` habilita execução paralela em worktrees, mas nunca atravessando duas sprints.

## Recovery — O que fazer quando algo trava

| Cenário | Ação |
|---------|------|
| Sprint Expert Gate bloqueia 3x seguidas | ft_manager pausa, apresenta o padrão de bloqueio ao stakeholder e pergunta: reduzir escopo da sprint, pedir ajuda externa, ou pivotar abordagem |
| Smoke trava (freeze/hang) | forge_coder documenta o travamento em `smoke-cycle-XX.md` com log completo. ft_manager avalia: bug de implementação (voltar para TDD) ou problema de ambiente (resolver antes de retry) |
| Gate.delivery BLOCK repetido na mesma task | Após 2 BLOCKs na mesma task, ft_manager avalia se a task é viável. Opções: quebrar em subtasks menores, marcar como blocked com motivo, ou escalar ao stakeholder |
| Stakeholder ausente (modo interactive) | Após timeout razoável (~1 sessão sem resposta), ft_manager registra `blocked: true, blocked_reason: "aguardando stakeholder"`. Não avança sem aprovação onde requerida |
| Estado corrompido | Rodar `ft.py validate state` para diagnóstico. Corrigir campos inválidos manualmente ou restaurar de git (`git checkout project/state/ft_state.yml`) |
| Divergência processo/state | Rodar `ft.py init --check` para detectar. `ft.py init` sincroniza versão automaticamente |

## Stakeholder Mode

Campo `stakeholder_mode` em `ft_state.yml`:
- `interactive`: stakeholder vê E2E ao fim de cada ciclo
- `autonomous`: stakeholder só vê na entrega final do MVP

## Modo Manutenção

Após `ft.handoff.01.specs`, `maintenance_mode: true` no state.
Skills disponíveis **apenas em maintenance mode**:
- `/backlog <ideia>` — registrar ideia futura em `BACKLOG.md`
- `/feature <descrição>` — implementar feature (lê SPEC.md; atualiza SPEC.md + CHANGELOG.md)

⛔ `/feature` e `/backlog` são rejeitadas pelo ft_manager durante o Fast Track.

## Estado

Arquivo: `project/state/ft_state.yml`
Campo chave: `next_step` (determinístico — o próximo step obrigatório, não uma sugestão)
Campos de qualidade: `min_coverage`, `desired_coverage`, `commit_strategy`, `interface_type`
Campos de sprint:
- `current_sprint`: sprint em execução
- `sprint_status`: planned | in_progress | expert_review | fixing | completed
- `cycle_sprint_scope`: sprints ativas no ciclo atual
- `backlog_sprints`: sprints fora do corte do ciclo atual
- `sprint_review_gate`: gateway configurado (`ask_fast_track`)
- `sprint_review_log`: histórico resumido dos reviews por sprint
Campos de paralelização (opt-in, só populados quando `parallel_mode: true`):
- `parallel_mode`: false (default) | true
- `parallel_max_agents`: max forge_coder simultâneos (default: 3)
- `parallel_tasks`: lista de `{task_id, worktree, branch, status, agent_id}`
- `parallel_merge_queue`: task_ids prontos para merge
- `parallel_merge_status`: idle | merging | conflict | done
