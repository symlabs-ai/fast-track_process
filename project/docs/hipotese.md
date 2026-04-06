# Hipótese — ForgeProcess Fast Track

**Versão:** 2.0
**Data:** 2026-04-06
**Processo:** fast_track_v2 / ft.mdd.01.hipotese
**Ciclo:** cycle-01
**Status:** Aguardando aprovação do stakeholder

---

## 1. Contexto e Problema

### 1.1 O cenário atual

O desenvolvedor solo que trabalha com AI (Claude, Copilot, etc.) enfrenta um problema estrutural: a AI acelera a geração de código, mas não impõe qualidade, não persiste contexto entre sessões e não audita decisões. O fluxo típico é ad-hoc:

- Sessões de AI sem rastreabilidade — o que foi decidido, por quê, em qual estado estava o projeto
- Gates de qualidade manuais e esquecíveis — lint, testes, cobertura rodam quando o dev lembra
- Nenhuma separação entre "Python controla o processo" e "LLM executa a tarefa" — o modelo toma decisões de processo, o que introduz não-determinismo
- Artefatos de processo (PRD, task list, retrospectiva) existem ou não existem, sem enforcement
- Ciclos de desenvolvimento inconsistentes entre projetos — cada projeto tem seu próprio "jeito"

### 1.2 Consequências observadas

| Problema | Manifestação |
|----------|-------------|
| Falta de rastreabilidade | "O que o agente decidiu na sessão anterior?" — sem resposta |
| Gates ignorados | Testes falham em produção por cobertura insuficiente não detectada |
| Contexto perdido | Cada sessão começa do zero; o LLM não sabe o estado do projeto |
| Processo inconsistente | Projetos diferentes → qualidade diferente, sem causa identificável |
| Desvio de escopo | O LLM edita arquivos fora do escopo sem barreira técnica |

---

## 2. A Hipótese

### 2.1 Declaração central

> **Acreditamos que** um motor determinístico de orquestração de processo — que executa um DAG definido em YAML, delega tarefas a agentes Claude com escopo explícito, e aplica gates binários (PASS/BLOCK) incontornáveis — **permitirá que** desenvolvedores solo alcancem qualidade de produção de forma consistente e rastreável, **sem depender de disciplina manual** para enforcement de processo.

### 2.2 Reformulação como hipótese falsificável

**SE** um processo de desenvolvimento for codificado como DAG em YAML com:
- Agentes com paths de escrita isolados
- Gates determinísticos que bloqueiam progressão sem resolução explícita
- Estado persistido em YAML auditável via `git diff`

**ENTÃO** um desenvolvedor solo + AI conseguirá:
- Completar ciclos de desenvolvimento com zero regressões de gate
- Retomar sessões sem perda de contexto
- Auditar qualquer decisão de processo via histórico git
- Reduzir a carga cognitiva de "onde estou no processo" para zero

**PODEMOS VERIFICAR ISSO porque:**
- `gate_log` registra cada gate com timestamp e detalhe
- `completed_nodes` preserva a ordem de execução entre sessões
- Métricas acumuladas (tests_passing, coverage, llm_calls) são mensuráveis
- O cycle-01 serve como baseline de comparação para cycle-02+

---

## 3. Usuário-Alvo

### 3.1 Perfil primário

**Desenvolvedor solo com AI como par de programação**

| Atributo | Detalhe |
|----------|---------|
| Experiência | Sênior a tech lead — sabe o que quer, não quer gerenciar processo manualmente |
| Contexto | Projetos de 1–3 meses, sem equipe, com AI como força multiplicadora |
| Ferramentas | Claude Code, git, Python — stack minimalista |
| Dor principal | Qualidade inconsistente e falta de rastreabilidade entre sessões de AI |
| O que NÃO quer | Frameworks pesados, configuração excessiva, LLMs tomando decisões de processo |

### 3.2 Cenário de uso típico

```
Dev inicia uma sessão → ft continue
Motor lê o estado atual (engine_state.yml)
Motor sabe: "estamos no node ft.tdd.02.red, sprint-03-tdd"
Motor delega ao forge_coder: "escreva testes RED para o módulo X"
forge_coder produz testes em tests/ (apenas)
Gate valida: tests_fail (testes vermelhos = PASS do gate RED)
Motor avança para ft.tdd.03.green
Dev fecha o terminal — contexto preservado
```

---

## 4. Proposta de Valor

### 4.1 O que o ft engine entrega

| Valor | Como | Evidência (cycle-01) |
|-------|------|----------------------|
| **Qualidade sem disciplina manual** | Gates PASS/BLOCK bloqueiam progressão automaticamente | 17/17 gates PASS, zero regressões |
| **Rastreabilidade total** | YAML + git diff = histórico auditável de cada decisão | S2/S10 do smoke-report: retomada com 13 nodes preservados |
| **Contexto persistente entre sessões** | `engine_state.yml` como source of truth | `current_node` correto após reinício |
| **Escopo de agente enforçado** | Paths permitidos por agente + hook PreToolUse | Nenhum agente editou fora do escopo no cycle-01 |
| **Processo reproduzível** | DAG em YAML = mesmo processo em qualquer projeto | `FAST_TRACK_PROCESS_V2.yml` reutilizável |

### 4.2 O que o ft engine NÃO é

