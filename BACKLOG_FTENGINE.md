# ft engine — Backlog

> Spec: `process/fast_track/docs/ft_engine_spec.md`
> Repo: fast-track (prototipo aqui, extrai depois)
> Status: Backlog

---

## Fase 1: Motor Basico (Continue Loop)

O minimo para rodar um processo de 5 steps de ponta a ponta.

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 1.1 | Grafo de processo | Parse YAML → DAG, topological sort, resolve_next() | `ft/engine/graph.py` | todo |
| 1.2 | State manager | Leitura/escrita ft_state.yml com lock. Unico escritor. | `ft/engine/state.py` | todo |
| 1.3 | Validadores basicos | file_exists, min_lines, has_sections, min_user_stories | `ft/engine/validators/artifacts.py` | todo |
| 1.4 | LLM executor | Interface para chamar Claude Code como subagente de construcao | `ft/engine/delegate.py` | todo |
| 1.5 | Step runner | Loop principal: resolve → delegate → validate → advance | `ft/engine/runner.py` | todo |
| 1.6 | Stakeholder IO | prompt_stakeholder(), approve(), reject(), answer() | `ft/engine/stakeholder.py` | todo |
| 1.7 | CLI: ft continue | Comando que inicia o loop principal | `ft/cli/continue_cmd.py` | todo |
| 1.8 | CLI: ft status | Mostra estado atual: no, fase, progresso, artefatos | `ft/cli/status_cmd.py` | todo |
| 1.9 | CLI: ft approve/reject | Stakeholder aprova ou rejeita artefato pendente | `ft/cli/approve_cmd.py` | todo |
| 1.10 | Hook PreToolUse | Impede LLM de editar ft_state.yml | `.claude/settings.json` | todo |
| 1.11 | Processo de teste | YAML simples de 5 steps para validar o loop | `process/test_process.yml` | todo |
| 1.12 | Teste E2E Fase 1 | Rodar ft continue no processo de teste ate o fim | manual | todo |

**Criterio de done:** `ft continue` roda 5 steps, delega ao LLM, valida artefatos, avanca estado, para no fim.

---

## Fase 2: Gates e Sprints

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 2.1 | Gate validators | gate.delivery, gate.smoke, gate.mvp como validadores compostos | `ft/engine/validators/gates.py` | todo |
| 2.2 | Sprint scoping | Agrupar tasks do TASK_LIST por sprint, delegar sprint inteira | `ft/engine/sprint.py` | todo |
| 2.3 | Retry com feedback | Reenviar ao LLM com feedback dos validadores. Max N retries. | `ft/engine/runner.py` | todo |
| 2.4 | Sprint report | Gerar sprint-report automaticamente apos sprint | `ft/engine/reporters.py` | todo |
| 2.5 | CLI: ft continue --sprint | Avancar ate o fim da sprint atual | `ft/cli/continue_cmd.py` | todo |
| 2.6 | CLI: ft continue --mvp | Modo autonomo ate MVP | `ft/cli/continue_cmd.py` | todo |
| 2.7 | CLI: ft graph | Mostra grafo com BLOCKED/READY/DONE | `ft/cli/graph_cmd.py` | todo |
| 2.8 | Teste E2E Fase 2 | Rodar ft continue --sprint com gates e retries | manual | todo |

**Criterio de done:** `ft continue --sprint` roda sprint inteira com gate.delivery por task e retry automatico.

---

## Fase 3: TDD Loop

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 3.1 | Test validators | tests_pass, tests_fail, coverage_min, coverage_per_file | `ft/engine/validators/tests.py` | todo |
| 3.2 | Code validators | lint_clean (ruff), types_clean (mypy), no_dead_code | `ft/engine/validators/code.py` | todo |
| 3.3 | Red/Green logic | Step runner entende: red=tests_fail, green=tests_pass | `ft/engine/runner.py` | todo |
| 3.4 | Self-review auto | Checklist de self-review rodado por validadores Python | `ft/engine/validators/review.py` | todo |
| 3.5 | Coverage enforcement | Rejeitar se cobertura < min nos arquivos alterados | `ft/engine/validators/tests.py` | todo |
| 3.6 | Commit automatico | Motor faz git commit apos green+review com mensagem padrao | `ft/engine/git_ops.py` | todo |
| 3.7 | Teste E2E Fase 3 | Rodar TDD loop: red → green → review → commit → gate | manual | todo |

