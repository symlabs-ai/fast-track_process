# Smoke Report — ft.smoke.01.cli_run

**Data:** 2026-04-01
**Sprint:** sprint-05-smoke
**Ciclo:** cycle-01
**Executor:** forge_coder

---

## Plano de Teste (TDD — Cenários Definidos Antes da Execução)

| # | Cenário | Critério de Aceite |
|---|---------|-------------------|
| S1 | CLI boot com `--help` | Exibe comandos disponíveis sem erro |
| S2 | `status` com estado existente (retomada de sessão) | Exibe `ft.smoke.01.cli_run` como node atual, 13/22 steps |
| S3 | `status --full` com processo explícito | Exibe grafo completo com sprints 01-04 PASS e sprint-05 pendente |
| S4 | Gate BLOCK — arquivo inexistente | `Validator.file_exists` retorna `BLOCK` com mensagem correta |
| S5 | Gate PASS — arquivo existente | `Validator.file_exists` retorna `PASS` |
| S6 | Gate BLOCK — `min_lines` abaixo do mínimo | `Validator.min_lines` retorna `BLOCK` com contagem real |
| S7 | Gate PASS — `min_lines` acima do mínimo | `Validator.min_lines` retorna `PASS` |
| S8 | Avanço de node simulado | `EngineState.advance` atualiza `current_node`, `steps_completed`, `gate_log` |
| S9 | Bloqueio de node simulado | `EngineState.block` define `node_status=blocked` e `blocked_reason` |
| S10 | Retomada de sessão — integridade do estado | Todos os 13 nodes anteriores preservados em `completed_nodes` |

---

## Execução Real — Saídas Capturadas

### S1 — CLI Help

**Comando:** `python -m ft.cli.main --help`

```
usage: ft [-h] [--process PROCESS]
          {init,continue,status,approve,reject,graph} ...

ft engine — motor deterministico de processos

positional arguments:
  {init,continue,status,approve,reject,graph}
    init                Inicializar/resetar estado do processo
    continue            Avancar no processo
    status              Estado atual
    approve             Aprovar artefato pendente
    reject              Rejeitar artefato pendente
    graph               Mostrar grafo com status

options:
  -h, --help            show this help message and exit
  --process PROCESS, -p PROCESS
                        Path do YAML de processo
```

**Resultado:** ✅ PASS — CLI inicializa e exibe ajuda sem erro.

---

### S2 — Status (retomada de sessão)

**Comando:** `python -m ft.cli.main status`

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Processo: fast_track_v2 v0.7.0
  Node atual: ft.smoke.01.cli_run
  Status: delegated
  Progresso: 13/22
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Resultado:** ✅ PASS — Estado retomado corretamente: node `ft.smoke.01.cli_run`, 13/22 steps.

---

### S3 — Status Full (grafo completo)

**Comando:** `python -m ft.cli.main -p process/fast_track/FAST_TRACK_PROCESS_V2.yml status --full`

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Processo: fast_track_v2 v0.7.0
  Node atual: ft.smoke.01.cli_run
  Status: delegated
  Sprint: sprint-05-smoke
  Progresso: 13/22
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [sprint-01-mdd] (3/3)
    ✓ ft.mdd.01.hipotese: Capturar Hipotese [PASS]
    ✓ ft.mdd.02.prd: Redigir PRD [PASS]
    ✓ ft.mdd.03.validacao: Validar PRD [PASS]

  [sprint-02-planning] (4/4)
    ✓ ft.plan.01.task_list: Criar Task List [PASS]
    ✓ ft.plan.02.tech_stack: Propor Tech Stack [PASS]
    ✓ ft.plan.03.diagrams: Gerar Diagramas Tecnicos [PASS]
    ✓ gate.planning: Gate de Planning [PASS]

  [sprint-03-tdd] (3/3)
    ✓ ft.tdd.02.red: Red — Escrever Testes [PASS]
    ✓ ft.tdd.03.green: Green — Implementar [PASS]
    ✓ gate.tdd: Gate TDD [PASS]

  [sprint-04-delivery] (3/3)
    ✓ ft.delivery.01.self_review: Self-Review [PASS]
    ✓ ft.delivery.02.refactor: Refactor [PASS]
    ✓ gate.delivery: Gate de Delivery [PASS]

  [sprint-05-smoke] (0/2)
    → ft.smoke.01.cli_run: Smoke CLI Run ◀
    ○ gate.smoke: Gate de Smoke
