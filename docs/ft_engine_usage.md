# ft engine — Guia de Uso

Motor determinístico de processos para solo dev + AI.
O Python controla o fluxo; o LLM executa apenas tarefas de construção.

---

## Conceito

```
YAML de processo → ft engine → LLM executa → validadores Python → avança
```

O engine lê um processo definido em YAML, executa cada step delegando ao LLM via CLI configurada
(`claude`, `codex`, `gemini` ou `opencode`), valida os artefatos produzidos com verificações determinísticas (Python puro)
e só avança se tudo passar.
O LLM nunca decide sobre o processo — só constrói.

---

## Instalação

```bash
# No projeto Fast Track
pip install -e .

# Verificar
ft --help
```

---

## Comandos

```bash
ft init --template base    # Criar layout versionado, sem estado de execução
ft migrate-layout . --cycle-id cycle-08  # Migrar process/ e atribuir artefatos soltos
ft continue                # Avançar 1 step
ft continue --sprint       # Avançar até fim da sprint atual
ft continue --auto         # Modo autônomo até human gate, MVP ou BLOCK
ft status                  # Status resumido
ft status --full           # Grafo completo agrupado por sprint
ft approve                 # Aprovar artefato pendente
ft reject "motivo"         # Rejeitar e reenviar ao LLM com feedback
ft reject --no-retry "m"   # Rejeitar sem retry (bloqueia)
ft lint-process                   # Lint semântico — detecta especificidades de projeto no YAML
```

### Lint de processo

O `ft lint-process` usa LLM para verificar se o YAML de processo está genérico — sem
referências a projeto específico (nomes de produto, tech stack hardcoded, specs de design).

```bash
ft lint-process                                    # YAML auto-detectado
ft lint-process --process .ft/process/process.yml    # YAML explícito
ft lint-process --gemini                            # usar Gemini como engine
```

Detecta: nomes de produto, cores/dimensões hardcoded, frameworks nos prompts, checklists
específicas. Retorna PASS (genérico) ou FAIL (com lista de violações e sugestões).

### Seleção do executor LLM

Por padrão, o engine usa `claude`. Você pode trocar o executor por comando:

```bash
ft init --codex
ft continue --codex --sprint
ft run ~/dev/projects/examples/pokemon --hipotese ~/dev/projects/examples/pokemon.md --codex
ft run . --opencode
ft run . --opencode anthropic/claude-sonnet-4-5
```

Ou definir o default por ambiente:

```bash
export FT_LLM_ENGINE=opencode
```

Quando `--opencode` é usado sem modelo explícito, o comando chama
`opencode run -m pgx/zai-org_glm-4.7-flash "<prompt>"`.

O default escolhido no `ft init` é persistido em `.ft/manifest.yml`. Durante uma
execução, o executor fica no runtime em `~/.ft/runtime/<projeto>/continuous/` no modo
continuous ou em `~/.ft/worktrees/<projeto>/cycle-NN/state/` no modo isolated.
Assim, `status`, `approve` e `reject` continuam usando o mesmo engine nas execuções
seguintes.

### Opção `--process`

Especificar YAML de processo manualmente:

```bash
ft --process .ft/process/process.yml continue --sprint
```

Sem `--process`, o engine usa exclusivamente `.ft/process/process.yml`. Não existe
fallback automático para nomes ou diretórios antigos; use `ft migrate-layout .`.
O migrador importa históricos de `docs/archive/` e preserva runtime legado fora do
repositório, em `$FT_HOME/migrations/`, sem torná-lo um ciclo ativo.
Também atualiza referências inequívocas nos arquivos atuais. Artefatos já movidos para
`.ft/cycles/` não são reescritos.

### Variáveis de ambiente

