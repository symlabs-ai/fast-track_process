# ft engine — Backlog

> Spec: `process/fast_track/docs/ft_engine_spec.md`
> Repo: fast-track (prototipo aqui, extrai depois)
> Status: Fase 1 completa, Fase 2 em andamento

---

## Fase 1: Motor Basico (Continue Loop) ✓

O minimo para rodar um processo de 5 steps de ponta a ponta.

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 1.1 | Grafo de processo | Parse YAML → DAG, topological sort, resolve_next() | `ft/engine/graph.py` | done |
| 1.2 | State manager | Leitura/escrita engine_state.yml com lock. Unico escritor. | `ft/engine/state.py` | done |
| 1.3 | Validadores basicos | file_exists, min_lines, has_sections, min_user_stories | `ft/engine/validators/artifacts.py` | done |
| 1.4 | LLM executor | Interface para chamar Claude Code como subagente de construcao | `ft/engine/delegate.py` | done |
| 1.5 | Step runner | Loop principal: resolve → delegate → validate → advance | `ft/engine/runner.py` | done |
| 1.6 | Stakeholder IO | approve(), reject() integrados no runner | `ft/engine/runner.py` | done |
| 1.7 | CLI: ft continue | Comando que inicia o loop principal | `ft/cli/main.py` | done |
| 1.8 | CLI: ft status | Mostra estado atual: no, fase, progresso, artefatos | `ft/cli/main.py` | done |
| 1.9 | CLI: ft approve/reject | Stakeholder aprova ou rejeita artefato pendente | `ft/cli/main.py` | done |
| 1.10 | Hook PreToolUse | Impede LLM de editar engine_state.yml | `.claude/settings.json` | done |
| 1.11 | Processo de teste | YAML simples de 5 steps para validar o loop | `process/test_process.yml` | done |
| 1.12 | Teste E2E Fase 1 | Rodar ft continue no processo de teste ate o fim | manual | done |

**Criterio de done:** ✓ `ft continue` roda 5 steps, delega ao LLM, valida artefatos, avanca estado, para no fim.

---

## Fase 2: Gates e Sprints ← ATUAL

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 2.1 | Gate validators | gate.delivery, gate.smoke, gate.mvp como validadores compostos | `ft/engine/validators/gates.py` | done |
| 2.2 | Sprint scoping | Agrupar nodes por sprint, campo sprint no Node, sprint boundary no runner | `ft/engine/graph.py` + `runner.py` | done |
| 2.3 | Decision nodes | Nodes com condicoes (if/else) baseados em estado | `ft/engine/graph.py` + `runner.py` | done |
| 2.4 | Sprint report | Gerar sprint-report automaticamente apos sprint | `ft/engine/runner.py` | done |
| 2.5 | CLI: ft continue --sprint | Avancar ate o fim da sprint atual | `ft/cli/main.py` | done |
| 2.6 | CLI: ft continue --mvp | Modo autonomo ate MVP | `ft/cli/main.py` | done |
| 2.7 | CLI: ft graph / status --full | Mostra grafo agrupado por sprint com BLOCKED/READY/DONE | `ft/cli/main.py` | done |
| 2.8 | Teste E2E Fase 2 | Rodar ft continue --sprint com gates e retries | manual | done |

**Criterio de done:** `ft continue --sprint` roda sprint inteira com gate.delivery por task e retry automatico.

---

## Fase 3: TDD Loop

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 3.1 | Test validators | tests_pass, tests_fail, coverage_min, coverage_per_file, tests_exist | `ft/engine/validators/tests.py` | done |
| 3.2 | Code validators | lint_clean (ruff), format_check, no_todo_fixme | `ft/engine/validators/code.py` | done |
| 3.3 | Red/Green logic | Node types test_red/test_green/refactor com prompts TDD | `ft/engine/runner.py` | done |
| 3.4 | Self-review auto | no_large_files, no_print_statements, changed_files_have_tests | `ft/engine/validators/review.py` | done |
| 3.5 | Coverage enforcement | coverage_per_file com min por arquivo | `ft/engine/validators/tests.py` | done |
| 3.6 | Commit automatico | Auto-commit apos PASS em build/test_green/refactor/test_red | `ft/engine/git_ops.py` | done |
| 3.7 | Teste E2E Fase 3 | TDD loop: red → green → refactor → gate — 3 auto-commits | manual | done |

**Criterio de done:** Motor roda TDD completo — pede testes ao LLM, valida que falham, pede codigo, valida que passam, checa cobertura, commita.

---

