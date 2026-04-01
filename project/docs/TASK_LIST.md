# Task List — ForgeProcess Fast Track

**Versão:** 1.0
**Data:** 2026-04-01
**Processo:** fast_track_v2
**Ciclo:** cycle-01
**Status:** Em execução (Planning)

---

## Estado Atual

| Fase | Sprint | Status |
|------|--------|--------|
| MDD | sprint-01-mdd | ✅ Concluído |
| Planning | sprint-02-planning | 🔄 Em andamento |
| TDD | sprint-03-tdd | ⏳ Pendente |
| Delivery | sprint-04-delivery | ⏳ Pendente |
| Smoke | sprint-05-smoke | ⏳ Pendente |
| E2E | sprint-06-e2e | ⏳ Pendente |
| Feedback | sprint-07-feedback | ⏳ Pendente |
| Auditoria | sprint-08-audit | ⏳ Pendente |
| Handoff | sprint-09-handoff | ⏳ Pendente |

**Progresso geral:** 3/22 steps concluídos (14%)

---

## Sprint 01 — MDD ✅

> Fase concluída. Artifacts produzidos: `hipotese.md`, `PRD.md`.

- [x] `ft.mdd.01.hipotese` — Capturar hipótese e problema
- [x] `ft.mdd.02.prd` — Redigir PRD com User Stories e critérios de aceitação
- [x] `ft.mdd.03.validacao` — Gate MDD (gatekeeper PASS)

---

## Sprint 02 — Planning 🔄

> Objetivo: definir stack técnica, diagramas de arquitetura e estrutura de sprints.

### ft.plan.01.task_list — Criar Task List
- [x] Levantar fases e nodes do processo (`FAST_TRACK_PROCESS_V2.yml`)
- [x] Mapear tasks por sprint com rastreabilidade aos RFs do PRD
- [x] Produzir `project/docs/TASK_LIST.md`

### ft.plan.02.tech_stack — Propor Tech Stack
- [ ] Documentar linguagem e runtime principal (Python 3.11+)
- [ ] Listar dependências: `PyYAML`, `pytest`, `coverage`, Claude Agent SDK
- [ ] Definir convenções de projeto: estrutura de pastas, padrão de commits
- [ ] Aprovar stack com stakeholder
- [ ] Produzir `project/docs/tech_stack.md`

### ft.plan.03.diagrams — Gerar Diagramas Técnicos
- [ ] Diagrama de fluxo do processo (fases → nodes → gates)
- [ ] Diagrama de componentes: ft engine, agentes, state YAML
- [ ] Diagrama de sequência: ciclo TDD dentro de um sprint
- [ ] Produzir `project/docs/diagrams/architecture.md`

### gate.planning — Gate de Planning
- [ ] Validar existência de `TASK_LIST.md`, `tech_stack.md`, `architecture.md`
- [ ] ft_gatekeeper emite PASS para avançar ao sprint-03-tdd

---

## Sprint 03 — TDD

> Objetivo: implementar features pendentes do ft engine com cobertura ≥ 80%.

### ft.tdd.02.red — Red: Escrever Testes

**Motor de estado (RF-01 a RF-05)**
- [ ] Teste: `process_id` único por processo
- [ ] Teste: motor avança somente após gate PASS (RF-02)
- [ ] Teste: `blocked_reason` preenchido em gate BLOCK (RF-03)
- [ ] Teste: suporte a múltiplos ciclos (`cycle-01`, `cycle-02`) (RF-04)
- [ ] Teste: métricas acumuladas (steps, cobertura, tokens) (RF-05)

**Gates de qualidade (RF-06 a RF-10)**
- [ ] Teste: gate TDD exige red→green sequencial (RF-07)
- [ ] Teste: gate de cobertura bloqueia se < 80% (RF-08)
- [ ] Teste: gate E2E falha se qualquer cenário não passar (RF-09)
- [ ] Teste: gate bloqueado não avança sem resolução explícita (RF-10)

**Agentes (RF-11 a RF-15)**
- [ ] Teste: ft_manager é único responsável por avançar nodes (RF-12)
- [ ] Teste: ft_gatekeeper retorna apenas PASS ou BLOCK (RF-13)
- [ ] Teste: forge_coder executa red→green→refactor por sprint (RF-14)
- [ ] Teste: ft_acceptance gera matriz happy/edge/error (RF-15)

