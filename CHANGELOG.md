# Changelog

Todas as mudanças notáveis do Fast Track são documentadas neste arquivo.

---

## [v0.13.0] - 2026-04-08

### Novas funcionalidades
- **SymGateway via env vars**: credenciais migradas para `SYM_GATEWAY_PROJECT_KEY` e `SYM_GATEWAY_ADMIN_KEY` — `--key`/`--admin-key` removidos do `ft run` e `ft setup-env`; provisionamento falha com erro claro e instruções se a env var não estiver definida
- **`ft init <nome>`**: aceita nome do projeto como argumento posicional — cria a pasta, inicializa dentro dela e provisiona SymGateway automaticamente se `SYM_GATEWAY_PROJECT_KEY` estiver definida
- **`ft validate` com verificação estrutural**: valida presença de `docs/`, `process/` e `src/` além do schema do YAML de processo — exit code 1 se qualquer verificação falhar
- **Template `docs/code_reference/`**: novo subdiretório no template base para código de referência (ex: `graph_routing.js`)

### Melhorias
- **`ft init` + SymGateway unificado**: provisiona CLAUDE.md e `.claude/settings.local.json` automaticamente — elimina etapa manual `ft setup-env`
- **`register_gateway.sh` atualizado**: template symgateway usa novas env vars; falha explicitamente se `SYM_GATEWAY_PROJECT_KEY` ausente
- **Limpeza de processo**: arquivos de processo órfãos removidos (`test_process*.yml`, `FAST_TRACK_PROCESS.yml` V1); testes migrados para YAML inline (BL-20/BL-21)
- **`status()` sincroniza versão**: `status()` agora chama `_sync_process_meta()` — corrige bug onde versão do YAML não era atualizada no state
- **`find_process_yaml` recursivo**: scan de `process/` agora usa `rglob` — encontra YAMLs em subdiretórios como `process/fast_track/`

---

## [v0.12.0] - 2026-04-08

### Novas funcionalidades
- **Worktrees externos (BL-20)**: ciclos agora vivem em `~/.ft/worktrees/<projeto>/` em vez de `runs/` dentro do repositório — repo fica limpo, ciclos paralelos isolados de verdade via `git worktree` nativo
- **Nova estrutura base (BL-21)**: `ft init --template base` cria `docs/`, `process/`, `src/` — sem `runs/`, sem `seed/`. Template com `process/process.yml`, `docs/PRD.md` e `docs/TECH_STACK.md`
- **`process/process.yml`**: novo nome canônico do YAML de processo — `find_process_yaml()` prioriza `process.yml` sobre `FAST_TRACK_PROCESS.yml`

### Melhorias
- **`_worktrees_home()`**: nova função utilitária que retorna `~/.ft/worktrees/<project_name>/`
- **`_next_cycle_num()`**: scan de worktrees externos + `runs/` legado + branches git para evitar conflitos de numeração
- **`_find_latest_state()`**: busca state em worktrees externos primeiro, depois `runs/` legado
- **`cmd_runs()`**: lista ciclos de worktrees externos e `runs/` legado simultaneamente
- **`get_runner()`**: `--cycle` flag busca em worktrees externos antes de `runs/`
- **`copy_template()`**: copia `docs/` e `src/` do template além do YAML; destino padrão `process/process.yml`
- **`_next_run_dir()`**: propaga `docs/` para o run dir quando `seed/` não existe (nova estrutura)
- **`cmd_init()`**: cria `src/` em vez de `runs/`; não chama `_ensure_runs_gitignore()`

### Compatibilidade
- Projetos com `runs/` existente continuam funcionando (fallback em todos os comandos)
- Projetos com `seed/` continuam copiando para run dir
- YAMLs com nome `FAST_TRACK_PROCESS.yml` continuam sendo encontrados
- **rate limit retry**: backoff exponencial 60→120→240s em `delegate_to_llm`
- **ft status/runs**: fonte de verdade unificada via `engine_state.yml`
- **find_process_yaml**: auto-detect por `process_id` do state ativo

---

## [v0.11.2] - 2026-04-08

