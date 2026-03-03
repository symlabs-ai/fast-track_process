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

- `ft.delivery.01.implement`
- `ft.delivery.02.self_review`
- `ft.delivery.03.commit`

## 5a. Smoke — Validação Real do Produto

- `ft.smoke.01.cli_run`

## 5b. E2E — Validation Gate

- `ft.e2e.01.cli_validation`

## 6. Feedback

- `ft.feedback.01.retro_note`

## 7. Handoff — Modo Manutenção

- `ft.handoff.01.specs`

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
| 9 | `ft.tdd.03.green` | TDD | Implementar até teste passar |
| 10 | `ft.delivery.01.implement` | Delivery | Integrar código e rodar suite |
| 11 | `ft.delivery.02.self_review` | Delivery | Self-review com checklist |
| 12 | `ft.delivery.03.commit` | Delivery | Commit com mensagem padronizada |
| 13 | `ft.smoke.01.cli_run` | Smoke | Executar produto real via PTY + pulse evidence |
| 14 | `ft.e2e.01.cli_validation` | E2E | Rodar E2E CLI gate (unit + smoke) |
| 15 | `ft.feedback.01.retro_note` | Feedback | Registrar retro do ciclo |
| 16 | `ft.handoff.01.specs` | Handoff | Gerar SPEC.md + CHANGELOG.md + BACKLOG.md |
