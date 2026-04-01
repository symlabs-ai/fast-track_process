# PRD — Product Requirements Document

> Projeto: ft engine
> Autor: Symlabs
> Data: 2026-04-01
> Status: draft

---

## 1. Hipotese

### 1.1 Contexto

O Fast Track e um processo agil para solo dev + AI que define 19 steps, 9 fases e 5 symbiotas (agentes LLM especializados). Hoje ele funciona como um framework de prompts: o LLM (ft_manager) orquestra o processo, decide qual step vem depois, edita estado e valida gates — tudo via interpretacao de texto em linguagem natural.

Isso funciona, mas e fragil. O LLM pode pular steps, esquecer validacoes, gravar estado incorreto ou tomar decisoes criativas sobre o fluxo.

### 1.2 Sinal de Mercado

- **LLMs como executores sao confiaveis; LLMs como orquestradores sao imprevisiveis.** A industria esta convergindo para "human-in-the-loop" e "code-in-the-loop".
- **Claude Code subagents** permitem delegar tarefas de construcao a LLMs de forma programatica, com contexto controlado e output capturavel.
- **Processos deterministicos para AI-assisted dev** ainda nao existem como produto. Ferramentas como SpecKit, BMAD e OpenSpec focam em geracao de specs, nao em orquestracao end-to-end com validacao automatica.
- **A propria Symlabs precisa disso internamente** — cada projeto Fast Track repete o mesmo padrao de orquestracao manual via prompts.

### 1.3 Oportunidade

Criar um motor Python que execute o processo Fast Track de forma deterministica, usando LLMs apenas como executores de construcao. O motor controla fluxo, estado, validacao e avanco — eliminando a classe inteira de bugs causados por "LLM decidiu errado".

- **Valor imediato:** projetos Symlabs rodam Fast Track com zero drift de processo.
- **Valor futuro:** qualquer processo YAML pode ser executado pelo motor, nao apenas Fast Track.

### 1.4 Grau de Certeza

**Medio-alto (65%)** — A dor e real e verificada internamente (drift de processo acontece em toda sessao longa). A arquitetura e viavel (spec ja desenhada, Claude Code subagents funcionam). O risco principal e escopo: a superficie de validacao e grande e pode ser dificil cobrir todos os casos sem over-engineering.

---

## 2. Visao

### 2.1 Intencao Central

Transformar o Fast Track de um framework de prompts em um motor deterministico Python que usa LLMs exclusivamente como executores de construcao.

### 2.2 Problema

Quando o LLM orquestra o processo, ele pode pular steps, gravar estado invalido, ignorar gates ou tomar decisoes criativas sobre o fluxo. Isso causa drift silencioso — o processo parece estar rodando, mas validacoes foram puladas e artefatos ficaram incompletos. O desenvolvedor so descobre tarde demais.

### 2.3 Publico-Alvo

Desenvolvedores solo que trabalham com assistentes de IA (Claude Code, Cursor, etc.) e precisam de um processo estruturado com garantias de qualidade. Inicialmente, a equipe Symlabs. Posteriormente, qualquer dev que adote o Fast Track como processo.

### 2.4 Diferencial Estrategico

**Processo como codigo, nao como prompt.** O motor le o processo de um YAML, executa cada step delegando construcao ao LLM, valida resultados com checagens deterministicas (Python puro, sem interpretacao), e so avanca quando tudo passa. O LLM nao sabe qual step vem depois — recebe uma tarefa, executa, devolve. Zero decisao de processo no LLM.

---

## 3. Modelo de Negocio

### 3.1 Monetizacao

Ferramenta interna Symlabs (open-source no futuro). Valor gerado pela eliminacao de retrabalho causado por drift de processo e pela reducao de tempo de supervisao humana sobre agentes LLM.

### 3.2 Mercado

- **TAM:** Desenvolvedores solo usando AI assistants (~2M globalmente, crescendo)
- **SAM:** Devs que adotam processos estruturados com AI (estimativa qualitativa: ~200K)
- **SOM:** Usuarios Symlabs + early adopters Fast Track (~50-200 no primeiro ano)

---

## 4. Metricas de Sucesso

| Metrica | Meta | Prazo |
|---------|------|-------|
| Steps executados sem drift | 100% (zero skip) | MVP |
| Validacao automatica pass rate | > 90% sem intervencao humana | MVP |
| Retry success rate (LLM corrige apos feedback) | > 70% | MVP |
| Tempo medio por step (delegacao + validacao) | < 120s | 30 dias pos-MVP |
| Projetos Symlabs usando ft engine | 3+ | 60 dias pos-MVP |

---

## 5. User Stories + Acceptance Criteria

### US-01: Executar proximo step do processo
**Como** desenvolvedor, **quero** rodar `ft continue` e o motor executar o proximo step automaticamente, **para** avancar no processo sem orquestrar manualmente.

