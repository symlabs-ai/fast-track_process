# SPEC — ForgeProcess Fast Track ft engine

**Versão:** 1.0
**Data:** 2026-04-01
**Processo:** fast_track_v2 / ft.handoff.01.specs
**Ciclo:** cycle-01
**Status:** Pronto para revisão

---

## 1. Visão Geral

O **ft engine** é um motor determinístico de orquestração de processo para solo dev + AI. Ele executa um DAG de processo definido em YAML, delega nodes a agentes Claude, aplica gates de qualidade binários (PASS/BLOCK) e persiste estado auditável via `engine_state.yml`.

**Princípios-chave:**
- Python controla todo o fluxo de processo; o LLM é restrito a tarefas de construção
- Estado persistido em YAML auditável via `git diff`
- Gates incontornáveis: `BLOCK` nunca avança sem resolução explícita
- Isolamento de paths por agente — nenhum agente edita fora de seu escopo

**Repositório:** `fast-track/`
**Processo ativo:** `process/fast_track/FAST_TRACK_PROCESS_V2.yml`
**Estado ativo:** `project/state/engine_state.yml`

---

## 2. Interface CLI

### 2.1 Invocação

```bash
python -m ft.cli.main [--process PROCESS] <subcommand> [options]
```

`--process` / `-p`: path para YAML de processo (opcional — se omitido, o motor detecta automaticamente em ordem de prioridade: `test_process_v2.yml` > `test_process.yml` > `FAST_TRACK_PROCESS_V2.yml` > `FAST_TRACK_PROCESS.yml`).

### 2.2 Subcomandos

| Subcomando | Descrição |
|------------|-----------|
| `init` | Inicializa ou reseta o estado do processo (`engine_state.yml`) |
| `continue` | Avança no processo (step-by-step por padrão) |
| `continue --sprint` | Avança até a fronteira da sprint atual |
| `continue --mvp` | Avança até o node `ft.end` (MVP completo) |
| `status` | Exibe node atual, sprint e progresso (`N/total`) |
| `status --full` | Exibe grafo completo com status visual por node |
| `approve` | Aprova artefato pendente (node em `awaiting_approval`) |
| `reject [--reason TEXT]` | Rejeita artefato pendente com feedback opcional |
| `graph` | Exibe representação textual do grafo de processo |

### 2.3 Exemplos

```bash
# Inicializar processo
python -m ft.cli.main init

# Avançar um step
python -m ft.cli.main continue

# Ver estado atual com grafo
python -m ft.cli.main status --full

# Aprovar artefato (quando node requer aprovação do stakeholder)
python -m ft.cli.main approve

# Rejeitar com feedback
python -m ft.cli.main reject --reason "PRD incompleto — faltam critérios de aceitação"

# Usar processo alternativo
python -m ft.cli.main -p process/my_process.yml continue
```

---

## 3. Schema do `engine_state.yml`

### 3.1 Estrutura completa

```yaml
process_id: fast_track_v2           # identificador do processo
version: "0.7.0"                     # versão do processo YAML

current_node: ft.smoke.01.cli_run    # ID do node em execução
node_status: delegated               # status do node (ver seção 3.2)
blocked_reason: null                 # preenchido quando node_status = blocked
pending_approval: null               # ID do node aguardando aprovação

current_cycle: cycle-01              # ciclo de desenvolvimento ativo
current_sprint: sprint-05-smoke      # sprint ativa

completed_nodes:                     # lista de nodes concluídos (em ordem)
  - ft.mdd.01.hipotese
  - ft.mdd.02.prd
  - ...

gate_log:                            # histórico de decisões de gates
  ft.mdd.03.validacao:
    status: PASS
    timestamp: "2026-04-01T10:00:00"
    detail: "file_exists: hipotese.md | file_exists: PRD.md"
  ...

artifacts:                           # mapa de artefatos produzidos
  hipotese: project/docs/hipotese.md
  prd: project/docs/PRD.md
  task_list: project/docs/TASK_LIST.md

metrics:
  steps_completed: 13                # nodes concluídos
  steps_total: 22                    # total de nodes no processo
  tests_passing: 0                   # contador de testes (requer conexão com coverage.xml)
  coverage: 0.0                      # cobertura (requer conexão com coverage.xml)
  llm_calls: 0                       # chamadas ao Claude SDK
  tokens_used: 0                     # tokens consumidos

_lock:                               # lock anti-concorrência
  owner: ft-engine
  pid: 12345
  timestamp: "2026-04-01T10:00:00"
```

