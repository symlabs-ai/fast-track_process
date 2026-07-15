# ft engine — Guia de Uso

Motor determinístico de processos para solo dev + AI. Python controla o fluxo;
o LLM executa apenas tarefas de construção.

## Conceito

```text
YAML de processo → ft engine → LLM constrói → Python valida → grafo avança
```

O engine lê um processo YAML, delega nodes às CLIs configuradas (`claude`,
`codex`, `gemini` ou `opencode`) e valida os artefatos com verificações
determinísticas. O LLM não escolhe processo, ciclo ou próxima transição.

O contrato V3 separa:

- **workspace**: base comum criada por `ft init`, sem processo associado;
- **template**: bundle global materializado copy-once em um fork local;
- **ciclo**: execução imutavelmente ligada a um template local e a uma worktree;
- **runtime**: estado, locks e logs sob `$FT_HOME`, fora do Git do produto.

## Instalação

```bash
pip install -e .
ft --help
```

O repositório do engine não é um projeto FT. Crie projetos fora dele; use
`FT_ALLOW_ENGINE_REPO=1` apenas para manutenção do próprio engine.

## Iniciar o workspace

```bash
ft init meu-projeto
cd meu-projeto
```

O argumento de diretório é opcional (`.` por padrão). O comando:

1. garante um repositório Git com HEAD;
2. cria `.ft/manifest.yml`, `.ft/.gitignore` e o playbook comum;
3. não escolhe nem materializa template;
4. não cria `docs/`, `src/`, worktree ou estado de ciclo.

Em um workspace saudável, repetir `ft init` é idempotente.

### Diagnóstico e reparo

```bash
ft init . --check   # somente leitura
ft init . --fix     # reparo explícito
```

`--check` relata cada invariante e nunca escreve. `--fix` restaura arquivos
comuns ausentes, reconstrói o catálogo a partir de `.ft/process/*/process.yml` e
corrige metadados inequivocamente recuperáveis. Ele não sobrescreve forks locais
nem históricos. Manifestos corrompidos substituídos recebem backup externo sob
`$FT_HOME`; ambiguidades são recusadas com instrução manual.

Manifesto inicial:

```yaml
schema_version: 3
processes: {}
```

Não há seletor de processo principal. O mapa `processes` é catálogo, não fila nem
ordem de preferência.

## Abrir um ciclo

Toda nova execução exige um template:

```bash
ft run . --template mvp-builder
ft run . --template feature --request "Adicionar busca por telefone"
ft run . --template feature --input demanda.md
ft run . --template bug --request "Terminal duplica o eco do input"
ft run . --template tweak --request "Mudar o botão Salvar para azul"
```

`ft run` é o único entrypoint para todos os templates. Não há comando específico
por categoria de trabalho nem opção para fornecer um YAML arbitrário.

### Resolução local-first

Para `--template T`, o engine:

1. usa `.ft/process/T/process.yml` quando `T` já está registrado e válido;
2. caso contrário, copia o bundle global para `.ft/process/T/` e o registra;
3. fixa path e digest locais no estado do novo ciclo;
4. preserva o fork local em execuções futuras.

O catálogo `templates/` nunca é executado diretamente. A materialização ocorre
uma única vez e não copia seeds genéricos para `docs/` ou `src/`.

Exemplo após duas materializações:

```yaml
schema_version: 3
processes:
  feature:
    path: .ft/process/feature/process.yml
    template: feature
    source_digest: sha256:...
    base_digest: sha256:...
  tweak:
    path: .ft/process/tweak/process.yml
    template: tweak
    source_digest: sha256:...
    base_digest: sha256:...
defaults:
  llm_engine: codex
```

### Política de entrada

Cada template declara se uma demanda é exigida, opcional ou proibida. A CLI
oferece duas formas uniformes:

- `--request "texto"`: demanda curta inline;
- `--input arquivo`: conteúdo lido de um arquivo.

A política é validada antes da criação do ciclo. Quando uma demanda é aceita, o
engine a transporta para a worktree sem modificar silenciosamente as fontes do
checkout principal.

## Concorrência entre ciclos

Múltiplos ciclos ativos são suportados por padrão:

```bash
# Terminal A
ft run . --template feature --request "Busca por telefone" --auto

# Terminal B
ft run . --template tweak --request "Reduzir padding" --auto
```

