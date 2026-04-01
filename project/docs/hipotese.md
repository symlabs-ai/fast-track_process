# Hipótese

> Projeto: ft engine
> Data: 2026-04-01
> Status: draft

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
