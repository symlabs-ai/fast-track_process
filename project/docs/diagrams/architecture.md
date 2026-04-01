# Diagramas de Arquitetura — ForgeProcess Fast Track

**Versão:** 1.0
**Data:** 2026-04-01
**Processo:** fast_track_v2 / ft.plan.03.diagrams
**Status:** Pronto para revisão

---

## 1. Visão Geral do Sistema

```mermaid
graph TD
    USER[Solo Dev / Stakeholder]

    subgraph ft_engine["ft engine (Python 3.11+)"]
        CLI[ft CLI\nft/cli/main.py]
        RUNNER[StepRunner\nft/engine/runner.py]
        STATE[StateManager\nft/engine/state.py]
        GRAPH[ProcessGraph\nft/engine/graph.py]
        DELEGATE[Delegate\nft/engine/delegate.py]
        VALIDATORS[Validators\nft/engine/validators/]
        GIT[GitOps\nft/engine/git_ops.py]
        PARALLEL[ParallelRunner\nft/engine/parallel.py]
    end

    subgraph storage["Persistência (YAML / Git)"]
        ENGINE_STATE[project/state/\nengine_state.yml]
        PROCESS_DEF[process/fast_track/\nFAST_TRACK_PROCESS_V2.yml]
        ARTIFACTS[project/docs/\nartefatos produzidos]
    end

    subgraph agents["Agentes Claude (Symbiotas)"]
        MANAGER[ft_manager]
        COACH[ft_coach]
        GATEKEEPER[ft_gatekeeper]
        ACCEPTANCE[ft_acceptance]
        CODER[forge_coder]
    end

    subgraph ai["Claude API (Anthropic SDK)"]
        SONNET[claude-sonnet-4-6\nsteps padrão]
        OPUS[claude-opus-4-6\nSprint Expert Gate]
    end

    USER -->|ft run / ft approve / ft reject| CLI
    CLI --> RUNNER
    RUNNER -->|load/save| STATE
    RUNNER -->|resolve next| GRAPH
    GRAPH -->|lê definição| PROCESS_DEF
    STATE -->|persiste| ENGINE_STATE
    RUNNER -->|delega task| DELEGATE
    DELEGATE -->|spawn agent| MANAGER
    DELEGATE -->|spawn agent| COACH
    DELEGATE -->|spawn agent| ACCEPTANCE
    DELEGATE -->|spawn agent| CODER
    RUNNER -->|review node| GATEKEEPER
    GATEKEEPER -->|usa modelo| OPUS
    MANAGER -->|usa modelo| SONNET
    COACH -->|usa modelo| SONNET
    ACCEPTANCE -->|usa modelo| SONNET
    CODER -->|usa modelo| SONNET
    COACH -->|escreve| ARTIFACTS
    ACCEPTANCE -->|escreve| ARTIFACTS
    CODER -->|escreve src/ tests/| ARTIFACTS
    RUNNER -->|valida artefatos| VALIDATORS
    RUNNER -->|commit automático| GIT
    RUNNER -->|fan-out/fan-in| PARALLEL
```

---

## 2. Fluxo de Processo — 9 Fases / 19 Steps