Cada chamada reserva atomicamente id, branch, worktree e state. Não há bloqueio
global de “execução ativa”. Um lock curto cobre apenas preparação compartilhada,
como reconciliação do manifesto e materialização; runners trabalham em paralelo
depois disso.

`ft close` usa um lock separado por projeto para serializar merge e arquivamento
no checkout principal. Esperar pelo close não bloqueia a execução de outros
ciclos.

Isso é diferente de `ft run --template <T> --parallel`: essa flag habilita
fan-out de nodes de um único ciclo quando o YAML possui `parallel_group`.

## Seleção de ciclo

Comandos que alteram ou conduzem um ciclo seguem uma regra comum:

- zero ciclos aplicáveis: erro;
- exatamente um: pode ser inferido;
- dois ou mais: `--cycle <id>` é obrigatório e o erro lista as opções.

O engine nunca escolhe pela data de criação. A regra vale para `continue`,
`graph`, `log`, `approve`, `reject`, `retry`, `fix`, `explore`, `abort`,
`cancel`, `process-candidates` e `close`. `ft status` é a exceção somente
leitura: sem `--cycle`, ele imprime um bloco rotulado para cada ciclo aberto;
com `--cycle`, mostra apenas o selecionado. O mesmo fan-out vale para
`ft status --report`.

```bash
ft runs
ft status --cycle cycle-07 --full
ft status --cycle cycle-07 --report
ft graph --cycle cycle-08
ft continue --cycle cycle-07 --auto
ft close --cycle cycle-08
```

## Comandos de condução

```bash
# Novo ciclo
ft run . --template <T> [--request "..."] [--input arquivo]
ft run . --template <T> --auto
ft run . --template <T> --auto --bypass-human-gates

# Avanço
ft continue --cycle <id>
ft continue --cycle <id> --sprint
ft continue --cycle <id> --auto

# Inspeção
ft status --cycle <id>
ft status --cycle <id> --full
ft status --cycle <id> --report
ft graph --cycle <id>
ft log --cycle <id>
ft runs

# Gates e recuperação
ft approve "nota opcional" --cycle <id>
ft reject "motivo acionável" --cycle <id>
ft reject "motivo" --no-retry --cycle <id>
ft retry --cycle <id>
ft fix "instrução" --cycle <id>
ft explore "pedido livre" --cycle <id>
ft abort --cycle <id>
ft cancel "motivo" --cycle <id>

# Encerramento
ft process-candidates --cycle <id>
ft close --cycle <id>
```

`--auto` avança até human gate, MVP ou BLOCK. Ele não pula human gates;
`--bypass-human-gates` autoriza o LLM a decidir nesses pontos.

`ft status --report` usa o trace append-only do ciclo. O relatório distingue
wall time real de tempo ativo de LLM, validators, espera humana, fila e close;
tentativas e ordinais sobrevivem a reinícios do runner. Métricas que o provider
não fornece aparecem como `—`/`null`, nunca como zero inventado. No close, o
resumo derivado é arquivado em `.ft/cycles/<cycle>/run-report.json`; logs crus
permanecem fora do Git.

## Seleção do executor LLM

Escolha o executor no início ou ao continuar um ciclo:

```bash
ft run . --template mvp-builder --codex
ft run . --template feature --request "Busca" --codex gpt-5.6-sol --effort max
ft run . --template tweak --request "Ajuste" --opencode
ft continue --cycle cycle-07 --claude --sprint
```

Ou defina o default por ambiente:

```bash
export FT_LLM_ENGINE=opencode
```

Quando `--opencode` é usado sem modelo explícito, o default é
`pgx/zai-org_glm-4.7-flash`.

Defaults persistentes ficam em `defaults.llm_engine`, `defaults.llm_model` e
`defaults.llm_effort` no manifesto. Durante uma execução, a combinação fica
fixada em `$FT_HOME/worktrees/<projeto>/<cycle>/state/`, de modo que comandos
posteriores preservam a escolha.

