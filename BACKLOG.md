# Fast Track — Backlog de Melhorias

> Melhorias identificadas na análise do repositório. Ordenadas por impacto.

## Prioridade Crítica (pré-requisito)

### BL-00: Reorganização do repositório — separação estático vs. dinâmico
- **Problema**: `process/` misturava definição do processo (estática) com estado do projeto (dinâmico). CLI e tooling em `process/` contaminavam o repo do projeto. Sem `.gitignore`.
- **Solução**: Mover `ft_state.yml` para `project/state/`. Adicionar `.gitignore` que exclui `process/` (estático, vem do template). Processo pode ser atualizado sobrescrevendo `process/` sem perder estado.
- **Entrega**: `project/state/ft_state.yml`, `.gitignore`, referências atualizadas em 8 arquivos (75 refs a ft_state.yml, ~80 refs a process/ paths)
- **Status**: concluido

## Prioridade Alta (impacto imediato)

### BL-01: CLI unificada (ft.py) com validador
- **Problema**: ft_gatekeeper é "determinístico" mas só existe como prompt MD — nenhum código valida gates programaticamente
- **Solução**: CLI unificada `ft.py` data-driven (lê FAST_TRACK_PROCESS.yml e schemas em runtime). Subcommands: `init`, `validate state`, `validate artifacts`, `validate gate`, `tokens`, `self-check`
- **Entrega**: `process/fast_track/tools/ft.py`
- **Status**: concluido

### BL-02: JSON Schema para ft_state.yml
- **Problema**: `ft_state.yml` não tinha schema — agentes LLM podiam gravar campos inválidos e corromper estado
- **Solução**: JSON Schema com todos os enums, tipos e constraints do state. Consumido pelo validador em runtime.
- **Entrega**: `process/fast_track/schemas/ft_state.schema.json`
- **Status**: concluido

### BL-03: Scaffold Clean/Hex em src/
- **Problema**: `src/` estava vazio (.gitkeep) — cada projeto reinventava a estrutura
- **Solução**: Scaffold com `domain/`, `application/`, `infrastructure/`, `adapters/` conforme ForgeBase Rules. Integrado ao `ft.py init` (cria automaticamente se ausente).
- **Entrega**: `src/` populado + `ft.py init` verifica/cria scaffold
- **Status**: concluido

## Prioridade Média (qualidade do processo)

### BL-04: Consistência YAML ↔ MD do processo
- **Problema**: `FAST_TRACK_PROCESS.yml` e `.md` descrevem o mesmo processo separadamente — propenso a drift
- **Solução**: `ft.py generate ids` (gera FAST_TRACK_IDS.md do YAML) + `ft.py generate check` (valida consistência YAML vs. IDs, MD, Summary)
- **Entrega**: Subcommands `generate ids` e `generate check` no ft.py
- **Status**: concluido

### BL-05: Tiering das regras críticas
- **Problema**: 32 regras todas com mesmo peso — carga cognitiva alta para LLMs
- **Solução**: Reorganizado em 3 tiers: Tier 1 invioláveis (10), Tier 2 defaults (18), Tier 3 contextuais (4). Tiers com headings e descrições no SUMMARY_FOR_AGENTS.md
- **Entrega**: SUMMARY_FOR_AGENTS.md atualizado
- **Status**: concluido

### BL-06: Procedimento de rollback/recovery
- **Problema**: State machine só avançava — sem procedimento para cenários de bloqueio
- **Solução**: Tabela de recovery no SUMMARY_FOR_AGENTS.md com 6 cenários: sprint gate 3x, smoke trava, gate.delivery repetido, stakeholder ausente, estado corrompido, divergência processo/state
- **Entrega**: Seção "Recovery" no SUMMARY_FOR_AGENTS.md
- **Status**: concluido

### BL-07: Hyper-mode parcial
- **Problema**: Hyper-mode assumia PRD "abrangente" mas não tratava PRDs parciais
- **Solução**: Quando 5+ seções ausentes, ft_coach conduz discovery conversacional para seções ausentes (como no modo normal) em vez de questionário gigante. Documentado no processo e no prompt do ft_coach
- **Entrega**: FAST_TRACK_PROCESS.md + ft_coach/prompt.md atualizados
- **Status**: concluido