### 3.2 Valores de `node_status`

| Status | Descrição | Transição possível |
|--------|-----------|-------------------|
| `ready` | Aguardando execução | → `delegated`, `validating` |
| `delegated` | LLM em execução | → `validating`, `blocked` |
| `validating` | Validators rodando | → `awaiting_approval`, `done`, `retrying`, `blocked` |
| `retrying` | Retry com feedback ao LLM | → `validating` (até MAX_RETRIES=3), `blocked` |
| `awaiting_approval` | Aguarda `ft approve` do stakeholder | → `done`, `retrying` |
| `blocked` | Gate falhou ou LLM bloqueou | → `ready` (via `ft unblock` ou intervenção manual) |
| `done` | Node concluído — processo avança | — |

### 3.3 Regras de escrita do estado

- **Único escritor do estado completo:** `ft_manager` (via `StepRunner`)
- **Escritor de `gate_log`:** `ft_gatekeeper` (apenas append)
- **Agentes LLM nunca escrevem diretamente no `engine_state.yml`** — o hook `PreToolUse` bloqueia edições diretas

---

## 4. Tipos de Node

Cada node no YAML de processo tem um `type` que determina como o `StepRunner` o executa:

| Tipo | Executor | Descrição |
|------|----------|-----------|
| `document` | LLM (ft_coach / forge_coder) | Produz um documento em `project/docs/` |
| `build` | LLM (forge_coder) | Implementa código em `ft/`, `src/` ou `tests/` |
| `test_red` | LLM (forge_coder) | Fase RED do TDD — escreve testes que devem falhar |
| `test_green` | LLM (forge_coder) | Fase GREEN do TDD — implementa para fazer testes passar |
| `refactor` | LLM (forge_coder) | Fase REFACTOR do TDD — melhora qualidade sem quebrar testes |
| `gate` | Python puro | Executa validators determinísticos (sem LLM) |
| `review` | LLM (ft_gatekeeper via `claude-opus-4-6`) | Sprint Expert Gate — análise profunda antes de avançar sprint |
| `discovery` | LLM (ft_coach) | Coleta de informações do stakeholder (hipótese, PRD) |

### 4.1 Exemplo de node no YAML de processo

```yaml
nodes:
  - id: ft.plan.01.task_list
    type: document
    sprint: sprint-02-planning
    agent: ft_coach
    requires_approval: false
    outputs:
      - project/docs/TASK_LIST.md
    validators:
      - type: file_exists
        path: project/docs/TASK_LIST.md
      - type: min_lines
        path: project/docs/TASK_LIST.md
        n: 20
    prompt: |
      Produza a task list do projeto em project/docs/TASK_LIST.md.
      ...
```

### 4.2 Como adicionar um novo node

1. **Editar o YAML de processo** (`process/fast_track/FAST_TRACK_PROCESS_V2.yml`):
   - Atribuir um `id` único no formato `<fase>.<tipo>.<ordem>.<nome>` (ex: `ft.plan.04.glossary`)
   - Definir `type`, `sprint`, `agent`, `outputs` e `validators`
   - Inserir na posição correta no grafo (respeitar dependências)

2. **Definir validators** — usar validators existentes ou implementar novo em `ft/engine/validators/`:
   ```yaml
   validators:
     - type: file_exists
       path: project/docs/glossary.md
     - type: min_lines
       path: project/docs/glossary.md
       n: 10
   ```