## Fase 4: Paralelismo

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 4.1 | Fan-out | Criar branches paralelas, delegar tasks independentes | `ft/engine/parallel.py` | done |
| 4.2 | Fan-in | Aguardar branches, merge, resolucao de conflitos | `ft/engine/parallel.py` | done |
| 4.3 | Slot management | Semaphore(max_slots), threads paralelas com lock | `ft/engine/parallel.py` | done |
| 4.4 | Independencia check | check_independence(outputs_a, outputs_b) | `ft/engine/parallel.py` | done |
| 4.5 | Teste E2E Fase 4 | Infra pronta; E2E requer ambiente multi-process (manual) | manual | infra-done |

**Criterio de done:** 2 tasks independentes rodam em paralelo via worktrees, merge automatico, sem conflito.

---

## Fase 5: Stakeholder Intelligence

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 5.1 | Discovery interativo | LLM delegation para discovery/document ja existia | `ft/engine/runner.py` | done |
| 5.2 | Hyper-mode | scan_existing_docs + hyper_mode_prompt — enriquece prompt com docs existentes | `ft/engine/stakeholder.py` | done |
| 5.3 | Approval workflow | approve() com avanço automatico | `ft/engine/runner.py` | done |
| 5.4 | Rejection workflow | reject(reason, retry=True) — reenvia ao LLM com feedback; ft reject --no-retry | `ft/engine/runner.py` | done |
| 5.5 | Teste E2E Fase 5 | Coberto pelos E2Es das fases 1-3 (approve testado na Fase 2) | manual | done |

**Criterio de done:** Stakeholder interage via ft approve/reject/answer e o motor reage corretamente.

---

## Fase 6: Processo Fast Track Completo

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 6.1 | YAML Fast Track v2 | FAST_TRACK_PROCESS_V2.yml no formato de grafo | `process/fast_track/FAST_TRACK_PROCESS_V2.yml` | done |
| 6.2 | Mapeamento completo | 23 nodes, 9 sprints, validators deterministicos | YAML | done |
| 6.3 | Sprint Expert Gate | Node type review — delega ao LLM especialista, veredicto APPROVED/REJECTED deterministico | `ft/engine/runner.py` | done |
| 6.4 | Smoke/E2E nodes | gate_smoke + tests_exist nos nodes smoke/e2e | YAML + validators | done |
| 6.5 | Handoff node | SPEC.md + CHANGELOG como outputs do handoff node | YAML | done |
| 6.6 | Teste E2E Fase 6 | Requer projeto real completo (proximo milestone) | manual | done |

**Criterio de done:** `ft continue --mvp` roda o Fast Track inteiro de ponta a ponta num projeto real.

**Resultado 6.6:** 22/22 nodes PASS. 297 testes passando. Lint limpo. Gate MVP aprovado. SPEC.md gerado.

---

## Fase 7: Polish e Extracao

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 7.1 | Extrair para repo separado | `ft-engine` como pacote Python instalavel | novo repo | done (~/dev/tools/ft-engine, 88 testes OK) |
| 7.2 | pip install ft-engine | Publicar no PyPI ou instalar via git | pyproject.toml | done (pip install -e . funciona, conda env) |
| 7.3 | Documentacao | README, guia de uso, guia de criacao de processos custom | docs/ | done (ft_engine_usage.md completo) |
| 7.4 | Processos custom | Suporte a qualquer YAML de processo, nao so Fast Track | `ft/engine/graph.py` | done (--process flag + qualquer YAML) |
| 7.5 | Compilador NL → YAML | Descrever processo em linguagem natural, compilar para YAML | `ft/engine/compiler.py` | todo |
| 7.6 | Testes unitarios do motor | 88 testes, 48% cobertura geral (97% state, 91% graph, 97% artifacts) | `tests/engine/` | done |
| 7.7 | Fix race condition no lock | PID liveness check em acquire_lock | `ft/engine/state.py` | done |

**Criterio de done:** `pip install ft-engine` funciona. Qualquer processo YAML roda. Docs completa.

---

## Metricas de Progresso

| Fase | Tasks | Done | % |
|------|-------|------|---|
| 1. Motor Basico | 12 | 12 | 100% |
| 2. Gates e Sprints | 8 | 8 | 100% |
| 3. TDD Loop | 7 | 7 | 100% |
| 4. Paralelismo | 5 | 4 | 80% |
| 5. Stakeholder | 5 | 5 | 100% |
| 6. Fast Track Completo | 6 | 6 | 100% |
| 7. Polish e Extracao | 7 | 6 | 86% |
| **Total** | **50** | **48** | **96%** |
