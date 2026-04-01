# Fast Track CLI — Documentacao Completa

## O que e

A `ft` CLI e a ferramenta de validacao e orquestracao do processo Fast Track. Ela e **deterministica** — le os YAMLs do processo, schemas JSON e estado do projeto em runtime, e retorna PASS ou BLOCK.

A CLI existe para resolver um problema fundamental: **agentes LLM nao podem ser forcados a seguir regras via prompt**. A CLI + hooks do Claude Code garantem enforcement mecanico — validacoes rodam automaticamente, nao dependem do modelo decidir.

## Arquitetura

```
~/.local/bin/ft                             <- Dispatcher global (no PATH)
~/.local/share/fast-track/process/          <- Engine global (YAML, schemas, tools, prompts)
~/.claude/agents/ft_*/                      <- Agents do Claude Code

projeto/
  .claude/settings.json                     <- Hooks de enforcement (criados pelo ft init)
  project/state/ft_state.yml                <- Estado do projeto (dinamico)
  process/fast_track/tools/ft.py            <- Engine local (se existir)
```

### Dispatcher vs. Engine

- **Dispatcher** (`~/.local/bin/ft`): script global no PATH. Decide se esta dentro ou fora de um projeto. Fora: cria projetos. Dentro: delega para a engine.
- **Engine** (`ft.py`): logica real. Pode estar local (`process/fast_track/tools/ft.py`) ou global (`~/.local/share/fast-track/`). O dispatcher resolve: local > global.

### Resolucao de paths

1. `FT_PROJECT_ROOT` (env var) — se setada, usa como raiz do projeto
2. Sobe a arvore de diretorios procurando `project/state/ft_state.yml`
3. Se nao encontrar, sobe procurando `process/fast_track/FAST_TRACK_PROCESS.yml`
4. Para o `process/`: local no projeto > engine global em `~/.local/share/fast-track/`

### Sincronia automatica

A cada execucao, o dispatcher compara versoes:
- Processo local vs. engine global → avisa se engine esta velha
- Processo vs. agents instalados (`.ft_version`) → avisa se agents estao velhos

Sugestao automatica: `ft update` ou `ft init`.

---

## Comandos — Fora de um projeto

### `ft init <nome> [opcoes]`

Cria um novo projeto a partir do template Fast Track.

```bash
ft init sym_builder
ft init sym_builder --remote git@github.com:user/sym_builder.git
ft init sym_builder --gateway anthropic:sk-sym_abc123
```

**O que faz:**
1. `git clone` do template
2. Atualiza engine global com o `process/` do template
3. Remove o remote do template
4. Conecta ao remote do projeto (se `--remote`)
5. Configura SymGateway (se `--gateway`) — cria `.claude/settings.local.json` com `ANTHROPIC_BASE_URL`
6. Executa `ft init` interno (dirs, scaffold, agents, hooks, .gitignore, versao)
7. Push inicial (se remote configurado)

**Opcoes:**
| Opcao | Descricao |
|-------|-----------|
| `--remote <url>` | Git remote do projeto |
| `--gateway <provider:apikey>` | Rotear pelo SymGateway (formato `provider:apikey`) |

### `ft update`

Atualiza a engine global com a ultima versao do template remoto.

```bash
ft update
```

**O que faz:**
1. `git clone --depth 1` do template em diretorio temporario
2. Copia `process/` para `~/.local/share/fast-track/`
3. Grava `.ft_version` nos agents do Claude Code
4. Reporta versao instalada

---

## Comandos — Dentro de um projeto

### `ft init [--check]`

Inicializa ou valida o projeto.

```bash
ft init            # cria o que falta
ft init --check    # valida sem modificar nada (dry-run)
```

**11 itens verificados/criados:**

| # | Item | O que faz |
|---|------|-----------|
| 1 | Diretorios obrigatorios | Cria `project/`, `src/`, `tests/`, `artifacts/`, etc. |
| 2 | Arquivos obrigatorios | Verifica `FAST_TRACK_PROCESS.yml`, `ft_state.yml` |
| 3 | .gitignore | Adiciona `process/` ao .gitignore do projeto |
| 4 | Scaffold Clean/Hex | Cria `src/domain/`, `src/application/`, `src/infrastructure/`, `src/adapters/` |
| 5 | Git remote | Verifica que nao aponta pro template |
| 6 | Virtualenv | Roda `setup_env.sh` se `.venv` nao existe |
| 7 | Versao | Sincroniza versao do processo com ft_state.yml |
| 8 | Token tracking | Grava snapshot init em `metrics.yml` |
| 9 | Estado do projeto | Verifica fase, next_step |
| 10 | Agents | Instala/sincroniza 5 agents do Claude Code |
| 11 | Hooks | Cria `.claude/settings.json` com enforcement automatico |

