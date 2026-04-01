# Avaliacao: SymBuilder como Camada 5 de Enforcement

## Contexto

Na analise comparativa (competitor_analysis.md), identificamos 5 camadas de enforcement:

```
Camada 5: Runtime block (impede acao)     → Ninguem
Camada 4: DAG mecanico (calcula estado)   → OpenSpec
Camada 3: Hooks automaticos               → Fast Track, SpecKit
Camada 2: Scripts de validacao            → Fast Track, SpecKit, BMAD
Camada 1: Instrucoes no prompt            → Todos
```

O SymBuilder foi projetado para ser a camada 5.

---

## O que o SymBuilder propoe

Um **runtime engine deterministico** que:
1. Le um processo YAML versionado
2. Resolve o proximo no elegivel (DAG real, nao prompt)
3. Cria tickets automaticamente por step
4. Bloqueia avanço se gate nao passou
5. Atribui executores (humanos ou agents) via ticket
6. Persiste tudo em SQL (audit trail completo)
7. O agente nao controla o fluxo — o motor controla

**Diferenca fundamental:** nos frameworks atuais (Fast Track, SpecKit, BMAD, OpenSpec), o agente
decide o que fazer e o framework tenta guiar via prompt/hooks/DAG. No SymBuilder, o **motor decide
o que fazer** e o agente so executa o ticket que recebeu.

---

## Como resolve cada problema identificado

### Problema 1: Agente ignora a CLI

**Hoje:** ft_manager deveria chamar `ft validate state` mas nao chama.

**SymBuilder:** Nao existe CLI para o agente chamar. O motor executa o processo.
O agente recebe um ticket com escopo definido. Quando termina, submete evidencia.
O motor valida e decide o proximo passo. O agente nao tem opcao de pular.

**Mecanismo:** RF-06 (execucao deterministrica), RF-07 (criacao automatica de tickets),
Invariante 1 (todo node type tem handler), Invariante 2 (todo step gera ticket).

### Problema 2: Agente declara MVP sem completar fases

**Hoje:** ft_manager escreveu `mvp_status: entregue` sem completar fases 5-9.

**SymBuilder:** O agente nao escreve estado. O motor controla o `process_run`.
O run so avanca para o proximo no quando o anterior esta completo.
Gates sao tickets especializados que bloqueiam o ticket pai (7.3.6).
O agente nao consegue marcar o run como completo — so o motor faz isso
quando o no `end` e alcancado no grafo.

**Mecanismo:** Fluxo de execucao (8.2), gate tickets (7.3.6),
Invariante 6 (gate_completed_block mantem pai bloqueado).

### Problema 3: Gate log vazio (53 tasks sem gate.delivery)

**Hoje:** forge_coder implementou tasks mas ninguem registrou gate.delivery.

**SymBuilder:** Gates sao nos no grafo do processo (`validation`, `stakeholder_review`).
O motor cria gate tickets automaticamente quando o fluxo chega nesse no.
O gate ticket tem executor proprio e bloqueia o pai ate avaliacao.
Nao existe opcao de "esquecer" — o grafo exige passagem pelo no.

**Mecanismo:** 8.2 item 3 (validation/stakeholder_review cria gate ticket, bloqueia run),
Side Effect: gate_completed_pass desbloqueia pai e avanca run,
gate_completed_block mantem bloqueado.

### Problema 4: Contexto explode com 70 tasks

**Hoje:** ft_manager acumula contexto de todas as tasks na conversa.

**SymBuilder:** O motor e um servidor (FastAPI + PostgreSQL). Estado vive no banco,
nao no contexto do agente. O agente recebe um ticket por vez (ou por sprint),
executa, submete, e o motor decide o proximo. O historico completo esta no SQL,
nao na conversa.

**Mecanismo:** Separacao runtime (SQL) vs definicao (YAML) em 11.2,
tickets como unidade operacional (principio 2).

### Problema 5: Agente assume papel errado

**Hoje:** forge_coder tentando fazer coisas do ft_manager.

**SymBuilder:** Executores sao tipados: humano, agent, orchestrator (6.1).
Cada ticket tem executor atribuido. Permissoes sao enforced pela API (14.2).
Um agent_executor nao consegue iniciar process run ou editar processo —
a API rejeita.

**Mecanismo:** Tabela de permissoes (14.2), tipos de atores (6.1),
API keys por usuario (RF-19).

### Problema 6: Subagentes morrem e perdem contexto

**Hoje:** forge_coder morre apos execucao, ft_manager perde comunicacao.

**SymBuilder:** Nao depende de subagentes do Claude Code. O motor e um servidor
persistente. O agente se conecta via API/CLI, recebe ticket, executa, submete.
Se o agente morre, o ticket fica `in_progress` ate timeout ou reassign.
O estado nunca se perde — esta no banco.