| Variável | Efeito |
|----------|--------|
| `FT_HOME` | Redireciona o diretório base de dados do ft (default `~/.ft`). Worktrees externos vivem em `$FT_HOME/worktrees/<projeto>/`. Usado pelos testes para isolamento. |
| `FT_ALLOW_ENGINE_REPO` | O `ft` opera sempre num repo de projeto — **todos** os comandos são bloqueados dentro do repositório do engine/template. Esta variável libera o bloqueio, só para desenvolvimento do engine. |
| `FT_SKIP_HEALTH_CHECK` | Pula o health check da API no início do `ft run`. |
| `FT_LLM_ENGINE` | Engine LLM default (`claude`, `codex`, `gemini`, `opencode`). |
| `FT_LLM_EXECUTOR_TIMEOUT` | Timeout geral de cada turno delegado, em segundos; default 1800. |
| `FT_CODEX_EXECUTOR_TIMEOUT` | Override do timeout de turnos Codex; reasoning `ultra` usa 3600 por default. |
| `FT_CODEX_REASONING_EFFORT` | Override explícito do nível de raciocínio do Codex (por exemplo `ultra`). Sem a variável, o Codex usa seu `config.toml`. |
| `FT_OPENCODE_CONTEXT_LIMIT` / `FT_OPENCODE_CONTEXT_WINDOW` | Sobrescreve a janela de contexto anunciada ao OpenCode para o modelo selecionado. O default de `pgx/zai-org_glm-4.7-flash` é 200000. |
| `FT_OPENCODE_OUTPUT_LIMIT` / `FT_OPENCODE_MAX_OUTPUT` | Sobrescreve o limite de saída anunciado ao OpenCode. O default de `pgx/zai-org_glm-4.7-flash` é 32768. |
| `FT_OPENCODE_PROVIDER_TIMEOUT` / `FT_OPENCODE_TIMEOUT` | Define `provider.options.timeout` no OpenCode, em milissegundos. |
| `FT_OPENCODE_CHUNK_TIMEOUT` / `FT_OPENCODE_PROVIDER_CHUNK_TIMEOUT` | Define `provider.options.chunkTimeout` no OpenCode, em milissegundos, para cortar streams sem novos chunks. |
| `FT_OPENCODE_HEADER_TIMEOUT` / `FT_OPENCODE_PROVIDER_HEADER_TIMEOUT` | Define `provider.options.headerTimeout` no OpenCode, em milissegundos. |
| `FT_OPENCODE_SANDBOX` | Controla o sandbox de filesystem do OpenCode via `bwrap` (default ligado). O worktree fica read-only e só os outputs/write_scope do node são montados como writable. Use `0` para desabilitar. |
| `FT_OPENCODE_DENY_EDIT_TOOLS` | Opt-in: nega ferramentas nativas de edição do OpenCode em nodes de código e força o modo legado de escrita indireta. |
| `FT_OPENCODE_BUNDLE_MODE` | Opt-in: força nodes de código OpenCode a responderem por bundle XML `<ft_file>`, materializado pelo engine. Use para diagnóstico ou nodes pequenos. |
| `FT_OPENCODE_SCRIPT_MODE` | Opt-in: força nodes de código OpenCode a responderem com um script Bash materializado pelo engine. |
| `FT_OPENCODE_DEBUG` | Ativa `opencode run --print-logs --log-level DEBUG`. |
| `FT_OPENCODE_PRINT_LOGS` / `FT_OPENCODE_LOG_LEVEL` / `FT_OPENCODE_THINKING` | Ajustes finos de log do OpenCode sem ativar todo o modo debug. |

### Governança de melhorias do processo

No template `fast-track-v3`, `ft.handoff.05.process_evolve` gera o relatório
humano `docs/process-improvements.md` e o contrato estruturado
`docs/process-improvements.yml`. Todo achado recebe ID `PI-NNN` e uma das
classificações:

- `local`: pertence ao fork `.ft/process/process.yml` daquele projeto;
- `global_candidate`: deve ser revisado para promoção no engine/template;
- `rejected`: foi analisado e não deve ser aplicado.

Uma melhoria só pode ser `global_candidate` quando for independente de domínio,
não contiver identificadores do produto, for configurável, tiver evidência
verificada no ciclo e for retrocompatível. O validator
`process_improvements_classified` bloqueia classificações inconsistentes.

O ciclo nunca escreve no checkout do engine. Após atualizar e testar o global,
o mantenedor registra a disposição no artefato do ciclo:

```bash
ft process-candidates
ft process-candidates PI-001 \
  --status promoted \
  --reason "Aplicado e validado pela suíte do engine" \
  --reference "commit abc123 templates/fast-track-v3/process.yml"

ft process-candidates PI-002 --status deferred --reason "Precisa de outro ciclo real"
ft process-candidates PI-003 --status rejected --reason "Regra específica do produto"
```

`ft close` recusa candidatos `pending`. `--force` permite ignorar a governança
somente de forma explícita. Ao fechar, os dois relatórios são arquivados em
`.ft/cycles/<cycle>/` com a decisão e a referência preservadas.

---