**Output:** PASS, PASS (com avisos), ou BLOCK.

**Quem usa:** ft_manager (bootstrap da sessao).

### `ft validate state`

Valida `ft_state.yml` contra o JSON Schema e regras do processo.

```bash
ft validate state
```

**7 verificacoes:**

| # | Verificacao | Detecta |
|---|-------------|---------|
| 1 | JSON Schema | Campos com tipo errado, enums invalidas |
| 2 | Step IDs | IDs inventados que nao existem no FAST_TRACK_PROCESS.yml |
| 3 | Phase/step | next_step incompativel com current_phase |
| 4 | Cobertura | min_coverage > desired_coverage |
| 5 | Versao | Versao do state != versao do processo |
| 6 | Blocked | blocked=true sem blocked_reason |
| 7 | completed_steps | IDs duplicados ou invalidos |

**Quem usa:** ft_manager, ft_gatekeeper. Tambem rodado automaticamente por hook apos editar ft_state.yml e apos git commit.

### `ft validate artifacts`

Verifica que artefatos esperados existem nos paths canonicos.

```bash
ft validate artifacts
```

Cruza `completed_steps` com os `outputs` definidos no `FAST_TRACK_PROCESS.yml`. Se um step foi completado, seus artefatos devem existir.

**Quem usa:** ft_manager (antes do handoff).

### `ft validate integration`

Mock audit, dead code detection, wiring check.

```bash
ft validate integration
```

**4 verificacoes:**

| # | Verificacao | Detecta |
|---|-------------|---------|
| 1 | Mock audit | Ports sem implementacao real em `infrastructure/` |
| 2 | Dead usecases | UseCases nao invocados por nenhum adapter |
| 3 | Loose adapters | Adapters que nao usam nenhum usecase |
| 4 | Interface enforcement | `interface_type != cli_only` sem adapter correspondente |

**Quem usa:** ft_gatekeeper (antes do gate.audit).

### `ft validate gate <id>`

Pre-flight mecanico de um gate especifico.

```bash
ft validate gate smoke
ft validate gate e2e
ft validate gate acceptance
ft validate gate handoff
ft validate gate mvp
```

**Gates disponiveis:**

| Gate | O que verifica |
|------|----------------|
| `smoke` | Todas as tasks tem gate.delivery PASS no gate_log |
| `e2e` | `run-all.sh` existe em `tests/e2e/` |
| `acceptance` | Diretorio de testes de aceitacao existe (quando interface_type != cli_only) |
| `handoff` | SPEC.md, CHANGELOG.md, BACKLOG.md existem |
| `mvp` | **Verificacao completa** — 12 itens (ver abaixo) |

**Gate MVP — 12 verificacoes:**

| # | Item | Detecta |
|---|------|---------|
| 1 | Steps completados | Steps do processo nao marcados em completed_steps |
| 2 | Gate log | Tasks sem gate.delivery executado |
| 3 | PRD.md | Artefato ausente |
| 4 | TASK_LIST.md | Artefato ausente |
| 5 | tech_stack.md | Artefato ausente |
| 6 | SPEC.md | Artefato ausente |
| 7 | CHANGELOG.md | Artefato ausente |
| 8 | BACKLOG.md | Artefato ausente |
| 9 | Smoke reports | Nenhum smoke-*.md |
| 10 | Sprint reports/reviews | Nenhum sprint-report ou sprint-review |
| 11 | Retro note | retro_note.md ausente |
| 12 | Diagramas e testes | Diagramas ausentes, poucos diretorios de teste |

**Quem usa:** ft_gatekeeper. O gate mvp e acionado automaticamente por hook quando `mvp_status: entregue` e escrito no ft_state.yml.

### `ft generate ids`

Gera `FAST_TRACK_IDS.md` a partir do `FAST_TRACK_PROCESS.yml`.

```bash
ft generate ids
```

**Quem usa:** ft_manager (apos mudanca no processo).

### `ft generate check`

Verifica consistencia entre YAML do processo, FAST_TRACK_IDS.md, FAST_TRACK_PROCESS.md e SUMMARY_FOR_AGENTS.md.

```bash
ft generate check
```

Detecta: steps presentes no YAML mas ausentes nos MDs, ou vice-versa.

**Quem usa:** ft_manager (diagnostico de drift).

### `ft tokens <subcomando>`

Proxy para `token_tracker.py`. Token tracking e metricas de consumo.

```bash
ft tokens status
ft tokens snapshot --step ft.mdd.02.prd
ft tokens history
```