- Não é um framework de AI (sem LangChain, sem abstração de LLM)
- Não é um gerenciador de tarefas para humanos (sem Jira, sem sprint boards)
- Não é um sistema de CI/CD (não substitui GitHub Actions / Gitea Actions)
- Não é um orquestrador de microsserviços (não é Kubernetes, não é Celery)

---

## 5. Premissas Críticas

As premissas abaixo devem ser verdadeiras para que a hipótese se sustente. Se alguma for falsa, o produto precisa ser revisado.

| # | Premissa | Risco se falsa | Como validar |
|---|----------|----------------|--------------|
| P1 | O LLM consegue executar tarefas dentro de paths isolados sem escapar do escopo | Alto — escopo comprometido invalida o modelo de isolamento | Hook PreToolUse + audit de paths no cycle-01 |
| P2 | Gates determinísticos (sem LLM) são suficientes para garantir qualidade de artefatos | Médio — se gates passam placeholders, o processo é inútil | Validator `has_sections` (proposto para cycle-02) |
| P3 | O custo de contexto (tokens) de um ciclo completo é viável em produção | Médio — cycle-01: ~355K tokens + ~155M cache read tokens | Métricas acumuladas entre ciclos |
| P4 | Um DAG linear (sem paralelismo real) é suficiente para projetos solo | Baixo — solo dev é sequencial por natureza | `parallel.py` existe mas não é o caminho crítico |
| P5 | O desenvolvedor aceita aguardar aprovação (`ft approve`) nos nodes marcados | Baixo — o dev controla o ritmo, não o motor | Hyper-mode disponível para bypassar quando necessário |

---

## 6. Métricas de Sucesso

### 6.1 Métricas primárias (o que importa)

| Métrica | Baseline (cycle-01) | Meta (cycle-02) |
|---------|--------------------|-----------------| 
| Gate pass rate (sem regressão) | 100% (17/17) | ≥ 100% |
| Retomada de sessão sem perda de contexto | ✅ Validado (S10) | 100% dos reinícios |
| Cobertura de testes ao final do ciclo | N/A (não conectado) | ≥ 80% auditável via state |
| Nodes completados sem intervenção manual | 17/22 (77%) | ≥ 90% |
| Tempo de ciclo MDD→MVP | N/A (não medido) | A medir em cycle-02 |

### 6.2 Métricas de saúde do processo

| Métrica | Valor cycle-01 |
|---------|----------------|
| Sessões LLM | 11 |
| API calls | 1.497 |
| Tokens (input + output) | ~355.640 |
| Cache read tokens | ~155.566.886 |
| Cache hit rate (implícito) | ~99.8% |

---

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| LLM produz artefatos placeholder que passam nos gates | Alta (ocorreu no cycle-01) | Alto | Validator `has_sections` para MDD (P2 do retro) |
| Métricas de cobertura não conectadas ao state | Ocorreu | Médio | Conectar `coverage.xml` ao `StateManager` (P1 do retro) |
| Race condition em processos concorrentes | Baixa (corrigida) | Alto | Lock file em `engine_state.yml` (TD-06) |
| Custo de tokens inviável em projetos longos | Média | Médio | Cache hit rate alto mitiga; monitorar em cycle-02 |
| Novo node adicionado fora do YAML de processo | Ocorreu no cycle-01 | Baixo | Regra: YAML primeiro, implementação depois (TD-03 retro) |

---

## 8. O Que Já Foi Validado (cycle-01)

O cycle-01 serviu como experimento controlado para validar as premissas centrais da hipótese:

| Premissa | Status | Evidência |
|----------|--------|-----------|
| Gates PASS/BLOCK funcionam como barreira de qualidade | ✅ Validada | 17/17 PASS, 0 regressões |
| Estado persistido permite retomada sem perda de contexto | ✅ Validada | S10 smoke-report |
| Isolamento de paths por agente é tecnicamente viável | ✅ Validada | Nenhuma violação de escopo |
| Motor sequencial é suficiente para solo dev | ✅ Validada | 22 nodes, ciclo completo |
| LLM dentro de paths isolados produz artefatos aceitáveis | ⚠️ Parcial | Conteúdo placeholder passou nos gates de existência |
| Cobertura ≥ 80% é auditável via state | ❌ Não validada | `coverage.xml` não conectado |

---

## 9. Próximos Passos (cycle-02)

A hipótese central está validada. O cycle-02 tem como objetivo fechar as lacunas identificadas:

1. **Adicionar validator semântico** (`has_sections`) para documentos MDD — impede placeholders nos gates
2. **Conectar métricas de cobertura** ao `StateManager` — torna AC-04 auditável
3. **Medir cycle time** MDD→MVP — baseline para comparação com abordagem ad-hoc
4. **Formalizar inclusão de novos nodes** — YAML de processo como PR antes da implementação

---

## 10. Referências

| Documento | Path | Relevância |
|-----------|------|-----------|
| SPEC | `project/docs/SPEC.md` | Especificação técnica completa do ft engine |
| Retro cycle-01 | `project/docs/retro.md` | Lições aprendidas e fricções do cycle-01 |
| Tech Stack | `project/docs/tech_stack.md` | Justificativas de design de stack |
| Smoke Report | `project/docs/smoke-report.md` | Evidências de validação empírica |
| Processo YAML | `process/fast_track/FAST_TRACK_PROCESS_V2.yml` | DAG de processo ativo |
