# Analise Comparativa: Fast Track vs SpecKit vs BMAD vs OpenSpec

## Resumo Executivo

Quatro frameworks para desenvolvimento guiado por IA. Todos prometem processos estruturados.
**Nenhum resolve o problema fundamental: agentes LLM podem ignorar qualquer instrucao.**

A diferenca esta em **quantas camadas de enforcement** cada um implementa e quao dificil
e para o agente desviar.

---

## Tabela Comparativa

| Dimensao | Fast Track | SpecKit | BMAD | OpenSpec |
|----------|-----------|---------|------|---------|
| **Repo** | symlabs-ai/fast-track_process | github/spec-kit | bmad-code-org/BMAD-METHOD | Fission-AI/OpenSpec |
| **Stars** | interno | ~84K | ~43K | ~36K |
| **Abordagem** | Processo + CLI + Hooks | Spec-first + Scripts | Agent personas + Workflows | Artifact DAG + Delta specs |
| **CLI** | `ft` (Python) | `specify` (Python/uv) | `npx bmad-method` (Node) | `openspec` (Node/npm) |
| **Agentes** | 5 symbiotas | Nao define agentes | 8 personas com nomes | Nao define agentes |
| **Linguagem** | Python-first | Agnostico | Agnostico | TypeScript-first |
| **Escopo** | Solo dev + AI, MVP rapido | Feature development | Full lifecycle enterprise | Brownfield changes |

---

## Enforcement: Analise Profunda

### Nivel 1: Instrucoes no Prompt (todos tem)

Todos os quatro usam markdown com instrucoes para o agente. Nenhum garante cumprimento.

| Framework | Linguagem usada | Funciona? |
|-----------|----------------|-----------|
| Fast Track | "REGRA OBRIGATORIA", tabelas de PODE/NAO PODE | Nao — ft_manager ignorou 53 gate.delivery |
| SpecKit | "STOP and ask", checklists | Nao — agente pode pular |
| BMAD | "FORBIDDEN", "HALT", "NEVER", "CRITICAL" em caps | Nao — tudo depende do modelo obedecer |
| OpenSpec | `<warning>` tags XML | Nao — aviso, nao bloqueio |

**Conclusao: Prompt-based enforcement nao existe. E sugestao.**

---

### Nivel 2: Shell Scripts / Validacao Pre-Execucao

| Framework | Mecanismo | O que bloqueia | Bypass possivel? |
|-----------|-----------|----------------|-----------------|
| **Fast Track** | `ft validate state`, `ft validate gate mvp` | State invalido, artefatos ausentes, gate_log vazio | Sim — agente pode nao chamar |
| **SpecKit** | `check-prerequisites.sh` com `exit 1` | Feature dir ausente, plan.md ausente, tasks.md ausente | Parcial — script roda dentro do slash command, mas agente pode nao usar o slash command |
| **BMAD** | `validate-skills.js --strict` | SKILL.md malformado, naming incorreto, sequencia violada | Sim — so roda em CI/dev, nao no agente |
| **OpenSpec** | `validator.ts` com `process.exitCode = 1` | Delta specs sem SHALL/MUST, sem cenarios, duplicados | Parcial — roda no `/opsx:archive`, bloqueia merge |

**SpecKit se destaca aqui**: os shell scripts rodam automaticamente quando o agente invoca um slash command.
O script faz `exit 1` se prerequisitos nao existem, e o agente recebe o erro.
Mas o agente pode simplesmente nao usar o slash command e fazer as coisas diretamente.

**Fast Track**: a CLI e mais completa (12+ comandos vs 1 script), mas depende do agente chamar.
Os hooks mitigam parcialmente — rodam automaticamente apos Edit/Write.

---

### Nivel 3: Hooks / Automacao Pos-Acao

| Framework | Hooks? | Trigger | O que faz |
|-----------|--------|---------|-----------|
| **Fast Track** | Sim — Claude Code hooks | PostToolUse (Edit ft_state.yml), PostBash (git commit), PostToolUse (mvp_status) | `ft validate state`, `ft validate gate mvp` |
| **SpecKit** | Sim — Extension hooks | before_specify, before_plan, before_implement, etc. | Executa comandos customizaveis; mandatory hooks bloqueiam |
| **BMAD** | Nao | — | — |
| **OpenSpec** | Nao | — | — |

**Fast Track e SpecKit sao os unicos com hooks.** A diferenca:
- Fast Track usa hooks do Claude Code (settings.json) — rodam no runtime da plataforma
- SpecKit usa hooks embarcados no template do slash command — dependem do agente ler o template

**BMAD e OpenSpec nao tem nenhum mecanismo automatico.** Tudo e prompt.

---

### Nivel 4: Estado Mecanico (DAG / State Machine)