`ft llm-capabilities --json` executa probes limitados e paralelos das CLIs
instaladas. `ft llm-defaults --agent ... --model ... --effort ...` valida uma
combinação com probe fresco e atualiza atomicamente o manifesto. O effort
`default` remove o override e devolve a escolha ao provider.

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `FT_HOME` | Runtime, worktrees, locks e backups; default `~/.ft` |
| `FT_ALLOW_ENGINE_REPO` | Libera manutenção dentro do repo do engine |
| `FT_SKIP_HEALTH_CHECK` | Pula health check da API no início do run |
| `FT_LLM_ENGINE` | Executor default (`claude`, `codex`, `gemini`, `opencode`) |
| `FT_LLM_EFFORT` | Effort herdado quando node, flag e state não definem valor |
| `FT_LLM_EXECUTOR_TIMEOUT` | Timeout geral de cada turno delegado, em segundos |
| `FT_CODEX_EXECUTOR_TIMEOUT` | Override do timeout de turnos Codex |
| `FT_CODEX_REASONING_EFFORT` | Override explícito do reasoning do Codex |
| `FT_OPENCODE_CONTEXT_LIMIT` / `FT_OPENCODE_CONTEXT_WINDOW` | Janela anunciada ao OpenCode |
| `FT_OPENCODE_OUTPUT_LIMIT` / `FT_OPENCODE_MAX_OUTPUT` | Limite de saída do OpenCode |
| `FT_OPENCODE_PROVIDER_TIMEOUT` / `FT_OPENCODE_TIMEOUT` | Timeout total do provider, em ms |
| `FT_OPENCODE_CHUNK_TIMEOUT` / `FT_OPENCODE_PROVIDER_CHUNK_TIMEOUT` | Timeout entre chunks, em ms |
| `FT_OPENCODE_HEADER_TIMEOUT` / `FT_OPENCODE_PROVIDER_HEADER_TIMEOUT` | Timeout de headers, em ms |
| `FT_OPENCODE_SANDBOX` | Sandbox `bwrap`; worktree read-only e outputs/write_scope graváveis |
| `FT_OPENCODE_DENY_EDIT_TOOLS` | Opt-in do modo legado sem ferramentas nativas de edição |
| `FT_OPENCODE_BUNDLE_MODE` | Opt-in de materialização por bundle XML |
| `FT_OPENCODE_SCRIPT_MODE` | Opt-in de materialização por script Bash |
| `FT_OPENCODE_DEBUG` | Logs detalhados da CLI OpenCode |
| `FT_FEATURE_SHARED_CACHE` | Habilita o cache compartilhado experimental do template feature |
| `FT_FEATURE_VALIDATION_HERMETIC` | Declara explicitamente que a validação feature é hermética; exigido pelo cache compartilhado |
| `FT_FEATURE_SHARED_CACHE_TTL_SECONDS` | TTL, em segundos, do cache compartilhado feature |
| `FT_FEATURE_EXTERNAL_DEPENDENCIES` | Dependências externas declaradas que entram no fingerprint feature |

## Governança de melhorias do processo

No `mvp-builder`, `ft.handoff.05.process_evolve` gera
`docs/process-improvements.md` e `docs/process-improvements.yml`. Cada achado
recebe ID `PI-NNN` e uma classificação:

- `local`: pertence ao fork local daquele projeto;
- `global_candidate`: merece revisão para o catálogo do engine;
- `rejected`: foi analisado e não deve ser aplicado.

Um candidato global precisa ser independente de domínio, configurável,
retrocompatível e verificado no ciclo. O ciclo nunca escreve no checkout do
engine. Depois de aplicar e testar a mudança global, registre a disposição:

```bash
ft process-candidates PI-001 --cycle cycle-07 \
  --status promoted \
  --reason "Aplicado e validado pela suíte do engine" \
  --reference "commit abc123 templates/mvp-builder/process.yml"

ft process-candidates PI-002 --cycle cycle-07 \
  --status deferred --reason "Precisa de outro ciclo real"
```

O close recusa candidatos globais pendentes. Os relatórios e decisões são
arquivados em `.ft/cycles/<cycle>/`.

## Evolução de processo

`ft evolve` melhora forks ou templates sem avançar nodes. Como pode haver vários
ciclos, forneça explicitamente `--cycle` quando quiser usar evidências de uma
execução:

```bash
ft evolve --project --cycle cycle-07
ft evolve --global --cycle cycle-07
ft evolve "reduzir retries no build" --project --cycle cycle-07
ft evolve --project --cycle cycle-07 --dry-run
```

O playbook roda em workspace descartável sob `$FT_HOME`, valida todo
`process.yml` staged e mostra o diff antes da aplicação. Mudanças no fork local
afetam somente ciclos futuros; promoção global continua sendo decisão explícita
do mantenedor.

## Formato do YAML de processo