## Formato do YAML de processo

```yaml
id: meu_processo
version: "1.0.0"
title: "Meu Processo"

artifact_policy:
  canonical: [docs/PRD.md, docs/PROJECT_BACKLOG.md, docs/FEATURES.md]
  cycle: [docs/task_list.md, docs/acceptance-report.md, docs/handoff.md]

nodes:
  - id: step.01.discovery
    type: discovery          # discovery | document | build | test_red | test_green
                             # refactor | gate | decision | review | end
    title: "Capturar requisitos"
    executor: llm_coach      # llm_coach | llm_coder | python
    sprint: sprint-01        # opcional — agrupa nodes por sprint
    outputs:
      - docs/requisitos.md
    requires_approval: true  # opcional — pausa para ft approve
    validators:
      - file_exists: docs/requisitos.md
      - min_lines: 20
      - has_sections:
          - Problema
          - Solucao
    next: step.02.prd

  - id: step.02.prd
    type: document
    title: "Escrever PRD"
    executor: llm_coach
    sprint: sprint-01
    outputs:
      - docs/PRD.md
    validators:
      - file_exists: docs/PRD.md
      - min_user_stories: 3
    next: gate.01

  - id: gate.01
    type: gate
    title: "Gate de qualidade"
    executor: python
    sprint: sprint-01
    validators:
      - file_exists: docs/PRD.md
      - tests_pass: true
    next: step.end

  - id: step.end
    type: end
    title: "Processo concluído"
```

---

## Regras de Design de Processo

O YAML de processo é **pura orquestração**. Ele define a sequência de passos, quem executa
cada um (LLM coach, LLM coder, python), e quais validators rodam. Nada mais.

### O que o YAML define

- **Sequência**: quais nodes existem e em que ordem executam
- **Executor**: quem roda cada node (llm_coach, llm_coder, python)
- **Validators**: quais verificações determinísticas rodam após cada node
- **Artifact policy**: quais outputs permanecem canônicos e quais são arquivados por ciclo
- **Hotspots de customização**: referências a arquivos que o LLM deve ler e seguir

### O que o YAML NÃO define

- Design, layout, cores, dimensões, specs visuais
- Requisitos funcionais, user stories, regras de negócio
- Tech stack, frameworks, dependências, linguagens
- Nomes de projeto, domínio, contexto específico

### Onde vive a especificidade do projeto

Toda informação específica do projeto vive nos documentos visíveis em `docs/`. Relatórios
de execução passam por `docs/` durante o run e são arquivados pelo `ft close`:

| Artefato | Conteúdo |
|----------|----------|
| `docs/PRD.md` | O que construir — visão, user stories, requisitos |
| `docs/PROJECT_BACKLOG.md` | Backlog canônico derivado do PRD; ciclos consomem itens daqui |
| `docs/FEATURES.md` | Capacidades entregues; cada `FEAT-*` referencia `PB-*` concluído e evidência |
| `.ft/cycles/<cycle>/task_list.md` | Quebra técnica arquivada ao fechar o ciclo |
| `docs/ui_criteria.md` | Como deve parecer — telas, componentes, estados e evidências |
| `docs/tech_stack.md` | Com que tecnologia — framework, linguagens, dependências |

### Como o YAML referencia especificidades

Os prompts nos nodes referenciam artefatos por caminho, nunca duplicam conteúdo:

```yaml
# ERRADO — polui o YAML com especificidades do projeto
prompt: |
  Implemente o layout VS Code com Activity Bar (40px) + Drawer retrátil.
  Use Svelte + Vite. Cores: fundo #0a0a1a, acento #f0c040.
  Grafo SVG com nodes 180×60px e arestas bezier.

# CERTO — o YAML só orquestra, a LLM lê os artefatos
prompt: |
  Implemente a interface completa do projeto.
  LEIA OBRIGATORIAMENTE:
    - docs/PRD.md              (user stories — todas são obrigatórias)
    - docs/tech_stack.md       (stack decidido)
    - seed/ui_guidelines.md    (especificações visuais completas)
  Siga TODAS as especificações dos artefatos acima.
```

### Hotspots de customização

O processo disponibiliza pontos de flexibilização:

1. **Hooks para scripts**: `env_setup`, `on_init` — executam shell scripts do projeto
2. **Hooks para LLM**: prompts que dizem "leia arquivo X e siga" — a LLM extrai o que precisa
3. **Validators genéricos**: `file_exists`, `has_sections`, `command_succeeds` — verificam estrutura e resultados estruturados, não conteúdo Markdown