| Framework | Mecanismo | Impede skip? |
|-----------|-----------|-------------|
| **Fast Track** | `ft_state.yml` + JSON Schema + `completed_steps` | Nao — agente pode escrever qualquer coisa no state |
| **SpecKit** | Filesystem — arquivo deve existir para proximo passo | Semi — script verifica existencia, mas agente pode criar arquivo vazio |
| **BMAD** | `stepsCompleted` array em frontmatter + arquivos step-by-step | Nao — agente pode editar o array |
| **OpenSpec** | **DAG real** com topological sort (Kahn's algorithm) + filesystem detection | **Mais proximo de enforcement real** — o DAG calcula o que esta BLOCKED/READY/DONE baseado em arquivos existentes |

**OpenSpec se destaca aqui.** O DAG e calculado pelo codigo TypeScript, nao pelo agente.
O agente recebe uma lista de artefatos com status BLOCKED/READY/DONE e instrucoes
que dizem explicitamente "estas dependencias nao foram cumpridas".

Mas ainda: o agente pode ignorar o warning e criar o arquivo mesmo assim.

---

### Nivel 5: Enforcement Real (ninguem tem)

Nenhum framework implementa:
- Bloquear escrita em arquivo se gate falhou
- Impedir commit se validacao nao passou
- Rejeitar mudanca de estado sem prerequisitos
- Runtime sandbox que impede acoes nao autorizadas

**Isso nao existe em nenhuma plataforma de AI coding hoje.**

---

## Pontos Fortes de Cada Um

### SpecKit (GitHub)
- **Shell scripts com exit 1** — o mais proximo de hard enforcement
- **Extension system** — hooks plugaveis por fase
- **25+ AI tools suportados** — maior compatibilidade
- **Constitution** — principios do projeto que sao validados em cada fase

### BMAD
- **8 personas nomeadas** — agentes com personalidade e restricoes de papel
- **Validador deterministico** (validate-skills.js) — 25 regras automatizadas
- **Step-by-step workflow** — cada step so aponta pro proximo
- **HALT semantics** — bloqueio explicito esperando input humano

### OpenSpec
- **DAG real com topological sort** — unico framework com state machine mecanica
- **Delta spec syncing** — ADDED/MODIFIED/REMOVED/RENAMED
- **Validacao de specs** — SHALL/MUST obrigatorios, cenarios obrigatorios
- **Brownfield-first** — projetado para mudancas em codigo existente

### Fast Track
- **CLI mais completa** — 12+ comandos cobrindo todo o ciclo de vida
- **Hooks do Claude Code** — unico que usa hooks da plataforma (nao do prompt)
- **Gate MVP** — verificacao completa de 12 itens antes de declarar entrega
- **Sprint-level delegation** — resolve o problema de contexto (sprint, nao task)
- **Sincronia automatica** — versioning de engine/agents com check a cada execucao
- **Role enforcement** — `ft role` com PASS/BLOCK por step/symbiota

---

## Fraquezas de Cada Um

### SpecKit
- Sem state machine — so verifica existencia de arquivos
- Sem tracking de progresso alem de filesystem
- Extension hooks dependem do agente ler o template

### BMAD
- **Zero automacao** — tudo e prompt, nada roda automaticamente
- Complexidade alta — 21 agents, 50+ workflows, curva de aprendizado ingreme
- Validador so roda em CI, nao no fluxo do agente

### OpenSpec
- **Warnings, nao blocks** — o DAG calcula BLOCKED mas so avisa
- Sem hooks — nada roda automaticamente
- Single-agent — nao tem delegacao multi-agente

### Fast Track
- **Agente pode nao chamar a CLI** — hooks mitigam mas nao garantem
- Processo complexo — 19 steps, 5 symbiotas, curva de aprendizado
- Python-only — nao suporta outros ecossistemas

---

## Matriz de Enforcement por Camada

```
Camada 5: Runtime block (impede acao)     | Nenhum
Camada 4: DAG mecanico (calcula estado)   | OpenSpec
Camada 3: Hooks automaticos               | Fast Track, SpecKit
Camada 2: Scripts de validacao             | Fast Track, SpecKit, BMAD
Camada 1: Instrucoes no prompt            | Todos
```

**Fast Track e o unico na camada 3 com hooks da plataforma (Claude Code).**
**OpenSpec e o unico na camada 4 com DAG real.**
**Ninguem esta na camada 5.**

---

## O que o Fast Track poderia incorporar

### Do SpecKit
- **Shell scripts nos slash commands** — prerequisite checks que rodam automaticamente
  quando o agente invoca um comando, nao quando ele decide chamar a CLI
- **Constitution** — documento de principios validado em cada fase

### Do BMAD
- **HALT semantics explicitas** — bloqueio que espera input humano em pontos criticos
- **Step-only-knows-next** — cada step so referencia o proximo, nao permite saltos

### Do OpenSpec
- **DAG real para artefatos** — calcular BLOCKED/READY/DONE mecanicamente
  em vez de depender do agente atualizar completed_steps manualmente
- **Delta spec syncing** — mecanismo formal para evolucao de specs

### Combinacao ideal (camada 5 — nao existe hoje)
- DAG do OpenSpec + Hooks do Fast Track + Scripts do SpecKit
- O DAG calcula o estado, os hooks rodam validacao automatica, os scripts bloqueiam
- Faltaria: a plataforma (Claude Code) impedir a acao se o gate falhar

---

## Conclusao

O problema de enforcement para agentes LLM e **nao resolvido** por nenhum framework.
Todos operam no espectro entre "sugestao" e "aviso". Nenhum chega a "bloqueio".

O Fast Track esta na posicao mais avancada em termos de **automacao de enforcement**
(hooks do Claude Code), enquanto o OpenSpec esta na posicao mais avancada em termos
de **modelagem de estado** (DAG real).

A combinacao dos dois — DAG mecanico + hooks automaticos + gate MVP — seria o estado
da arte. Mas o limite final (camada 5: impedir a acao) depende da plataforma, nao do
framework.