**Criterio de done:** Motor roda TDD completo — pede testes ao LLM, valida que falham, pede codigo, valida que passam, checa cobertura, commita.

---

## Fase 4: Paralelismo

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 4.1 | Fan-out | Criar branches paralelas, delegar tasks independentes | `ft/engine/parallel.py` | todo |
| 4.2 | Fan-in | Aguardar branches, merge, resolucao de conflitos | `ft/engine/parallel.py` | todo |
| 4.3 | Slot management | Limitar N agents simultaneos, queue de tasks | `ft/engine/parallel.py` | todo |
| 4.4 | Independencia check | Avaliar se tasks podem paralelizar (VTs diferentes, etc.) | `ft/engine/parallel.py` | todo |
| 4.5 | Teste E2E Fase 4 | Rodar 2+ tasks em paralelo, merge, validar integridade | manual | todo |

**Criterio de done:** 2 tasks independentes rodam em paralelo via worktrees, merge automatico, sem conflito.

---

## Fase 5: Stakeholder Intelligence

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 5.1 | Discovery interativo | Hipotese e PRD com perguntas ao stakeholder | `ft/engine/stakeholder.py` | todo |
| 5.2 | Hyper-mode | Absorver docs existentes em project/docs/ e pular discovery | `ft/engine/stakeholder.py` | todo |
| 5.3 | Approval workflow | Queue de aprovacoes pendentes, ft approve com contexto | `ft/engine/stakeholder.py` | todo |
| 5.4 | Rejection workflow | ft reject com motivo, reenvio ao LLM com feedback | `ft/engine/stakeholder.py` | todo |
| 5.5 | Teste E2E Fase 5 | Rodar discovery completo com stakeholder simulado | manual | todo |

**Criterio de done:** Stakeholder interage via ft approve/reject/answer e o motor reage corretamente.

---

## Fase 6: Processo Fast Track Completo

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 6.1 | YAML Fast Track v2 | Reescrever FAST_TRACK_PROCESS.yml no formato de grafo com node types | `process/fast_track/FAST_TRACK_PROCESS_V2.yml` | todo |
| 6.2 | Mapeamento completo | Todos os 19 steps com validators, executors e outputs definidos | YAML | todo |
| 6.3 | Sprint Expert Gate | Node type review com invocacao de /ask fast-track | `ft/engine/runner.py` | todo |
| 6.4 | Smoke/E2E nodes | Steps de smoke e E2E como nodes com validators especificos | YAML + validators | todo |
| 6.5 | Handoff node | Gerar SPEC.md, CHANGELOG, BACKLOG automaticamente | `ft/engine/reporters.py` | todo |
| 6.6 | Teste E2E Fase 6 | Rodar Fast Track completo (19 steps) em projeto real | manual | todo |

**Criterio de done:** `ft continue --mvp` roda o Fast Track inteiro de ponta a ponta num projeto real.

---

## Fase 7: Polish e Extracao

| # | Task | Descricao | Entrega | Status |
|---|------|-----------|---------|--------|
| 7.1 | Extrair para repo separado | `ft-engine` como pacote Python instalavel | novo repo | todo |
| 7.2 | pip install ft-engine | Publicar no PyPI ou instalar via git | pyproject.toml | todo |
| 7.3 | Documentacao | README, guia de uso, guia de criacao de processos custom | docs/ | todo |
| 7.4 | Processos custom | Suporte a qualquer YAML de processo, nao so Fast Track | `ft/engine/graph.py` | todo |
| 7.5 | Compilador NL → YAML | Descrever processo em linguagem natural, compilar para YAML | `ft/engine/compiler.py` | todo |
| 7.6 | Testes unitarios do motor | Cobertura > 90% do engine | `tests/` | todo |

**Criterio de done:** `pip install ft-engine` funciona. Qualquer processo YAML roda. Docs completa.

---

## Metricas de Progresso

| Fase | Tasks | Done | % |
|------|-------|------|---|
| 1. Motor Basico | 12 | 0 | 0% |
| 2. Gates e Sprints | 8 | 0 | 0% |
| 3. TDD Loop | 7 | 0 | 0% |
| 4. Paralelismo | 5 | 0 | 0% |
| 5. Stakeholder | 5 | 0 | 0% |
| 6. Fast Track Completo | 6 | 0 | 0% |
| 7. Polish e Extracao | 6 | 0 | 0% |
| **Total** | **49** | **0** | **0%** |
