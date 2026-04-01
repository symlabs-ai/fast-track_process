# Hipótese

> Projeto: ft engine
> Data: 2026-04-01
> Status: complete

---

## 1. Contexto

O Fast Track é um processo ágil para solo dev + AI que define 19 steps, 9 fases e 5 symbiotas (agentes LLM especializados). Hoje ele funciona como um **framework de prompts**: o LLM (ft_manager) orquestra o processo, decide qual step vem depois, edita estado e valida gates — tudo via interpretação de texto em linguagem natural.

Isso funciona, mas é frágil. O LLM pode pular steps, esquecer validações, gravar estado incorreto ou tomar decisões criativas sobre o fluxo. A CLI existente (`ft.py`) valida artefatos e estado, mas não controla a execução — é reativa, não proativa.

## 2. Sinal de Mercado

- **LLMs como executores são confiáveis; LLMs como orquestradores são imprevisíveis.** A indústria está convergindo para "human-in-the-loop" e "code-in-the-loop" — agentes que fazem o que mandam, não que decidem o que fazer.
- **Claude Code subagents** permitem delegar tarefas de construção a LLMs de forma programática, com contexto controlado e output capturável.
- **Processos determinísticos para AI-assisted dev** ainda não existem como produto. Ferramentas como SpecKit, BMAD e OpenSpec focam em geração de specs, não em orquestração end-to-end com validação automática.
- **A própria Symlabs precisa disso internamente** — cada projeto Fast Track repete o mesmo padrão de orquestração manual via prompts.

## 3. Oportunidade

Criar um motor Python que execute o processo Fast Track de forma determinística, usando LLMs apenas como executores de construção (escrever código, docs, responder perguntas). O motor controla fluxo, estado, validação e avanço — eliminando a classe inteira de bugs causados por "LLM decidiu errado".

Valor imediato: projetos Symlabs rodam Fast Track com zero drift de processo.
Valor futuro: qualquer processo YAML pode ser executado pelo motor, não apenas Fast Track.

## 4. Grau de Certeza

**Médio-alto (65%)** — A dor é real e verificada internamente (drift de processo acontece em toda sessão longa). A arquitetura é viável (spec já desenhada, Claude Code subagents funcionam). O risco principal é escopo: a superfície de validação é grande e pode ser difícil cobrir todos os casos sem over-engineering.

---

## 5. Visão Inicial

### 5.1 Intenção Central

Transformar o Fast Track de um framework de prompts em um motor determinístico Python que usa LLMs exclusivamente como executores de construção.

### 5.2 Problema

Quando o LLM orquestra o processo, ele pode pular steps, gravar estado inválido, ignorar gates ou tomar decisões criativas sobre o fluxo. Isso causa drift silencioso — o processo parece estar rodando, mas validações foram puladas e artefatos ficaram incompletos. O desenvolvedor só descobre tarde demais.

### 5.3 Público-Alvo

Desenvolvedores solo que trabalham com assistentes de IA (Claude Code, Cursor, etc.) e precisam de um processo estruturado com garantias de qualidade. Inicialmente, a equipe Symlabs. Posteriormente, qualquer dev que adote o Fast Track como processo.

### 5.4 Diferencial Estratégico

**Processo como código, não como prompt.** O motor lê o processo de um YAML, executa cada step delegando construção ao LLM, valida resultados com checagens determinísticas (Python puro, sem interpretação), e só avança quando tudo passa. O LLM não sabe qual step vem depois — recebe uma tarefa, executa, devolve. Zero decisão de processo no LLM.

---

## 6. Value Tracks (candidatos)

> Fluxos de negócio que o cliente executaria repetidamente. Serão formalizados no PRD (seção 10).

| Track (candidato) | Done = | KPIs (rascunho) |
|-------------------|--------|------------------|
| continue_loop | Processo avança do step atual até o próximo gate ou fim, com validação automática | steps_advanced, validation_pass_rate, retry_count |
| sprint_execution | Sprint inteira executada com TDD loop, gate.delivery por task e commit automático | sprint_completion_rate, coverage_delta, tasks_per_sprint |
| stakeholder_review | Artefato apresentado ao stakeholder, feedback capturado, decisão (approve/reject) registrada | response_time, approval_rate, rework_count |

### Support Tracks (quando aplicável)

| Track (candidato) | Sustenta | Descrição |
|-------------------|----------|-----------|
| retry_with_feedback | continue_loop | Quando validação falha, reenvia ao LLM com feedback dos validadores. Max N retries antes de BLOCK. |
| state_recovery | continue_loop | Detecta estado corrompido ou inconsistente e oferece procedimento de recovery. |
| gate_enforcement | sprint_execution | Executa gates compostos (delivery, smoke, MVP) como validadores Python determinísticos. |

---

## 7. Premissas Críticas

> Condições que precisam ser verdadeiras para a hipótese se sustentar. Se qualquer premissa for invalidada, a hipótese deve ser revista.

| # | Premissa | Como verificar | Status |
|---|----------|----------------|--------|
| P1 | LLMs (Claude Code subagents) conseguem executar tasks de construção com qualidade suficiente quando recebem contexto controlado e prompt específico | Testes com delegação real em 3+ steps do processo | A verificar |
| P2 | Validadores determinísticos (Python puro) conseguem detectar artefatos incompletos ou incorretos sem falsos positivos excessivos | Implementar 5+ validadores e medir false positive rate < 10% | A verificar |
| P3 | O processo Fast Track pode ser representado como grafo YAML sem perda de semântica relevante | Converter processo completo para YAML e executar E2E | Parcialmente validado (v3 funciona com 5 steps) |
| P4 | O desenvolvedor solo aceita ceder controle de orquestração ao motor em troca de garantias de processo | Dogfooding interno em 2+ projetos Symlabs | A verificar |
| P5 | O overhead do motor (startup, validação, retry) não torna o fluxo mais lento que orquestração manual | Medir tempo por step < 120s em média | A verificar |

---

## 8. Critérios de Validação da Hipótese

> O que precisa acontecer para considerar a hipótese **validada** e avançar para o PRD completo.

### Validação Mínima (gate para avançar)

- [ ] Motor executa processo E2E (discovery → delivery) sem intervenção manual no fluxo
- [ ] Zero drift de processo em execução completa (nenhum step pulado ou validação ignorada)
- [ ] Pelo menos 1 projeto Symlabs real usa o motor em dogfooding

### Sinais de Invalidação (red flags)

- LLM não consegue produzir artefatos válidos mesmo com retry + feedback específico (retry success < 30%)
- Validadores geram tantos falsos positivos que o dev desativa validação (false positive > 30%)
- Tempo por step excede 5min consistentemente, tornando o motor mais lento que processo manual
- Representação YAML do processo perde nuances que causam comportamento incorreto do motor

---

## 9. Próximos Passos

1. **Validar P3** — Executar processo V3 completo com motor atual e documentar gaps
2. **Validar P1** — Testar delegação real a Claude Code subagents em steps de construção (código, docs)
3. **Validar P2** — Implementar validadores core e medir taxa de falsos positivos
4. **Dogfooding** — Usar motor no próximo projeto Symlabs interno para validar P4 e P5
5. **Decisão** — Com premissas validadas, avançar para PRD completo (já rascunhado)