```mermaid
flowchart TD
    START([Início])

    subgraph mdd["Fase 1: MDD (sprint-01-mdd)"]
        M1[ft.mdd.01.hipotese\ndiscovery / ft_coach]
        M2[ft.mdd.02.prd\ndocument / ft_coach]
        M3{ft.mdd.03.validacao\ngate}
    end

    subgraph planning["Fase 2: Planning (sprint-02-planning)"]
        P1[ft.plan.01.task_list\ndocument / ft_coach]
        P2[ft.plan.02.tech_stack\ndocument / forge_coder]
        P3[ft.plan.03.diagrams\ndocument / forge_coder]
        P4{gate.planning}
    end

    subgraph tdd["Fase 3: TDD (sprint-03-tdd)"]
        T1[ft.tdd.02.red\ntest_red / forge_coder]
        T2[ft.tdd.03.green\ntest_green / forge_coder]
        T3{gate.tdd}
    end

    subgraph delivery["Fase 4: Delivery (sprint-04-delivery)"]
        D1[ft.delivery.01.self_review\nrefactor / forge_coder]
        D2[ft.delivery.02.refactor\nrefactor / forge_coder]
        D3{gate.delivery}
    end

    subgraph smoke["Fase 5: Smoke (sprint-05-smoke)"]
        S1[ft.smoke.01.cli_run\nbuild / forge_coder]
        S2{gate.smoke}
    end

    subgraph e2e["Fase 6: E2E (sprint-06-e2e)"]
        E1[ft.e2e.01.cli_validation\nbuild / forge_coder]
        E2{gate.e2e}
    end

    subgraph feedback["Fase 7: Feedback (sprint-07-feedback)"]
        F1[ft.feedback.01.retro_note\ndocument / ft_coach]
    end

    subgraph audit["Fase 8: Auditoria (sprint-08-audit)"]
        A1[ft.audit.01.forgebase\nbuild / forge_coder]
        A2{gate.audit}
    end

    subgraph handoff["Fase 9: Handoff (sprint-09-handoff)"]
        H1[ft.handoff.01.specs\ndocument / ft_coach]
        H2{gate.mvp}
    end

    END([ft.end — MVP Entregue])

    START --> M1
    M1 -->|requires_approval| M2
    M2 -->|requires_approval| M3
    M3 -->|PASS| P1
    M3 -->|BLOCK| BLOCK1([BLOCKED])
    P1 --> P2
    P2 -->|requires_approval| P3
    P3 --> P4
    P4 -->|PASS| T1
    P4 -->|BLOCK| BLOCK2([BLOCKED])
    T1 --> T2
    T2 --> T3
    T3 -->|PASS| D1
    T3 -->|BLOCK| BLOCK3([BLOCKED])
    D1 --> D2
    D2 --> D3
    D3 -->|PASS| S1
    D3 -->|BLOCK| BLOCK4([BLOCKED])
    S1 --> S2
    S2 -->|PASS| E1
    S2 -->|BLOCK| BLOCK5([BLOCKED])
    E1 --> E2
    E2 -->|PASS| F1
    E2 -->|BLOCK| BLOCK6([BLOCKED])
    F1 --> A1
    A1 --> A2
    A2 -->|PASS| H1
    A2 -->|BLOCK| BLOCK7([BLOCKED])
    H1 --> H2
    H2 -->|PASS| END
    H2 -->|BLOCK| BLOCK8([BLOCKED])
```

---

## 3. Ciclo de Vida de um Node (StepRunner)

```mermaid
stateDiagram-v2
    [*] --> ready : boot / advance

    ready --> delegated : executor=llm → delegate_to_llm()
    ready --> validating : executor=python (gate puro)

    delegated --> validating : LLM retorna output
    delegated --> blocked : LLM reporta BLOCKED

    validating --> awaiting_approval : PASS + requires_approval=true
    validating --> done : PASS + sem aprovação requerida
    validating --> retrying : FAIL + retryable (llm node)
    validating --> blocked : FAIL + não retryável

    retrying --> validating : delegate_with_feedback() → nova validação
    retrying --> blocked : esgotou MAX_RETRIES (3)

    awaiting_approval --> done : ft approve
    awaiting_approval --> retrying : ft reject + reason

    done --> [*] : state.advance() → próximo node

    blocked --> ready : intervenção manual / ft unblock
```

---

## 4. Isolamento de Paths por Agente

```mermaid
graph LR
    subgraph agents["Agentes"]
        COACH[ft_coach]
        GATEKEEPER[ft_gatekeeper]
        ACCEPTANCE[ft_acceptance]
        CODER[forge_coder]
        MANAGER[ft_manager]
    end

    subgraph paths["Paths de Escrita Permitidos"]
        DOCS[project/docs/]
        STATE_FILE[project/state/\nengine_state.yml]
        SRC[ft_engine/ / src/]
        TESTS[tests/]
    end

    COACH -->|escreve| DOCS
    ACCEPTANCE -->|escreve| DOCS
    GATEKEEPER -->|escreve gate_log| STATE_FILE
    MANAGER -->|escreve estado| STATE_FILE
    CODER -->|escreve| SRC
    CODER -->|escreve| TESTS
    CODER -->|escreve| DOCS
```

---

## 5. Estrutura de Pastas

```
fast-track/
├── pyproject.toml              # config: deps, ruff, mypy, pytest
├── ft/                         # motor determinístico
│   ├── cli/
│   │   └── main.py             # CLI: ft init | run | status | approve | reject
│   └── engine/
│       ├── runner.py           # StepRunner — loop principal
│       ├── state.py            # StateManager — leitura/escrita engine_state.yml
│       ├── graph.py            # ProcessGraph — carrega FAST_TRACK_PROCESS_V2.yml
│       ├── delegate.py         # Delegação ao Claude Agent SDK
│       ├── git_ops.py          # Auto-commit após PASS
│       ├── parallel.py         # ParallelRunner — fan-out/fan-in via worktrees
│       ├── stakeholder.py      # Hyper-mode, prompts de rejeição
│       └── validators/
│           ├── artifacts.py    # file_exists, min_lines, has_sections, ...
│           ├── tests.py        # tests_exist, tests_pass, coverage_per_file
│           ├── code.py         # lint_clean, format_check, no_todo_fixme
│           ├── gates.py        # gate_delivery, gate_smoke, gate_mvp
│           └── review.py       # no_large_files, changed_files_have_tests
├── src/                        # código produzido pelo forge_coder
├── tests/
│   ├── unit/                   # testes unitários (mocked)
│   └── e2e/                    # cenários E2E (AC-01 a AC-05)
├── process/
│   └── fast_track/
│       └── FAST_TRACK_PROCESS_V2.yml   # definição do grafo de processo
└── project/
    ├── state/
    │   └── engine_state.yml    # estado persistente (único escritor: ft engine)
    └── docs/                   # artefatos produzidos pelos agentes
        ├── hipotese.md
        ├── PRD.md
        ├── TASK_LIST.md
        ├── tech_stack.md
        ├── diagrams/
        │   └── architecture.md
        └── sessions/           # logs de sessão dos agentes
```

