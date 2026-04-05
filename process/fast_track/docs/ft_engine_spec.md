# ft engine — Especificacao da Engine Deterministica

> Status: Draft v1
> Data: 2026-04-01
> Autor: Symlabs

---

## 1. Visao

Transformar o Fast Track de um **framework de prompts** em um **motor deterministico Python**
que usa LLMs exclusivamente como executores de construcao.

```
ANTES: LLM orquestra → chama CLI (ou nao) → avanca (ou nao)
AGORA: Python orquestra → chama LLM para construir → Python valida → Python avanca
```

O LLM nao decide nada sobre o processo. Nao sabe qual step vem depois. Nao edita estado.
Nao escolhe o que validar. Recebe uma tarefa de construcao, executa, devolve resultado.
O motor Python faz todo o resto.

---

## 2. Principios

1. **Zero decisao de processo no LLM** — toda logica de fluxo, gates, validacao e avanço e Python.
2. **LLM so constroi** — escreve codigo, docs, responde perguntas. Nada mais.
3. **Estado e do motor** — ft_state.yml so e escrito pelo motor. LLM nunca toca.
4. **Validacao antes de avanço** — nenhum step avança sem checagem deterministica.
5. **Offline-first** — estado em YAML/SQLite local, sem servidor.
6. **Processo como codigo** — o YAML do processo e o programa. O motor o executa.

---

## 3. Arquitetura

```
┌─────────────────────────────────────────────────┐
│                ft engine (Python)                │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ Grafo de │  │ State    │  │ Validadores   │ │
│  │ Processo │  │ Manager  │  │ Deterministicos│ │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘ │
│       │              │                │          │
│  ┌────▼──────────────▼────────────────▼───────┐ │
│  │              Step Runner                    │ │
│  │  resolve_next() → delegate() → validate()  │ │
│  │  → advance() → resolve_next() → ...        │ │
│  └────────────────────┬───────────────────────┘ │
│                       │                          │
└───────────────────────┼──────────────────────────┘
                        │
           ┌────────────▼────────────┐
           │    LLM (executor)       │
           │  Claude Code subagent   │
           │  ou API call direto     │
           └─────────────────────────┘
```

### 3.1 Componentes

| Componente | Responsabilidade | Linguagem |
|------------|-----------------|-----------|
| **Grafo de Processo** | Parse do YAML, topological sort, calculo de BLOCKED/READY/DONE | Python |
| **State Manager** | Leitura/escrita do ft_state.yml. Unico escritor. | Python |
| **Validadores** | Checagem deterministica de artefatos, codigo, testes, cobertura | Python |
| **Step Runner** | Loop principal: resolve → delega → valida → avanca | Python |
| **LLM Executor** | Interface para chamar LLM (subagente ou API). So construcao. | Python |
| **Stakeholder IO** | Interface para input humano quando processo exige decisao | Python |

### 3.2 O que e Python (deterministico)

- Ler o grafo do processo e calcular proximo step
- Determinar qual executor usar (LLM, humano, validador)
- Validar artefatos produzidos (existe? tem conteudo? schema valido?)
- Rodar testes e checar cobertura
- Checar lint, types, dead code
- Avaliar gates (mecanico: arquivos existem, testes passam, cobertura ok)
- Avancar estado (completed_steps, next_step, current_phase)
- Registrar gate_log
- Gerar sprint-reports
- Controlar paralelismo

### 3.3 O que e LLM (construcao)

- Escrever codigo (implementar usecases, entities, adapters)
- Escrever testes (unit, smoke, e2e)
- Escrever documentos (PRD, hipotese, tech_stack, specs)
- Responder perguntas do stakeholder (discovery, clarificacao)
- Refatorar codigo existente
- Corrigir codigo que falhou na validacao

### 3.4 O que e humano (decisao)

- Aprovar hipotese
- Revisar PRD
- Escolher tech stack
- Aprovar MVP
- Responder perguntas que o processo nao cobre

---

## 4. Modelo de Estado

### 4.1 ft_state.yml (estado atual — so o motor escreve)

