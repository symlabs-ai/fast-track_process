# PRD — ForgeProcess Fast Track

**Versão:** 1.0
**Data:** 2026-04-01
**Status:** Em revisão

---

## 1. Visão Geral

### 1.1 Problema

Desenvolvedores solo que trabalham com assistentes de IA não têm um processo estruturado que equilibre rigor técnico (TDD, gates de qualidade, E2E) com agilidade — sem a burocracia de cerimônias tradicionais de squad (reviews em grupo, BDD Gherkin extenso, planning coletivo). O resultado é entrega inconsistente, qualidade variável e retrabalho frequente.

### 1.2 Oportunidade

Existe espaço para um processo ágil especializado no contexto solo dev + AI que:

- Garanta rigor técnico via TDD obrigatório e Sprint Expert Gate
- Elimine cerimônias desnecessárias adaptando o fluxo para um único desenvolvedor
- Automatize a orquestração do processo com um motor determinístico
- Entregue valor de forma consistente do insight à produção em ciclos curtos

### 1.3 Solução

**ForgeProcess Fast Track** é um processo de desenvolvimento estruturado em 22 steps distribuídos em fases, operado por 5 agentes especializados (symbiotas), com motor Python determinístico e gates de qualidade automatizados.

---

## Visao

ForgeProcess Fast Track é um processo de desenvolvimento ágil para solo dev + AI que garante rigor técnico (TDD obrigatório, gates de qualidade, E2E) sem a burocracia de cerimônias tradicionais de squad. O motor determinístico (ft engine) orquestra 5 agentes especializados do insight à produção em ciclos curtos e rastreáveis.

---

## User Stories

### US-01 — Iniciar processo com hipótese
**Como** solo dev,
**quero** submeter uma hipótese de feature ao ft engine,
**para que** o processo seja iniciado de forma estruturada e rastreável, sem precisar definir o fluxo manualmente.

**Critérios de Aceitação:**
- Motor cria `process_id` único e persiste estado em `engine_state.yml`
- ft_coach é convocado para conduzir MDD (hipótese → PRD)
- ft_gatekeeper valida o PRD antes de avançar para Planning

### US-02 — Executar sprint com TDD obrigatório
**Como** solo dev,
**quero** que cada sprint seja executada com ciclos TDD (red → green → refactor),
**para que** a qualidade técnica seja garantida antes de avançar para a próxima fase.

**Critérios de Aceitação:**
- forge_coder executa testes que falham (red) antes de implementar
- Gate de cobertura exige ≥ 80% antes de marcar sprint como concluída
- Sprint Expert Gate (ft_gatekeeper) revisa qualidade e bloqueia se necessário

### US-03 — Retomar processo após interrupção
**Como** solo dev,
**quero** retomar o processo de onde parei em uma nova sessão,
**para que** não haja perda de contexto ou retrabalho após interrupções.

**Critérios de Aceitação:**
- Motor carrega estado de `engine_state.yml` no boot
- Node ativo, sprint atual e gate_log são restaurados corretamente
- Agente correto é convocado para o node pendente sem necessidade de configuração manual

### US-04 — Validar aceitação antes do deploy
**Como** solo dev,
**quero** que cenários de aceitação sejam executados antes do deploy,
**para que** nenhuma feature chegue à produção sem validação funcional completa.

**Critérios de Aceitação:**
- ft_acceptance gera matriz de cenários (happy path, edge cases, error cases)
- Gate de aceitação bloqueia deploy se qualquer cenário falhar
- forge_coder executa testes E2E cobrindo todos os cenários mapeados

---

## 2. Personas

### Persona Principal: Solo Dev + AI

- Desenvolvedor individual trabalhando com assistentes de IA (Claude, etc.)
- Entrega features do início ao fim sem time
- Precisa de disciplina de processo sem sobrecarga burocrática
- Valoriza qualidade técnica (cobertura de testes, sem regressões)
- Quer velocidade: hipótese → produção em ciclos curtos

---

## 3. Escopo

### 3.1 Dentro do Escopo

- Processo completo de desenvolvimento: hipótese → produção
- Motor determinístico de orquestração (ft engine)
- Gates de qualidade automatizados (TDD, cobertura, E2E)
- Agentes especializados por papel no processo
- Rastreamento de estado persistente entre sessões

### 3.2 Fora do Escopo

- Suporte a squads/times (o processo é solo)
- Integração com ferramentas de PM externas (Jira, Linear)
- Gerenciamento de infraestrutura ou deploy (delegado ao DevOps)

---

## 4. Arquitetura do Processo

### 4.1 Fases e Nodes

O processo é estruturado em fases sequenciais, cada uma com nodes específicos:

| Fase | Código | Descrição |
|------|--------|-----------|
| MDD | `ft.mdd.*` | Model-Driven Design — hipótese e PRD |
| Planning | `ft.plan.*` | Task list e breakdown de sprints |
| Sprint | `ft.sprint.*` | Execução TDD por sprint |
| Expert Gate | `ft.expert.*` | Review especializado pós-sprint |
| Acceptance | `ft.acc.*` | Testes de aceitação |
| E2E | `ft.e2e.*` | Testes end-to-end |
| Deploy | `ft.deploy.*` | Deploy para produção |
| Retro | `ft.retro.*` | Retrospectiva e handoff |

### 4.2 Agentes (Symbiotas)

| Agente | Papel |
|--------|-------|
| `ft_manager` | Orquestrador principal — gerencia o fluxo e delega |
| `ft_gatekeeper` | Validador determinístico de stage gates (PASS/BLOCK) |
| `ft_coach` | Conduz MDD, planning e retrospectiva |
| `ft_acceptance` | Design de cenários de teste de aceitação |
| `forge_coder` | Implementação TDD, testes e commits |