---

## 6. Modelo de Estado (`engine_state.yml`)

```mermaid
classDiagram
    class EngineState {
        +str process_id
        +str version
        +str|None current_node
        +str node_status
        +list completed_nodes
        +str current_cycle
        +str|None current_sprint
        +dict gate_log
        +dict artifacts
        +str|None blocked_reason
        +str|None pending_approval
        +dict metrics
        +dict _lock
    }

    class Metrics {
        +int steps_completed
        +int steps_total
        +int tests_passing
        +float coverage
        +int llm_calls
        +int tokens_used
    }

    class Lock {
        +str owner
        +int pid
        +str timestamp
    }

    EngineState --> Metrics : contém
    EngineState --> Lock : contém (_lock)
```

**Transições de `node_status`:**

| Status | Descrição |
|--------|-----------|
| `ready` | Aguardando execução |
| `delegated` | LLM em execução |
| `validating` | Validadores rodando |
| `awaiting_approval` | Aguarda `ft approve` |
| `blocked` | Gate falhou / LLM bloqueou |
| `done` | Processo completo |

---

## 7. Fluxo de Delegação ao LLM

```mermaid
sequenceDiagram
    participant SR as StepRunner
    participant DL as delegate.py
    participant SDK as Claude Agent SDK
    participant AGENT as Agente (symbiota)
    participant FS as Filesystem

    SR->>DL: delegate_to_llm(task, allowed_paths)
    DL->>SDK: spawn agent com tool access restrito
    SDK->>AGENT: executa task prompt
    AGENT->>FS: escreve artefatos (paths permitidos)
    AGENT->>SDK: retorna resultado
    SDK->>DL: DelegateResult(success, output)
    DL->>SR: retorna resultado
    SR->>SR: run_validators(node)
    alt PASS
        SR->>SR: state.advance()
    else FAIL + retryável
        SR->>DL: delegate_with_feedback(feedback)
        Note over SR,DL: até MAX_RETRIES=3
    else FAIL definitivo
        SR->>SR: state.block(reason)
    end
```

---

## 8. Gate de Qualidade — Critérios por Fase

| Gate | Validators obrigatórios | Resultado |
|------|------------------------|-----------|
| `ft.mdd.03.validacao` | `file_exists` hipotese.md + PRD.md, `min_lines: 30` | PASS / BLOCK |
| `gate.planning` | `file_exists` TASK_LIST + tech_stack + diagrams | PASS / BLOCK |
| `gate.tdd` | `tests_pass: true` | PASS / BLOCK |
| `gate.delivery` | `tests_pass`, `lint_clean`, `format_check` | PASS / BLOCK |
| `gate.smoke` | `file_exists` smoke-report.md, `min_lines: 10` | PASS / BLOCK |
| `gate.e2e` | `tests_pass: true` (tests/e2e/) | PASS / BLOCK |
| `gate.audit` | `file_exists` forgebase-audit.md, `tests_pass`, `lint_clean` | PASS / BLOCK |
| `gate.mvp` | `file_exists` PRD + TASK_LIST + SPEC + CHANGELOG, `tests_pass` | PASS / BLOCK |

**Regra invariante:** Gates `BLOCK` nunca podem ser contornados sem intervenção explícita (`ft unblock`). `ft_gatekeeper` retorna apenas `PASS` ou `BLOCK` — sem estados intermediários (RF-13).

---

## 9. Decisões de Design Relevantes para Arquitetura

| ID | Decisão | Impacto na Arquitetura |
|----|---------|----------------------|
| TD-01 | YAML para estado persistente | `engine_state.yml` é o único source of truth; auditável via git diff |
| TD-03 | SDK Anthropic direto (sem LangChain) | `delegate.py` é thin wrapper; sem abstração de agente intermediária |
| TD-04 | Python puro (sem async) | `StepRunner` é síncrono; `ParallelRunner` usa worktrees + processos |
| TD-05 | `dict` + YAML (sem Pydantic) | `StateManager` serializa/deserializa com `yaml.safe_load` + `yaml.dump` |