**Quem usa:** ft_manager (momentos-chave: init, pos-PRD, pos-sprint, pos-E2E, handoff).

### `ft role <symbiota_id> [--check-step <step>]`

Mostra permissoes e escopo de um symbiota.

```bash
ft role ft_gatekeeper              # mostra PODE / NAO PODE
ft role forge_coder --check-step ft.mdd.02.prd   # BLOCK
ft role forge_coder --check-step ft.tdd.02.red    # PASS
```

**Symbiotas disponiveis:** ft_manager, ft_gatekeeper, ft_acceptance, ft_coach, forge_coder.

**Output de `ft role <id>`:**
- PODE: lista de acoes permitidas
- NAO PODE: lista de acoes proibidas
- STEPS: steps que o symbiota pode executar
- PHASES: fases de atuacao

**Com `--check-step`:** Retorna PASS ou BLOCK se o symbiota pode executar aquele step.

**Quem usa:** ft_manager (antes de delegar), qualquer agente (auto-verificacao).

### `ft self-check`

Verifica consistencia interna da CLI vs. processo.

```bash
ft self-check
```

**4 verificacoes:**
1. Schema `ft_state.schema.json` existe
2. Processo define N steps (conta)
3. Schema cobre todas as phases do processo
4. `FAST_TRACK_IDS.md` existe

**Quem usa:** ft_manager (diagnostico).

### `ft help [topico]`

Manual completo para agentes. Com topico, detalha um comando.

```bash
ft help              # manual completo
ft help init         # detalha init
ft help validate     # detalha validate
ft help role         # detalha role
```

---

## Hooks de Enforcement

O `ft init` instala hooks no `.claude/settings.json` do projeto. Estes hooks rodam automaticamente — nao dependem do modelo decidir.

| Trigger | Condicao | Acao |
|---------|----------|------|
| PostToolUse (Edit/Write) | Editou `ft_state.yml` | `ft validate state` |
| PostToolUse (Edit/Write) | Escreveu `mvp_status.*entregue` | `ft validate gate mvp` |
| PostBash | `git commit` executado | `ft validate state` |

**Por que hooks?** Instrucoes no prompt sao sugestoes — o modelo pode ignorar. Hooks sao deterministicos: rodam sempre, retornam feedback visivel ao modelo, e bloqueiam se BLOCK.

---

## Relacao com o Processo Fast Track

A CLI e o **braco mecanico** do processo. O processo define regras em YAML/MD; a CLI enforca essas regras deterministicamente.

```
FAST_TRACK_PROCESS.yml (regras)
        |
        v
    ft.py (enforcement)
        |
        v
  hooks (automatizacao)
        |
        v
  agente ve PASS/BLOCK (feedback)
```

### Mapeamento CLI → Processo

| Momento do processo | Comando da CLI | Enforcement |
|---------------------|----------------|-------------|
| Bootstrap da sessao | `ft init --check` | Verifica projeto configurado |
| Atualizacao do ft_state.yml | `ft validate state` | Hook automatico |
| Antes de cada gate | `ft validate gate <id>` | Pre-flight mecanico |
| Antes do gate.audit | `ft validate integration` | Mock audit, dead code |
| Apos cada git commit | `ft validate state` | Hook automatico |
| Antes de declarar MVP | `ft validate gate mvp` | Hook automatico |
| Delegacao entre agentes | `ft role <id> --check-step` | Escopo enforcement |
| Drift de versao | `ft update` / sincronia | Check automatico |

### O que a CLI NAO faz

- Nao executa steps do processo (isso e responsabilidade dos agentes)
- Nao modifica codigo (isso e responsabilidade do forge_coder)
- Nao toma decisoes (isso e responsabilidade do ft_manager)
- Nao interpreta semantica (isso e responsabilidade do ft_gatekeeper na analise pos-pre-flight)

A CLI e **binaria**: PASS ou BLOCK. Sem interpretacao, sem "mais ou menos". Se algo e verificavel mecanicamente, a CLI verifica. Se requer julgamento, o agente decide.

---

## Severidades de Output

| Severidade | Icone | Significado |
|------------|-------|-------------|
| ok | `[ok]` | Item validado com sucesso |
| warn | `[WARN]` | Problema nao bloqueante — pode ser resolvido depois |
| fail | `[FAIL]` | Problema bloqueante — deve ser resolvido antes de prosseguir |

**RESULTADO final:**
- `PASS` — tudo ok
- `PASS (com avisos)` — funciona mas tem pendencias nao criticas, listadas abaixo
- `BLOCK` — nao pode prosseguir, itens bloqueantes listados abaixo
