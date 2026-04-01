# Changelog

Todas as mudanças notáveis do Fast Track são documentadas neste arquivo.

---

## [v0.7.3] - 2026-04-01

- feat: E2E Fast Track V2 completo — 22/22 nodes PASS, 297 testes passando, gate MVP aprovado
- feat: SPEC.md gerado (ft.handoff.01.specs) — documentação completa da interface pública do ft engine
- feat: E2E CLI Validation (sprint-06-e2e) — 285 testes E2E passando
- feat: Auditoria ForgeBase (sprint-08-audit) — lint limpo, gate.audit PASS

---

## [v0.7.2] - 2026-04-01

- fix: prevenir race condition quando dois processos `ft continue` rodam simultaneamente — `StateManager.load(check_lock=True)` verifica se o PID do lock ainda está vivo e lança `StateLockError`
- fix: gate `ft.mdd.03.validacao` no Fast Track V2 — adicionado `outputs` ao node para `min_lines` ter caminho correto
- e2e: Fast Track V2 validado em 15/22 nodes (sprints 1–5 completas), bloqueado por quota de LLM

---

## [v0.7.1] - 2026-04-01

- feat: add Sprint Expert Gate — node type `review` com veredicto APPROVED/REJECTED determinístico via parse do relatório LLM
- feat: unit tests do engine — 88 testes cobrindo graph (91%), state (97%), artifacts (97%), stakeholder (76%), validators e runner

---

## [v0.7.0] - 2026-04-01

### Novas funcionalidades

- **ft engine — Motor Determinístico**: Implementação completa de um runtime Python que elimina orquestração não-determinística por LLMs. O Python controla todo o fluxo de processo; o LLM é restrito a tarefas de construção.
- **DAG de Processo (graph.py)**: Parser YAML → DAG com suporte a sprints, grupos paralelos, decision nodes e custom prompts por node.
- **TDD Loop Nativo**: Node types `test_red`, `test_green`, `refactor` com prompts dedicados e validadores `tests_fail`/`tests_pass` que forçam o ciclo red→green→refactor.
- **Sprint Boundaries**: `ft continue --sprint` para na fronteira de sprint com sprint report automático.
- **Auto-commit**: Motor faz `git commit` automaticamente após PASS em nodes de build/test/refactor com labels `red:`, `green:`, `refactor:`, `feat:`.
- **Paralelismo via Worktrees**: `parallel.py` com fan-out/fan-in via git worktrees, semáforo de slots e verificação de independência de outputs.
- **Stakeholder Intelligence**: `stakeholder.py` com hyper-mode (absorve docs existentes e enriquece prompt), rejection workflow com retry automático ao LLM.
- **Fast Track V2**: `FAST_TRACK_PROCESS_V2.yml` — processo completo mapeado no formato de grafo: 23 nodes, 9 sprints, validators determinísticos para cada fase.
- **Gate Validators Compostos**: `gate_delivery`, `gate_smoke`, `gate_mvp` como validadores que agregam múltiplas verificações.
- **Validators de Código**: `lint_clean` (ruff), `format_check`, `coverage_per_file`, `tests_exist`, `no_large_files`, `no_print_statements`.
- **Hook PreToolUse**: Bloqueia LLM de editar `engine_state.yml` diretamente.

### Melhorias

- **CLI ft**: Adicionado `ft init` para resetar/inicializar estado; `ft reject --no-retry` para rejeição sem LLM; prioridade de processo (v2 > v1 > fast_track).
- **ft status --full**: Grafo agrupado por sprint com indicadores ✓/→/○ e ◀ no node atual.
- **Retry com feedback**: Falha de validação gera feedback específico para o LLM (max 3 retries).

### Outros

- Backlog `BACKLOG_FTENGINE.md`: 49 tasks em 7 fases, progresso 82% (40/49)
- Spec `ft_engine_spec.md`: documentação completa da arquitetura
- Análise competitiva: SpecKit, BMAD, OpenSpec vs Fast Track
- Processos de teste v1, v2, v3 (TDD), v4 (parallel) para validação do engine

---

## [v0.6.6] - 2026-04-01

- feat: add ft validate gate mvp + Claude Code hooks for enforcement

## [v0.6.5] - 2026-04-01

- feat: sprint-level delegation model — forge_coder receives full sprint
- fix: add continuity rule — never announce and stop
- fix: ft_manager checks project/docs/ for existing artifacts before asking

## [v0.6.4] - 2026-03-31