- **rate limit retry**: `delegate_to_llm` detecta rate limit no output (429, RESOURCE_EXHAUSTED, overloaded, etc.) e reexecuta com backoff exponencial 60 → 120 → 240s — evita ciclos bloqueados por quota temporária
- **ft status sem efeitos colaterais**: removida chamada a `_sync_process_meta()` do método `status()` — eliminava corrupção silenciosa do `engine_state.yml` com metadados do YAML errado
- **ft runs lê engine_state**: `cmd_runs` reescrito para ler `engine_state.yml` diretamente em vez de `*_log.md` — `ft runs` e `ft status` agora têm a mesma fonte de verdade
- **find_process_yaml auto-detecta**: lê `process_id` do `engine_state.yml` ativo e busca o YAML correspondente pelo campo `id:` — resolve ambiguidade quando há múltiplos YAMLs em `process/`
- **--process removido de ft status/continue**: argumento era redundante pois o processo já é auto-detectado pelo engine_state

---

## [v0.11.1] - 2026-04-07

### Correções
- **_is_cycle_dir**: aceita formato `cycle-NN-engine` (ex: `cycle-01-claude`) — antes `name[6:].isdigit()` falhava para nomes compostos
- **runs/ no .gitignore da raiz**: `_ensure_runs_gitignore` agora adiciona `runs/` ao `.gitignore` do projeto em vez de criar `runs/.gitignore` interno — ciclos são efêmeros e nunca versionados
- **Validator root em modo worktree**: `_resolve_validator_root` prefere `work_dir` (worktree) em vez de `project_root` — corrige falso FAIL em `screenshot_review_passed` quando LLM escreve artefatos no worktree
- **Worktree em `runs/`**: `_setup_worktree` cria worktrees em `runs/cycle-NN-<engine>/` unificando os conceitos de run dir e worktree

---

## [v0.11.0] - 2026-04-07

### Novas funcionalidades
- **ft lint-process**: comando CLI que usa LLM para validar semanticamente um YAML de processo — detecta referências a projetos específicos (nomes de produto, specs de design, tech stack hardcoded) e retorna relatório com violations + verdict PASS/FAIL
- **Process design rules**: documentação formal em `docs/ft_engine_usage.md` da regra "YAML = orquestração pura" — toda especificidade de projeto vive em `seed/` e `scripts/`; hotspots são hooks e referências a artefatos

### Melhorias
- **guidelines_review_passed**: adicionado ao `VALIDATOR_REGISTRY` — validator que lê `docs/guidelines-review.md` e extrai veredicto APPROVED/ITERATE; lista itens ❌ em caso de falha
- **decision node com file_exists**: condição `file_exists:<path>` suportada em nodes de decisão — avalia existência do arquivo em tempo de execução e propaga para `_reconcile_state_with_graph`
- **FT_UI_PROTOTYPE.yml v3.0.0**: processo agora é completamente genérico — decision node `ui.route` roteia para seed path (PRD existente) ou demand path (demanda bruta); `frontend/`, `Playwright`, `npm`, `1920x1080`, `localhost:4173` removidos dos prompts; env_setup usa `scripts/build.sh` e `scripts/serve.sh`
- **scripts/serve.sh**: encontra porta livre incrementalmente a partir de 4173, escreve URL em `.serve_url` — elimina conflito de porta entre ciclos paralelos
- **seed/tech_stack.md + ui_guidelines.md §9**: tech stack e specs de captura (viewport, ferramenta, telas obrigatórias) movidos para seed — LLM lê os arquivos em vez de ter specs hardcoded no YAML

---

## [v0.10.0] - 2026-04-07

### Novas funcionalidades
- **guidelines_review_passed**: validator LLM que lê `docs/guidelines-review.md` e extrai veredicto APPROVED/ITERATE — substitui `gate_ui_vscode_layout` (keyword scan insuficiente) por validação semântica via LLM
- **ui.proto.06.guidelines_review**: novo node no `FT_UI_PROTOTYPE.yml` — LLM revisa screenshots contra `seed/ui_guidelines.md` e produz relatório com veredicto
- **E2E em 3 sub-nodes**: `ui.e2e.01.build`, `ui.e2e.02.screenshots`, `ui.e2e.03.report` — quebra o node monolítico E2E (60 turns) em etapas menores para evitar timeout em Gemini/Codex