```yaml
id: meu_processo
version: "1.0.0"
title: "Meu Processo"

# A política de entrada é validada antes da criação do ciclo.
input_policy:
  required: true
  destination: docs/feature-request.md
  prompt: "Descreva a demanda a implementar"

artifact_policy:
  canonical: [docs/PRD.md, docs/PROJECT_BACKLOG.md, docs/FEATURES.md]
  cycle: [docs/task_list.md, docs/acceptance-report.md, docs/handoff.md]

nodes:
  - id: step.01.discovery
    type: discovery
    title: "Capturar requisitos"
    executor: llm_coach
    sprint: sprint-01
    outputs:
      - docs/requisitos.md
    requires_approval: true
    validators:
      - file_exists: docs/requisitos.md
      - min_lines: 20
      - has_sections: [Problema, Solucao]
    next: step.02.prd

  - id: step.02.prd
    type: document
    title: "Escrever PRD"
    executor: llm_coach
    sprint: sprint-01
    outputs: [docs/PRD.md]
    validators:
      - file_exists: docs/PRD.md
      - min_user_stories: 3
    next: gate.01

  - id: gate.01
    type: gate
    title: "Gate de qualidade"
    executor: python
    sprint: sprint-01
    validators:
      - file_exists: docs/PRD.md
      - tests_pass: true
    next: step.end

  - id: step.end
    type: end
    title: "Processo concluído"
```

## Regras de design de processo

O YAML é pura orquestração. Ele define:

- sequência e transições;
- executor de cada node;
- validadores determinísticos;
- política de entrada e de artefatos;
- referências a arquivos que o LLM deve ler.

Ele não deve hardcodar design, regras de negócio, tech stack, nome de produto ou
contexto de um projeto. Essa especificidade fica em fontes visíveis como:

| Artefato | Conteúdo |
|---|---|
| `docs/PRD.md` | visão, user stories e requisitos |
| `docs/PROJECT_BACKLOG.md` | mudanças desejadas e decisões |
| `docs/FEATURES.md` | capacidades entregues e evidências |
| `docs/ui_criteria.md` | telas, componentes e estados |
| `docs/tech_stack.md` | frameworks, linguagens e dependências |
| `.ft/cycles/<cycle>/task_list.md` | quebra técnica arquivada |

Prompts referenciam caminhos em vez de duplicar conteúdo:

```yaml
prompt: |
  Implemente a interface do projeto.
  Leia obrigatoriamente docs/PRD.md, docs/tech_stack.md e
  docs/ui_criteria.md. Siga os contratos dessas fontes.
```

Hotspots legítimos incluem hooks de shell (`env_setup`, `on_init`), prompts que
referenciam arquivos e validadores genéricos. Um processo bem desenhado funciona
para qualquer projeto do mesmo tipo trocando apenas as fontes de conhecimento.

## Tipos de node

| Tipo | Executor | Descrição |
|---|---|---|
| `discovery` | `llm_coach` | Captura hipótese/contexto; suporta hyper-mode |
| `document` | `llm_coach` | Produz documento Markdown |
| `build` | `llm_coder` | Implementa código |
| `test_red` | `llm_coder` | Escreve teste que deve falhar |
| `test_green` | `llm_coder` | Implementa para o teste passar |
| `refactor` | `llm_coder` | Refatora mantendo testes verdes |
| `gate` | `python` | Validação pura Python |
| `human_gate` | humano/LLM | Decisão explícita de stakeholder |
| `decision` | `python` | Branch condicional pelo state |
| `review` | `llm_coder` | Veredicto estruturado |
| `end` | — | Marca conclusão |

## Validadores disponíveis

### Artefatos

| Validador | Uso | Descrição |
|---|---|---|
| `file_exists` | `file_exists: path/to/file.md` | Arquivo existe |
| `min_lines` | `min_lines: 20` | Mínimo de linhas no primeiro output |
| `has_sections` | `has_sections: [A, B]` | Seções presentes |
| `min_user_stories` | `min_user_stories: 3` | Mínimo de histórias `### US-` |
| `demand_coverage` | mapa de paths | Cobertura determinística da demanda |
| `project_backlog_valid` | path do backlog | IDs, prioridade e status válidos |
| `task_list_references_backlog` | paths | Task list referencia backlog |
| `backlog_pending_decisions` | path | P0/P1 sem decisão são recusados |
| `backlog_referenced_decisions` | paths/campo | Valida PBs selecionados pelo ciclo |
| `features_catalog_valid` | paths | Catálogo e origens entregues válidos |
| `implemented_backlog_covered_by_features` | paths | Entregas têm `FEAT-*` correspondente |

