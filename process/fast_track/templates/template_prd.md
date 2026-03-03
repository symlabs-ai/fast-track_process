# PRD — Product Requirements Document

> Projeto: [Nome do Projeto]
> Autor: [Nome]
> Data: [YYYY-MM-DD]
> Status: draft | validated | rejected

---

## 1. Hipótese

### 1.1 Contexto
<!-- Cenário atual que despertou a hipótese -->

### 1.2 Sinal de Mercado
<!-- Evidência ou tendência observada (dados, conversas, analogias) -->

### 1.3 Oportunidade
<!-- Potencial de valor percebido -->

### 1.4 Grau de Certeza
<!-- Baixo (0-25%) | Médio-baixo (25-50%) | Médio-alto (50-75%) | Alto (75-100%) -->

---

## 2. Visão

### 2.1 Intenção Central
<!-- Propósito do produto em uma frase -->

### 2.2 Problema
<!-- Dor real que o produto resolve -->

### 2.3 Público-Alvo
<!-- Quem sofre a dor e em qual contexto -->

### 2.4 Diferencial Estratégico
<!-- O que torna este produto único -->

---

## 3. Modelo de Negócio

### 3.1 Monetização
<!-- Como o produto gera receita (ou valor, se open-source) -->

### 3.2 Mercado
<!-- TAM / SAM / SOM estimado (pode ser qualitativo para MVPs) -->

---

## 4. Métricas de Sucesso

| Métrica | Meta | Prazo |
|---------|------|-------|
| <!-- ex: Usuários ativos --> | <!-- ex: 100 --> | <!-- ex: 30 dias --> |

---

## 5. User Stories + Acceptance Criteria

> Cada User Story deve ter ACs no formato Given/When/Then.
> Estas ACs substituem features Gherkin formais no Fast Track.

### US-01: [Título]
**Como** [persona], **quero** [ação], **para** [benefício].

**Acceptance Criteria:**
- **AC-01**: Given [contexto], When [ação], Then [resultado esperado]
- **AC-02**: Given [contexto], When [ação], Then [resultado esperado]

### US-02: [Título]
**Como** [persona], **quero** [ação], **para** [benefício].

**Acceptance Criteria:**
- **AC-01**: Given [contexto], When [ação], Then [resultado esperado]

<!-- Adicionar mais User Stories conforme necessário -->

---

## 6. Requisitos Não-Funcionais

| Requisito | Descrição | Prioridade |
|-----------|-----------|------------|
| <!-- ex: Performance --> | <!-- ex: Response time < 200ms --> | <!-- P0/P1/P2 --> |

---

## 7. Restrições Técnicas + Decision Log

### 7.1 Restrições
<!-- Stack obrigatória, dependências, limitações de infra -->

### 7.2 Decision Log
<!-- Decisões técnicas relevantes (substitui ADR formal) -->

| # | Decisão | Contexto | Alternativas Consideradas | Data |
|---|---------|----------|---------------------------|------|
| 1 | <!-- ex: Usar SQLite --> | <!-- ex: MVP local --> | <!-- ex: Postgres, Redis --> | <!-- YYYY-MM-DD --> |

---

## 8. Riscos e Mitigações

| Risco | Impacto | Probabilidade | Mitigação |
|-------|---------|---------------|-----------|
| <!-- ex: Escopo cresce --> | Alto | Médio | <!-- ex: Fora de escopo definido --> |

---

## 9. Fora de Escopo (v1)

- <!-- Item explicitamente excluído do MVP -->
- <!-- Item explicitamente excluído do MVP -->

---

## 10. Value Tracks & Support Tracks

> Fluxos de negócio mensuráveis que o cliente executa repetidamente.
> Mapeados para ForgeBase Pulse via `forgepulse.value_tracks.yml`.

### Value Tracks (2-5 para o MVP)

| Track ID | Descrição | Done = | KPIs |
|----------|-----------|--------|------|
| <!-- ex: fiscal_issuance --> | <!-- Emissão fiscal end-to-end --> | <!-- XML autorizado na SEFAZ ou contingência válida --> | <!-- success_rate, p95_duration_ms --> |
| <!-- ex: sale_checkout --> | <!-- Fluxo de venda completo --> | <!-- Venda registrada + pagamento confirmado --> | <!-- error_rate, p95_duration_ms --> |

### Support Tracks (1-3, quando aplicável)

| Track ID | Sustenta | Descrição | KPIs |
|----------|----------|-----------|------|
| <!-- ex: fiscal_resilience --> | <!-- fiscal_issuance --> | <!-- Contingência e recuperação fiscal --> | <!-- time_to_contingency_ms, recovery_time_ms --> |

### Mapeamento US → Track

| User Story | Value Track | Subtrack (opcional) |
|------------|-------------|---------------------|
| US-01 | <!-- track_id --> | <!-- subtrack --> |
| US-02 | <!-- track_id --> | <!-- subtrack --> |

### Contrato de Observabilidade

- **Métricas por execução**: count, duration, success, error
- **Eventos mínimos**: start, finish, error
- **Edges observáveis** (quando usados): LLM / HTTP / DB
- **Disciplina de tags**: proibido alta cardinalidade (ex: user_id como tag)
- **Implementação**: toda execução passa por `forge_base.pulse.UseCaseRunner` — nunca chamar `use_case.execute()` direto nos entrypoints