### Melhorias
- **Worktree como padrão em modo isolated**: cada ciclo roda em branch git própria (`worktrees/cycle-NN-<engine>`); fallback para `runs/cycle-NN/` se repo não tiver commits
- **seed/ no run dir**: `_next_run_dir` copia `seed/` do project root para o run dir — LLM encontra `seed/PRD.md`, `seed/ui_guidelines.md` sem erros de path
- **VS Code Layout no ui_guidelines.md**: seção "Layout Geral — VS Code Style" adicionada com diagrama ASCII e specs de Activity Bar, Drawer, Tabs, Terminal — referência obrigatória para o LLM

### Correções
- **_seed_from_previous usa allowlist**: em vez de exclude-list, copia apenas `frontend`, `backend`, `src`, `lib`, `tests`, `docs` — evita propagar `pyproject.toml`, `CHANGELOG.md`, `seed/`, `process/`, `node_modules/` entre ciclos
- **FT_UI_PROTOTYPE.yml**: referencia `seed/ui_guidelines.md` corretamente (estava `docs/ui_guidelines.md`)

---

## [v0.9.0] - 2026-04-07

### Novas funcionalidades
- **Ciclos paralelos**: `ft resume --cycle <cycle-NN>` permite retomar um ciclo específico sem conflitar com outros ciclos em execução simultânea
- **FT UI Prototype**: novo processo `FT_UI_PROTOTYPE.yml` para validação visual de interfaces sem TDD ou backend — scaffold → screenshots → E2E → stakeholder
- **gate_ui_vscode_layout**: validator que verifica se a UI implementou o layout VS Code (Activity Bar, Drawer, Tabs, Terminal) por keyword scan no `frontend/src/`
- **unique_screenshots**: validator MD5 que detecta screenshots duplicados copiados de ciclos anteriores — exige que cada screenshot seja único
- **Gemini CLI**: suporte ao Gemini como engine de delegação com seleção de modelo (`--gemini gemini-2.5-flash`)
- **human_gate**: tipo de node para checkpoints humanos obrigatórios; `ft approve` para liberar
- **Process Triage (BL-19)**: classifica demanda bruta, separa produto/processo, adapta YAML automaticamente
- **demand_coverage validator**: PRD deve cobrir todas as features da demanda bruta
- **engine/model por node no YAML**: `llm_engine` e `llm_model` configuráveis individualmente por node

### Melhorias
- **Seed de código entre ciclos**: `_seed_from_previous` copia artefatos do ciclo anterior excluindo screenshots, node_modules, dist — LLM parte de código existente
- **Flush de output**: `sys.stdout.reconfigure(line_buffering=True)` em `ft resume` corrige ausência de output ao redirecionar para arquivo
- **runs/.gitignore**: padrões específicos (não `*`) — permite que Codex/ripgrep vejam arquivos do run
- **ft resume** como comando principal (alias `continue`)

### Correções
- **env_setup não trava**: Popen+proc.wait com arquivos temporários — pipes de background não bloqueiam mais
- **gate_* em modo isolated**: validators booleanos usam `work_dir` corretamente quando frontend/ está em `runs/<N>/`
- **Seed exclui screenshots**: docs/screenshots/, docs/e2e/, docs/final/ não são mais copiados entre ciclos

### Outros
- Rename de runs para cycles: diretórios `runs/cycle-NN` (backward-compatible)
- UI guidelines para ft-studio (NODE_W, NODE_H, bezier, minimap, CSS namespace fts-*)
- KB: pitfalls P4 (routing sem URL), P5 (prd_review REJECTED), api_contract elimina mismatch

---

## [v0.8.29] - 2026-04-06

- feat(engine): colorized CLI output with step cards, type-colored badges, ANSI colors
- feat(cli): `-v`/`--verbose` flag to show LLM stream output in terminal
- feat(cli): `ft fix` command — user describes fix in natural language, LLM applies it
- feat(cli): friendly error display instead of raw tracebacks (`FT_DEBUG=1` for full)
- feat(engine): gate retry via LLM in mvp mode with configurable `max_gate_retries`
- feat(engine): smarter retries with error history (LLM told not to repeat failed approaches)
- feat(engine): autofix for missing gate outputs (infers from file_exists validator)
- feat(engine): irreversible errors skip retry, show plain-language explanation + alternatives
- feat(process): diagrams node now requires Mermaid format (no ASCII art)
- feat(process): visual regression check before MVP gate (`ft.visual_check` + `gate.visual_check`)
- feat(process): step progress shows `[X/total]` instead of raw node ID
- fix(engine): gate.e2e.browser crash on missing outputs + defensive validator
- fix(cli): `--from-project .` no longer crashes with SameFileError
- fix(engine): "pulando LLM" → "pulando etapa" (user-friendly message)