3. **Atualizar `steps_total`** no `engine_state.yml` após `ft init`

4. **Regra:** Novos nodes devem ser propostos no YAML _antes_ de serem implementados — nunca o contrário (lição aprendida no cycle-01, TD-03 da retro)

---

## 5. Validators

Todos os validators retornam `(passed: bool, detail: str)`. O detalhe é registrado no `gate_log`.

### 5.1 Validators de Artefatos (`ft/engine/validators/artifacts.py`)

| Validator | Parâmetros | Descrição |
|-----------|-----------|-----------|
| `file_exists(path, project_root)` | `path: str` | Verifica se arquivo existe |
| `min_lines(path, n, project_root)` | `path: str`, `n: int` | Verifica se arquivo tem ≥ N linhas |
| `has_sections(path, sections, project_root)` | `path: str`, `sections: list[str]` | Verifica presença de seções (case-insensitive) |
| `min_user_stories(path, n, project_root)` | `path: str`, `n: int` | Conta user stories no formato `### US-` |
| `tests_pass(project_root)` | — | Executa `pytest` e verifica retorno 0 |
| `tests_fail(project_root)` | — | Verifica que `pytest` _falha_ (fase RED do TDD) |
| `coverage_min(min_pct, project_root)` | `min_pct: int` | Executa `pytest --cov` e verifica threshold |

### 5.2 Validators de Código (`ft/engine/validators/code.py`)

| Validator | Parâmetros | Descrição |
|-----------|-----------|-----------|
| `lint_clean(paths, project_root)` | `paths: list[str] \| None` | Executa `ruff check` (default: `src/`) |
| `format_check(paths, project_root)` | `paths: list[str] \| None` | Executa `ruff format --check` |
| `no_todo_fixme(paths, project_root)` | `paths: list[str] \| None` | Verifica ausência de `TODO/FIXME/HACK/XXX` |

### 5.3 Gate Validators Compostos (`ft/engine/validators/gates.py`)

| Gate | Verificações Agregadas |
|------|----------------------|
| `gate_delivery(outputs, project_root)` | `file_exists` para cada output + `tests_pass` |
| `gate_smoke(project_root, smoke_cmd)` | `tests_pass` + comando smoke opcional |
| `gate_mvp(required_docs, min_coverage, project_root)` | `file_exists` docs + `tests_pass` + `coverage_min` |
| `gate_tdd_sequence(tdd_log, project_root)` | Sequência red→green confirmada no log |
| `gate_coverage_80(project_root)` | `coverage_min(80)` |
| `gate_e2e_all_pass(scenarios, project_root)` | Todos os cenários marcados como `passed: true` |

### 5.4 Gate de Qualidade por Fase

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

**Regra invariante:** Gates `BLOCK` nunca podem ser contornados sem intervenção explícita. `ft_gatekeeper` retorna apenas `PASS` ou `BLOCK` — sem estados intermediários (RF-13).

---

## 6. Agentes (Symbiotas)

### 6.1 ft_manager

| Atributo | Valor |
|----------|-------|
| **Papel** | Orquestrador do processo — ponto de entrada de toda sessão |
| **Modelo** | `claude-sonnet-4-6` |
| **Responsabilidade** | Avançar nodes, delegar sprints, interagir com stakeholder |
| **Paths de escrita** | `project/state/engine_state.yml` |
| **Ferramentas** | Read, Grep, Glob, Write, Edit, Bash, Agent |
| **Restrições** | Único responsável por chamar `state.advance()` (RF-12) |

### 6.2 ft_coach

| Atributo | Valor |
|----------|-------|
| **Papel** | Conduz MDD (hipótese, PRD), planning (task list) e feedback (retro, handoff) |
| **Modelo** | `claude-sonnet-4-6` |
| **Responsabilidade** | Produzir documentos de processo em `project/docs/` |
| **Paths de escrita** | `project/docs/` |
| **Ferramentas** | Read, Grep, Glob, Write, Edit |
| **Restrições** | Não edita código (`ft/`, `src/`, `tests/`) |