`command_succeeds` aceita `command`, `timeout` e `resume_command`. O alternativo
é usado ao recuperar uma delegação órfã; se falhar, o comando completo roda uma
única vez:

```yaml
validators:
  - command_succeeds:
      command: bash scripts/product.sh full --record docs/validation.json
      resume_command: bash scripts/product.sh verify docs/validation.json
      timeout: 300
```

Validadores são agregados por padrão. Para gates com checks caros, o node pode
parar na primeira falha; um validator isolado também pode interromper a lista:

```yaml
validation_mode: fail_fast
validators:
  - file_exists: docs/contract.md
  - command_succeeds:
      command: make build
      stop_on_failure: true
  - command_succeeds: make test
```

Use `aggregate` quando o diagnóstico conjunto tiver mais valor que a latência.
`fail_fast` é decisão do node, não uma política global implícita.

Nodes LLM podem combinar deadline por chamada e orçamento cumulativo por
episódio. O primeiro limita também retries internos do provider; o segundo
persiste chamadas e tempo entre retomadas do runner:

```yaml
llm_timeout_seconds: 900
llm_episode: implementation
llm_episode_budget_seconds: 1800
llm_episode_max_calls: 2
```

Um decision pode iniciar novo episódio apenas para rejeições semânticas
declaradas:

```yaml
episode_restart:
  implementation: implementation
  scope: implementation
```

Ao esgotar o orçamento, o engine pausa, grava um checkpoint compacto no state e
preserva o diff; não inicia silenciosamente outra chamada.

### Testes, código e gates

| Grupo | Validadores |
|---|---|
| Testes | `tests_pass`, `tests_fail`, `coverage_min`, `coverage_per_file`, `tests_exist` |
| Código | `lint_clean`, `format_check`, `no_todo_fixme` |
| Gates | `gate_delivery`, `gate_smoke`, `gate_mvp` |
| Review | `no_large_files`, `no_print_statements`, `changed_files_have_tests` |

## TDD loop

```yaml
- id: tdd.red
  type: test_red
  executor: llm_coder
  outputs: [tests/test_feature.py]
  validators: [{tests_fail: true}]
  next: tdd.green

- id: tdd.green
  type: test_green
  executor: llm_coder
  outputs: [src/feature.py]
  validators: [{tests_pass: true}]
  next: tdd.refactor

- id: tdd.refactor
  type: refactor
  executor: llm_coder
  outputs: [src/feature.py]
  validators: [{tests_pass: true}, {lint_clean: true}]
  next: gate.delivery
```

O engine faz auto-commit após PASS: `red:` para testes, `green:` para
implementação e `refactor:` para refatoração.

## Sprint workflow

```bash
ft run . --template mvp-builder
ft continue --cycle cycle-01 --sprint
ft approve --cycle cycle-01
ft continue --cycle cycle-01 --sprint

# ou
ft continue --cycle cycle-01 --auto
```

O sprint report é gerado ao cruzar boundaries. Quando documentos já existem em
`docs/`, o hyper-mode enriquece nodes `discovery` e `document` com esse contexto.

## Templates do catálogo

### `mvp-builder`

Processo completo de MVP. Materialize e execute com:

```bash
ft run . --template mvp-builder --auto
```

Com `--parallel`, nodes do mesmo `parallel_group` rodam em worktrees internas e
fazem fan-in validado. Somente nodes LLM com outputs disjuntos podem participar;
gates, decisions e dependências cruzadas são recusados pelo validador do grafo.
`--max-parallel N` limita os workers. Sem a flag, os grupos permanecem
sequenciais.

### `feature`

Implementa uma capacidade em produto existente, com elucidação de escopo,
aprovação, implementação, validação, evidência, review e aceite:

```bash
ft run . --template feature --request "Adicionar busca por telefone" --codex
ft run . --template feature --input demanda.md --codex
```

Cada demanda deve citar exatamente um PB preexistente; ciclos simultâneos usam
PBs distintos. Para uma capacidade nova, o processo reserva o FEAT definitivo
sob lock curto. O state fixa path e digest do fork local.

Código/testes, validação completa, evidência referencial e review semântica são
nodes diferentes. A review produz rota estruturada (`approved`,
`implementation`, `evidence` ou `scope`) e o decision node invalida o progresso
posterior quando volta no grafo. A implementação possui deadline por chamada e
orçamento cumulativo por episódio; um hard stop preserva diff e artefatos.