```yaml
version: "1.0.0"
process_id: forgeprocess_fast_track
current_node: ft.mdd.01.hipotese      # no atual no grafo
node_status: ready                      # ready | delegated | validating | done | blocked
completed_nodes: []                     # historico de nos completados
current_cycle: cycle-01
current_sprint: null
sprint_status: null

# Gate log — preenchido pelo motor apos cada validacao
gate_log: {}

# Artefatos produzidos — mapeados pelo motor
artifacts:
  hipotese: null                        # path ou null
  prd: null
  task_list: null
  tech_stack: null
  # ...

# Metricas
metrics:
  steps_completed: 0
  steps_total: 19
  tests_passing: 0
  coverage: 0
  llm_calls: 0
  tokens_used: 0
```

### 4.2 Lock de estado

O ft_state.yml tem um campo `_lock`:

```yaml
_lock:
  owner: ft_engine
  pid: 12345
  timestamp: 2026-04-01T18:30:00Z
```

Se o LLM tentar editar o arquivo, o hook rejeita:
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": "if echo \"$CLAUDE_TOOL_INPUT\" | grep -q 'ft_state.yml'; then echo 'BLOCKED: ft_state.yml is managed by ft engine. Do not edit.' >&2; exit 1; fi"
      }]
    }]
  }
}
```

O hook usa **PreToolUse** (nao PostToolUse) — bloqueia ANTES da escrita.

---

## 5. Grafo de Processo

### 5.1 Node Types

Cada no do grafo tem um tipo que define como o motor o trata:

| Node Type | Executor | Validacao | Exemplo |
|-----------|----------|-----------|---------|
| `build` | LLM | Artefato existe + validador especifico | Implementar usecase |
| `document` | LLM | Artefato existe + conteudo minimo | Escrever PRD |
| `discovery` | LLM + Humano | Artefato existe + aprovacao humana | Hipotese, validacao PRD |
| `test` | LLM + Python | Testes passam + cobertura minima | TDD red/green |
| `gate` | Python | Validador deterministico retorna PASS | gate.delivery, gate.smoke |
| `review` | Humano | Aprovacao explicita | Sprint Expert Gate |
| `decision` | Python | Avalia condicao, escolhe branch | Modo de execucao |
| `sync` | Python | Fan-out/fan-in completo | Paralelismo |
| `end` | Python | Todos os nos anteriores DONE | MVP entregue |

### 5.2 Estrutura do YAML

```yaml
nodes:
  - id: ft.mdd.01.hipotese
    type: discovery
    title: "Hipotese do produto"
    executor: llm_coach
    outputs: [project/docs/hipotese.md]
    requires_approval: true
    validators:
      - file_exists: project/docs/hipotese.md
      - min_lines: 10
    next: ft.mdd.02.prd

  - id: ft.mdd.02.prd
    type: document
    title: "PRD"
    executor: llm_coach
    outputs: [project/docs/PRD.md]
    validators:
      - file_exists: project/docs/PRD.md
      - has_sections: [Hipotese, Visao, User Stories, Requisitos]
      - min_user_stories: 5
    next: ft.mdd.03.validacao

  - id: ft.tdd.02.red
    type: test
    title: "TDD Red — escrever testes que falham"
    executor: llm_coder
    scope: current_task
    validators:
      - tests_exist: tests/unit/
      - tests_fail: true          # Devem FALHAR (red phase)
    next: ft.tdd.03.green

  - id: ft.tdd.03.green
    type: test
    title: "TDD Green — implementar codigo"
    executor: llm_coder
    scope: current_task
    validators:
      - tests_pass: true           # Agora devem PASSAR
      - coverage_min: 85
      - lint_clean: true
      - types_clean: true
    next: ft.delivery.01.self_review

  - id: gate.delivery
    type: gate
    title: "Gate de entrega por task"
    validators:
      - tests_pass: true
      - coverage_min: 85
      - no_dead_code: true
      - no_mock_only_ports: true
    next: next_task_or_sprint_gate

  - id: gate.mvp
    type: gate
    title: "Gate final MVP"
    validators:
      - all_nodes_done: true
      - all_gates_pass: true
      - artifacts_complete: [PRD, TASK_LIST, tech_stack, SPEC, smoke_report, sprint_reports, retro, diagrams]
      - tests_pass: true
      - coverage_min: 85
    next: end
```

### 5.3 Resolucao de Proximo No

```python
def resolve_next(state, graph):
    """Determina o proximo no a executar. Puramente deterministico."""
    current = state["current_node"]
    node = graph.get_node(current)

    if node["type"] == "decision":
        # Avaliar condicao e escolher branch
        return evaluate_decision(node, state)

    if node["type"] == "sync" and node["action"] == "fan_out":
        # Criar branches paralelas
        return create_parallel_branches(node, state)

    # Default: proximo no na sequencia
    return node["next"]