---

## [v0.8.28] - 2026-04-06

- feat(engine): BL-14 — environment hooks system (`ft/engine/hooks.py`)
- feat(engine): Hooks disparam em on_init, on_env_setup, on_node_start, on_node_end, on_gate_pass, on_gate_fail, on_deliver
- feat(engine): Scripts em `process/scripts/` executados via subprocess, bloqueiam se falhar
- feat(engine): BL-15 — RunMode isolated vs continuous via `run_mode` em `environment.yml`
- feat(cli): `ft run` em modo continuous usa `state/` na raiz e CycleManager avança ciclos
- feat(engine): `_find_latest_state` prioriza continuous > isolated > legacy

---

## [v0.8.27] - 2026-04-06

- feat(engine): BL-13 — estrutura de projeto V3 com `process/`, `docs/`, `runs/`
- feat(cli): `ft init` cria `process/`, `docs/`, `runs/` e `runs/.gitignore` automaticamente
- feat(cli): `ft run` cria subpasta `runs/<N>/` com state isolado por run
- feat(engine): `find_project_root()` detecta raiz por `process/` (não mais `project/state/`)
- feat(engine): state migrado de `project/state/` para `runs/<N>/state/` (descartável por run)
- feat(engine): docs migrados de `project/docs/` para `docs/` (conhecimento que evolui)
- feat(engine): fallback legado preservado para `project/state/` em projetos antigos

---

## [Unreleased]

- feat(engine): `ft-engine` agora permite escolher o executor LLM por comando com `--claude` ou `--codex`
- feat(engine): a escolha do executor é persistida em `project/state/engine_state.yml` (`llm_engine`) e reaplicada em `continue`, `approve`, `reject`, `status` e `run`
- feat(engine): delegação para Codex usa `codex exec --dangerously-bypass-approvals-and-sandbox`, mantendo execução autônoma sem prompts de permissão
- feat(engine): logs nativos do Codex agora são capturados em JSONL por step em `project/state/llm_logs/`, com ponteiro visível em `ft-engine status`
- fix(process): nodes podem declarar `write_scope` explícito no YAML; `ft.acceptance.01.cli` e `ft.audit.01.forgebase` agora podem corrigir código real em vez de só documentar bloqueios
- fix(process): `ft.acceptance.01.cli` virou implementação-first para `api/mixed`, reexecutando a aceitação após corrigir backend até ficar verde ou esgotar turns
- fix(process): projetos `interface_type: ui` agora pulam `ft.acceptance.01.cli` e seguem direto para `ft.smoke.01.cli_run`
- fix(process): `ft.prd.rewrite` agora cria baseline determinístico e bloqueia mudanças automáticas em `Hipotese`, `Visao` e `User Stories`; visão e escopo só mudam com decisão explícita do stakeholder
- fix(engine): reexecução bem-sucedida de node bloqueado agora limpa o bloqueio antes de avançar, permitindo recuperar gates e reviews sem reset manual

---

## [v0.8.26] - 2026-04-05

- feat(cli): instalação editable agora expõe `ft` como CLI do template/processo e `ft-engine` como CLI do motor determinístico, eliminando a duplicidade entre cópia global e código local
- feat(logging): run log da engine agora usa nome derivado do projeto (`<projeto>_log.md`) em vez de `servicemate_log.md` hardcoded
- fix: `ft.mdd.02.prd` agora recebe prompt explícito com seções obrigatórias e formato canônico de `### US-XX`, reduzindo falhas de validação no PRD
- fix: `ft.audit.01.forgebase` agora é implementação-first, com `gate_pulse_instrumented: true` no próprio nó e `max_turns: 80`
- fix: `ft.acceptance.01.cli` generaliza a limpeza de banco stale para `*.db` e `*.sqlite`, sem hardcode de nome de arquivo