**Acceptance Criteria:**
- **AC-01**: Given processo no step `ft.mdd.01.hipotese` com status READY, When rodo `ft continue`, Then motor delega ao LLM, valida artefato, e avanca para o proximo step
- **AC-02**: Given step que requer aprovacao, When LLM produz artefato valido, Then motor pausa e aguarda `ft approve` do stakeholder
- **AC-03**: Given step com validacao falhando, When LLM nao corrige apos 3 retries, Then motor marca step como BLOCKED com motivo especifico

### US-02: Consultar estado do processo
**Como** desenvolvedor, **quero** rodar `ft status` e ver onde estou no processo, **para** entender progresso e proximos passos.

**Acceptance Criteria:**
- **AC-01**: Given processo em andamento, When rodo `ft status`, Then vejo node atual, fase, steps completados e proximo step
- **AC-02**: Given processo em andamento, When rodo `ft status --full`, Then vejo tambem artefatos produzidos, gate_log e metricas

### US-03: Aprovar ou rejeitar artefato
**Como** stakeholder, **quero** aprovar ou rejeitar artefatos pendentes via CLI, **para** que o processo avance ou retorne ao LLM com feedback.

**Acceptance Criteria:**
- **AC-01**: Given artefato pendente de aprovacao, When rodo `ft approve`, Then motor marca step como PASS e avanca
- **AC-02**: Given artefato pendente, When rodo `ft reject --reason "falta X"`, Then motor reenvia ao LLM com o motivo como feedback

### US-04: Validacao deterministica de artefatos
**Como** desenvolvedor, **quero** que o motor valide artefatos com checagens Python puras, **para** garantir que nenhum step avanca com artefato incompleto ou invalido.

**Acceptance Criteria:**
- **AC-01**: Given node com validator `file_exists`, When artefato nao existe, Then validacao retorna BLOCK
- **AC-02**: Given node com validator `has_sections`, When documento nao contem todas as secoes requeridas, Then validacao retorna BLOCK com lista de secoes faltantes
- **AC-03**: Given node com validator `tests_pass`, When testes falham, Then validacao retorna BLOCK com output dos testes

### US-05: Retry automatico com feedback
**Como** desenvolvedor, **quero** que quando o LLM produz resultado invalido o motor reenvie com feedback especifico dos validadores, **para** corrigir sem intervencao manual.

**Acceptance Criteria:**
- **AC-01**: Given LLM produziu artefato que falhou em `coverage_min`, When retry e disparado, Then prompt inclui "cobertura atual X%, minimo Y%" e items especificos que falharam
- **AC-02**: Given retry limite atingido (default 3), When LLM nao corrige, Then motor marca BLOCKED e para

### US-06: Estado protegido por lock
**Como** desenvolvedor, **quero** que apenas o motor Python escreva no ft_state.yml, **para** prevenir corrupcao de estado por LLMs ou edicao acidental.

**Acceptance Criteria:**
- **AC-01**: Given motor rodando, When LLM tenta editar ft_state.yml, Then escrita e bloqueada por hook PreToolUse
- **AC-02**: Given motor salva estado, When verifico ft_state.yml, Then campo `_lock` contem owner, pid e timestamp validos

### US-07: Inicializar novo projeto
**Como** desenvolvedor, **quero** rodar `ft init <nome>` e ter um projeto Fast Track configurado, **para** comecar um novo produto com o processo ja pronto.

**Acceptance Criteria:**
- **AC-01**: Given nenhum projeto existente, When rodo `ft init meu_projeto`, Then cria estrutura de diretorios, ft_state.yml inicial e vincula processo YAML
- **AC-02**: Given projeto inicializado, When rodo `ft status`, Then vejo step inicial como READY

### US-08: Executar sprint completa
**Como** desenvolvedor, **quero** rodar `ft continue --sprint` e o motor executar todas as tasks da sprint atual, **para** avancar multiplas tasks sem interacao manual por task.

**Acceptance Criteria:**
- **AC-01**: Given sprint com 5 tasks, When rodo `ft continue --sprint`, Then motor executa cada task sequencialmente com TDD loop e gate.delivery
- **AC-02**: Given task falha apos retries, When task esta BLOCKED, Then motor registra e avanca para a proxima task (ou para, conforme configuracao)

---

## 6. Requisitos Nao-Funcionais

| Requisito | Descricao | Prioridade |
|-----------|-----------|------------|
| Offline-first | Estado em YAML/SQLite local, sem dependencia de servidor | P0 |
| Determinismo | Mesma entrada + mesmo estado = mesmo comportamento do motor | P0 |
| Idempotencia | Rodar `ft continue` duas vezes no mesmo estado nao causa efeitos duplicados | P0 |
| Testabilidade | Todo validador e funcao Python pura, testavel isoladamente | P0 |
| Tempo de startup | CLI inicia em < 500ms | P1 |
| Extensibilidade | Novos validadores sao funcoes Python registradas no catalogo | P1 |
| Processo-agnostico | Motor executa qualquer YAML no formato definido, nao apenas Fast Track | P2 |

