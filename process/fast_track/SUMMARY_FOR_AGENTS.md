# Fast Track — Summary for Agents

> Resumo compacto para LLMs. Leia isto para entender o Fast Track em < 30 segundos.

## O que é

ForgeProcess: **18 steps, 3 symbiotas, 1 PRD → 1 SPEC**.
Para solo dev + AI. Sem BDD Gherkin, sem sprints formais, sem roadmap separado.

`ft_manager` orquestra tudo. `ft_coach` e `forge_coder` executam quando delegados.

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
  |                              [ft_manager valida PRD]
  | rejected -> END                          |
  v                                    approved
ft.plan.01.task_list
  [ft_manager valida task list]
  |
  v
ft.plan.02.tech_stack (forge_coder propõe) → stakeholder revisa/aprova
  |
  v
ft.plan.03.diagrams (class / components / database / architecture)
  |
  v
LOOP[
  ft.tdd.01.selecao -> ft.tdd.02.red -> ft.tdd.03.green (suite completa obrigatória)
  -> ft.delivery.01.self_review (expandido, 10 itens) -> ft.delivery.02.refactor -> ft.delivery.03.commit
  [ft_manager valida entrega + cobertura >= 85%]
  -> more_tasks? -> LOOP / done? -> EXIT
]
  -> ft.smoke.01.cli_run (GATE — processo real, PTY real, sem mocks, output documentado)
  -> ft.e2e.01.cli_validation (GATE — unit + smoke)
  -> interface_type != cli_only? -> ft.acceptance.01.interface_validation (GATE — ACs × interface real)
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
| ft.acceptance.01.interface_validation | forge_coder | ft_manager |
| ft.feedback.01.retro_note | ft_coach | ft_manager |
| ft.audit.01.forgebase | forge_coder | ft_manager |
| ft.handoff.01.specs | ft_coach | ft_manager |

## Artefatos

| Artefato | Path | Criado em |
|----------|------|-----------|
| Hipótese | project/docs/hipotese.md | ft.mdd.01.hipotese |
| PRD | project/docs/PRD.md | ft.mdd.02.prd |
| Task List | project/docs/TASK_LIST.md | ft.plan.01.task_list |
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

1. **Smoke gate é obrigatório** — Ciclo não avança sem produto real executado e output documentado.
2. **E2E CLI gate é obrigatório** — Ciclo não fecha sem `run-all.sh` passando (unit + smoke).
3. **Acceptance gate é condicional** — Obrigatório quando `interface_type` != `cli_only`. Cada AC do PRD testado contra a interface real.
4. **`mvp_status: demonstravel` exige smoke PASSOU** — nunca declarar com base em unit tests.
5. **TDD Red-Green** — Teste falhando antes de código. Sempre. Suite completa verde no green.
6. **PRD é fonte única** — Sem documentos satélite.
7. **ACs substituem BDD** — Given/When/Then dentro do PRD, sem .feature files.
8. **ft_manager valida tudo** — Nenhuma fase avança sem checkpoint de validação passar.
9. **Modo autônomo não dispensa critérios** — ft_manager valida internamente com os mesmos padrões.
10. **SPEC.md é obrigatório ao encerrar** — MVP concluído sem SPEC.md gerado não está realmente encerrado.
11. **SPEC.md reflete o entregue, não o planejado** — features não implementadas vão para "fora do escopo".
12. **Value Tracks são obrigatórios** — PRD deve ter 2-5 Value Tracks com KPIs. Cada US mapeada para pelo menos 1 track.
13. **Observabilidade via ForgeBase Pulse** — todo UseCase passa por `UseCaseRunner`. Smoke gate gera `pulse_snapshot.json` com `mapping_source: "spec"`. Nunca inventar telemetria própria.
14. **Cobertura mínima 85%** — Arquivos alterados devem ter >= 85% de cobertura (desejável 90%). Validado no self-review com `--cov`.
15. **Self-review expandido** — 10 itens em 3 grupos: segurança/higiene, qualidade de código, arquitetura Clean/Hex + ForgeBase.
16. **Refactor é step formal** — Após self-review, antes do commit. No-op documentado se nada a refatorar.
17. **Decisão de ciclo é contextual, não genérica** — ft_manager analisa critérios de MVP antes de oferecer opções. Se tasks P0 pendentes ou interface não entregue (quando `interface_type` != `cli_only`), recomenda novo ciclo. "Encerrar MVP" só é opção primária quando critérios estão atendidos.
18. **Progresso visível** — forge_coder exibe progress report ao iniciar/concluir cada task. ft_manager exibe resumo de ciclo com tasks por prioridade ao concluir fase TDD/Delivery.
19. **Acceptance tests devem ser reais** — Testes que fazem grep em arquivos, verificam existência de arquivos ou passam sem servidor rodando NÃO são testes de aceitação válidos. O ft_manager DEVE inspecionar o código dos testes para confirmar interação real (HTTP requests, Playwright, Chrome automation).
20. **Execução final do acceptance gate no ambiente do cliente** — Testes de dev são válidos durante desenvolvimento, mas a execução final que vale para o report deve usar build de produção + ambiente do cliente. UI tests com Playwright headed (browser visível). PWA exige HTTPS. 100% dos ACs cobertos nesta execução.
21. **Auditoria ForgeBase é obrigatória antes do handoff** — Verificar UseCaseRunner wiring, Value/Support Tracks completos, qualidade de logging (sem print, logs estruturados, níveis corretos, sem dados sensíveis), Pulse snapshot com mapping_source: "spec", e aderência Clean/Hex. MVP não é entregue sem auditoria passando.

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

Arquivo: `process/fast_track/state/ft_state.yml`
Campo chave: `next_recommended_step`
Novos campos: `min_coverage`, `desired_coverage`, `commit_strategy`, `interface_type`