- fix: ft_manager must be main Claude persona, not a subagent

## [v0.6.3] - 2026-03-31

- feat: add --gateway option to ft init for SymGateway routing
- fix: show warnings and failures summary in RESULTADO line

## [v0.6.2] - 2026-03-31

- fix: differentiate WARN from FAIL in ft init reports
- fix: track env/git-dev/ configs and scope gitignore to root only

## [v0.6.1] - 2026-03-31

- feat: add ft help (agent discovery) and ft role (scope enforcement)
- refactor: use ft command instead of full path in all prompts
- feat: add global engine support to ft.py
- feat: add ft system-wide command for creating new projects
- docs: add agent installation as first step in AGENTS.md

## [v0.6.0] — 2026-03-31

### Novas funcionalidades
- **CLI unificada (ft.py)**: Ferramenta de validação determinística data-driven com subcommands: `init`, `validate state/artifacts/gate/integration`, `generate ids/check`, `tokens`, `self-check`. Lê FAST_TRACK_PROCESS.yml e schemas em runtime, zero constantes hardcoded.
- **JSON Schema para ft_state.yml**: Validação de tipos, enums e constraints do estado do processo. Detecta campos inválidos (ex: `sprint_status: done`) antes que corrompam o estado.
- **Symbiota ft_acceptance**: Novo especialista em design de cenários de teste de aceitação por Value/Support Track. Gera matriz de cenários (happy/edge/error), identifica dados faltantes e demanda do stakeholder.
- **Step ft.acceptance.01.scenario_design**: Novo step no processo onde ft_acceptance projeta cenários antes do forge_coder implementar os testes. Processo passa de 18 para 19 steps, de 4 para 5 symbiotas.
- **Mock audit e dead code check** (`ft.py validate integration`): Verifica ports sem implementação real, usecases não invocados por adapters, adapters desconectados do wiring, e enforcement de interface_type.
- **Design system obrigatório**: Quando `interface_type != cli_only`, ft_manager exige design system definido no tech_stack. Se stakeholder não souber, ft_manager propõe com justificativa.
- **Sync de agents do Claude Code**: `ft.py init` verifica/cria/atualiza agents em `~/.claude/agents/` para os 5 symbiotas do processo.
- **Runner E2E funcional**: `tests/e2e/run-all.sh` que roda pytest (unit+smoke) + tracks E2E por ciclo com exit codes.
- **Anti-patterns doc**: 10 erros comuns documentados com cenário, consequência e correção.