## Prioridade Baixa (housekeeping)

### BL-08: Runner E2E funcional
- **Problema**: Nenhum runner real — processo exigia "E2E CLI gate obrigatório" mas template não entregava isso
- **Solução**: `tests/e2e/run-all.sh` que roda pytest (unit+smoke) + tracks E2E por ciclo, com exit codes e output colorido
- **Entrega**: `tests/e2e/run-all.sh` (executável)
- **Status**: concluido

### BL-09: Eliminar git-dev.zip
- **Problema**: Blob binário opaco dificultava review e versionamento
- **Solução**: Extraído para `env/git-dev/` (4 arquivos: pre-commit-config, ruff.toml, install script, requirements). setup_env.sh atualizado para copiar em vez de unzip.
- **Entrega**: `env/git-dev/` + `setup_env.sh` atualizado + zip removido
- **Status**: concluido

### BL-10: Mover changelog para CHANGELOG.md
- **Problema**: ~230 linhas de changelog no README.md
- **Solução**: Movido para `CHANGELOG.md`. README mantém só versão atual + link.
- **Entrega**: `CHANGELOG.md` + README enxuto
- **Status**: concluido

### BL-11: Documento de anti-patterns
- **Problema**: Regras genéricas são menos efetivas que exemplos concretos de erros
- **Solução**: 10 anti-patterns com cenário, consequência e correção: mvp_status sem smoke, pular sprint expert gate, step IDs inventados, N/A em gates, paths errados, acceptance falsos, tasks de sprint futura, ft_manager auto-validando, hyper-mode com seções ausentes, ignorar cobertura
- **Entrega**: `process/fast_track/ANTI_PATTERNS.md`
- **Status**: concluido

---

## Evolução Arquitetural — Fast Track V3

### BL-12: Separação Base / Ambiente — Framework Architecture
- **Problema**: O processo concreto (`FAST_TRACK_PROCESS_V2.yml`) vive no repo central junto com o engine. Customizações de domínio (ForgeBase Pulse, screenshot review, SymGateway) poluem o framework genérico. Qualquer pessoa que use o Fast Track herda regras que são específicas da Symlabs.
- **Solução**: Separar em duas camadas:
  - **Base (framework)**: engine Python (`ft/`), conceitos (gate, nó, cycle, validator), schema do YAML, CLI. Instalável via `pip install ft-engine`. Não contém nenhum processo concreto.
  - **Ambiente (software house)**: processo YAML concreto, prompts, validators específicos, config de ambiente. Vive no repo do produto/ambiente, versionado com Git independente do framework.
- **Entrega**: Engine carrega processo de `./process/` (relativo ao projeto) em vez de path hardcoded no repo central. O `FAST_TRACK_PROCESS_V2.yml` atual migra para template/exemplo. Inclui `ft validate process` para validar schema, grafo e semântica do YAML customizado (ver `docs/V3_ARCHITECTURE.md` §9).
- **Status**: concluido
- **Prioridade**: Alta

### BL-13: Estrutura de Projeto — `process/`, `docs/`, `runs/`
- **Problema**: Cada run (SM1, SM2...SM13) cria uma pasta isolada no mesmo nível, poluindo o diretório. Não há separação entre conhecimento que evolui (PRD, retro, processo) e artefatos descartáveis (código gerado, logs).
- **Solução**: Estrutura padronizada por produto:
  ```
  service_mate/                  ← repo Git
    process/
      FAST_TRACK_PROCESS.yml     ← processo do domínio (evolui)
      environment.yml            ← config do ambiente (SymGateway, portas, etc.)
      scripts/                   ← hooks do ambiente
    docs/
      PRD.md                     ← evolui a cada run
      plano_de_voo.md            ← handoff do último run
      retro.md                   ← aprendizados acumulados
    runs/                        ← .gitignore (descartável)
      01/
      02/
  ```
- **Entrega**: `ft init` cria essa estrutura. `ft run` cria subpasta em `runs/` automaticamente. `ft.prd.rewrite` e `ft.handoff` escrevem de volta em `docs/`.
- **Status**: concluido
- **Prioridade**: Alta