**Rastreabilidade (RF-16 a RF-18)**
- [ ] Teste: artifact registrado em `artifacts` map após produção (RF-16)
- [ ] Teste: `gate_log` preserva histórico acumulado (RF-17)
- [ ] Teste: sessões de agentes salvas em `project/docs/sessions/` (RF-18)

### ft.tdd.03.green — Green: Implementar

**Motor de estado**
- [ ] Implementar suporte a múltiplos ciclos no engine
- [ ] Implementar rastreamento de métricas: `tokens_used`, `coverage` via relatório pytest
- [ ] Implementar validação de `process_id` único no boot

**Gates de qualidade**
- [ ] Implementar gate de cobertura com threshold configurável (default: 80%)
- [ ] Implementar gate E2E: verifica `tests/e2e/` e resultado dos testes
- [ ] Implementar lock file com validação anti-corrução no boot (RNF-03)

**Agentes**
- [ ] Implementar enforcement de path permitido por agente (RNF-05)
- [ ] Implementar rastreamento de sessão de agente em `sessions/`

**Rastreabilidade**
- [ ] Implementar registro automático de artifacts no `engine_state.yml`
- [ ] Implementar audit trail completo no `gate_log`

### gate.tdd — Gate TDD
- [ ] Todos os testes verdes
- [ ] Cobertura ≥ 80%
- [ ] ft_gatekeeper emite PASS

---

## Sprint 04 — Delivery

> Objetivo: consolidar qualidade do código antes de smoke e E2E.

### ft.delivery.01.self_review — Self-Review
- [ ] Revisar todos os módulos implementados no sprint-03
- [ ] Verificar ausência de `print` debug, dead code e TODO sem issue
- [ ] Confirmar lint limpo (`pylint` / `ruff`)
- [ ] Todos os testes ainda passam após revisão

### ft.delivery.02.refactor — Refactor
- [ ] Extrair duplicações identificadas no self-review
- [ ] Simplificar validators com alto acoplamento
- [ ] Garantir que cada módulo tem responsabilidade única
- [ ] Cobertura mantida ≥ 80% após refactor

### gate.delivery — Gate de Delivery
- [ ] Testes passando, lint limpo, cobertura ≥ 80%
- [ ] ft_gatekeeper emite PASS

---

## Sprint 05 — Smoke

> Objetivo: validar funcionamento básico via execução manual do CLI.

### ft.smoke.01.cli_run — Smoke CLI Run
- [ ] Executar `ft engine` com estado atual e verificar saída esperada
- [ ] Testar boot do motor com `engine_state.yml` existente (retomada de sessão)
- [ ] Testar gate BLOCK: simular falha e verificar mensagem `blocked_reason`
- [ ] Testar avanço de node: simular PASS e verificar incremento de `steps_completed`
- [ ] Produzir `project/docs/smoke-report.md` com resultado de cada cenário

### gate.smoke — Gate de Smoke
- [ ] Todos os cenários do smoke-report com status OK
- [ ] ft_gatekeeper emite PASS

---

## Sprint 06 — E2E

> Objetivo: cobrir o fluxo completo de ponta a ponta com testes automatizados.

### ft.e2e.01.cli_validation — E2E CLI Validation
- [ ] Cenário 1 — Happy Path completo: hipótese → PRD → task list → TDD → handoff
- [ ] Cenário 2 — Gate BLOCK: gate falha, motor registra `blocked_reason`, não avança
- [ ] Cenário 3 — Retomada de sessão: interromper e retomar, estado preservado (AC-03)
- [ ] Cenário 4 — Múltiplos ciclos: cycle-01 → cycle-02 sem corrupção de estado
- [ ] Cenário 5 — Cobertura insuficiente: gate bloqueia se coverage < 80% (AC-04)
- [ ] Produzir artefatos em `tests/e2e/`

### gate.e2e — Gate E2E
- [ ] Todos os 5 cenários passando
- [ ] Zero regressões (AC-05)
- [ ] ft_gatekeeper emite PASS

---

## Sprint 07 — Feedback

> Objetivo: registrar aprendizados e decisões do ciclo para referência futura.

### ft.feedback.01.retro_note — Retro Note
- [ ] Registrar o que funcionou bem no processo (gates, TDD, orquestração)
- [ ] Registrar fricções encontradas (gates lentos, ambiguidades de spec)
- [ ] Listar decisões de design tomadas e suas justificativas
- [ ] Propor melhorias para cycle-02 (se houver)
- [ ] Produzir `project/docs/retro.md`