---

## 7. Restricoes Tecnicas + Decision Log

### 7.1 Restricoes

- **Linguagem:** Python 3.12+
- **Estado:** YAML (ft_state.yml) com lock; SQLite para metricas futuras
- **LLM:** Claude Code subagents (via SDK) ou API direta Anthropic
- **Dependencias minimas:** PyYAML, Click (CLI), pytest (testes)
- **Sem servidor:** tudo roda local na maquina do desenvolvedor

### 7.2 Decision Log

| # | Decisao | Contexto | Alternativas Consideradas | Data |
|---|---------|----------|---------------------------|------|
| 1 | YAML para estado (nao SQLite) | MVP local, legibilidade humana, git-friendly | SQLite, JSON | 2026-04-01 |
| 2 | Claude Code subagents como executor LLM | Integracao nativa com Claude Code, contexto controlado | API direta, LangChain, CrewAI | 2026-04-01 |
| 3 | Validadores como funcoes Python puras | Testabilidade, debugabilidade, sem interpretacao | LLM-as-judge, regex-only | 2026-04-01 |
| 4 | Lock via campo _lock no YAML + hook PreToolUse | Prevencao de corrupcao sem overhead de file locking OS | flock, lockfile, pid file | 2026-04-01 |
| 5 | Grafo de processo em YAML (nao hardcoded) | Extensibilidade — qualquer processo YAML pode ser executado | Python classes, JSON, TOML | 2026-04-01 |

---

## 8. Riscos e Mitigacoes

| Risco | Impacto | Probabilidade | Mitigacao |
|-------|---------|---------------|-----------|
| LLM produz codigo semanticamente errado (testes passam, logica incorreta) | Alto | Medio | Sprint Expert Gate com revisao humana; motor nao substitui julgamento |
| Superficie de validacao grande demais para MVP | Medio | Medio | Implementar validadores incrementalmente (fase 1: basicos, fase 3: TDD completo) |
| Retry loop infinito com LLM que nao corrige | Medio | Baixo | Limite de retries configuravel (default 3), BLOCKED apos limite |
| Estado YAML corrompido por bug no motor | Alto | Baixo | Backup automatico antes de cada escrita; `ft validate state` para verificacao manual |
| Performance — subagente LLM lento | Baixo | Medio | Paralelismo (fase 4); sprint-level delegation; nao bloquear dev |
| Processo YAML cresce e fica complexo | Medio | Medio | Manter expressividade minima; compilador NL→YAML planejado para futuro |

---

## 9. Fora de Escopo (v1)

- UI web ou dashboard grafico
- Suporte a multiplos LLM providers simultaneamente (MVP: apenas Claude)
- Compilador de linguagem natural para YAML de processo
- Colaboracao multi-usuario (MVP: solo dev)
- Deploy automatico apos MVP
- Integracao com CI/CD externo
- Metricas em banco de dados (MVP: YAML simples)
- Paralelismo de tasks (planejado para fase 4, nao MVP)

---

## 10. Value Tracks & Support Tracks

### Value Tracks

| Track ID | Descricao | Done = | KPIs |
|----------|-----------|--------|------|
| continue_loop | Processo avanca do step atual ate o proximo gate ou fim, com validacao automatica | Step validado e estado avancado para proximo node | steps_advanced, validation_pass_rate, retry_count |
| sprint_execution | Sprint inteira executada com TDD loop, gate.delivery por task e commit automatico | Todas as tasks da sprint com gate.delivery PASS | sprint_completion_rate, coverage_delta, tasks_per_sprint |
| stakeholder_review | Artefato apresentado ao stakeholder, feedback capturado, decisao registrada | Decisao (approve/reject) registrada no gate_log | response_time, approval_rate, rework_count |

### Support Tracks

| Track ID | Sustenta | Descricao | KPIs |
|----------|----------|-----------|------|
| retry_with_feedback | continue_loop | Quando validacao falha, reenvia ao LLM com feedback dos validadores. Max N retries antes de BLOCK. | retry_success_rate, avg_retries_per_step |
| state_recovery | continue_loop | Detecta estado corrompido ou inconsistente e oferece procedimento de recovery. | corruption_detected_count, recovery_success_rate |
| gate_enforcement | sprint_execution | Executa gates compostos (delivery, smoke, MVP) como validadores Python deterministicos. | gate_pass_rate, false_positive_rate |

### Mapeamento US → Track

| User Story | Value Track | Support Track |
|------------|-------------|---------------|
| US-01 | continue_loop | retry_with_feedback |
| US-02 | continue_loop | — |
| US-03 | stakeholder_review | — |
| US-04 | continue_loop | gate_enforcement |
| US-05 | continue_loop | retry_with_feedback |
| US-06 | continue_loop | state_recovery |
| US-07 | continue_loop | — |
| US-08 | sprint_execution | gate_enforcement |