### BL-14: Environment Hooks — Scripts executáveis por fase
- **Problema**: O engine implementa integrações específicas (SymGateway, port-registry) em código Python. Cada novo ambiente exige mudanças no engine. Lógica de Bash/infra não pertence ao framework.
- **Solução**: Sistema de hooks em `environment.yml`:
  ```yaml
  hooks:
    on_init:
      - ./scripts/register_gateway.sh
      - ./scripts/setup_ports.sh
    on_env_setup:
      - ./scripts/provision_claude.sh
    on_cycle_end:
      - ./scripts/notify_telegram.sh
    on_deliver:
      - ./scripts/deploy_staging.sh
  ```
  O engine executa `subprocess.run(script, check=True)` no momento do hook. Se falhar, bloqueia como um gate. O engine não sabe o que o script faz.
- **Entrega**: Hook runner no engine + `environment.yml` schema + momentos definidos (on_init, on_env_setup, on_node_start, on_node_end, on_gate_pass, on_gate_fail, on_cycle_end, on_deliver).
- **Status**: concluido
- **Prioridade**: Alta

### BL-15: RunMode — `isolated` vs `continuous`
- **Problema**: Hoje cada execução do processo é manual (criar pasta, copiar PRD, rodar `ft init`). O conceito de `CycleManager` existe no engine mas não é utilizado. Não há forma declarativa de escolher entre evoluir o mesmo código vs. começar do zero.
- **Solução**: Dois modos configuráveis em `environment.yml`:
  - **`isolated`** (padrão): cada `ft run` cria uma subpasta em `runs/N+1/`, gera código do zero a partir do PRD atual. Ao final, `ft.prd.rewrite` atualiza `docs/PRD.md`.
  - **`continuous`**: `ft run` opera no mesmo diretório, o `CycleManager` avança `cycle-01 → cycle-02`. Git com tags por cycle. Código evolui incrementalmente.
- **Entrega**: Flag `run_mode: isolated|continuous` em `environment.yml`. Engine adapta comportamento de `ft init`, `ft run` e `ft.end`.
- **Status**: concluido
- **Prioridade**: Média