---

## Sprint 08 — Auditoria

> Objetivo: validar conformidade do código com os requisitos do PRD.

### ft.audit.01.forgebase — Auditoria ForgeBase
- [ ] Verificar rastreabilidade: cada RF do PRD tem ao menos um teste cobrindo-o
- [ ] Verificar RNFs: determinismo (RNF-01), rastreabilidade (RNF-02), resiliência (RNF-03)
- [ ] Verificar isolamento de agentes: nenhum agente edita fora de seu path (RNF-05)
- [ ] Confirmar métricas de sucesso mensuráveis (cobertura ≥ 80%, gate pass rate)
- [ ] Produzir `project/docs/forgebase-audit.md`

### gate.audit — Gate de Auditoria
- [ ] Audit report produzido com ≥ 20 linhas
- [ ] Todos os testes passando, lint limpo
- [ ] ft_gatekeeper emite PASS

---

## Sprint 09 — Handoff

> Objetivo: produzir documentação final e fechar o ciclo de desenvolvimento.

### ft.handoff.01.specs — Gerar SPEC.md
- [ ] Documentar interface pública do ft engine (CLI, `engine_state.yml`, gate API)
- [ ] Listar todos os nodes, tipos e validators disponíveis
- [ ] Documentar como adicionar novos nodes ao processo
- [ ] Documentar cada agente: papel, tools permitidas, paths de saída
- [ ] Produzir `project/docs/SPEC.md`
- [ ] Produzir `CHANGELOG.md` com histórico de versões

### gate.mvp — Gate MVP
- [ ] `PRD.md`, `TASK_LIST.md`, `SPEC.md`, `CHANGELOG.md` existem
- [ ] Todos os testes passando
- [ ] ft_gatekeeper emite PASS

### ft.end — MVP Entregue
- [ ] Processo cycle-01 concluído
- [ ] 22/22 steps completados
- [ ] Métricas registradas em `engine_state.yml`

---

## Rastreabilidade — Requisitos Funcionais

| Requisito | Sprint | Task |
|-----------|--------|------|
| RF-01 `process_id` único | sprint-03 | ft.tdd.02.red |
| RF-02 Avança somente após PASS | sprint-03 | ft.tdd.02.red |
| RF-03 `blocked_reason` em BLOCK | sprint-03 | ft.tdd.02.red |
| RF-04 Múltiplos ciclos | sprint-03 | ft.tdd.02.red |
| RF-05 Métricas acumuladas | sprint-03 | ft.tdd.02.red |
| RF-06 Gates com critérios explícitos | sprint-03 | ft.tdd.02.red |
| RF-07 Gate TDD red→green | sprint-03 | ft.tdd.02.red |
| RF-08 Gate cobertura ≥ 80% | sprint-03 | ft.tdd.02.red |
| RF-09 Gate E2E todos os cenários | sprint-06 | ft.e2e.01 |
| RF-10 Gates incontornáveis | sprint-03 | ft.tdd.02.red |
| RF-11 Agentes no escopo próprio | sprint-03 | ft.tdd.03.green |
| RF-12 ft_manager avança nodes | sprint-03 | ft.tdd.03.green |
| RF-13 ft_gatekeeper PASS/BLOCK | sprint-03 | ft.tdd.02.red |
| RF-14 forge_coder TDD red→green→refactor | sprint-03 | ft.tdd.02.red |
| RF-15 ft_acceptance matriz de cenários | sprint-03 | ft.tdd.02.red |
| RF-16 Artifacts no state | sprint-03 | ft.tdd.03.green |
| RF-17 gate_log preservado | sprint-03 | ft.tdd.03.green |
| RF-18 Sessões em sessions/ | sprint-03 | ft.tdd.03.green |

---

## Critérios de Aceitação do Produto

| ID | Critério | Sprint |
|----|----------|--------|
| AC-01 | Motor executa 22 steps sem intervenção manual | sprint-06 E2E |
| AC-02 | Gates bloqueantes impedem progressão | sprint-05 Smoke |
| AC-03 | Estado preservado entre sessões | sprint-06 E2E |
| AC-04 | Cobertura ≥ 80% ao final de cada sprint | sprint-03 TDD |
| AC-05 | Zero regressões nos gates E2E | sprint-06 E2E |
| AC-06 | Cycle time reduzido vs baseline ad-hoc | sprint-08 Auditoria |