---

## [v0.8.17] - 2026-04-05

- fix: `ft.audit.01.forgebase` — adicionado `gate_pulse_instrumented: true` nos validators do próprio nó; LLM agora falha no nó se não implementar os tracks (antes só falhava em `gate.audit`)
- fix: `ft.audit.01.forgebase` — prompt reestruturado para implementação-first (passos numerados, track-infra com código de referência, verificação final explícita)
- fix: `ft.audit.01.forgebase` — `max_turns: 80` adicionado
- fix: `ft.acceptance.01.cli` — REGRA DADOS generalizada: sem hardcode de `service_mate.db`; instrução genérica para deletar `*.db` / `*.sqlite`

---

## [v0.8.9] - 2026-04-03

- chore: validação E2E retroativa SM6 — `ft.handoff.02.plano_voo` executado sobre SM6; `plano_de_voo.md` gerado com veredicto ITERATE e 7 débitos (DT-01..DT-07) para SM7

---

## [v0.8.8] - 2026-04-03

- feat: `ft.handoff.02.plano_voo` — node que gera `project/docs/plano_de_voo.md` com veredicto ITERATE/RELEASE_CANDIDATE, débitos, correções obrigatórias e comandos de init para o próximo ciclo
- feat: `setup_env.sh --from-project` — copia plano_de_voo.md do ciclo anterior para o novo projeto; hyper-mode injeta automaticamente
- fix: `scan_kb_lessons()` agora extrai apenas seções "Lições para o Processo" (genéricas) — remove injeção de detalhes específicos de projeto

## [v0.8.7] - 2026-04-03

- feat: `ft.plan.05.api_contract` — node no planning que define contrato canônico de API (nomes de endpoints, idioma único) como fonte de verdade para frontend e backend
- feat: `ft.frontend.02.implement` e `ft.delivery.00.entrypoint` referenciam `api_contract.md` e exigem BrowserRouter com URL paths
- feat: `gate_kb_review` pitfall KB-P4 — detecta frontend sem BrowserRouter/Route path (deep links quebrados)
- feat: `gate_kb_review` pitfall KB-P5 — detecta `frontend-prd-review.md` com veredicto REJECTED não resolvido
- feat: `gate.planning` exige `api_contract.md` como artefato obrigatório

## [v0.8.6] - 2026-04-03

- fix: `StateManager.advance()` agora levanta `RuntimeError` quando estado está bloqueado — remove auto-unblock silencioso; 273 testes passando

## [v0.8.5] - 2026-04-03

- feat: `ft.plan.04.test_data` — node no planning que gera massa de dados realista para aceitação (project/docs/test_data.md)
- feat: `ft.acceptance.01.cli` — acceptance test CLI First: insere test_data via API e valida respostas antes de tocar no frontend
- feat: `gate.acceptance.cli` com `gate_acceptance_cli` — bloqueia se qualquer [FAIL] no relatório
- feat: `gate.planning` agora exige `test_data.md` como pré-requisito

## [v0.8.4] - 2026-04-02

- feat: `avaliacao_e2e_service_mate_6.md` — KB entry SM6 (nota 6.5/10): mismatch de rotas API (inglês vs. português), deep links sem URL change, Catálogo ausente
- feat: `ft.prd.rewrite` node obrigatório no handoff — PRD reescrito com aprendizados do ciclo (seções 8.5 e 8.6)
- feat: `ft.delivery.00.entrypoint` — node dedicado para criação do `main.py` HTTP antes da fase de delivery
- fix: `gate_server_starts` aceita `uvicorn` e `import app` como indicadores de HTTP server
- fix: `ft.prd.rewrite` adicionado `next: ft.handoff.01.specs` que estava faltando
- feat: `environment/` — pasta local gitignored para configurações de workspace (gateway.md)
- fix: gateway é opcional no `ft init` — remover aviso incorreto

## [v0.8.3] - 2026-04-02