```

---

## 6. Step Runner — Loop Principal

```python
def run(state_path, process_path):
    """Loop principal do motor. Roda ate MVP ou BLOCK."""
    state = load_state(state_path)
    graph = load_graph(process_path)

    while True:
        node_id = state["current_node"]
        node = graph.get_node(node_id)

        if node["type"] == "end":
            print("MVP ENTREGUE")
            break

        # 1. Verificar se o no esta pronto
        if not dependencies_met(node, state):
            print(f"BLOCKED: {node_id} — dependencias nao cumpridas")
            break

        # 2. Delegar ao executor correto
        print(f"Executando: {node_id} ({node['title']})")
        result = delegate(node, state, graph)

        # 3. Validar resultado deterministicamente
        validation = validate(node, state)

        if validation.passed:
            # 4. Avancar estado
            state["completed_nodes"].append(node_id)
            state["current_node"] = resolve_next(state, graph)
            state["gate_log"][node_id] = "PASS"
            save_state(state, state_path)
            print(f"  PASS → proximo: {state['current_node']}")

        elif validation.retryable:
            # 5. Mandar de volta ao LLM com feedback
            print(f"  RETRY: {validation.feedback}")
            result = delegate_with_feedback(node, state, validation.feedback)
            # Re-validar...

        else:
            # 6. BLOCK — precisa de intervencao humana
            state["node_status"] = "blocked"
            state["blocked_reason"] = validation.feedback
            save_state(state, state_path)
            print(f"  BLOCK: {validation.feedback}")
            break
```

### 6.1 Delegate

```python
def delegate(node, state, graph):
    """Delega execucao ao executor correto."""
    executor_type = node["executor"]

    if executor_type == "llm_coder":
        return delegate_to_llm(
            role="forge_coder",
            task=build_coding_prompt(node, state),
            allowed_paths=node.get("allowed_paths", ["src/", "tests/"]),
        )

    elif executor_type == "llm_coach":
        return delegate_to_llm(
            role="ft_coach",
            task=build_document_prompt(node, state),
            allowed_paths=["project/docs/"],
        )

    elif executor_type == "human":
        return prompt_stakeholder(node, state)

    elif executor_type == "python":
        # Validacao/gate — sem LLM
        return run_validator(node, state)
```

### 6.2 Delegate to LLM

```python
def delegate_to_llm(role, task, allowed_paths):
    """Chama o LLM como executor de construcao. Sem decisao de processo."""
    prompt = f"""
Voce e um executor de construcao. Sua unica tarefa:

{task}

REGRAS:
- Escreva APENAS nos paths permitidos: {allowed_paths}
- NAO edite ft_state.yml (o motor gerencia o estado)
- NAO tome decisoes sobre o processo (o motor decide)
- Quando terminar, diga DONE e liste os arquivos criados/modificados
"""
    # Chama Claude Code subagent ou API
    result = invoke_llm(role=role, prompt=prompt)
    return result
```

---

## 7. Validadores Deterministicos

Cada validador e uma funcao Python pura que retorna PASS ou BLOCK.

### 7.1 Catalogo de Validadores

```python
VALIDATORS = {
    # Artefatos
    "file_exists": lambda path: Path(path).exists(),
    "min_lines": lambda path, n: len(Path(path).read_text().splitlines()) >= n,
    "has_sections": lambda path, sections: all(s in Path(path).read_text() for s in sections),
    "min_user_stories": lambda path, n: count_pattern(path, r"### US-\d+") >= n,

    # Testes
    "tests_exist": lambda dir: any(Path(dir).rglob("test_*.py")),
    "tests_pass": lambda: run_pytest() == 0,
    "tests_fail": lambda: run_pytest() != 0,  # Para TDD red phase
    "coverage_min": lambda min_pct: get_coverage() >= min_pct,

    # Codigo
    "lint_clean": lambda: run_ruff() == 0,
    "types_clean": lambda: run_mypy() == 0,
    "no_dead_code": lambda: check_dead_code() == 0,
    "no_mock_only_ports": lambda: check_mock_audit() == 0,

    # Gates compostos
    "all_nodes_done": lambda state, graph: set(graph.all_node_ids()) <= set(state["completed_nodes"]),
    "all_gates_pass": lambda state: all(v == "PASS" for v in state["gate_log"].values()),
    "artifacts_complete": lambda names, state: all(state["artifacts"].get(n) for n in names),
}
```

### 7.2 Execucao de Validadores

```python
def validate(node, state):
    """Roda todos os validadores do no. Retorna ValidationResult."""
    results = []
    for validator_spec in node.get("validators", []):
        name, args = parse_validator(validator_spec)
        fn = VALIDATORS[name]
        passed = fn(*args) if args else fn()
        results.append(ValidationItem(name=name, passed=passed))

    all_passed = all(r.passed for r in results)
    retryable = not all_passed and node["executor"].startswith("llm")
    feedback = format_failures(results) if not all_passed else None

    return ValidationResult(
        passed=all_passed,
        retryable=retryable,
        feedback=feedback,
        items=results,
    )