### 4.3 Motor (ft engine)

O ft engine é o componente Python que:

- Mantém estado persistente em `project/state/engine_state.yml`
- Avança o processo de forma determinística (sem ambiguidade)
- Delega nodes aos agentes corretos
- Registra gate results e bloqueia progressão em caso de falha
- Rastreia métricas (steps, testes, cobertura, tokens)

---

## 5. Requisitos Funcionais

### 5.1 Motor de Estado

**RF-01** O motor deve manter um estado único e persistente por processo (`process_id`).
**RF-02** O motor deve avançar somente após gate PASS do `ft_gatekeeper`.
**RF-03** O motor deve registrar `blocked_reason` quando um gate falha.
**RF-04** O motor deve suportar múltiplos ciclos (cycle-01, cycle-02, ...) para iterações.
**RF-05** O motor deve rastrear métricas acumuladas (steps completados, cobertura, tokens).

### 5.2 Gates de Qualidade

**RF-06** Cada fase deve ter critérios de gate explícitos e verificáveis.
**RF-07** O gate TDD deve exigir testes red→green antes de avançar.
**RF-08** O gate de cobertura deve exigir ≥ 80% de cobertura para progressão.
**RF-09** O gate E2E deve passar em todos os cenários de aceitação mapeados.
**RF-10** Gates bloqueados não podem ser contornados sem intervenção explícita.

### 5.3 Agentes

**RF-11** Cada agente deve operar apenas dentro do seu escopo de responsabilidade.
**RF-12** O `ft_manager` é o único agente que pode iniciar e avançar nodes.
**RF-13** O `ft_gatekeeper` retorna apenas `PASS` ou `BLOCK` — sem estados intermediários.
**RF-14** O `forge_coder` deve executar ciclos TDD (red → green → refactor) por sprint.
**RF-15** O `ft_acceptance` deve gerar matriz de cenários (happy path, edge cases, error cases).

### 5.4 Rastreabilidade

**RF-16** Cada artifact produzido deve ser registrado no estado (`artifacts` map).
**RF-17** O histórico de gate results deve ser preservado em `gate_log`.
**RF-18** Sessões de agentes devem ser armazenadas em `project/docs/sessions/`.

---

## 6. Requisitos Não-Funcionais

**RNF-01 Determinismo:** O motor deve produzir o mesmo resultado dado o mesmo estado de entrada — sem randomness no fluxo de controle.
**RNF-02 Rastreabilidade:** Toda decisão do motor deve ser auditável via `gate_log` e `engine_state.yml`.
**RNF-03 Resiliência:** O processo deve ser retomável após interrupção sem perda de contexto.
**RNF-04 Velocidade:** O overhead do processo (gates, estado, orquestração) não deve superar 10% do tempo total de desenvolvimento.
**RNF-05 Isolamento:** Agentes não devem editar arquivos fora de seus paths permitidos.

---

## 7. Fluxo Principal (Happy Path)

```
hipotese.md validada
    ↓
[MDD] ft_coach redige PRD → gatekeeper valida → PASS
    ↓
[Planning] ft_coach gera task list → sprints definidas
    ↓
[Sprint N] forge_coder executa TDD (red→green→refactor)
    ↓
[Expert Gate] ft_gatekeeper revisa qualidade do sprint → PASS
    ↓
[Acceptance] ft_acceptance valida cenários → PASS
    ↓
[E2E] forge_coder executa testes E2E → PASS
    ↓
[Deploy] ft_manager aciona deploy via DevOps
    ↓
[Retro] ft_coach registra lições aprendidas → handoff
```

---

## 8. Critérios de Aceitação do Produto

| ID | Critério |
|----|----------|
| AC-01 | Motor executa processo completo (22 steps) sem intervenção manual no fluxo de controle |
| AC-02 | Gates bloqueantes impedem progressão até resolução |
| AC-03 | Estado é preservado entre sessões (retomada sem perda) |
| AC-04 | Cobertura de testes ≥ 80% ao final de cada sprint |
| AC-05 | Zero regressões nos gates E2E ao longo dos ciclos |
| AC-06 | Tempo hipótese → produção reduzido em relação ao baseline ad-hoc |

---

## 9. Métricas de Sucesso

- **Cobertura de testes:** ≥ 80% ao final de cada sprint
- **Taxa de regressão E2E:** 0 regressões entre ciclos
- **Cycle time:** Redução mensurável do tempo hipótese → produção
- **Gate pass rate:** % de gates que passam na primeira tentativa (meta: ≥ 70%)
- **Retrabalho:** Redução de steps que precisam ser reabertos após conclusão

---

## 10. Dependências e Restrições

- **Claude Agent SDK:** Os agentes são implementados como Claude agents com tool access restrito por papel
- **Python 3.11+:** Motor implementado em Python com estado YAML
- **Git:** Commits atômicos por step TDD são parte do contrato do processo
- **DevOps:** Deploy e infraestrutura são delegados ao DevOps — fora do escopo do processo

---

## 11. Riscos

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Agente ignora restrição de path | Alto | ft_gatekeeper valida artifacts antes de avançar |
| Gate bloqueado sem caminho de resolução | Médio | `blocked_reason` explícito + intervenção do ft_manager |
| Estado corrompido entre sessões | Alto | Lock file (`_lock`) + validação no boot do motor |
| Cobertura de testes superficial (mocks excessivos) | Médio | Gate de cobertura exige testes de integração reais |