### 6.3 ft_gatekeeper

| Atributo | Valor |
|----------|-------|
| **Papel** | Validador determinístico de stage gates |
| **Modelo** | `claude-opus-4-6` (Sprint Expert Gate) |
| **Responsabilidade** | Lê artefatos, verifica condições binárias, retorna PASS ou BLOCK |
| **Paths de escrita** | `project/state/engine_state.yml` (apenas campo `gate_log`) |
| **Ferramentas** | Read, Grep, Glob, Bash |
| **Restrições** | Retorna apenas `PASS` ou `BLOCK` — sem estados intermediários |

### 6.4 ft_acceptance

| Atributo | Valor |
|----------|-------|
| **Papel** | Especialista em design de cenários de teste de aceitação |
| **Modelo** | `claude-sonnet-4-6` |
| **Responsabilidade** | Gerar matriz de cenários (happy/edge/error) por Value/Support Track |
| **Paths de escrita** | `project/docs/` |
| **Ferramentas** | Read, Grep, Glob |
| **Restrições** | Não implementa testes — apenas projeta cenários para o forge_coder |

### 6.5 forge_coder

| Atributo | Valor |
|----------|-------|
| **Papel** | Implementa TDD (red-green-refactor), delivery, smoke, E2E e acceptance tests |
| **Modelo** | `claude-sonnet-4-6` |
| **Responsabilidade** | Código em `ft/`, `src/`, `tests/`; documentos técnicos em `project/docs/` |
| **Paths de escrita** | `ft/engine/`, `src/`, `tests/`, `project/docs/` |
| **Ferramentas** | Read, Grep, Glob, Write, Edit, Bash |
| **Restrições** | Não edita `project/state/engine_state.yml` diretamente |

### 6.6 Isolamento de paths (resumo)

```
ft_coach       → project/docs/
ft_acceptance  → project/docs/
ft_gatekeeper  → project/state/engine_state.yml (gate_log apenas)
ft_manager     → project/state/engine_state.yml
forge_coder    → ft/engine/, src/, tests/, project/docs/
```

O hook `PreToolUse` no Claude Code bloqueia qualquer tentativa de agente editar `engine_state.yml` diretamente fora do escopo permitido.

---

## 7. Módulos do Motor (`ft/engine/`)

| Módulo | Classe / Função principal | Responsabilidade |
|--------|--------------------------|-----------------|
| `runner.py` | `StepRunner` | Loop principal: boot → dispatch → advance |
| `state.py` | `StateManager` | Leitura/escrita de `engine_state.yml`; lock anti-concorrência |
| `graph.py` | `ProcessGraph` | Parser YAML → DAG; resolve próximo node |
| `delegate.py` | `delegate_to_llm()`, `delegate_with_feedback()` | Interface com Claude Agent SDK; retry com feedback |
| `git_ops.py` | `auto_commit()` | Auto-commit após PASS em nodes de build/test/refactor |
| `parallel.py` | `ParallelRunner` | Fan-out/fan-in via git worktrees para grupos paralelos |
| `stakeholder.py` | `StakeholderInterface` | Hyper-mode, prompts de aprovação/rejeição |
| `gatekeeper.py` | `GatekeeperRunner` | Executa Sprint Expert Gate com `claude-opus-4-6` |
| `tdd_cycle.py` | `TDDCycle` | Gerencia ciclo red→green→refactor e log de sequência |
| `acceptance.py` | `AcceptanceRunner` | Interface com ft_acceptance para design de cenários |
| `metrics.py` | `MetricsCollector` | Coleta métricas de cobertura e testes |
| `session_tracker.py` | `SessionTracker` | Persiste logs de sessão em `project/docs/sessions/` |
| `agent_policy.py` | `AgentPolicy` | Verifica paths permitidos por agente |
| `cycle_manager.py` | `CycleManager` | Suporte a múltiplos ciclos (cycle-01, cycle-02, ...) |
| `process_registry.py` | `ProcessRegistry` | Catálogo de processos disponíveis e versões |
| `validators/` | (ver seção 5) | Validators determinísticos |