- feat: `gate_kb_review` — gate final pre-liberação que verifica pitfalls P0 da KB (SM4: frontend ausente, SM5: HTTP server ausente, vite proxy ausente, interface_type inconsistente)
- feat: KB-mode — injeta lições de `kb/avaliacao_e2e_*.md` no prompt de nodes `build` e `retro`
- feat: `scan_kb_lessons` + `kb_lessons_prompt` em `stakeholder.py`
- feat: node `gate.kb_review` no YAML entre `gate.mvp` e `decision.mvp_frontend`
- feat: `avaliacao_e2e_service_mate_5.md` — KB entry do ciclo SM5 (nota 6/10)

## [v0.8.2] - 2026-04-02

- feat: activity logging com `_log_activity` + geração automática de `servicemate_log.md`
- feat: node `ft.frontend.03.prd_review` — valida conformidade do frontend com o PRD
- feat: processo V2 com 31 nodes

## [v0.8.1] - 2026-04-02

- fix: scaffold prompt agora especifica `index.html` na raiz do Vite (não em `public/`)
- fix: template obrigatório de `vite.config.js` com `rewrite` no proxy `/api`
- fix: `gate_frontend` valida presença de `frontend/index.html`
- fix: `--mvp` agora auto-aprova nodes com `requires_approval` (modo não-interativo)
- fix: diretriz de navegação explícita no prompt do `ft.frontend.02.implement`

## [v0.8.0] - 2026-04-02

### Novas funcionalidades

- **Frontend/PWA Support**: Processo V2 agora detecta `interface_type` do `tech_stack.md` e roteia projetos UI/mixed por `sprint-03-frontend` antes do TDD. Projetos `cli_only`/`api` pulam o frontend e vão direto para TDD.
- **sprint-03-frontend**: Três novos nodes — `ft.frontend.01.scaffold` (estrutura PWA + manifest.json), `ft.frontend.02.implement` (telas e componentes), `gate.frontend` (valida estrutura mínima de PWA).
- **decision nodes por interface_type**: `decision.interface_type` após planning e `decision.mvp_frontend` no handoff garantem que o gate MVP exige frontend apenas quando o projeto pede UI.
- **Validador `read_artifact`**: Lê valor via regex de qualquer arquivo e grava em `state.artifacts` para uso em decision nodes. Padrão `key=value` propagado automaticamente.
- **Validador `gate_frontend`**: Verifica estrutura mínima de PWA — `package.json`, `manifest.json` com campos obrigatórios (`name`, `start_url`, `display`), `frontend/src/`.
- **`_default` em branches**: Decision nodes suportam a chave especial `_default` como fallback quando nenhum branch explícito casa com o valor da condição.

### Melhorias

- **Processo V2**: 30 nodes, 10 sprints (era 23 nodes, 9 sprints). `ft.plan.02.tech_stack` agora exige `interface_type` no documento gerado.
- **`ValidationResult.artifacts`**: Validators com side-effects de state (como `read_artifact`) propagam artifacts de volta ao runner sem quebrar a interface dos demais validators.
- **`ft` binary (`~/.local/bin/ft`)**: Roteia subcomandos `continue/status/approve/reject/graph` para o engine CLI v0.7+, sem aviso de sincronia desnecessário.
- **`pip install -e .`**: `pyproject.toml` adicionado — `ft-engine` instalável como pacote Python com entry point `ft`.

### Correções

- **`advance()` auto-unblock**: Estado `blocked` é limpo automaticamente quando uma validação passa, eliminando crash `RuntimeError: Estado bloqueado` ao retomar após gate BLOCK.
- **Race condition no lock**: `StateManager.load(check_lock=True)` verifica se o PID do lock ainda está vivo e lança `StateLockError`, impedindo dois `ft continue` simultâneos.
- **Timeout do delegate**: Aumentado de 600s para 1800s — projetos reais com implementações complexas (ServiceMate) precisam de mais tempo no TDD green.
- **Gate `ft.mdd.03.validacao`**: Adicionado `outputs` ao node para que `min_lines` tenha caminho correto.

### Outros

- `pyproject.toml` adicionado ao repo — `pip install -e ".[dev]"` funciona
- Engine extraído para `~/dev/tools/ft-engine` como repo standalone
- `kb/` criado com avaliações de runs E2E (service_mate_4: 4/10, causa raiz e ação documentadas)
- 88 testes unitários do engine mantidos passando

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
