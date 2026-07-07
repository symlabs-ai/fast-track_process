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
ft init                    # Inicializar/resetar estado do processo
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
ft lint-process --process process/MEU_PROCESSO.yml  # YAML explícito
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

O executor selecionado é persistido em `state/engine_state.yml` no modo continuous
ou em `~/.ft/worktrees/<projeto>/cycle-NN/state/engine_state.yml` no modo isolated.
Assim, `status`, `approve` e `reject` continuam usando o mesmo engine nas execuções
seguintes.

### Opção `--process`

Especificar YAML de processo manualmente:

```bash
ft --process process/process.yml continue --sprint
```

Sem `--process`, o engine procura automaticamente (ordem de prioridade):
1. YAML cujo `id` bate com o `process_id` do estado ativo
2. `process/process.yml`
3. `process/FAST_TRACK_PROCESS.yml`
4. Qualquer `process/*.yml` local
5. `process/fast_track/FAST_TRACK_PROCESS_V2.yml` ou `FAST_TRACK_PROCESS.yml` legado

### Variáveis de ambiente

| Variável | Efeito |
|----------|--------|
| `FT_HOME` | Redireciona o diretório base de dados do ft (default `~/.ft`). Worktrees externos vivem em `$FT_HOME/worktrees/<projeto>/`. Usado pelos testes para isolamento. |
| `FT_ALLOW_ENGINE_REPO` | O `ft` opera sempre num repo de projeto — **todos** os comandos são bloqueados dentro do repositório do engine/template. Esta variável libera o bloqueio, só para desenvolvimento do engine. |
| `FT_SKIP_HEALTH_CHECK` | Pula o health check da API no início do `ft run`. |
| `FT_LLM_ENGINE` | Engine LLM default (`claude`, `codex`, `gemini`, `opencode`). |

---

## Formato do YAML de processo

```yaml
id: meu_processo
version: "1.0.0"
title: "Meu Processo"

nodes:
  - id: step.01.discovery
    type: discovery          # discovery | document | build | test_red | test_green
                             # refactor | gate | decision | review | end
    title: "Capturar requisitos"
    executor: llm_coach      # llm_coach | llm_coder | python
    sprint: sprint-01        # opcional — agrupa nodes por sprint
    outputs:
      - project/docs/requisitos.md
    requires_approval: true  # opcional — pausa para ft approve
    validators:
      - file_exists: project/docs/requisitos.md
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
      - project/docs/PRD.md
    validators:
      - file_exists: project/docs/PRD.md
      - min_user_stories: 3
    next: gate.01

  - id: gate.01
    type: gate
    title: "Gate de qualidade"
    executor: python
    sprint: sprint-01
    validators:
      - file_exists: project/docs/PRD.md
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
- **Hotspots de customização**: referências a arquivos que o LLM deve ler e seguir

### O que o YAML NÃO define

- Design, layout, cores, dimensões, specs visuais
- Requisitos funcionais, user stories, regras de negócio
- Tech stack, frameworks, dependências, linguagens
- Nomes de projeto, domínio, contexto específico

### Onde vive a especificidade do projeto

Toda informação específica do projeto vive nos **artefatos seed** (`seed/`):

| Artefato | Conteúdo |
|----------|----------|
| `seed/PRD.md` | O que construir — user stories, requisitos |
| `seed/ui_guidelines.md` | Como deve parecer — layout, cores, dimensões, animações |
| `seed/tech_stack.md` | Com que tecnologia — framework, linguagens, dependências |

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
para `process/process.yml` em projetos novos:

```bash
ft init meu-projeto --template fast-track-v3
cd meu-projeto
git init && git add -A && git commit -m "chore: bootstrap fast track"
ft run . --auto
```

O processo V2 legado continua disponível em `process/fast_track/FAST_TRACK_PROCESS_V2.yml`
para compatibilidade e testes históricos.

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
process/
  process.yml          # Processo local do projeto
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
ft init      # resetar e recomeçar (perde progresso)
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