```

---

## 8. Interface CLI

### 8.1 Comandos

```bash
# Criar projeto
ft init <nome> [--gateway provider:apikey] [--remote url]

# Executar o processo — loop principal
ft-engine continue              # avanca ate o proximo BLOCK ou fim
ft-engine continue --step       # avanca exatamente 1 step
ft-engine continue --sprint     # avanca ate o fim da sprint atual
ft-engine continue --mvp        # avanca ate o MVP (modo autonomo)

# Consultar estado
ft-engine status                # estado atual: no, fase, progresso
ft-engine status --full         # estado detalhado com artefatos e gates
ft-engine graph                 # mostra o grafo com BLOCKED/READY/DONE

# Validacao manual
ft validate state               # valida ft_state.yml
ft validate gate <id>           # roda gate especifico
ft validate all                 # roda todos os validadores do no atual

# Interacao com stakeholder
ft-engine approve               # aprovar artefato pendente de aprovacao
ft-engine reject --reason "..." # rejeitar com motivo
ft-engine answer "..."          # responder pergunta do processo

# Operacional
ft update                       # atualizar engine
ft help                         # manual
ft role <id>                    # permissoes de um executor
```

### 8.2 Fluxo Tipico

```bash
$ ft init sym_builder --gateway anthropic:sk-sym_abc123
  Projeto criado. Processo: Fast Track v1.0
  Estado: ft.mdd.01.hipotese (READY)

$ ft-engine continue
  [ft.mdd.01.hipotese] Delegando ao LLM (ft_coach)...
  → Hipotese gerada: project/docs/hipotese.md
  → Validacao: file_exists PASS, min_lines PASS
  → AGUARDANDO APROVACAO do stakeholder
  Rode: ft-engine approve (ou ft-engine reject --reason "...")

$ ft-engine approve
  [ft.mdd.01.hipotese] PASS → avancando
  [ft.mdd.02.prd] Delegando ao LLM (ft_coach)...
  → PRD gerado: project/docs/PRD.md
  → Validacao: file_exists PASS, has_sections PASS, min_user_stories PASS (8 encontradas)
  → AGUARDANDO APROVACAO do stakeholder

$ ft-engine approve
  [ft.mdd.02.prd] PASS → avancando
  [ft.mdd.03.validacao] Validando PRD...
  → Validacao automatica: PASS
  [ft.plan.01.task_list] Delegando ao LLM (ft_coach)...
  ...

$ ft-engine continue --sprint
  [sprint-01] Delegando 8 tasks ao LLM (forge_coder)...
  → T-01: PASS (testes: 12, cobertura: 92%)
  → T-02: PASS (testes: 28, cobertura: 88%)
  → T-03: RETRY — coverage 78% < 85%. Reenviando com feedback...
  → T-03: PASS (testes: 35, cobertura: 86%)
  → ...
  → Sprint-01: 8/8 tasks PASS
  → Gate sprint: PASS
  → Avancando para sprint-02...

$ ft-engine continue --mvp
  [sprint-02] ... [sprint-08] ...
  [gate.smoke] Rodando smoke test... PASS
  [gate.e2e] Rodando E2E... PASS
  [gate.mvp] Validando MVP...
    all_nodes_done: PASS (19/19)
    all_gates_pass: PASS
    artifacts_complete: PASS (12/12)
    tests_pass: PASS (1013 testes)
    coverage_min: PASS (91%)
  MVP ENTREGUE
