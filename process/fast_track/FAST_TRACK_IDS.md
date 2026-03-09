# Fast Track — Step IDs

> Convenção: `ft.<fase>.<numero>.<nome_curto>`

## 1. MDD — Market Driven Development (comprimido)

- `ft.mdd.01.hipotese`
- `ft.mdd.02.prd`
- `ft.mdd.03.validacao`

## 2. Planning

- `ft.plan.01.task_list`
- `ft.plan.02.tech_stack`
- `ft.plan.03.diagrams`

## 3. TDD — Test Driven Development

- `ft.tdd.01.selecao`
- `ft.tdd.02.red`
- `ft.tdd.03.green`

## 4. Delivery

- `ft.delivery.01.self_review`
- `ft.delivery.02.refactor`
- `ft.delivery.03.commit`

## 5a. Smoke — Validação Real do Produto

- `ft.smoke.01.cli_run`

## 5b. E2E — Validation Gate

- `ft.e2e.01.cli_validation`

## 5c. Acceptance — Validação de Interface *(condicional)*

- `ft.acceptance.01.interface_validation`

## 6. Feedback

- `ft.feedback.01.retro_note`

## 7. Auditoria ForgeBase *(obrigatório antes do handoff)*

- `ft.audit.01.forgebase`

## 8. Handoff — Modo Manutenção

- `ft.handoff.01.specs`

---

## Orchestration Nodes (paralelização)

> Nós de orquestração para execução paralela de tasks e controle de sprint. Não são steps — são nós de decisão, sincronização e orquestração no flow do `FAST_TRACK_PROCESS.yml`.

| Node ID | Tipo | Descrição |
|---------|------|-----------|
| `decisao_paralelo` | decision | Decide se executa tasks em paralelo (worktrees) ou sequencial |
| `ft_sprint_prepare` | state_update | Alinha `current_sprint` com a primeira sprint pendente do escopo do ciclo |
| `ft_parallel_fanout` | orchestration | Cria worktrees e lança forge_coder em slots paralelos (max 3) |
| `ft_parallel_wait` | synchronization | Aguarda todos os slots sinalizarem done/failed |
| `ft_parallel_fanin` | orchestration | Merge sequencial (--no-ff), suite completa, cleanup worktrees |
| `decisao_mais_tasks_pos_merge` | decision | Verifica se há mais tasks na sprint atual após merge paralelo |
| `ft_preflight_sprint_gates` | validation | Confere se todas as tasks done da sprint atual têm `gate.delivery: PASS` |
| `ft_sprint_expert_gate` | orchestration | Chama `/ask fast-track`, salva `sprint-review-sprint-XX.md` e atualiza `sprint_status` |
| `decisao_sprint_status` | decision | Decide se a sprint volta para correção (`fixing`) ou segue (`completed`) |
| `decisao_proxima_sprint` | decision | Verifica se há próxima sprint no `cycle_sprint_scope` |
| `ft_sprint_advance` | state_update | Avança `current_sprint` para a próxima sprint do ciclo |

**Pré-condição**: `parallel_mode: true` no `ft_state.yml`. Quando `false`, o nó `decisao_paralelo` redireciona para `ft_tdd_01` (path sequencial).

---

## Resumo

| # | Step ID | Fase | Descrição |
|---|---------|------|-----------|
| 1 | `ft.mdd.01.hipotese` | MDD | Capturar hipótese e sinal de mercado |
| 2 | `ft.mdd.02.prd` | MDD | Redigir PRD consolidado |
| 3 | `ft.mdd.03.validacao` | MDD | Validar PRD (go/no-go) |
| 4 | `ft.plan.01.task_list` | Planning | Derivar task list das User Stories |
| 5 | `ft.plan.02.tech_stack` | Planning | Propor tech stack (ForgeBase obrigatório) |
| 6 | `ft.plan.03.diagrams` | Planning | Gerar diagramas técnicos (Mermaid) |
| 7 | `ft.tdd.01.selecao` | TDD | Selecionar próxima task |
| 8 | `ft.tdd.02.red` | TDD | Escrever teste que falha |
| 9 | `ft.tdd.03.green` | TDD | Implementar até teste passar + suite completa verde |
| 10 | `ft.delivery.01.self_review` | Delivery | Self-review expandido (10 itens, 3 grupos) |
| 11 | `ft.delivery.02.refactor` | Delivery | Refactor se necessário, suite verde |
| 12 | `ft.delivery.03.commit` | Delivery | Commit com mensagem padronizada + strategy |
| 13 | `ft.smoke.01.cli_run` | Smoke | Executar produto real via PTY + pulse evidence |
| 14 | `ft.e2e.01.cli_validation` | E2E | Rodar E2E CLI gate (unit + smoke) |
| 15 | `ft.acceptance.01.interface_validation` | Acceptance | Validar ACs contra interface real (condicional) |
| 16 | `ft.feedback.01.retro_note` | Feedback | Registrar retro do ciclo |
| 17 | `ft.audit.01.forgebase` | Auditoria | Auditar ForgeBase, Pulse, logging, Clean/Hex |
| 18 | `ft.handoff.01.specs` | Handoff | Gerar SPEC.md + CHANGELOG.md + BACKLOG.md |