### Melhorias
- **Reorganização estático/dinâmico**: `ft_state.yml` movido de `process/fast_track/state/` para `project/state/`. `.gitignore` exclui `process/` (estático). Processo pode ser atualizado sobrescrevendo `process/` sem perder estado.
- **Scaffold Clean/Hex em src/**: Estrutura `domain/`, `application/`, `infrastructure/`, `adapters/` conforme ForgeBase Rules, criada automaticamente pelo `ft.py init`.
- **Tiering das regras críticas**: 32 regras reorganizadas em 3 tiers — 10 invioláveis, 18 defaults, 4 contextuais.
- **Procedimento de recovery**: Tabela com 6 cenários de bloqueio e ações de recuperação.
- **Hyper-mode parcial**: Quando PRD entregue tem 5+ seções ausentes, ft_coach conduz discovery conversacional para seções faltantes.
- **Consistência YAML ↔ MD**: `ft.py generate check` valida que YAML, MD, IDs e Summary estão alinhados. `ft.py generate ids` gera FAST_TRACK_IDS.md do YAML.
- **gate.acceptance expandido**: Cobertura por track obrigatória (>= 3 cenários por Value Track, >= 1 por Support Track). Separação cenários (ft_acceptance) vs. implementação (forge_coder).
- **gate.audit expandido**: Mock audit (ports sem impl real = BLOCK), dead code (usecases soltos = BLOCK), design system conformidade.
- **CLI integrada nos prompts**: ft_manager, ft_gatekeeper e AGENTS.md referenciam ft.py nos momentos corretos.

### Outros
- `env/git-dev.zip` substituído por `env/git-dev/` (arquivos versionáveis)
- Changelog movido do README.md para CHANGELOG.md
- `setup_env.sh` atualizado para copiar configs em vez de unzip

---

### [v0.5.0] — 2026-03-09

#### Added
- **Sprints técnicas por dependência** — `ft.plan.01.task_list` agora exige agrupamento das tasks em sprints incrementais com objetivo explícito e gate de saída.
- **Sprint Expert Gate** — ao final de cada sprint, o `ft_manager` deve chamar `/ask fast-track`, registrar o retorno em `project/docs/sprint-review-sprint-XX.md` e tratar todas as recomendações antes de seguir.
- **Estado de sprint no `ft_state.yml`** — suporte a `current_sprint`, `sprint_status`, `cycle_sprint_scope`, `backlog_sprints`, `planned_sprints`, `sprint_review_gate` e `sprint_review_log`.
- **Template `template_sprint_review.md`** — artefato canônico para registrar a pergunta ao especialista, feedback, recomendações e correções aplicadas.

#### Changed
- **Loop TDD/Delivery** passa a operar sprint a sprint, sem puxar tasks de sprint futura.
- **Paralelização** continua opt-in, mas agora é limitada à sprint atual.
- **Documentação central do processo** atualizada para refletir o loop `sprint -> Sprint Expert Gate -> correções -> próxima sprint`.

### [v0.4.0] — 2026-03-04

#### Added
- **Acceptance Gate** (`ft.acceptance.01.interface_validation`) — nova fase 5c condicional após E2E.
- **Refactor step** (`ft.delivery.02.refactor`) — step formal do TDD "R" após self-review.
- **Cobertura mínima de testes** — >= 85% nos arquivos alterados (desejável 90%).
- **Commit strategy** para ciclos longos — `commit_strategy: per_task | squash_cycle`.
- **Campo `interface_type`** em `ft_state.yml` — `cli_only | api | ui | mixed`.

#### Changed
- **`ft.delivery.01.implement` removido** — absorvido por `ft.tdd.03.green`.
- **Self-review expandido** — de 5 para 10 itens, 3 grupos.
- Step count: 16 → 17. Phase count: 7 → 8.

### [v0.3.0] — 2026-03-03

#### Added
- **hipotese.md** como artefato próprio antes do PRD.
- **Stack obrigatória**: ForgeBase always, Forge_LLM quando PRD contiver features LLM.
- **Value Tracks & Support Tracks**: fluxos de valor mensuráveis integrados ao processo.
- **Bridge Processo→ForgeBase** no forge_coder.
- **Pulse evidence no smoke gate**.

#### Changed
- `ft.mdd.02.prd` recebe `project/docs/hipotese.md` como input.
- Regra "PRD é a fonte única" ajustada para acomodar `hipotese.md`.

### [v0.2.0] — 2026-02-26

#### Added
- **ft.handoff.01.specs** — fase Handoff para gerar SPEC.md.
- **SPEC.md**: documento de referência do produto entregue.
- **Maintenance mode**: após SPEC.md, projeto evolui via `/feature`.
- Step count: 15 → 16.

### [v0.1.6] — 2026-02-25

#### Added
- **Smoke Gate** (`ft.smoke.01.cli_run`) — validação real via PTY.
- **Separação `tests/unit/` e `tests/smoke/`**.
- **Campo `mvp_status`** — `null | demonstravel | entregue`.

### [v0.1.5] — 2026-02-25

#### Added
- **ft.plan.02.tech_stack** — proposta de stack técnica.
- **ft.plan.03.diagrams** — 4 diagramas Mermaid.
- **TDD interaction mode** — `phase_end | per_task | mvp_end`.
- **Status header obrigatório** em toda mensagem do ft_manager.

### [v0.1.4] — 2026-02-25

#### Fixed
- **ft_manager**: detecção de hyper-mode tornada obrigatória na delegação de discovery.

### [v0.1.3] — 2026-02-25

#### Added
- **Hyper-mode**: processamento de PRD abrangente em um único pass.
- **template_hyper_questionnaire.md**.
- **Campo `mdd_mode`** — `normal | hyper`.

### [v0.1.2] — 2026-02-25

#### Added
- **ft_manager**: verificação de vínculo git na inicialização.

### [v0.1.1] — 2026-02-25

#### Added
- **ft_manager** — novo symbiota orquestrador.
- Modos `interactive` e `autonomous`.
- Checkpoints de validação em PRD, task list e entrega por task.

### [v0.1.0] — 2026-02-25

#### Added
- Estrutura inicial: 12 steps, 6 fases.
- Symbiotas `ft_coach` e `forge_coder`.
- Templates, state, processo YAML/MD.
- `setup_env.sh`, testes E2E, docs de integração.