### Teste de genericidade

Um processo YAML bem desenhado deve funcionar para **qualquer projeto do mesmo tipo**
trocando apenas os artefatos em `seed/`. Se precisa editar o YAML para mudar de projeto,
a especificidade vazou para o processo.

---

## Tipos de node

| Tipo | Executor | Descrição |
|------|----------|-----------|
| `discovery` | llm_coach | Captura hipótese/contexto; suporta hyper-mode |
| `document` | llm_coach | Produz documento markdown |
| `build` | llm_coder | Implementa código (TDD implícito) |
| `test_red` | llm_coder | TDD red — escreve testes que devem **falhar** |
| `test_green` | llm_coder | TDD green — implementa código para testes passarem |
| `refactor` | llm_coder | Refatora mantendo testes verdes |
| `gate` | python | Validação pura Python — sem LLM |
| `decision` | python | Branch condicional baseado em estado |
| `review` | llm_coder | Sprint Expert Gate — veredicto APPROVED/REJECTED |
| `end` | — | Marca fim do processo |

---

## Validadores disponíveis

### Artefatos
| Validador | Uso | Descrição |
|-----------|-----|-----------|
| `file_exists` | `file_exists: path/to/file.md` | Arquivo existe |
| `min_lines` | `min_lines: 20` | Mínimo de linhas (usa `outputs[0]`) |
| `has_sections` | `has_sections: [A, B, C]` | Seções presentes |
| `min_user_stories` | `min_user_stories: 3` | Mínimo de US no formato `### US-` |
| `demand_coverage` | `demand_coverage: {prd_path: docs/PRD.md, demand_path: docs/demanda.md}` | Cobertura determinística da demanda por keywords normalizadas |
| `project_backlog_valid` | `project_backlog_valid: {path: docs/PROJECT_BACKLOG.md}` | Backlog tem IDs, prioridade e status válidos |
| `task_list_references_backlog` | `task_list_references_backlog: {task_path: docs/task_list.md, backlog_path: docs/PROJECT_BACKLOG.md}` | Task list do ciclo referencia itens do backlog |
| `backlog_pending_decisions` | `backlog_pending_decisions: {path: docs/PROJECT_BACKLOG.md}` | P0/P1 não ficam abertos sem decisão explícita |
| `features_catalog_valid` | `features_catalog_valid: {path: docs/FEATURES.md, backlog_path: docs/PROJECT_BACKLOG.md}` | Catálogo tem schema, IDs, lifecycle, origem entregue e evidência válidos |
| `implemented_backlog_covered_by_features` | `implemented_backlog_covered_by_features: {features_path: docs/FEATURES.md, backlog_path: docs/PROJECT_BACKLOG.md}` | Todo item de feature/US entregue está representado por algum `FEAT-*` |

### Testes
| Validador | Uso | Descrição |
|-----------|-----|-----------|
| `tests_pass` | `tests_pass: true` | pytest passa |
| `tests_fail` | `tests_fail: true` | pytest falha (red phase) |
| `coverage_min` | `coverage_min: 80` | Cobertura global mínima |
| `coverage_per_file` | `coverage_per_file: 85` | Cobertura mínima por arquivo |
| `tests_exist` | `tests_exist: tests/` | Existem arquivos de teste |

### Código
| Validador | Uso | Descrição |
|-----------|-----|-----------|
| `lint_clean` | `lint_clean: true` | ruff check sem erros |
| `format_check` | `format_check: true` | ruff format --check |
| `no_todo_fixme` | `no_todo_fixme: true` | Sem TODO/FIXME |

### Gates compostos
| Validador | Uso | Descrição |
|-----------|-----|-----------|
| `gate_delivery` | `gate_delivery: true` | outputs existem + testes passam |
| `gate_smoke` | `gate_smoke: true` | testes + smoke cmd opcional |
| `gate_mvp` | `gate_mvp: {required_docs: [...], min_coverage: 70}` | docs + testes + cobertura |

### Review
| Validador | Uso | Descrição |
|-----------|-----|-----------|
| `no_large_files` | `no_large_files: 500` | Arquivos < N linhas |
| `no_print_statements` | `no_print_statements: true` | Sem print() em src/ |
| `changed_files_have_tests` | `changed_files_have_tests: true` | Arquivos modificados têm testes |