**Mecanismo:** API Layer (11.1), locking e concorrencia (15), local-first (RNF-07).

---

## Avaliacao critica

### O que esta muito bem resolvido

1. **Motor deterministico real** — nao e prompt, nao e hook, e codigo executando um DAG de processo.
   O grafo de fluxo tem 9 node types com handlers obrigatorios (Invariante 1).

2. **Tickets como unidade operacional** — todo trabalho e rastreavel. O agente nao "faz coisas" —
   ele "resolve tickets". Audit trail completo por design (RNF-03).

3. **Gates mecanicos** — validation e stakeholder_review sao nos no grafo que criam tickets
   e bloqueiam o run. Nao e sugestao — o run nao avanca.

4. **Separacao estado/definicao** — processo em YAML (imutavel apos save), runtime em SQL
   (mutavel pelo motor). O agente nao edita o processo.

5. **Compilador NL → YAML** — processo definido em linguagem natural, compilado para estrutura
   deterministica. Resolve o gap entre intenção humana e execução mecanica.

6. **Paralelismo como node type** — fan-out/fan-in sao nos no grafo, nao decisoes do agente.
   O motor cria worktrees, distribui tickets, faz merge (Invariante 8).

### O que precisa de atencao

1. **Dependencia de servidor** — Fast Track roda local sem servidor. SymBuilder precisa de
   FastAPI + PostgreSQL rodando. Para solo dev, isso e overhead. Precisa de um modo
   embedded/SQLite para desenvolvimento local leve.

2. **Compiler como ponto de falha** — o compilador NL → YAML usa LLM.
   Se o LLM gerar YAML invalido, o sistema rejeita (RF-05). Mas se gerar YAML valido
   mas semanticamente errado (processo que nao faz o que o usuario queria), o motor
   executa fielmente o processo errado. Validacao semantica e humana.

3. **Autonomia do Symbiota** — principio 5 diz "autonomia definida pelo processo".
   Mas o que acontece quando o processo tem gaps? O Symbiota precisa de fallback
   para situacoes nao previstas. Risco 4 do PRD reconhece isso.

4. **Agente ainda pode fazer coisas fora do ticket** — o motor controla o fluxo,
   mas o agente tem acesso ao filesystem. Ele pode editar arquivos que nao estao
   no escopo do ticket. O enforcement e no nivel do processo, nao no nivel do
   sistema operacional. Um agent sandbox (chroot, container) resolveria.

5. **Complexidade de setup** — para usar SymBuilder em um projeto novo, precisa:
   PostgreSQL, FastAPI rodando, processo cadastrado, usuarios criados, agents registrados.
   Versus `ft init` que e um comando. O onboarding precisa ser dramaticamente simplificado.

6. **Falta enforcement de escrita** — o motor impede avanço do processo sem gates,
   mas nao impede o agente de escrever codigo ruim. O gate avalia depois.
   Ideal: o agente submete um PR/diff, o gate valida o diff antes de merge.
   Isso fecharia o loop completamente.

---

## Relacao com o Fast Track atual

O SymBuilder nao substitui o Fast Track — ele o **executa**. O Fast Track e o primeiro
processo nativo do SymBuilder (seção 16 do PRD). A relacao e:

```
Fast Track (processo) → define YAML com phases, steps, gates, flows
SymBuilder (motor)    → executa o YAML deterministicamente
```

O que muda para os agentes:
- **Hoje:** ft_manager le o prompt, decide o que fazer, chama (ou nao) a CLI
- **SymBuilder:** motor cria ticket, agent recebe ticket, executa, submete, motor avalia

O ft_manager deixa de ser um agente LLM orquestrando via prompt e se torna o
**Symbiota** do SymBuilder — um executor privilegiado que o motor chama quando o
processo exige orquestracao.

---

## Conclusao

O SymBuilder e a **unica proposta que realmente resolve o problema de enforcement**.
Nao e enforcement por prompt, hook, ou DAG consultivo. E enforcement por **runtime
deterministico** — o motor controla o fluxo, o agente so executa o que recebeu.

A camada 5 existe na especificacao. Os mecanismos sao solidos:
- Grafo de processo com 9 node types
- Gates como nos bloqueantes
- Tickets como unidade operacional
- SQL como persistencia de estado
- API com permissoes por papel
- Invariantes explicitas (8 regras inviolaveis)

Os riscos sao gerenciaveis: complexidade de setup (resolver com modo embedded),
compilador como ponto de falha (mitigar com validacao rigorosa), e enforcement
no nivel de filesystem (resolver com agent sandbox).

**Se implementado conforme especificado, seria o primeiro framework de camada 5
para desenvolvimento guiado por IA.**