Baseline attestation e implementation receipt são separados. `ensure` verifica
primeiro o receipt local; cache compartilhado só existe como experimento opt-in
para validação declarada hermética. O reconcile propõe YAML, o engine valida os
IDs permitidos e aplica os documentos canônicos deterministicamente. Entradas
novas de changelog começam com `#FEAT`.

### `bug`

Correção focal para defeito reproduzível. Exige diagnóstico, teste RED,
correção mínima, o mesmo teste GREEN, build/test e aceite:

```bash
ft run . --template bug --request "Terminal duplica o eco do input" --codex
```

Use `feature` quando houver comportamento novo, contrato, auth/security,
migração, dados, dependência, infraestrutura ou mudança transversal. Entradas de
changelog começam com `#BUG`.

### `tweak`

Mudança pequena e de baixo risco, com implementação única, check focal, build
curto e aceite:

```bash
ft run . --template tweak --request "Mudar o botão Salvar para azul" --codex
```

Não executa discovery completo, review independente ou E2E full. Limites de
arquivos, linhas, patch e áreas de risco impedem que um ajuste pequeno vire uma
mudança transversal; nesses casos, abra outro ciclo com `feature`.

### Outros

`base` fornece grafo mínimo; `ft-ui-prototype` cobre prototipagem de UI;
`symgateway` demonstra integração externa opt-in; `fast-track-v2` preserva o
processo histórico. Todos usam o mesmo comando de run e viram forks locais.

## Encerramento e artefatos

```bash
ft close --cycle cycle-07 --merge full
ft close --cycle cycle-08 --merge docs
ft close --cycle cycle-09 --merge selective --merge-paths "src/a tests/a"
ft close --cycle cycle-10 --keep-worktree
```

O lock de close serializa merges. Se somente CHANGELOG, PROJECT_BACKLOG e
FEATURES conflitarem de forma aditiva e inequívoca, o resolvedor canônico os
reconcilia; qualquer conflito ambíguo ou fora desses documentos permanece
manual. A política do processo mantém artefatos canônicos em `docs/` e arquiva
relatórios específicos em `.ft/cycles/<cycle>/`. Estado e logs brutos nunca são
mergeados.

Depois do merge, reinstale dependências alteradas, limpe caches antigos,
reinicie serviços no checkout promovido, confira as rotas principais e exerça a
capacidade entregue antes de demonstrá-la.

## Migração V2 → V3

Layouts com `process/`, bundle flat ou manifesto anterior exigem migração
explícita e sem runtime em mutação:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

O preflight valida grafo, contenção, colisões e symlinks antes de escrever. A
migração preserva todos os processos e ciclos, converte o manifesto para schema
V3 e elimina o seletor default sem promover substituto. Históricos em
`docs/archive/` são importados; runtime legado recebe backup inativo em
`$FT_HOME/migrations/`. Conteúdo em `.ft/cycles/` nunca é reescrito.

Use `ft init --check`/`--fix` para saúde de um workspace já V3; reparo não é
migração.

## Estrutura do engine e do projeto

```text
ft/
  engine/
    graph.py          # YAML → DAG
    state.py          # escrita do engine_state.yml
    runner.py         # loop determinístico
    delegate.py       # executores LLM
    git_ops.py        # commits após PASS
    parallel.py       # fan-out/fan-in de nodes
    validators/
  cli/main.py         # CLI pública
  project/            # bootstrap, diagnóstico e reparo
  templates/          # resolução/materialização local-first
  runs/               # locks, alocação e seleção de ciclos

$FT_HOME/worktrees/<projeto>/<cycle>/
  state/engine_state.yml

<projeto>/.ft/
  manifest.yml
  process/<template>/process.yml
  cycles/<cycle>/

templates/<template>/process.yml
```

## Troubleshooting

**`ft: command not found`**

```bash
pip install -e .
# ou
python -m ft.cli.main
```

**Workspace inconsistente**

```bash
ft init . --check
ft init . --fix
```

**Comando ambíguo**

```bash
ft runs
ft status --cycle <id>
```

**BLOCKED após validação**

```bash
ft status --cycle <id>
ft retry --cycle <id>
```

**Artefato rejeitado**

```bash
ft reject "feedback específico" --cycle <id>
ft reject "motivo" --no-retry --cycle <id>
```

**LLM não encontrado**

```bash
claude --version
codex --version
gemini --version
opencode --version
```