### BL-16: Stakeholder Review — Validação interativa do produto
- **Problema**: O processo termina sem apresentar o produto ao stakeholder. O usuário não sabe como testar (qual comando, qual URL, quais endpoints). Feedback do playtest não é capturado sistematicamente.
- **Solução**: Node `ft.stakeholder_review` entre `gate.mvp` e `ft.end` que:
  1. Detecta `interface_type` do `tech_stack.md`
  2. Para UI: sobe o servidor e apresenta o link (ex: http://localhost:8000)
  3. Para CLI: mostra o comando para testar
  4. Para API: mostra os endpoints principais
  5. Coleta feedback do stakeholder em linguagem natural
  6. Anota o feedback como novas User Stories no PRD para o próximo ciclo
  7. Não corrige o protótipo — o próximo ciclo resolve
- **Entrega**: Node novo no YAML + lógica no runner para subir servidor e aguardar input
- **Status**: proposto
- **Prioridade**: Alta

### BL-17: Análise Crítica — Sugestões do LLM ao final do ciclo
- **Problema**: O stakeholder só vê o que ele próprio percebe. O LLM tem contexto completo do ciclo (PRD, retro, screenshots, código, logs) e pode identificar gaps, oportunidades e débitos que o stakeholder não vislumbrou.
- **Solução**: Node `ft.critical_analysis` após stakeholder review que:
  1. LLM analisa todos os artefatos do ciclo
  2. Apresenta lista numerada de sugestões de melhoria (UX, produto, técnico, processo)
  3. Stakeholder escolhe por número quais quer incluir no próximo ciclo
  4. Escolhidas são anotadas como User Stories no PRD
  5. Rejeitadas são descartadas sem impacto
- **Entrega**: Node novo no YAML com prompt consultivo/estratégico
- **Status**: proposto
- **Prioridade**: Alta

### BL-18: Código gerado dentro de runs/ no modo isolated
- **Problema**: No modo isolated, o LLM delega com `cwd=project_root` e escreve `src/`, `frontend/`, `tests/`, `main.py`, `pyproject.toml` na raiz do projeto. Isso polui o diretório com artefatos de um run específico, causando ambiguidade entre o que é do processo (docs, process) e o que é do run.
- **Solução**: No modo isolated, o `delegate_to_llm` deve usar `cwd=runs/<N>/` em vez de `project_root`. O LLM gera código dentro do run dir. Apenas `docs/` e `process/` ficam na raiz (são compartilhados entre ciclos).
- **Entrega**: Alterar `_run_llm_step` no runner para usar o run dir como CWD quando `run_mode=isolated`. Adaptar paths de `allowed_paths` para serem relativos ao run dir.
- **Status**: concluido
- **Prioridade**: Alta

### BL-19: Process Triage — Classificação da hipótese e adaptação do processo
- **Problema**: O usuário mistura requisitos de produto e de processo na hipótese. O engine não distingue e roda o processo padrão, que pode não atender o que o usuário quer (ex: "prototipar UI antes de codar" roda num processo que vai direto pro TDD).
- **Solução**: Node `ft.process_triage` como primeiro passo de qualquer run:
  1. LLM lê a hipótese e classifica o que é **produto** vs **processo**
  2. Extrai os requisitos de processo (ex: "protótipo primeiro", "sem backend", "mock data")
  3. Compara com o processo base (YAML atual)
  4. Se o processo não atende → **para e conversa** com o stakeholder:
     - Mostra o que detectou
     - Faz perguntas de esclarecimento
     - Propõe adaptações no YAML
     - Stakeholder aprova → LLM adapta o YAML → `ft validate` → continua
     - Stakeholder rejeita → roda o processo padrão
  5. Separa a parte de produto e salva como hipótese limpa em `docs/hipotese.md`
- **Entrega**: Node novo no início do grafo + lógica de adaptação do YAML via LLM + validação automática do YAML adaptado
- **Status**: concluido
- **Prioridade**: Crítica

### BL-20: Worktrees fora do repositório — eliminar `runs/`
- **Problema**: `runs/` dentro do repo polui o git status, permite que LLMs editem ciclos vizinhos e cria acoplamento artificial entre o código do processo e os artefatos gerados. Múltiplos ciclos paralelos são frágeis quando todos vivem no mesmo diretório.
- **Solução**: `ft run` usa `git worktree add <path-externo> -b cycle-NN-<engine>` em vez de criar pasta em `runs/`. Os worktrees vivem fora do repo (ex: `~/.ft/worktrees/<projeto>/cycle-NN/`). O repo base fica limpo. Cada ciclo tem isolamento real. `ft runs` lista worktrees ativos via `git worktree list`. `ft status` localiza o worktree pelo branch name.
- **Entrega**: Alterar `ft run` para usar `git worktree add` externo. Remover criação de `runs/`. Adaptar `ft status`, `ft runs`, `ft continue` para operar por path do worktree. Atualizar `engine_state.yml` com path absoluto do worktree.
- **Status**: proposto
- **Prioridade**: Alta

### BL-21: Nova estrutura base do projeto
- **Problema**: `seed/` é jargão interno do processo — não intuitivo para quem abre o repo. Múltiplos YAMLs em `process/` causam ambiguidade. `runs/` polui o repo (ver BL-20). Não há separação clara entre documentação do produto e arquivos de referência de código.
- **Solução**: Estrutura base padronizada, agnóstica de processo:
  ```
  ├── docs/
  │   ├── PRD.md
  │   ├── TECH_STACK.md
  │   ├── UI_GUIDELINES.md    (placeholder — opcional por processo)
  │   └── code_snippets/      (referências de código para o LLM, ex: graph_routing.js)
  ├── process/
  │   ├── environment.yml
  │   ├── process.yml         (único YAML — sem ambiguidade)
  │   ├── structure.yml       (contrato da estrutura — ver BL-22)
  │   └── scripts/
  │       └── setup_env.sh
  └── src/
  ```
  `ft init` cria essa estrutura. Template agnóstico — não presume UI, backend ou qualquer domínio.
- **Entrega**: Novo template base. `ft init` atualizado. Migração do ft-studio como projeto piloto.
- **Status**: proposto
- **Prioridade**: Alta

### BL-22: `process/structure.yml` — contrato da estrutura materializado pelo ambiente
- **Problema**: A estrutura de diretórios e os arquivos obrigatórios estão espalhados entre `tech_stack.md`, `agent_rules.md` e a cabeça do LLM. O LLM cria o scaffold do zero a cada ciclo com variações não intencionais. Arquivos de referência (como `graph_routing.js`) precisam ser copiados manualmente ou via instrução no prompt.
- **Solução**: `process/structure.yml` declara: diretórios a criar, arquivos obrigatórios (validados ao final) e arquivos a copiar de `docs/code_snippets/` para o worktree antes do LLM começar. O `setup_env.sh` lê esse YAML e materializa tudo deterministicamente no `on_init`. O LLM recebe a estrutura pronta e só precisa preencher os arquivos.
  ```yaml
  directories: [src/components, src/utils, ...]
  required_files: [src/App.svelte, package.json, ...]
  copy_from_docs:
    - from: code_snippets/graph_routing.js
      to: src/utils/layout.js
  ```
- **Entrega**: Schema de `structure.yml`. Script `create_structure.sh` que lê e materializa. Hook no `environment.yml`. Validator que confere `required_files` ao final do ciclo.
- **Status**: proposto
- **Prioridade**: Média

### BL-23: Handoff genérico entre ciclos — substituir `plano_de_voo.md`
- **Problema**: `plano_de_voo.md` é um artefato específico do processo FT_UI_PROTOTYPE — não é conceito base do framework. O mecanismo de passar contexto entre ciclos está acoplado ao processo, não ao engine. O formato (markdown narrativo) não é estruturado o suficiente para o engine consumir programaticamente.
- **Solução**: O engine gera automaticamente um `handoff.yml` estruturado ao final de cada ciclo (no worktree), contendo: US implementadas, US pendentes, itens críticos validados, porta do servidor, paths relevantes, notas do LLM. O próximo ciclo lê esse `handoff.yml` via `--from-cycle` flag. O `plano_de_voo.md` narrativo vira responsabilidade do processo específico (opcional), não do framework base.
- **Entrega**: Geração automática de `handoff.yml` no `on_cycle_end`. Flag `--from-cycle <path>` no `ft run`. Remoção do `plano_de_voo.md` do template base.
- **Status**: proposto
- **Prioridade**: Média

---

## Resumo

| ID | Título | Prioridade | Status |
|----|--------|------------|--------|
| BL-00 | Reorganização estático/dinâmico | Crítica | concluido |
| BL-01 | CLI unificada (ft.py) | Alta | concluido |
| BL-02 | JSON Schema ft_state | Alta | concluido |
| BL-03 | Scaffold Clean/Hex | Alta | concluido |
| BL-04 | Consistência YAML ↔ MD | Média | concluido |
| BL-05 | Tiering das regras | Média | concluido |
| BL-06 | Rollback/recovery | Média | concluido |
| BL-07 | Hyper-mode parcial | Média | concluido |
| BL-08 | Runner E2E funcional | Baixa | concluido |
| BL-09 | Eliminar git-dev.zip | Baixa | concluido |
| BL-10 | Mover changelog | Baixa | concluido |
| BL-11 | Anti-patterns doc | Baixa | concluido |
| BL-12 | Separação Base / Ambiente | Alta | concluido |
| BL-13 | Estrutura `process/`, `docs/`, `runs/` | Alta | concluido |
| BL-14 | Environment Hooks | Alta | concluido |
| BL-15 | RunMode isolated/continuous | Média | concluido |
| BL-16 | Stakeholder Review | Alta | proposto |
| BL-17 | Análise Crítica do LLM | Alta | proposto |
| BL-18 | Código em runs/ (modo isolated) | Alta | concluido |
| BL-19 | Process Triage (classificar demanda) | Crítica | concluido |
| BL-20 | Worktrees fora do repositório | Alta | proposto |
| BL-21 | Nova estrutura base do projeto | Alta | proposto |
| BL-22 | process/structure.yml — contrato da estrutura | Média | proposto |
| BL-23 | Handoff genérico entre ciclos | Média | proposto |