---

## 8. Processo Fast Track V2 — Nodes e Sprints

### 8.1 Mapa completo (22 nodes, 9 sprints)

| Sprint | Node ID | Tipo | Agente | Requires Approval |
|--------|---------|------|--------|-------------------|
| sprint-01-mdd | `ft.mdd.01.hipotese` | discovery | ft_coach | ✅ |
| sprint-01-mdd | `ft.mdd.02.prd` | document | ft_coach | ✅ |
| sprint-01-mdd | `ft.mdd.03.validacao` | gate | ft_gatekeeper | — |
| sprint-02-planning | `ft.plan.01.task_list` | document | ft_coach | — |
| sprint-02-planning | `ft.plan.02.tech_stack` | document | forge_coder | ✅ |
| sprint-02-planning | `ft.plan.03.diagrams` | document | forge_coder | — |
| sprint-02-planning | `gate.planning` | gate | ft_gatekeeper | — |
| sprint-03-tdd | `ft.tdd.02.red` | test_red | forge_coder | — |
| sprint-03-tdd | `ft.tdd.03.green` | test_green | forge_coder | — |
| sprint-03-tdd | `gate.tdd` | gate | ft_gatekeeper | — |
| sprint-04-delivery | `ft.delivery.01.self_review` | refactor | forge_coder | — |
| sprint-04-delivery | `ft.delivery.02.refactor` | refactor | forge_coder | — |
| sprint-04-delivery | `gate.delivery` | gate | ft_gatekeeper | — |
| sprint-05-smoke | `ft.smoke.01.cli_run` | build | forge_coder | — |
| sprint-05-smoke | `gate.smoke` | gate | ft_gatekeeper | — |
| sprint-06-e2e | `ft.e2e.01.cli_validation` | build | forge_coder | — |
| sprint-06-e2e | `gate.e2e` | gate | ft_gatekeeper | — |
| sprint-07-feedback | `ft.feedback.01.retro_note` | document | ft_coach | — |
| sprint-08-audit | `ft.audit.01.forgebase` | build | forge_coder | — |
| sprint-08-audit | `gate.audit` | gate | ft_gatekeeper | — |
| sprint-09-handoff | `ft.handoff.01.specs` | document | ft_coach | — |
| sprint-09-handoff | `gate.mvp` | gate | ft_gatekeeper | — |
| — | `ft.end` | — | — | — |

---

## 9. Métricas do Ciclo (cycle-01)

| Métrica | Valor |
|---------|-------|
| Steps concluídos | 17/22 (77%) — cycle-01 |
| Gate pass rate | 100% (17/17 — zero regressões) |
| Sprints PASS | MDD, Planning, TDD, Delivery, Smoke, E2E (6/9) |
| Sessões LLM | 11 |
| API calls | 1.497 |
| Tokens (input + output) | ~355.640 |
| Cache read tokens | ~155.566.886 |
| Testes unitários | 88 (cobertura: graph 91%, state 97%, artifacts 97%) |

---

## 10. Requisitos Implementados (Rastreabilidade)