```

---

## 9. Retry com Feedback

Quando o LLM produz algo que nao passa na validacao, o motor reenvia com feedback
deterministico (nao com "tente de novo" generico):

```python
def delegate_with_feedback(node, state, feedback):
    """Re-delega com feedback especifico dos validadores."""
    task = build_coding_prompt(node, state)
    retry_prompt = f"""
TAREFA ORIGINAL:
{task}

RESULTADO DA VALIDACAO (FALHOU):
{feedback}

CORRIJA especificamente os itens que falharam.
Nao modifique o que ja esta funcionando.
"""
    return invoke_llm(role=node["executor"], prompt=retry_prompt)
```

**Limite de retries:** configuravel por node type (default: 3).
Apos limite, o no vai para BLOCKED e o motor para, pedindo intervencao humana.

---

## 10. O que muda vs. Fast Track atual

| Aspecto | Fast Track atual | ft engine |
|---------|-----------------|-----------|
| Quem orquestra | LLM (ft_manager) | Python (step runner) |
| Quem decide proximo step | LLM le o prompt | Python le o grafo |
| Quem escreve ft_state.yml | LLM (com hook de validacao) | So o motor Python |
| Quem roda gates | LLM deveria chamar CLI (mas nao chama) | Motor roda automaticamente |
| Quem valida artefatos | LLM deveria chamar CLI | Motor roda validadores Python |
| Papel do LLM | Orquestrador + executor | So executor de construcao |
| Papel do humano | Stakeholder passivo | Stakeholder com approve/reject |
| Enforcement | Hooks (camada 3) | Runtime deterministico (camada 5) |
| Estado | YAML editavel por qualquer um | YAML com lock, so motor escreve |

---

## 11. Implementacao Incremental

### Fase 1: Motor basico (Continue Loop)
- Parse do YAML do processo como grafo
- State manager com lock
- Step runner com resolve_next + delegate + validate
- Validadores: file_exists, min_lines, tests_pass, coverage_min
- LLM executor via Claude Code subagent
- Comandos: `ft-engine continue`, `ft-engine status`, `ft-engine approve`

### Fase 2: Gates e Sprints
- Gate validators compostos (gate.delivery, gate.smoke, gate.mvp)
- Sprint scoping (agrupar tasks, delegar sprint inteira)
- Retry com feedback
- Comando: `ft-engine continue --sprint`

### Fase 3: TDD Loop
- Red/green validation (tests_fail → tests_pass)
- Lint, types, dead code validators
- Coverage enforcement por arquivo
- Self-review checklist automatico

### Fase 4: Paralelismo
- Fan-out: criar branches paralelas, delegar tasks independentes
- Fan-in: merge, resolucao de conflitos
- Slot management

### Fase 5: Stakeholder Intelligence
- Discovery interativo (hipotese, PRD)
- Approval workflow (approve/reject)
- Hyper-mode (absorver docs existentes)

---

## 12. Estrutura de Arquivos do Motor

```
ft/
  engine/
    __init__.py
    graph.py              # Parse YAML → DAG, topological sort
    state.py              # State manager com lock
    runner.py             # Step runner (loop principal)
    delegate.py           # LLM executor interface
    validators/
      __init__.py
      artifacts.py        # file_exists, min_lines, has_sections
      tests.py            # tests_pass, tests_fail, coverage_min
      code.py             # lint_clean, types_clean, dead_code
      gates.py            # gate.delivery, gate.smoke, gate.mvp
    stakeholder.py        # Input humano (approve, reject)
  cli/
    __init__.py
    main.py               # Argparse → comandos do ft-engine
```

---

## 13. Riscos e Mitigacoes

| Risco | Mitigacao |
|-------|----------|
| LLM produz codigo que passa nos testes mas esta errado semanticamente | Sprint Expert Gate com humano (type=review). Motor nao substitui julgamento humano. |
| Validadores falham em edge cases | Validadores sao Python puro — testaveis, debugaveis, evoluiveis |
| LLM nao consegue resolver apos N retries | Motor para com BLOCKED, pede intervencao humana |
| Performance — subagente demora | Paralelismo (fase 4) + sprint-level delegation |
| Lock de estado impede debug | `ft-engine status --full` mostra tudo; `ft validate` roda manualmente |
| Processo YAML fica complexo | Manter expressividade minima. Compilador NL→YAML no futuro. |
