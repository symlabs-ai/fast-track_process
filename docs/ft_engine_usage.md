# ft engine — Guia de Uso

Motor determinístico de processos para solo dev + AI.
O Python controla o fluxo; o LLM executa apenas tarefas de construção.

---

## Conceito

```
YAML de processo → ft engine → LLM executa → validadores Python → avança
```

O engine lê um processo definido em YAML, executa cada step delegando ao LLM via `claude --print`,
valida os artefatos produzidos com verificações determinísticas (Python puro) e só avança se tudo passar.
O LLM nunca decide sobre o processo — só constrói.

---

## Instalação

```bash
# No projeto Fast Track
pip install -e .

# Verificar
ft-engine --help
```

---

## Comandos

```bash
ft-engine init                    # Inicializar/resetar estado do processo
ft-engine continue                # Avançar 1 step
ft-engine continue --sprint       # Avançar até fim da sprint atual
ft-engine continue --mvp          # Modo autônomo até MVP ou BLOCK
ft-engine status                  # Status resumido
ft-engine status --full           # Grafo completo agrupado por sprint
ft-engine approve                 # Aprovar artefato pendente
ft-engine reject "motivo"         # Rejeitar e reenviar ao LLM com feedback
ft-engine reject --no-retry "m"   # Rejeitar sem retry (bloqueia)
```

### Opção `--process`

Especificar YAML de processo manualmente:

```bash
ft-engine --process process/fast_track/FAST_TRACK_PROCESS_V2.yml continue --sprint
```

Sem `--process`, o engine procura automaticamente (ordem de prioridade):
1. `process/test_process_v2.yml`
2. `process/test_process.yml`
3. `process/fast_track/FAST_TRACK_PROCESS_V2.yml`
4. `process/fast_track/FAST_TRACK_PROCESS.yml`

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
    requires_approval: true  # opcional — pausa para ft-engine approve
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
ft-engine init
ft-engine continue --sprint    # sprint-01-discovery
ft-engine approve              # aprovar artefatos pendentes
ft-engine continue --sprint    # sprint-02-tdd
ft-engine continue --sprint    # sprint-03-quality
...

# Ou modo autônomo
ft-engine continue --mvp       # roda tudo até MVP ou BLOCK
```

O sprint report é gerado automaticamente ao cruzar boundaries de sprint.

---

## Hyper-mode

Quando docs existem em `project/docs/`, o engine automaticamente enriquece o
prompt com contexto dos documentos existentes (evita repetição, foca em completar).

Ativa automaticamente para nodes de tipo `discovery` e `document`.

---

## Processo Fast Track V2

O processo completo está em `process/fast_track/FAST_TRACK_PROCESS_V2.yml`:

```
sprint-01-mdd:       hipotese → PRD → gate
sprint-02-planning:  task_list → tech_stack → diagrams → gate
sprint-03-tdd:       red → green → gate
sprint-04-delivery:  self_review → refactor → gate
sprint-05-smoke:     smoke_run → gate
sprint-06-e2e:       e2e_validation → gate
sprint-07-feedback:  retro
sprint-08-audit:     forgebase_audit → gate
sprint-09-handoff:   SPEC.md → gate_mvp
```

```bash
ft --process process/fast_track/FAST_TRACK_PROCESS_V2.yml init
ft --process process/fast_track/FAST_TRACK_PROCESS_V2.yml continue --sprint
```

---

## Estrutura de arquivos

```
ft/
  engine/
    graph.py          # DAG parser — YAML → nodes → resolve_next
    state.py          # StateManager — único escritor de engine_state.yml
    runner.py         # StepRunner — loop principal
    delegate.py       # LLM executor via claude CLI
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
project/
  state/
    engine_state.yml  # Estado do motor (NUNCA editar manualmente)
process/
  fast_track/
    FAST_TRACK_PROCESS_V2.yml   # Processo completo
  test_process*.yml             # Processos de teste
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
ft-engine status    # ver motivo do block
ft-engine init      # resetar e recomeçar (perde progresso)
```

**Artefato rejeitado pelo stakeholder**
```bash
ft-engine reject "feedback específico"    # reenvia ao LLM com o motivo
ft-engine reject --no-retry "motivo"      # bloqueia sem retry
```

**LLM não encontrado**
O engine usa `claude` CLI. Certifique-se de que está instalado e autenticado:
```bash
claude --version
```