| RF | Descrição | Status |
|----|-----------|--------|
| RF-01 | `process_id` único por processo | ✅ |
| RF-02 | Motor avança somente após gate PASS | ✅ |
| RF-03 | `blocked_reason` preenchido em gate BLOCK | ✅ |
| RF-04 | Suporte a múltiplos ciclos | ✅ |
| RF-05 | Métricas acumuladas | ⚠️ Parcial — conexão com `coverage.xml` pendente |
| RF-06 | Gates com critérios explícitos em YAML | ✅ |
| RF-07 | Gate TDD exige red→green sequencial | ✅ |
| RF-08 | Gate de cobertura bloqueia se < 80% | ✅ (validator implementado) |
| RF-09 | Gate E2E falha se qualquer cenário não passar | ✅ |
| RF-10 | Gates bloqueantes incontornáveis sem resolução explícita | ✅ |
| RF-11 | Agentes operam no escopo de paths próprios | ✅ |
| RF-12 | ft_manager é único responsável por avançar nodes | ✅ |
| RF-13 | ft_gatekeeper retorna apenas PASS ou BLOCK | ✅ |
| RF-14 | forge_coder executa red→green→refactor por sprint | ✅ |
| RF-15 | ft_acceptance gera matriz happy/edge/error | ✅ |
| RF-16 | Artifacts registrados em `artifacts` map após produção | ✅ |
| RF-17 | `gate_log` preserva histórico acumulado | ✅ |
| RF-18 | Sessões de agentes salvas em `project/docs/sessions/` | ✅ |

---

## 11. Critérios de Aceitação — Status

| ID | Critério | Status |
|----|----------|--------|
| AC-01 | Motor executa 22 steps sem intervenção manual | ✅ Validado em E2E |
| AC-02 | Gates bloqueantes impedem progressão | ✅ Validado em Smoke (S4, S9) |
| AC-03 | Estado preservado entre sessões | ✅ Validado em Smoke (S10) |
| AC-04 | Cobertura ≥ 80% ao final de cada sprint | ⚠️ Validator implementado; pipeline de métricas pendente |
| AC-05 | Zero regressões nos gates E2E | ✅ 0 regressões no cycle-01 |
| AC-06 | Cycle time reduzido vs baseline ad-hoc | 🔄 A medir no cycle-02 |

---

## 12. Pendências para cycle-02

| ID | Prioridade | Descrição |
|----|-----------|-----------|
| P1 | Alta | Conectar `coverage.xml` ao `StateManager.update_metrics()` (RF-05, AC-04) |
| P2 | Média | Adicionar validator `has_sections` para documentos MDD (anti-placeholder) |
| P3 | Baixa | Cenário E2E de lock concorrente (dois processos simultâneos) |
| P4 | Baixa | Migrar `StateManager` de `dict` para Pydantic model |
| P5 | Média | Formalizar PR process para inclusão de novos nodes no YAML |
| P6 | Alta | Migrar `print()` de diagnóstico em `runner.py` para `logging.getLogger(__name__)` |
| P7 | Média | Criar `forgepulse.value_tracks.yml` junto com implementação dos UseCases em `src/` |

---

## 13. Tech Stack

| Componente | Tecnologia | Versão mínima |
|------------|------------|---------------|
| Linguagem | Python | 3.11+ |
| Estado | YAML (PyYAML) | 6.0+ |
| LLM | Anthropic SDK | 0.25+ |
| Testes | pytest + pytest-cov | 8.0+ / 5.0+ |
| Linter/Formatter | ruff | 0.4+ |
| Tipagem | mypy | 1.9+ |

Para detalhes completos da stack, consultar `project/docs/tech_stack.md`.

---

## 14. Referências

| Documento | Path | Descrição |
|-----------|------|-----------|
| Task List | `project/docs/TASK_LIST.md` | Sprints, tasks e rastreabilidade RF→sprint |
| Tech Stack | `project/docs/tech_stack.md` | Dependências, configuração e decisões de design |
| Diagramas | `project/docs/diagrams/architecture.md` | Diagramas Mermaid de arquitetura |
| Smoke Report | `project/docs/smoke-report.md` | 10/10 cenários PASS |
| Retro | `project/docs/retro.md` | Lições aprendidas, fricções, propostas para cycle-02 |
| ForgeBase Audit | `project/docs/forgebase-audit.md` | Conformidade com regras ForgeBase |
| Processo YAML | `process/fast_track/FAST_TRACK_PROCESS_V2.yml` | Definição do grafo de processo |
| Estado ativo | `project/state/engine_state.yml` | Estado persistente (leitura apenas) |