```

**Resultado:** ✅ PASS — Grafo exibe 13 nodes concluídos, sprint atual identificada, node corrente marcado com `◀`.

---

### S4 — Gate BLOCK (arquivo inexistente)

**Código:** `Validator.file_exists('project/docs/nonexistent.md')`

```python
{'status': 'BLOCK', 'reason': 'File not found: project/docs/nonexistent.md'}
```

**Resultado:** ✅ PASS — `BLOCK` retornado com mensagem descritiva. Motor não avança.

---

### S5 — Gate PASS (arquivo existente)

**Código:** `Validator.file_exists('project/docs/hipotese.md')`

```python
{'status': 'PASS', 'reason': ''}
```

**Resultado:** ✅ PASS — `PASS` retornado para arquivo existente.

---

### S6 — Gate BLOCK (min_lines abaixo do mínimo)

**Código:** `Validator.min_lines('project/docs/hipotese.md', 100)`

```python
{'status': 'BLOCK', 'reason': 'File has 1 lines, minimum is 100'}
```

**Resultado:** ✅ PASS — `BLOCK` com contagem real de linhas e threshold explícito.

---

### S7 — Gate PASS (min_lines acima do mínimo)

**Código:** `Validator.min_lines('project/docs/tech_stack.md', 5)`

```python
{'status': 'PASS', 'reason': ''}
```

**Resultado:** ✅ PASS — `PASS` quando arquivo tem linhas suficientes.

---

### S8 — Avanço de Node (simulado, sem escrita em disco)

**Código:** `EngineState.advance(state_copy, 'ft.smoke.01.cli_run', 'gate.smoke')`

```python
current_node:      gate.smoke
steps_completed:   14
last_completed:    ft.smoke.01.cli_run
gate_log_entry:    PASS
```

**Resultado:** ✅ PASS — `advance` atualiza corretamente: node avança, contador incrementa, `gate_log` registra PASS.

---

### S9 — Bloqueio de Node (simulado)

**Código:** `EngineState.block(state_copy, 'File not found: project/docs/smoke-report.md')`

```python
node_status:    blocked
blocked_reason: File not found: project/docs/smoke-report.md
```

**Resultado:** ✅ PASS — `block` registra `node_status=blocked` e `blocked_reason` descritivo. Processo não avança sem resolução.

---

### S10 — Integridade do Estado (retomada de sessão)

**Código:** `EngineState.load('project/state/engine_state.yml')`

```python
current_node:            ft.smoke.01.cli_run
node_status:             delegated
steps_completed:         13
completed_nodes_count:   13
```

**Resultado:** ✅ PASS — Estado preservado com 13 nodes concluídos. Todos os gates anteriores registrados em `gate_log`.

---

## Resumo Final

| # | Cenário | Status |
|---|---------|--------|
| S1 | CLI Help | ✅ PASS |
| S2 | Status — retomada de sessão | ✅ PASS |
| S3 | Status Full — grafo completo | ✅ PASS |
| S4 | Gate BLOCK — arquivo inexistente | ✅ PASS |
| S5 | Gate PASS — arquivo existente | ✅ PASS |
| S6 | Gate BLOCK — min_lines insuficiente | ✅ PASS |
| S7 | Gate PASS — min_lines satisfeito | ✅ PASS |
| S8 | Avanço de node | ✅ PASS |
| S9 | Bloqueio de node | ✅ PASS |
| S10 | Integridade do estado | ✅ PASS |

**Total: 10/10 cenários PASS**

**Conclusão:** CLI do ft engine opera corretamente. Boot, retomada de sessão, validators, avanço e bloqueio de nodes funcionam conforme especificado. Pronto para `gate.smoke`.
