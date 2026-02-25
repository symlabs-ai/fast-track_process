# Fast Track — Summary for Agents

> Resumo compacto para LLMs. Leia isto para entender o Fast Track em < 30 segundos.

## O que é

ForgeProcess: **14 steps, 3 symbiotas, 1 PRD**.
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
  ft.tdd.01.selecao -> ft.tdd.02.red -> ft.tdd.03.green
  -> ft.delivery.01.implement -> ft.delivery.02.self_review -> ft.delivery.03.commit
  [ft_manager valida entrega]
  -> more_tasks? -> LOOP / done? -> EXIT
]
  -> ft.e2e.01.cli_validation (GATE)
  -> [ft_manager decide modo]
     interactive: apresenta ao stakeholder -> feedback / MVP / autonomous
     autonomous:  valida internamente -> prossegue até MVP -> apresenta stakeholder
  -> ft.feedback.01.retro_note
  -> continue? -> ft.plan.01 / complete? -> END
```

## Step IDs (12 total)

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
| ft.delivery.01.implement | forge_coder | ft_manager |
| ft.delivery.02.self_review | forge_coder | ft_manager |
| ft.delivery.03.commit | forge_coder | ft_manager |
| ft.e2e.01.cli_validation | forge_coder | ft_manager |
| ft.feedback.01.retro_note | ft_coach | ft_manager |

## Artefatos

| Artefato | Path | Criado em |
|----------|------|-----------|
| PRD | project/docs/PRD.md | ft.mdd.02.prd |
| Task List | project/docs/TASK_LIST.md | ft.plan.01.task_list |
| Tech Stack | project/docs/tech_stack.md | ft.plan.02.tech_stack |
| Diagramas | project/docs/diagrams/ | ft.plan.03.diagrams |
| Código | src/ | ft.tdd.03.green |
| Testes | tests/ | ft.tdd.02.red |
| Retro | project/docs/retro-cycle-XX.md | ft.feedback.01.retro_note |

## Regras Críticas

1. **E2E CLI gate é obrigatório** — Ciclo não fecha sem `tests/e2e/cycle-XX/run-all.sh` passando.
2. **TDD Red-Green** — Teste falhando antes de código. Sempre.
3. **PRD é fonte única** — Sem documentos satélite.
4. **ACs substituem BDD** — Given/When/Then dentro do PRD, sem .feature files.
5. **ft_manager valida tudo** — Nenhuma fase avança sem checkpoint de validação passar.
6. **Modo autônomo não dispensa critérios** — ft_manager valida internamente com os mesmos padrões.

## Stakeholder Mode

Campo `stakeholder_mode` em `ft_state.yml`:
- `interactive`: stakeholder vê E2E ao fim de cada ciclo
- `autonomous`: stakeholder só vê na entrega final do MVP

## Estado

Arquivo: `process/fast_track/state/ft_state.yml`
Campo chave: `next_recommended_step`