---

## TDD Loop

Sequência canônica:

```yaml
- id: tdd.red
  type: test_red
  executor: llm_coder
  sprint: sprint-02-tdd
  outputs:
    - tests/test_feature.py
  validators:
    - file_exists: tests/test_feature.py
    - tests_fail: true          # ← DEVE falhar (red)
  next: tdd.green

- id: tdd.green
  type: test_green
  executor: llm_coder
  sprint: sprint-02-tdd
  outputs:
    - src/feature.py
  validators:
    - file_exists: src/feature.py
    - tests_pass: true          # ← DEVE passar (green)
  next: tdd.refactor

- id: tdd.refactor
  type: refactor
  executor: llm_coder
  sprint: sprint-02-tdd
  outputs:
    - src/feature.py
  validators:
    - tests_pass: true
    - lint_clean: true
  next: gate.delivery
```

O engine faz **auto-commit** após PASS em cada fase:
- `red:` → commit dos testes
- `green:` → commit da implementação
- `refactor:` → commit da refatoração

---

## Sprint workflow

```bash
# Rodar sprint por sprint
ft init
ft continue --sprint    # sprint-01-discovery
ft approve              # aprovar artefatos pendentes
ft continue --sprint    # sprint-02-tdd
ft continue --sprint    # sprint-03-quality
...

# Ou modo autônomo
ft continue --auto      # roda até human gate, MVP ou BLOCK
```

O sprint report é gerado automaticamente ao cruzar boundaries de sprint.

---

## Hyper-mode

Quando docs existem em `docs/`, o engine automaticamente enriquece o
prompt com contexto dos documentos existentes (evita repetição, foca em completar).

Ativa automaticamente para nodes de tipo `discovery` e `document`.

---

## Processo Fast Track V3

O template recomendado está em `templates/fast-track-v3/process.yml` e é copiado
para `.ft/process/process.yml` em projetos novos:

```bash
ft init meu-projeto --template fast-track-v3
cd meu-projeto
git init && git add -A && git commit -m "chore: bootstrap fast track"
ft run . --auto
```

O processo V2 continua disponível como template histórico, mas projetos atuais usam
o mesmo path canônico `.ft/process/process.yml` após o `ft init`.

---

## Estrutura de arquivos

```
ft/
  engine/
    graph.py          # DAG parser — YAML → nodes → resolve_next
    state.py          # StateManager — único escritor de engine_state.yml
    runner.py         # StepRunner — loop principal
    delegate.py       # LLM executor via Claude/Codex CLI
    git_ops.py        # auto_commit após PASS
    parallel.py       # ParallelRunner — worktrees + fan-out/fan-in
    stakeholder.py    # hyper-mode, approval/rejection helpers
    validators/
      artifacts.py    # file_exists, min_lines, has_sections, ...
      tests.py        # tests_pass, tests_fail, coverage_*
      code.py         # lint_clean, format_check, ...
      gates.py        # gate_delivery, gate_smoke, gate_mvp
      review.py       # no_large_files, no_print_statements, ...
  cli/
    main.py           # argparse CLI — ft init/continue/status/approve/reject
~/.ft/worktrees/<projeto>/cycle-NN/
  state/
    engine_state.yml  # Estado do ciclo em modo isolated (NUNCA editar manualmente)
<projeto>/
  docs/                # PRD, stack, UI criteria, backlog e catálogo de features visíveis
  .ft/
    manifest.yml
    process/
      process.yml      # Fork local e versionado do processo
      environment.yml
      scripts/
    cycles/
      cycle-NN/        # Task list, evidências, retro e handoff duráveis
templates/
  fast-track-v3/
    process.yml        # Template recomendado
```

---

## Troubleshooting

**`ft: command not found`**
```bash
pip install -e .
# ou: python -m ft.cli.main
```

**BLOCKED após validação**
```bash
ft status    # ver motivo do block
ft retry     # retentar o node atual
```

**Artefato rejeitado pelo stakeholder**
```bash
ft reject "feedback específico"    # reenvia ao LLM com o motivo
ft reject --no-retry "motivo"      # bloqueia sem retry
```

**LLM não encontrado**
O engine usa a CLI selecionada (`claude` por default, ou `codex`, `gemini` e `opencode`
via flag/`FT_LLM_ENGINE`).
Certifique-se de que o binário escolhido está instalado:
```bash
claude --version
codex --version
gemini --version
opencode --version
```
