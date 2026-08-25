# Como Construir um Processo no Fast Track

Guia de autoria de `process.yml` — escrito para o **agente** que vai redigir um
processo novo, não para quem só quer executá-lo. Para uso do CLI, ciclos e
comandos, veja `docs/ft_engine_usage.md`.

---

## 1. Antes de escrever uma linha

### 1.1 O contrato mental

```text
YAML de processo → engine (Python) → delega node ao LLM → valida artefato → avança o grafo
```

Três regras que não se negociam:

1. **O YAML é orquestração pura.** Ele diz *quando* algo acontece, *quem*
   executa e *como se prova* que deu certo. Ele nunca diz *o que* o produto é.
2. **O LLM não decide o fluxo.** Ele não escolhe o próximo node, não pula gate,
   não declara sucesso. Só produz artefatos; a engine julga.
3. **Todo avanço tem prova determinística.** Se um passo não pode ser validado
   por um programa, ele não é um node de produção — é um `human_gate`.

Se um trecho do seu YAML só funciona para um produto específico, ele está no
lugar errado. Especificidade vive em `docs/PRD.md`, `docs/TECH_STACK.md`,
`docs/ui_criteria.md`, `docs/api_contract.md`, no backlog e nos scripts do
bundle. O `ft lint-process` existe justamente para caçar esse vazamento.

### 1.2 As seis perguntas de projeto

Responda antes de abrir o editor. Cada resposta trava decisões estruturais.

| Pergunta | Consequência no YAML |
|---|---|
| O processo **cria** um produto ou **evolui** um existente? | `execution_policy.project_role`: `builder` / `maintenance` / `neutral` |
| Ele toca código de produto? | `requires_worktree`, `requires_initialized_project`, `close_policy.merge` |
| Qual é a **entrada** do ciclo? | `input_policy` (destino e obrigatoriedade) |
| Quais artefatos **sobrevivem** ao ciclo e quais são descartáveis? | `artifact_policy.canonical` vs `.cycle` |
| Quantas decisões humanas são **realmente** necessárias? | número de `human_gate` |
| Qual é a **evidência mínima** de que a entrega funciona? | os validators dos gates finais |

### 1.3 O erro mais caro

Processos ruins quase sempre falham do mesmo jeito: **muitos nodes LLM em
sequência sem gate determinístico entre eles.** O LLM acumula deriva, o erro só
aparece no fim, e o retrabalho custa o ciclo inteiro.

A regra prática: *toda delegação LLM que produz artefato precisa de pelo menos
um validator que falharia se o artefato fosse plausível-porém-errado.*
`file_exists` sozinho não é validação — é presença.

---

## 2. Anatomia do bundle

Um processo não é só um arquivo. É um diretório em `templates/<nome>/`:

```text
templates/meu-processo/
├── process.yml          # obrigatório — o grafo e as políticas
├── environment.yml      # opcional — modo de execução, retries, hooks
├── README.md            # recomendado — o contrato humano do processo
├── process-flow.md      # recomendado — o diagrama do fluxo
├── experts/             # opcional — perfis especialistas referenciados por nodes LLM
│   ├── code_reviewer.md
│   └── marketing_analyst.md
├── scripts/             # validadores determinísticos próprios do processo
│   ├── validate_<nome>.py
│   ├── product.sh
│   └── serve.sh
└── examples/            # opcional — entradas de exemplo
```

### Materialização copy-once

O template global **nunca é executado**. Na primeira `ft run . --template
meu-processo`, o engine copia o bundle inteiro para
`.ft/process/meu-processo/` no projeto e executa apenas essa cópia local. Isso
tem duas consequências que você precisa internalizar:

- **Todo path de script no YAML aponta para a cópia local**, sempre com o
  prefixo `.ft/process/<nome>/`:
  ```yaml
  - command_succeeds: "python .ft/process/meu-processo/scripts/validate.py implementation"
  ```
- **Editar o template global não afeta ciclos já materializados.** Correções
  em um ciclo em andamento vão para o fork local; melhorias duráveis voltam
  para o template global (via `ft evolve`).

Restrições do bundle: sem symlinks em lugar nenhum, sem YAML ambíguo (só um
`process.yml`, `environment.yml` é a única exceção), scripts sempre relativos e
dentro do bundle.

---

## 3. O cabeçalho: políticas do processo

Tudo acima de `nodes:` são metadados lidos pela engine (`graph.meta`). Cada
bloco resolve um problema específico. Declare só o que o processo usa.

### Referência do cabeçalho: contrato, metadado e extensão

O loader preserva em `graph.meta` **toda** chave de primeiro nível que não seja
`nodes`. Isso não torna uma chave automaticamente suportada: somente as chaves
consumidas pelo catálogo, runner, close ou validador produzem comportamento.
Typos no cabeçalho podem, portanto, sobreviver silenciosamente e não fazer nada.

| Tag | Tipo / default | Regra | Efeito na engine |
|---|---|---|---|
| `id` | string, sem default | Convenção: `snake_case`; é a identidade humana do processo. | Metadado e identificação em relatórios; não substitui o nome do template. |
| `version` | string, sem default | Faça bump em mudança comportamental. | Versão comunicada para humanos e consumers do bundle; o loader não impõe semver. |
| `title` | string, opcional | Descrição curta do processo. | Metadado de apresentação; não confundir com `nodes[].title`. |
| `nodes` | lista, obrigatória | Cada item precisa de `id`; o grafo exige exatamente um `type: end`, sem órfãos ou destinos inexistentes. | É o grafo executável. |
| `execution_policy` | mapping, obrigatório em template V3 executável | `entrypoint: run` e `template` igual ao diretório são validados pelo catálogo. | Autoriza `ft run` e declara ciclo, worktree, role e merge. |
| `input_policy` | mapping, opcional | Se `required: true`, exige `destination` seguro e `prompt`. | Valida e materializa a entrada antes de criar o ciclo. |
| `artifact_policy` | mapping, opcional | Use `canonical` e/ou `cycle` com paths relativos. | Decide merge/arquivamento dos artefatos no close. |
| `close_policy` | mapping, opcional | `backlog.mode` é `global`, `referenced` ou `none`; `referenced` exige `references_path`. | Define a prova e o modo de merge no close. |
| `correction_policy` | mapping, opcional | IDs citados devem existir e manter a cadeia de revisão segura. | Controla reentrada, retries e reviews obrigatórios após correção. |
| `session_policy` | mapping, opcional | Schema fechado: `mode: sprint`, providers `claude`/`codex`, `initial_plan`, `parallel_strategy: fork`, `recovery: rehydrate`. | Habilita sessões persistentes e plano interno. |
| `parallel_policy` | mapping, opcional | `planner_timeout_seconds` é inteiro positivo ou `null`; `rate_limit_respawns` é inteiro ≥ 0. | Configura o planejador de `--parallel`. |
| `batch_policy` | mapping, obrigatório se houver `type: batch` | Exige os quatro paths canônicos; limites positivos; `protected_paths` em lista. | Contrato do fan-out dinâmico de lanes. |
| `commit_policy` | mapping, opcional | `verify_hooks` é booleano. | Quando `false`, commits feitos pela engine usam `--no-verify`. |
| `product_engineering_policy` | mapping de extensão, opcional | Não possui schema/consumo genérico no core atual. | Documenta a intenção do template; não cria controle de execução sozinho. |
| `validation_policy` | mapping de extensão, opcional | Não possui schema/consumo genérico no core atual. | Documenta a política de validação do template; os validators e `.ft/project.yml` continuam autoritativos. |
| `sprints` (no topo) | metadado de extensão, opcional | Não confundir com `nodes[].sprint`. | O core agrupa sprints pelos nodes; esta chave de topo não altera a execução. |
| outra chave de topo | qualquer valor | Só declare quando existir consumidor explícito no bundle/engine. | É preservada em `graph.meta`, mas é ignorada pelo core se ninguém a consultar. |

As seções seguintes detalham os subcampos dos blocos contratuais. A tabela
acima também resolve uma ambiguidade comum: `product_engineering_policy`,
`validation_policy` e `sprints` podem ser úteis como documentação local, mas
não são mecanismos de enforcement do engine.

### 3.1 Identidade (obrigatório)

```yaml
id: meu_processo          # snake_case, único no registro de processos
version: "1.0.0"          # string, entre aspas; bump a cada mudança semântica
title: "Meu Processo — Descrição Curta"
```

### 3.2 `execution_policy` — obrigatório para todo template executável

Sem este bloco o catálogo recusa o template.

```yaml
execution_policy:
  entrypoint: run                  # sempre "run" em templates V3
  template: meu-processo           # DEVE ser idêntico ao nome do diretório
  materialization: copy_once
  runtime_source: local_only
  requires_initialized_project: true
  requires_worktree: true
  local_process_path: .ft/process/meu-processo/process.yml
  merge_command: ft close --merge full
  project_role: maintenance
  allowed_project_phases:
    - maintenance
```

| Campo | Efeito |
|---|---|
| `entrypoint` | `run` — o único entrypoint V3 |
| `template` | validado contra o nome do diretório; divergência = template recusado |
| `materialization` | política declarada do bundle; em V3 use `copy_once` |
| `runtime_source` | política declarada da origem do processo; em V3 use `local_only` |
| `requires_initialized_project` | `false` só para processos que não tocam produto (pesquisa, documentação) |
| `requires_worktree` | `true` isola o ciclo em worktree Git própria; `false` executa no checkout |
| `local_process_path` | path canônico da cópia materializada |
| `merge_command` | comando humano recomendado para promover o ciclo; não substitui `close_policy.merge` |
| `project_role` | `builder` (cria produto), `maintenance` (evolui), `neutral` (ambos) |
| `allowed_project_phases` | **deve casar exatamente** com o role: builder→`[building]`, maintenance→`[maintenance]`, neutral→`[building, maintenance]` |

`project_role` e `allowed_project_phases` são obrigatórios em conjunto: declarar
um sem o outro é erro de validação.

### 3.3 `input_policy` — a demanda que abre o ciclo

```yaml
input_policy:
  required: true
  destination: docs/feature-request.md
  prompt: "Descreva a feature a implementar"
```

Validado **antes** de criar o ciclo. `destination` precisa ser path relativo
POSIX, seguro, e não pode escrever em metadados internos (`.ft/`). Se
`required: true`, `destination` e `prompt` viram obrigatórios.

Ad case — processo sem entrada: um processo de auditoria periódica que só lê o
repositório declara `input_policy` ausente ou `required: false`.

### 3.4 `artifact_policy` — o que sobrevive ao ciclo

```yaml
artifact_policy:
  canonical:                       # duráveis: merge de volta ao produto
    - docs/PRD.md
    - docs/PROJECT_BACKLOG.md
    - docs/FEATURES.md
    - CHANGELOG.md
  cycle:                           # descartáveis: arquivados em .ft/cycles/<id>/
    - docs/feature-request.md
    - docs/feature-review.md
    - docs/implementation-report.md
```

Regra de decisão: **se um ciclo futuro precisa ler o artefato, ele é
`canonical`. Se ele só prova o que aconteceu neste ciclo, é `cycle`.**

Um path listado nos dois vence como canonical (não é arquivado). `CHANGELOG.md`,
`docs/PROJECT_BACKLOG.md` e `docs/FEATURES.md` têm merge three-way
determinístico próprio quando dois ciclos os alteram em paralelo — mantenha-os
canonical e no formato de tabela esperado.

### 3.5 `close_policy` — o que o encerramento exige

```yaml
close_policy:
  backlog:
    mode: referenced               # global | referenced | none
    references_path: docs/feature.md
    reference_field: backlog_item
    required_count: 1
    accepted_statuses: [done, accepted]
  features_catalog: required
  merge: full                      # full | docs | selective | none
```

| `backlog.mode` | Quando usar |
|---|---|
| `global` | processo que decide o backlog inteiro no fim (builder de MVP) |
| `referenced` | processo que resolve apenas os itens que ele declarou (feature, bug) |
| `none` | processo que não movimenta backlog (tweak, pesquisa) |

`mode: referenced` exige `references_path`. Sem isso o processo é recusado.

### 3.6 `correction_policy` — o que acontece depois de uma rejeição

```yaml
correction_policy:
  follow_graph_after_retry: true
  scope_rejection_restarts_at: feature.discovery
  acceptance_rejection_restarts_at: feature.implement
  mandatory_after_implementation:
    - feature.pre_review
    - feature.review
    - feature.acceptance
```

`mandatory_after_implementation` é o campo mais importante e o mais esquecido:
lista os nodes que **nunca podem ser pulados por pre-seed**. Sem ele, uma
reentrada no grafo encontra o relatório de review antigo, os validators passam,
e a engine avança sem auditar a correção. É a diferença entre um loop de
correção honesto e um teatro.

### 3.7 `session_policy`, `commit_policy`, `parallel_policy`

```yaml
session_policy:                    # sessões LLM persistentes entre nodes
  mode: sprint                     # único valor aceito
  providers: [claude, codex]       # claude e/ou codex, sem duplicatas
  initial_plan: internal           # internal | disabled
  parallel_strategy: fork          # único valor aceito
  recovery: rehydrate              # único valor aceito

commit_policy:
  verify_hooks: false              # false pula hooks de commit do projeto

parallel_policy:
  planner_timeout_seconds: 120     # inteiro positivo ou null
  rate_limit_respawns: 0           # inteiro >= 0
```

`session_policy` só aceita os valores acima — o validador é fechado de
propósito. `commit_policy.verify_hooks: false` é legítimo em processos curtos
que já rodam seus próprios checks; em processos longos, deixe os hooks ligados.

### 3.8 `batch_policy` — obrigatório se existir node `type: batch`

```yaml
batch_policy:
  schema_version: 1
  plan_path: docs/mvp-batch-plan.yml
  request_path: docs/demanda.md
  report_path: docs/mvp-batch-report.md
  foundation_report_path: docs/mvp-batch-foundation.md
  evidence_root: docs/batches/meu-processo
  min_lanes: 2
  max_lanes: 8
  default_max_parallel: 2
  max_acceptance_criteria_per_lane: 6
  protected_paths:                 # nunca escritos por uma lane
    - .ft/
    - docs/PRD.md
    - docs/TECH_STACK.md
```

Os quatro `*_path` são obrigatórios e não vazios. Os inteiros são opcionais mas,
se presentes, positivos.

### 3.9 `environment.yml` — política de execução e retries

Arquivo separado, ao lado do `process.yml`, resolvido a partir da **cópia
local**:

```yaml
run_mode: isolated       # isolated (default) | continuous
max_node_retries: 1      # retries automáticos de node LLM após validator falhar
max_gate_retries: 0      # retries de gate com auto-fix
max_auto_fix: 0          # tentativas de auto-fix por LLM (só no modo --auto/mvp)

hooks:
  on_init: [scripts/setup.sh]
  on_node_start: []
  on_node_end: []
  on_gate_pass: []
  on_gate_fail: [scripts/collect_diagnostics.sh]
  on_deliver: [scripts/notify.sh]
```

Hooks recebem `cwd = project_root`, timeout de 300s, e **exit != 0 bloqueia como
um gate**. O script precisa estar dentro do bundle local; path fora dele é
recusado.

Escolha dos limites: processos curtos e focais usam `0` em tudo — falhar e parar
para decisão humana é mais barato que um retry cego. Processos longos de
construção toleram `max_node_retries: 1`.

---

## 4. Anatomia de um node

Campos reconhecidos pelo loader. Nada fora desta lista é lido (chaves
desconhecidas são silenciosamente ignoradas — cuidado com typos).

### Referência completa de tags genéricas de node

<!-- process-yml-node-reference:start -->

Esta é a tabela de compatibilidade do YAML atual, derivada de `Node` no loader.
"Todos" significa que o loader aceita a tag em qualquer node; isso não quer
dizer que ela tenha efeito útil fora do tipo indicado. Defaults são os do loader
antes da validação semântica.

| Tag | Tipo / default | Válida em | Restrição | Efeito |
|---|---|---|---|---|
| `id` | string, obrigatório | todos | único e não vazio | identifica o node e seus receipts/logs. |
| `type` | string; `build` | todos | um de `batch`, `build`, `decision`, `discovery`, `document`, `end`, `exploration`, `gate`, `human_gate`, `refactor`, `retro`, `review`, `sync`, `test_green`, `test_red` | seleciona a semântica de execução. |
| `title` | string; default = `id` | todos | precisa resultar não vazio | nome exibido no status/card. |
| `description` | string ou ausente | todos | texto curto para o usuário | explica o step; não roteia. |
| `executor` | string; `python` | todos, exceto irrelevante em `end` | YAML aceita `python`, `human`, `claude`, `codex`, `gemini`, `opencode`; aliases LLM viram `llm_*` | escolhe Python, humano ou provider LLM. |
| `sprint` | string ou ausente | todos | use ID estável | agrupa status e delimita `ft continue --sprint`. |
| `next` | ID ou ausente | não terminais | destino deve existir; node não terminal precisa de `next` ou `branches` | aresta normal do grafo. |
| `branches` | mapping ou ausente | `decision` | todos os destinos devem existir; use `_default` para fallback | roteamento determinístico da decisão. Fora de `decision`, não roteia. |
| `condition` | string ou ausente | `decision` | chave de estado ou forma especial aceita pelo runner | valor consultado para escolher `branches`. |
| `outputs` | lista; `[]` | todos | obrigatória para `parallel_group`; fortemente recomendada para LLM/build | declara produtos e infere o escopo de escrita. |
| `write_scope` | lista; `[]` | todos | paths relativos permitidos; se ausente, deriva de `outputs` e, sem ambos, cai no fallback amplo `project/`, `docs/` | limita onde a delegação pode escrever. |
| `prompt` | string ou ausente | LLM | referencie fontes/outputs sem inserir contexto efêmero no processo | instrução concreta enviada ao modelo. |
| `expert` | ID ou ausente | executor LLM | requer `experts/<id>.md` válido e versionado | acrescenta orientação especialista auditável. |
| `max_turns` | inteiro ou ausente | LLM | recomendado; default depende do tipo/executor | teto de turns da delegação. |
| `llm_engine` | string ou ausente | LLM | override por node | substitui o provider do run para esse node. |
| `llm_model` | string ou ausente | LLM | use modelo disponível no provider escolhido | substitui o modelo do run. |
| `llm_effort` | string ou ausente | LLM | provider-specific | substitui reasoning effort do run. |
| `codex_auth` | `chatgpt` ou ausente | Codex | qualquer outro valor falha; exige executor/engine Codex | usa rota built-in OpenAI/ChatGPT para o node inteiro. |
| `env_setup` | lista; `[]` | delegações que precisam de preflight | comandos determinísticos e seguros | executa antes da delegação. |
| `env_teardown` | lista; `[]` | delegações com recursos temporários | pareie com `env_setup` | executa após a delegação para encerrar recursos. |
| `validators` | lista; `[]` | todos | nome e argumentos precisam existir no registry | prova determinística antes de avançar. |
| `validation_mode` | `aggregate` | não `end` | `aggregate` ou `fail_fast` | agrega todos os diagnósticos ou para no primeiro. |
| `on_fail` | mapping ou ausente | nodes com falha roteável | `goto` deve existir; pode informar `human_gate` | rota de recuperação depois de validator/review falhar. |
| `requires_approval` | booleano; `false` | todos | use quando um node aprovado precisa parar para humano | pausa depois de passar. |
| `no_pre_seed` | booleano; `false` | todos | use em trabalho/review que deve sempre ser refeito | impede reaproveitar outputs já válidos. |
| `allow_pre_seed` | booleano; `false` | principalmente build/review/test | declare deliberadamente | permite reaproveitar checkpoint em tipos fail-closed. |
| `preserve_outputs_on_reentry` | booleano; `false` | nodes `no_pre_seed` reentrantes | booleano | preserva draft existente em vez de apagá-lo antes de refazer. |
| `fix_review` | ID ou ausente | node de correção | destino deve ser `review` e alcançável por cadeia linear de `next` | força auditoria focal depois do fix. |
| `optional` | booleano; `false` | todos | use somente para trabalho dispensável | permite `ft explore --skip`. |
| `reject_next` | ID ou ausente | `human_gate` | destino deve existir | rota quando o stakeholder rejeita. |
| `approval_message_required` | booleano; `false` | `human_gate` | exige texto no `ft approve` | impede aprovação silenciosa. |
| `decision_context` | mapping ou ausente | `human_gate` | só `decision`, `why_now`, `review_paths`, `checklist`, `limitations`, `approve_effect`, `reject_effect`; paths seguros | pacote de decisão exibido ao humano. |
| `bypass_prompt` | string ou ausente | `human_gate` | só usado com `--bypass-human-gates` | atribui explicitamente a decisão delegada ao LLM. |
| `bypass_reject_when` | `{path, pattern}` ou ausente | `human_gate` | path seguro e regex válida | em bypass, escolhe `reject_next` quando a evidência recomenda rejeição. |
| `parallel_group` | string ou ausente | LLM não-controlador | membros têm outputs disjuntos; requer `--parallel` | executa fan-out estático em worktrees. |
| `llm_timeout_seconds` | inteiro positivo ou ausente | LLM | não aceita bool/zero/negativo | sugestão da janela de inatividade, não deadline absoluto. |
| `llm_episode` | string ou ausente | LLM | obrigatório se houver orçamento de episódio | agrupa chamadas relacionadas para telemetria/limite. |
| `llm_episode_budget_seconds` | inteiro positivo ou ausente | LLM | exige `llm_episode` | meta cumulativa de telemetria; não interrompe trabalho. |
| `llm_episode_max_calls` | inteiro positivo ou ausente | LLM | exige `llm_episode` | limite estrutural de chamadas no episódio. |
| `episode_restart` | mapping branch→episode ou ausente | `decision` | cada chave deve ser branch existente e valor texto não vazio | reinicia o episódio LLM da rota escolhida. |
| `review_route_path` | path ou ausente | `review` | fora de review é erro | deixa a decisão seguinte interpretar o veredito estruturado. |
| `hyper_mode_docs` | lista de paths ou ausente | LLM | paths Markdown não vazios; incompatível com `context_profile` | documentos de contexto resumido. |
| `hyper_mode_full_docs` | lista de paths ou ausente | LLM | mesma restrição | documentos injetados integralmente. |
| `hyper_mode_preview_lines` | inteiro ≥ 0 ou ausente | LLM | incompatível com `context_profile` | limite de linhas do contexto resumido. |
| `hyper_mode_full_max_lines` | inteiro ≥ 0 ou ausente | LLM | incompatível com `context_profile` | limite de linhas do contexto integral. |
| `context_profile` | string ou ausente | LLM | precisa existir em `ft/engine/context_profiles.py`; incompatível com HyperMode | seleciona contexto determinístico e limitado. |

<!-- process-yml-node-reference:end -->

Para evitar que esta tabela se torne uma segunda implementação, toda alteração
no loader ou no validador que adicione/remova uma tag deve atualizar esta seção
na mesma mudança. O comando de verificação continua sendo
`ft lint-process <path>`; a referência explica a tag, mas não substitui a
validação do grafo.

### 4.1 Identidade e fluxo

| Campo | Tipo | Nota |
|---|---|---|
| `id` | string | obrigatório, único. Convenção: `<processo>.<etapa>` ou `<fase>.<nn>.<nome>` |
| `type` | string | obrigatório na prática (default `build`) |
| `title` | string | opcional; sem ele o loader usa `id` no card do step |
| `description` | string | texto amigável exibido ao usuário quando o step inicia |
| `executor` | string | `python`, `human`, `claude`, `codex`, `gemini`, `opencode` |
| `sprint` | string | agrupa nodes; `ft continue --sprint` para na fronteira |
| `next` | string | próximo node |
| `branches` | map | só em `decision` (e no roteamento de `run_route`) |
| `condition` | string | chave de estado avaliada pelo `decision` |
| `optional` | bool | node pulável via `ft explore --skip` |

### 4.2 Contrato de saída

| Campo | Efeito |
|---|---|
| `outputs` | lista de paths que o node produz. **Também define o escopo de escrita default** e o path inferido por validators posicionais |
| `write_scope` | escopo de escrita explícito; quando presente, substitui o derivado de `outputs` |

Sem `write_scope` e sem `outputs`, o fallback é `["project/", "docs/"]` — amplo
demais para qualquer node sério. **Sempre declare `outputs`.** Em nodes de
review e documento, declare também `write_scope` idêntico aos outputs: isso
impede que um revisor "conserte" o produto em vez de reportar.

### 4.3 Delegação ao LLM

| Campo | Uso |
|---|---|
| `prompt` | instrução do node. Referencia paths, não duplica conteúdo |
| `expert` | id de um perfil em `experts/<id>.md`; somente em executor LLM |
| `max_turns` | teto de turnos. Sem ele, o default por tipo é aplicado (30 na maioria, 12 em `retro`) |
| `llm_engine` / `llm_model` / `llm_effort` | override por node do provider global do run |
| `codex_auth` | exceção explícita por node; único valor aceito: `chatgpt` |
| `llm_timeout_seconds` | janela de **inatividade** (não deadline) que dispara sonda |
| `llm_episode` | nome de uma sequência semanticamente relacionada |
| `llm_episode_budget_seconds` | meta de telemetria — não interrompe trabalho |
| `llm_episode_max_calls` | limite **estrutural** de chamadas no episódio |
| `context_profile` | perfil determinístico e limitado de contexto |
| `hyper_mode_docs` / `hyper_mode_full_docs` / `hyper_mode_preview_lines` / `hyper_mode_full_max_lines` | carga de docs por node |

`context_profile` e os campos `hyper_mode_*` são **mutuamente exclusivos** — usar
os dois no mesmo node é erro de validação. Perfis são registrados no engine
(`ft/engine/context_profiles.py`); um nome não registrado é recusado. Se seu
processo precisa de um perfil novo, ele é uma mudança de engine, não de YAML.

Orçamento de episódio só é válido com `llm_episode` declarado.

#### Exceção Codex para ferramentas built-in do ChatGPT

Por default, nodes Codex herdam `FT_CODEX_PROFILE`. Quando uma capability
built-in só existe no provider OpenAI autenticado pelo ChatGPT, o processo pode
declarar a exceção estreita:

```yaml
- id: visual.mockups
  type: build
  executor: codex
  llm_engine: codex
  llm_model: gpt-5.6-sol
  llm_effort: max
  codex_auth: chatgpt
  env_setup:
    - >-
        python3 -c "import subprocess;
        status=subprocess.check_output(['codex','login','status'],text=True,stderr=subprocess.STDOUT);
        assert 'Logged in using ChatGPT' in status;
        features=subprocess.check_output(['codex','features','list'],text=True);
        assert any(line.split()[:1]==['image_generation'] and line.split()[-1:] == ['true'] for line in features.splitlines())"
  prompt: |
    Invoque `$imagegen` e use sua ferramenta built-in `image_gen`.
```

O FT ignora `FT_CODEX_PROFILE` somente nessa delegação e fixa
`model_provider="openai"` com `forced_login_method="chatgpt"`. A exceção cobre
o node inteiro — raciocínio textual, tools e pixels — portanto essa execução
não atravessa nem recebe a auditoria do SymGateway. Retries preservam a mesma
rota; uma sessão criada no gateway nunca é retomada no modo direto.

Use o campo apenas quando a ferramenta built-in for requisito do artefato.
Ausência do login ou da capability deve bloquear o node, sem fallback por API,
renderer ou bypass ad hoc. Experts podem pedir `$imagegen`, mas não concedem a
capability; a rota é responsabilidade explícita do processo.

#### Experts — especialização reutilizável

Um expert define **como o agente aborda um assunto**; o `prompt` do node define
**o trabalho concreto daquele passo**. O arquivo fica no próprio bundle para
que a materialização copy-once também pine sua versão:

```markdown
---
id: code_reviewer
name: Code Reviewer
description: Revisa código com foco em correção, regressões e evidência.
version: 2
tags: [engineering, quality]
---
Comece pelos critérios de aceitação, pela baseline informada e pelo diff real.
Não aprove sem evidência atual nem modifique o produto durante a revisão.
```

O contrato é fail-closed:

- o nome deve ser `experts/<id>.md` e o `id` usa `snake_case` minúsculo;
- `id`, `name`, `description` e `version` são obrigatórios;
- o corpo Markdown após o frontmatter é o prompt especialista e não pode ser vazio;
- `tags` é opcional e aceita uma lista de textos;
- symlinks, expert ausente e uso por executor não-LLM invalidam o processo.

Toda mudança comportamental no corpo do expert exige bump de `version`. O
SHA-256 identifica os bytes exatos; a versão comunica intencionalmente a mudança
para humanos e consumers do catálogo.

Referencie o expert pelo id:

```yaml
- id: review.code
  type: review
  title: "Revisar implementação"
  executor: codex
  expert: code_reviewer
  no_pre_seed: true
  prompt: |
    Revise a implementação corrente contra docs/PRD.md.
    Grave o parecer em docs/code-review.md com os headings Veredito e Evidências.
  outputs: [docs/code-review.md]
  write_scope: [docs/code-review.md]
  validators:
    - file_exists: docs/code-review.md
    - has_sections:
        path: docs/code-review.md
        sections: [Veredito, Evidências]
  next: end
```

O expert não escolhe provider/modelo, não concede ferramentas e não expande
paths de escrita. Em conflito, prevalecem a segurança da engine, o node, seus
outputs e validadores. Na delegação, a engine registra id, versão e SHA-256 no
run log. Esses metadados não ocupam o prompt do modelo; reviews recebem também
o `base_commit` exato do ciclo em um bloco de contexto auditável.

Um prompt especialista continua sendo orientação, não gate. Pareceres que
decidem o fluxo devem combinar `no_pre_seed`, `write_scope` restrito,
`has_sections`, `document_quality` e um validator semântico ou receipt
estruturado. O catálogo base usa `expert_review_report_valid` para recusar
veredito conclusivo sem baseline e evidência observada.

Use `ft experts --template <T>` para inspecionar o catálogo efetivo e
`--json` para obter id, descrição, versão, tags e digest de forma estruturada.

### 4.4 Reentrância e pre-seed

Esses quatro campos governam o que acontece quando o grafo **volta** a um node.
São a fonte mais comum de bugs sutis.

| Campo | Significado |
|---|---|
| `no_pre_seed: true` | o node **sempre roda**, mesmo que os outputs já existam e os validators passem |
| `allow_pre_seed: true` | permite explicitamente reaproveitar checkpoint válido em node de código (default é fail-closed em `build`/`review`/`test`) |
| `preserve_outputs_on_reentry: true` | em node `no_pre_seed`, mantém os outputs existentes para que o LLM **refine** em vez de recriar do zero |
| `fix_review` | aponta o review focal autoritativo deste node de correção |

Regra: **todo node que produz um veredicto sobre trabalho recém-feito precisa de
`no_pre_seed: true`.** Um review pre-seedado é um review que não aconteceu.

`preserve_outputs_on_reentry` é para nodes que respondem perguntas/gates: sem
ele, o LLM apaga o rascunho e perde o contexto acumulado.

### 4.5 Validação e falha

| Campo | Efeito |
|---|---|
| `validators` | lista de checks determinísticos |
| `validation_mode` | `aggregate` (default, roda tudo e agrega diagnóstico) ou `fail_fast` |
| `on_fail` | `{human_gate: "mensagem", goto: node_id}` — rota de falha do node |
| `requires_approval` | pausa para aprovação humana após o node passar |
| `env_setup` / `env_teardown` | comandos shell determinísticos antes/depois da delegação |

`fail_fast` é decisão do node, não política global: use quando um check estático
barato precede uma suíte cara. `aggregate` quando o diagnóstico conjunto vale
mais que a latência.

### 4.6 Decisão humana

| Campo | Efeito |
|---|---|
| `reject_next` | destino quando um `human_gate` é rejeitado |
| `approval_message_required` | exige instrução textual do stakeholder para avançar |
| `decision_context` | pacote que enriquece a apresentação da decisão |
| `bypass_prompt` | com `--bypass-human-gates`, delega a resposta ao LLM com atribuição explícita |
| `bypass_reject_when` | `{path, pattern}` — em bypass, segue `reject_next` se o regex casar |

`decision_context` só é aceito em `human_gate`. Campos de texto (`decision`,
`why_now`, `approve_effect`, `reject_effect`) e de lista (`checklist`,
`limitations`, `review_paths`); `review_paths` só aceita paths relativos e
seguros. A engine sempre deriva um fallback completo, então o bloco é opcional —
mas um gate sem contexto explícito produz decisão humana pior.

### 4.7 Paralelismo

| Campo | Efeito |
|---|---|
| `parallel_group` | nodes com o mesmo grupo rodam em worktrees paralelas (opt-in via `--parallel`) |

Restrições validadas: só executor LLM, nunca tipo de controle (`gate`,
`human_gate`, `decision`, `end`, `exploration`, `batch`), `outputs` obrigatórios
e **disjuntos** entre membros. Grupo com um só membro é warning (não faz nada).

---

## 5. Os tipos de node

`discovery`, `document`, `build`, `test_red`, `test_green`, `refactor`,
`review`, `retro`, `gate`, `decision`, `sync`, `human_gate`, `exploration`,
`batch`, `end`.

### `gate` — validação pura, sem LLM

O node mais barato e o mais subutilizado. Executor `python`, nenhuma delegação,
só validators.

```yaml
- id: feature.preflight
  type: gate
  title: "Preflight do Produto Existente"
  description: >-
    Confirma que a demanda existe, catálogo e backlog são consistentes e a
    baseline passa antes de qualquer alteração. Diagnóstico apenas.
  sprint: feature-01-scope
  executor: python
  validation_mode: fail_fast
  validators:
    - file_exists: docs/feature-request.md
    - file_exists: docs/PROJECT_BACKLOG.md
    - command_succeeds:
        command: "python .ft/process/feature-fast/scripts/validate_feature.py preflight"
        timeout: 60
  next: feature.discovery
```

**Quando usar:** antes de qualquer delegação cara; depois de qualquer delegação
que produza artefato estruturado; para extrair valores de roteamento
(`read_artifact`); para preparar âncoras determinísticas antes de um fix.

**Ad case — gate de preparação:** um gate que roda um script e emite um YAML de
âncora (`docs/feature-fix-baseline.yml`) antes do node de correção. Ele congela
o commit revisado, os achados e o workset, permitindo que a auditoria seguinte
examine apenas o delta. Sem essa âncora, "revisar só a correção" é impossível.

**Armadilha:** gate sem validators é warning e não prova nada. Se você criou um
gate só para ter um ponto de parada, use `sync`.

### `decision` — roteamento determinístico

Avalia `condition` contra o estado e segue a branch.

```yaml
- id: feature.review_decision
  type: decision
  title: "Encaminhar Veredicto da Revisão"
  sprint: feature-02-build
  executor: python
  condition: review_route
  branches:
    approved: feature.acceptance
    implementation: feature.fix_prepare
    evidence: feature.evidence
    scope: feature.discovery
    _default: feature.review
  episode_restart:
    implementation: feature_implementation
    scope: feature_implementation
```

De onde vêm os valores de `condition`:

| Fonte | Como |
|---|---|
| `read_artifact` em um gate anterior | grava `key=value` nos artifacts do estado |
| `gate_log` | resultado de gates anteriores por node id |
| `run_route` | flag `--route` do CLI |
| `parallel_enabled` | `"true"`/`"false"` |
| `node_status`, `blocked_reason` | estado corrente |
| `file_exists:<path>` | condição especial resolvida no filesystem |
| `validation_profiles_active`, `project_validation_mode` | condições especiais da engine |

Sempre declare `_default`. Sem ele, um valor inesperado cai no `next` (ou
bloqueia). O `_default` correto quase sempre é *repetir o node que produziu o
valor* — não avançar.

**Branch que volta atrás:** se o destino é um node já concluído, a engine faz
rewind (limpa o valor de roteamento e reabre os nodes posteriores) em vez de um
avanço incoerente. Isso é automático — mas só funciona se o valor veio de um
`read_artifact`, então não invente estado por outros meios.

`episode_restart` só existe em `decision`, e cada chave precisa ser uma branch
declarada.

**Ad case — `--route` do CLI:** para expor rotas semânticas
(`ft run . --template x --route validation`), o **primeiro node do YAML** precisa
ser um `decision` com `condition: run_route`. As chaves de `branches` (exceto
`_default`) viram as rotas aceitas. Qualquer outro desenho faz o CLI recusar a
flag.

### `document` e `discovery` — produzir conhecimento

Delegações LLM que escrevem Markdown. `discovery` captura hipótese/contexto;
`document` consolida um artefato.

```yaml
- id: feature.discovery
  no_pre_seed: true
  preserve_outputs_on_reentry: true
  type: discovery
  context_profile: feature_delta.discovery
  title: "Delimitar a Feature"
  sprint: feature-01-scope
  executor: claude
  max_turns: 24
  llm_timeout_seconds: 600
  outputs:
    - docs/feature-discovery.md
    - docs/feature.md
  write_scope:
    - docs/feature-discovery.md
    - docs/feature.md
  prompt: |
    Delimite a feature pedida em docs/feature-request.md contra o produto atual.
    Leia obrigatoriamente docs/PRD.md, docs/PROJECT_BACKLOG.md e
    docs/FEATURES.md. Não altere código.

    Produza docs/feature.md com: o item de backlog referenciado
    (`backlog_item: PB-NN`), os critérios AC-01..AC-NN numerados e verificáveis,
    e o que está explicitamente fora de escopo.
  validators:
    - file_exists: docs/feature.md
    - has_sections:
        path: docs/feature.md
        sections: ["Critérios de Aceite", "Fora de Escopo"]
    - command_succeeds: "python .ft/process/feature-fast/scripts/validate_feature.py discovery"
  next: feature.discovery_gate
```

**Regra do prompt:** referencie paths, nunca cole conteúdo. Um prompt que
descreve o produto é um processo que só serve para um produto.

**Regra da saída:** peça formato **verificável**. "Escreva um PRD bom" não é
validável. "Produza `docs/feature.md` com `backlog_item: PB-NN` e critérios
`AC-NN`" é — e o validator confirma.

### `build` — implementar

```yaml
- id: feature.implement
  type: build
  context_profile: feature_delta.implement
  title: "Implementar a Feature"
  sprint: feature-02-build
  executor: claude
  max_turns: 40
  llm_timeout_seconds: 900
  llm_episode: feature_implementation
  llm_episode_max_calls: 3
  outputs:
    - src/
    - tests/
    - docs/implementation-report.md
  prompt: |
    Implemente os AC-* de docs/feature.md seguindo docs/TECH_STACK.md.
    Não amplie o escopo, não refatore oportunisticamente, não altere contratos
    fora do workset declarado.
  validators:
    - git_diff_not_empty: "."
    - file_exists: docs/implementation-report.md
  next: feature.impact_prepare
```

**Armadilha clássica:** validar um build com `tests_pass` no próprio node. Isso
transforma o node numa sessão longa em que o LLM roda a suíte repetidamente. É
mais barato e mais honesto: o `build` prova que **mudou** algo
(`git_diff_not_empty`) e um `gate` seguinte roda build+test **uma vez** com
receipt gravado.

### `test_red` / `test_green` / `refactor` — o loop TDD

```yaml
- id: tdd.red
  type: test_red
  executor: claude
  outputs: [tests/test_feature.py]
  validators:
    - tests_fail: true
  next: tdd.green

- id: tdd.green
  type: test_green
  executor: claude
  outputs: [src/feature.py]
  validators:
    - tests_pass: true
  next: tdd.refactor

- id: tdd.refactor
  type: refactor
  executor: claude
  outputs: [src/feature.py]
  validators:
    - tests_pass: true
    - lint_clean: true
  next: gate.delivery
```

A engine faz auto-commit após PASS, com prefixo por fase (`red:`, `green:`,
`refactor:`). Use `pytest_red_quality` no `test_red` para recusar teste vazio ou
sem asserção — um teste que falha por `ImportError` não é um teste vermelho
legítimo.

### `review` — veredicto estruturado

O tipo mais delicado. Existem **duas formas**, e misturá-las é a origem da
maioria dos processos quebrados.

**Forma legada (verdict em texto):** a engine lê o Markdown do review e procura
`APPROVED`, `APPROVED WITH NOTES`, `REJECTED`, `ITERATE`, `BLOCKED`,
`INCOMPLETE`. Rejeição dispara `on_fail` ou bloqueia. Simples, mas o
roteamento é binário e a engine é quem interpreta.

**Forma estruturada (recomendada):** o node declara `review_route_path`. A
engine então **só valida estrutura** e deixa o `decision` seguinte interpretar a
rota. Isso permite mais de duas saídas e mantém o roteamento no grafo.

```yaml
- id: feature.review
  no_pre_seed: true
  type: review
  review_route_path: docs/feature-review.yml
  context_profile: feature_delta.review
  title: "Revisão Independente do Incremento"
  sprint: feature-02-build
  executor: claude
  max_turns: 28
  llm_timeout_seconds: 600
  outputs:
    - docs/feature-review.md
    - docs/feature-review.yml
  write_scope:                       # o revisor NÃO conserta o produto
    - docs/feature-review.md
    - docs/feature-review.yml
  prompt: |
    Audite o incremento contra cada AC-* de docs/feature.md. Não altere produto,
    testes ou contratos. Não rode a suíte completa — o receipt já foi gravado.

    Para cada AC registre PASS ou FAIL com evidência verificável. Achados de
    implementação numerados F-01, F-02...

    Gere docs/feature-review.md com a tabela AC/veredicto/evidência.
    Gere docs/feature-review.yml com `schema_version: 1`, `verdict`,
    `review_route`, `summary` e `findings`.
    Rotas válidas: approved, implementation, evidence, scope.
  validation_mode: fail_fast
  validators:
    - file_exists: docs/feature-review.md
    - file_exists: docs/feature-review.yml
    - command_succeeds: "python .ft/process/feature-fast/scripts/validate_feature.py review"
  next: feature.review_route
```

`review_route_path` só é válido em `type: review`.

**Três invariantes de um review honesto:**

1. `no_pre_seed: true` — senão o relatório anterior é reaproveitado.
2. `write_scope` restrito aos relatórios — senão o revisor vira implementador.
3. O node listado em `correction_policy.mandatory_after_implementation` — senão
   uma reentrada pula a auditoria.

### `human_gate` — decisão de stakeholder

```yaml
- id: feature.acceptance
  type: human_gate
  title: "Aceite da Feature"
  description: >-
    Valide a capacidade implementada e seus AC-*. Rejeite com um desvio
    reproduzível.
  sprint: feature-03-acceptance
  executor: python
  env_setup:
    - bash .ft/process/feature-fast/scripts/serve.sh
  env_teardown:
    - bash .ft/process/feature-fast/scripts/serve.sh stop
  outputs:
    - docs/stakeholder-feedback.md
  decision_context:
    decision: >-
      Decidir se a capacidade entregue satisfaz os AC-* sem ampliar escopo.
    why_now: >-
      Implementação, validação e auditoria terminaram; falta a confirmação
      observável antes de consolidar catálogos.
    review_paths:
      - docs/feature.md
      - docs/feature-review.md
    checklist:
      - Abra o produto pela entrada apresentada e complete o fluxo pedido.
      - Compare o comportamento observado com cada AC-*.
      - Não aprove com bug reproduzível ou evidência inacessível.
    approve_effect: "Segue para reconciliação de catálogos e encerramento."
    reject_effect: "Volta à implementação com o desvio reportado."
  reject_next: feature.implement
  next: feature.reconcile
```

`env_setup`/`env_teardown` são o que torna um aceite real: sobem o produto para
o humano ver e derrubam depois. Sempre pareie os dois.

**Onde apontar `reject_next`:** para o node que pode *de fato* resolver a
objeção. Rejeição de stakeholder costuma trazer requisito semântico novo — volte
para a implementação (ou até o discovery), não para o review.

**Ad case — bypass honesto.** Com `--bypass-human-gates`, um gate sem
`bypass_prompt` é simplesmente pulado. Dois campos tornam o bypass defensável:

```yaml
  bypass_prompt: |
    Você responde em nome do stakeholder AUSENTE. Leia docs/research-questions.md
    e responda TODAS as perguntas editando o arquivo, prefixando cada resposta
    com `**[LLM Responde / {llm_label}]**`. Quando a pergunta pedir dado interno
    que você não possui, declare "dado interno indisponível" e assuma a posição
    mais conservadora, justificando-a.
  bypass_reject_when:
    path: docs/business-case.md
    pattern: "recommendation:\\s*no_go"
```

`bypass_reject_when` impede o absurdo de um bypass aprovar automaticamente um
artefato que já recomenda rejeição.

### `exploration` — sandbox do stakeholder

Pausa o grafo e abre perguntas read-only ao LLM (`ft explore`). Marque
`optional: true` para que modos autônomos possam pular. Em modo `mvp`/`--auto` é
pulado automaticamente.

### `batch` — fan-out dinâmico de lanes

Exige `executor: python` e `batch_policy` no topo. A engine calcula waves,
limita concorrência, roda cada lane em worktree, valida ownership pelo diff real
e faz fan-in atômico.

```yaml
- id: ft.batch.04.execute
  type: batch
  title: "Executar Lanes Isoladas e Integrar"
  sprint: sprint-00-batch
  executor: python
  outputs:
    - docs/mvp-batch-report.md
  validators:
    - file_exists: docs/mvp-batch-report.md
    - command_succeeds: "git diff --check"
  next: ft.batch.05.review
```

O plano de lanes é produzido por um node `document` anterior e validado por
`builder_batch_plan_valid`. O batch é para **construção ampla com ownership
disjunto** — não para paralelizar duas tarefas.

### `parallel_group` — fan-out estático

Alternativa mais simples ao `batch`, opt-in via `--parallel`:

```yaml
- id: build.api
  type: build
  executor: claude
  parallel_group: layer
  outputs: [src/api/]
  next: sync.layers

- id: build.ui
  type: build
  executor: claude
  parallel_group: layer
  outputs: [src/ui/]
  next: sync.layers
```

Outputs **disjuntos** é requisito duro — sobreposição gera conflito de merge no
fan-in e é erro de validação.

### `retro` — retrospectiva

A engine injeta o activity log, o gate log e os nodes concluídos no prompt.
Default de 12 turnos. Use no fim de processos longos para alimentar melhorias de
processo.

### `sync` — marcador de convergência

Tipo de controle sem validação própria. Útil como ponto de junção após um
`parallel_group` ou como âncora de roteamento legível.

### `end` — terminal

**Exatamente um por processo.** Sem executor, sem validators.

```yaml
- id: feature.end
  type: end
  title: "Feature Pronta para Merge"
  description: >-
    Execute `ft close --merge full` para arquivar os artefatos do ciclo,
    integrar a mudança e remover a worktree.
```

Ao chegar aqui a engine consolida métricas de uso do LLM, commita o
conhecimento produzido, faz o merge configurado e dispara `on_deliver`.

---

## 6. Validadores

### 6.1 As cinco formas de argumento

A forma escolhida muda como a função é chamada:

```yaml
validators:
  - tests_pass: true                  # fn(project_root=...)
  - file_exists: docs/PRD.md          # fn("docs/PRD.md", project_root=...)
  - min_lines: 20                     # fn(outputs[0], 20, project_root=...)  ← usa o 1º output!
  - has_sections: [Problema, Solução] # fn(outputs[0], [...], project_root=...)
  - command_succeeds:                 # fn(**kwargs, project_root=...)
      command: make test
      timeout: 300
```

As formas posicionais (int e lista) **dependem de `outputs[0]`**. Node sem
outputs falha com erro explícito. Na dúvida, use a forma dict com `path`
explícito — é mais verbosa e imune a reordenação de outputs.

### 6.2 Modificadores

```yaml
validation_mode: fail_fast            # do node inteiro
validators:
  - file_exists: docs/contract.md
  - command_succeeds:
      command: make build
      stop_on_failure: true           # interrompe a lista neste ponto
  - command_succeeds:
      command: "bash scripts/product.sh full --record docs/validation.json"
      resume_command: "bash scripts/product.sh verify docs/validation.json"
      timeout: 300
```

`resume_command` (exclusivo de `command_succeeds`) só é usado ao recuperar uma
delegação órfã: a retomada valida o receipt determinístico em vez de repetir a
suíte cara que o produziu. Se ele falhar, o comando completo roda uma vez.

### 6.3 Catálogo

**Artefatos e documentos**

| Validador | Assinatura útil |
|---|---|
| `file_exists` | `path` |
| `min_lines` | `path, n` |
| `has_sections` | `path, sections[]` |
| `document_quality` | `path, min_lines_count, max_lines_count, forbidden[], required_terms[], min_required_terms` |
| `expert_review_report_valid` | `path` — exige veredito único, baseline e evidência coerente no parecer |
| `min_user_stories` | `path, n` (conta `### US-`) |
| `relative_dates_only` | `path` |
| `sections_unchanged` | `path, snapshot_path, sections[]` — congela seções críticas |
| `read_artifact` | `path, key, pattern` — extrai valor para roteamento |
| `unique_screenshots` | `screenshots_dir, min_count` |

**Contratos e catálogos**

| Validador | Uso |
|---|---|
| `project_contract_valid` | schema, objetivo, fase e DoD de `.ft/project.yml` |
| `project_backlog_valid` | IDs, prioridade e status do backlog |
| `backlog_pending_decisions` | recusa P0/P1 sem decisão |
| `backlog_referenced_decisions` | valida os PBs que o ciclo declarou (`reference_field`, `required_count`, `accepted_statuses`) |
| `features_catalog_valid` | catálogo `FEAT-*` e origens |
| `implemented_backlog_covered_by_features` | toda entrega tem `FEAT-*` |
| `task_list_references_backlog` | task list referencia backlog |
| `api_contract_complete` / `library_contract_complete` | contratos de interface |
| `demand_coverage` | cobertura da demanda pelo PRD |
| `prd_coverage` | cobertura do PRD pelos outputs (`min_ratio`) |
| `navigation_contract_valid` / `navigation_reachability` | contrato de navegação |
| `ui_criteria_ids` / `ui_criteria_coverage` / `visual_p0_acceptance` | critérios de UI |
| `builder_batch_plan_valid` | plano de lanes do batch |
| `process_improvements_classified` | melhorias de processo classificadas |

**Reviews**

| Validador | Uso |
|---|---|
| `review_outcome_valid` | `path, scope_path, scope_pattern, markdown_path, require_approved` |
| `review_chain_approved` | valida review + fix_review encadeados |

**Testes, código e execução**

| Grupo | Validadores |
|---|---|
| Testes | `tests_pass`, `tests_fail`, `pytest_red_quality`, `coverage_min`, `coverage_per_file`, `tests_exist` |
| Código | `lint_clean`, `format_check`, `no_todo_fixme` |
| Review estático | `no_large_files`, `no_print_statements`, `changed_files_have_tests` |
| Execução | `command_succeeds`, `bash_passes`, `git_diff_not_empty`, `paths_clean` |
| Gates prontos | `gate_delivery`, `gate_smoke`, `gate_mvp`, `gate_frontend`, `gate_server_starts`, `gate_acceptance_cli`, `gate_kb_review`, `gate_pulse_instrumented`, `gate_ui_vscode_layout` |
| Plataformas | `validation_matrix_valid`, `validation_profile_hooks`, `platform_validation_report`, `platform_validation_ready`, `test_identity_ready` |

`gate_delivery: true` recebe automaticamente os `outputs` do node.

### 6.4 Quando escrever um script próprio

O catálogo cobre o genérico. Regras específicas do **processo** (não do
produto) vão para `scripts/validate_<nome>.py`, invocado por
`command_succeeds` com um subcomando por etapa:

```yaml
- command_succeeds: "python .ft/process/meu-processo/scripts/validate.py discovery"
```

Isso é legítimo e recomendado: limites de diff, coerência entre artefatos do
próprio processo, âncoras de correção, fingerprints de receipt. O script sai com
código != 0 e mensagem clara no stderr.

---

## 7. Receitas

### 7.1 Review → rota → decisão (a espinha dorsal)

Três nodes, sempre nesta ordem:

```yaml
- id: x.review          # review estruturado, no_pre_seed, escreve o .yml
  review_route_path: docs/x-review.yml
  next: x.review_route

- id: x.review_route    # gate: extrai a rota para o estado
  type: gate
  executor: python
  validators:
    - read_artifact:
        path: docs/x-review.yml
        key: review_route
        pattern: "review_route:\\s*(approved|implementation|evidence|scope)"
  next: x.review_decision

- id: x.review_decision # decision: roteia
  type: decision
  executor: python
  condition: review_route
  branches:
    approved: x.acceptance
    implementation: x.fix_prepare
    evidence: x.evidence
    scope: x.discovery
    _default: x.review
```

O `pattern` restringe os valores aceitos — uma rota inventada pelo LLM faz o
gate falhar em vez de rotear para lugar nenhum.

### 7.2 Loop de correção focal

Depois de uma rejeição, corrigir "o produto" é caro e arriscado. O padrão:

```text
review (rejeita, gera F-01..F-NN)
  → fix_prepare (gate: ancora commit, achados, workset)
  → fix (build: corrige APENAS os F-*)
  → fix_validate (gate: valida o delta)
  → fix_review (review: audita SÓ a correção, contra a âncora)
  → fix_review_route → fix_review_decision
  → fix_full_validate (gate: renova receipt completo UMA vez)
  → acceptance
```

A auditoria da correção não reabre ACs não tocados e não repete a suíte — o
validator confere o receipt ancorado. A suíte completa roda uma única vez,
depois da aprovação focal.

O campo `fix_review` no node de correção declara qual review é autoritativo: ao
rejeitar, a engine percorre só a cadeia linear entre `next` e esse review, e
volta ao mesmo gate. O grafo valida que essa cadeia existe e é linear (sem
`decision` no meio).

### 7.3 Falha de gate que volta ao trabalho

```yaml
- id: x.product_validate
  type: gate
  executor: python
  validation_mode: fail_fast
  validators:
    - command_succeeds:
        command: "python .ft/process/x/scripts/validate.py implementation"
        stop_on_failure: true
    - command_succeeds:
        command: "bash .ft/process/x/scripts/product.sh ensure --record docs/validation.json"
        resume_command: "bash .ft/process/x/scripts/product.sh verify docs/validation.json"
        timeout: 300
  on_fail:
    human_gate: "A validação do produto falhou; corrija a implementação preservada."
    goto: x.implement
  next: x.evidence
```

`on_fail` transforma falha em rota, com aviso ao humano. Sem ele, a falha
simplesmente bloqueia o ciclo.

### 7.4 Processo ultraleve (uma delegação)

O `tweak` é o modelo: preflight determinístico → uma delegação com limites
explícitos no prompt → aceite humano → fim.

```yaml
# environment.yml
run_mode: isolated
max_node_retries: 0
max_gate_retries: 0
max_auto_fix: 0
```

Limites explícitos no prompt (nº de arquivos, linhas, áreas proibidas) e uma
saída de escape declarada: se a mudança real ultrapassar os limites, o LLM
**não altera o produto** e escreve `Resultado: ESCALATE` recomendando o processo
maior. Isso evita que um processo pequeno seja usado como cavalo de Troia.

### 7.5 Processo sem produto (pesquisa, decisão)

```yaml
execution_policy:
  requires_initialized_project: false
  requires_worktree: false
close_policy:
  backlog:
    mode: none
```

Ad case do `innovation`: dois fins legítimos — handoff (go) e post-mortem
(no-go). Matar uma ideia barata é entrega de valor, então o `end` é alcançável
pelas duas rotas e o `reject_next` do gate go/no-go aponta para o post-mortem,
não para uma correção.

Nesse tipo de processo, o validator determinístico substitui o teste: todo
claim precisa de fonte (URL), data e confiança; claim sem fonte bloqueia.

### 7.6 Rotas de entrada (`--route`)

```yaml
nodes:
  - id: x.route_mode            # PRIMEIRO node do arquivo
    type: decision
    executor: python
    condition: run_route
    branches:
      validation: x.batch.plan
      _default: x.start
```

---

## 8. Anti-padrões

| Anti-padrão | Por que quebra | O que fazer |
|---|---|---|
| Nome de produto, cor hex, framework ou spec de layout no prompt | o processo deixa de ser reutilizável; `ft lint-process` acusa | referencie `docs/TECH_STACK.md`, `docs/ui_criteria.md` |
| Review sem `no_pre_seed` | reaproveita o veredicto anterior | `no_pre_seed: true` + `mandatory_after_implementation` |
| Review com `write_scope` amplo | o revisor conserta em vez de reportar | `write_scope` = só os relatórios |
| Node sem `outputs` | escopo de escrita vira `project/`+`docs/` | declare `outputs` sempre |
| Só `file_exists` como validação | arquivo vazio passa | some com `has_sections`, `document_quality` ou script próprio |
| Suíte completa dentro do prompt do build | sessão longa, custo alto, prova fraca | `git_diff_not_empty` no build; suíte no gate seguinte com receipt |
| `decision` sem `_default` | valor inesperado rota errado | `_default` volta ao node de origem |
| Dois nodes LLM seguidos sem gate | deriva acumulada, erro tardio | gate determinístico entre eles |
| `parallel_group` com outputs sobrepostos | conflito no fan-in — erro de validação | outputs disjuntos ou serialize |
| `env_setup` sem `env_teardown` | processo servido fica órfão | sempre em par |
| `context_profile` + `hyper_mode_*` | erro de validação | escolha um |
| Editar o YAML dentro de `runs/` ou `.ft/cycles/` | ciclo é descartável, a mudança se perde | altere o template global / fork local e rode `ft evolve` |

---

## 9. Fechando o processo

### 9.1 Invariantes do grafo

O carregamento falha (exceção) se:

- houver IDs duplicados;
- um `next`, `branches`, `reject_next`, `on_fail.goto` ou `fix_review` apontar
  para node inexistente;
- não houver **exatamente um** node `type: end`;
- `review_route_path` estiver fora de um `review`;
- `episode_restart` estiver fora de um `decision` ou citar branch inexistente;
- orçamento de episódio for declarado sem `llm_episode`;
- `fix_review` não for alcançável por cadeia linear de `next`.

A validação (`ft validate`) reporta como **erro**:

- `type` ou `executor` desconhecidos;
- nó órfão (ninguém aponta para ele, exceto o primeiro) ou inalcançável;
- nó não-terminal sem `next` nem `branches`;
- `end` inalcançável;
- validator com kwarg inexistente ou faltando argumento obrigatório;
- `batch` sem `executor: python` ou sem `batch_policy`;
- `decision_context` fora de `human_gate`, ou com campo/valor inválido;
- `parallel_group` com node de controle, executor não-LLM, sem outputs ou com
  outputs sobrepostos;
- políticas de topo malformadas.

E como **warning**: `gate` sem validators, `build` sem outputs, `decision` sem
branches, validator ausente no registry, `parallel_group` de um membro só.

### 9.2 Fluxo de validação

```bash
# 1. Grafo, schema, assinaturas de validator
ft validate --template meu-processo

# 2. Lint semântico — caça especificidade de projeto no YAML
ft lint-process --template meu-processo

# 3. Execução real em um projeto de teste
ft run . --template meu-processo
ft status
ft graph
```

`ft validate` aceita o nome de um template materializado ou o path canônico
`.ft/process/<nome>/process.yml`. Se o template local não existir, ele valida o
global e avisa.

### 9.3 Checklist de revisão final

- [ ] `execution_policy.template` é idêntico ao nome do diretório
- [ ] `project_role` e `allowed_project_phases` coerentes
- [ ] `input_policy.destination` é path relativo seguro fora de `.ft/`
- [ ] Todo artefato produzido está em `canonical` **ou** em `cycle`
- [ ] Todo review está em `correction_policy.mandatory_after_implementation`
- [ ] Todo review tem `no_pre_seed: true` e `write_scope` restrito
- [ ] Todo node LLM tem `outputs`, `max_turns` e ao menos um validator de conteúdo
- [ ] Todo `decision` tem `_default`
- [ ] Todo `human_gate` tem `reject_next` apontando para quem resolve a objeção
- [ ] Todo `env_setup` tem `env_teardown` correspondente
- [ ] Todo path de script usa o prefixo `.ft/process/<nome>/`
- [ ] Existe exatamente um `end`, alcançável por todas as rotas legítimas
- [ ] `ft validate` PASS sem erros e sem warnings inesperados
- [ ] `ft lint-process` sem violações
- [ ] `README.md` e `process-flow.md` do bundle descrevem o fluxo real

---

## 10. Esqueleto mínimo

Ponto de partida copiável — um processo de manutenção com discovery, build,
review estruturado e aceite:

```yaml
# Meu Processo — descrição de uma linha
#
# Este arquivo global nunca é executado diretamente. Na primeira invocação de
# `ft run . --template meu-processo`, o engine materializa o diretório em
# `.ft/process/meu-processo/` e executa somente a cópia local.

id: meu_processo
version: "0.1.0"
title: "Meu Processo — Descrição Curta"

execution_policy:
  entrypoint: run
  template: meu-processo
  materialization: copy_once
  runtime_source: local_only
  requires_initialized_project: true
  requires_worktree: true
  local_process_path: .ft/process/meu-processo/process.yml
  merge_command: ft close --merge full
  project_role: maintenance
  allowed_project_phases:
    - maintenance

input_policy:
  required: true
  destination: docs/demanda.md
  prompt: "Descreva a demanda"

correction_policy:
  follow_graph_after_retry: true
  acceptance_rejection_restarts_at: mp.implement
  mandatory_after_implementation:
    - mp.review
    - mp.acceptance

close_policy:
  backlog:
    mode: none
  merge: full

artifact_policy:
  canonical:
    - docs/PRD.md
    - docs/FEATURES.md
  cycle:
    - docs/demanda.md
    - docs/mp-plan.md
    - docs/mp-review.md
    - docs/mp-review.yml
    - docs/mp-report.md

nodes:
  - id: mp.preflight
    type: gate
    title: "Preflight"
    description: "Confirma demanda presente e produto em estado válido."
    sprint: mp-01
    executor: python
    validation_mode: fail_fast
    validators:
      - file_exists: docs/demanda.md
      - command_succeeds:
          command: "python .ft/process/meu-processo/scripts/validate.py preflight"
          timeout: 60
    next: mp.plan

  - id: mp.plan
    no_pre_seed: true
    preserve_outputs_on_reentry: true
    type: discovery
    title: "Delimitar a Demanda"
    sprint: mp-01
    executor: claude
    max_turns: 24
    llm_timeout_seconds: 600
    outputs:
      - docs/mp-plan.md
    write_scope:
      - docs/mp-plan.md
    prompt: |
      Delimite a demanda de docs/demanda.md contra o produto atual.
      Leia docs/PRD.md e docs/FEATURES.md. Não altere código.
      Produza docs/mp-plan.md com critérios AC-01..AC-NN verificáveis e uma
      seção "Fora de Escopo".
    validators:
      - file_exists: docs/mp-plan.md
      - has_sections:
          path: docs/mp-plan.md
          sections: ["Critérios de Aceite", "Fora de Escopo"]
    next: mp.implement

  - id: mp.implement
    type: build
    title: "Implementar"
    sprint: mp-02
    executor: claude
    max_turns: 40
    llm_timeout_seconds: 900
    outputs:
      - src/
      - tests/
      - docs/mp-report.md
    prompt: |
      Implemente os AC-* de docs/mp-plan.md seguindo docs/TECH_STACK.md.
      Não amplie escopo nem refatore oportunisticamente.
      Registre em docs/mp-report.md os arquivos alterados e o comportamento novo.
    validators:
      - git_diff_not_empty: "."
      - file_exists: docs/mp-report.md
    next: mp.validate

  - id: mp.validate
    type: gate
    title: "Validar Produto"
    sprint: mp-02
    executor: python
    validation_mode: fail_fast
    outputs:
      - docs/mp-validation.json
    validators:
      - command_succeeds:
          command: "bash .ft/process/meu-processo/scripts/product.sh ensure --record docs/mp-validation.json"
          resume_command: "bash .ft/process/meu-processo/scripts/product.sh verify docs/mp-validation.json"
          timeout: 300
    on_fail:
      human_gate: "A validação falhou; corrija a implementação."
      goto: mp.implement
    next: mp.review

  - id: mp.review
    no_pre_seed: true
    type: review
    review_route_path: docs/mp-review.yml
    title: "Auditoria Independente"
    sprint: mp-02
    executor: claude
    max_turns: 28
    llm_timeout_seconds: 600
    outputs:
      - docs/mp-review.md
      - docs/mp-review.yml
    write_scope:
      - docs/mp-review.md
      - docs/mp-review.yml
    prompt: |
      Audite o incremento contra cada AC-* de docs/mp-plan.md. Não altere
      produto nem testes; não repita a suíte — o receipt já está gravado.
      Registre PASS/FAIL por AC com evidência verificável; achados numerados
      F-01, F-02...
      Gere docs/mp-review.md com a tabela e docs/mp-review.yml com
      `schema_version: 1`, `verdict`, `review_route`, `summary` e `findings`.
      Rotas válidas: approved, implementation, scope.
    validation_mode: fail_fast
    validators:
      - file_exists: docs/mp-review.md
      - file_exists: docs/mp-review.yml
      - command_succeeds: "python .ft/process/meu-processo/scripts/validate.py review"
    next: mp.review_route

  - id: mp.review_route
    type: gate
    title: "Registrar Rota da Revisão"
    sprint: mp-02
    executor: python
    validators:
      - read_artifact:
          path: docs/mp-review.yml
          key: review_route
          pattern: "review_route:\\s*(approved|implementation|scope)"
    next: mp.review_decision

  - id: mp.review_decision
    type: decision
    title: "Encaminhar Veredicto"
    sprint: mp-02
    executor: python
    condition: review_route
    branches:
      approved: mp.acceptance
      implementation: mp.implement
      scope: mp.plan
      _default: mp.review

  - id: mp.acceptance
    type: human_gate
    title: "Aceite"
    description: "Valide o comportamento entregue contra os AC-*."
    sprint: mp-03
    executor: python
    env_setup:
      - bash .ft/process/meu-processo/scripts/serve.sh
    env_teardown:
      - bash .ft/process/meu-processo/scripts/serve.sh stop
    outputs:
      - docs/mp-report.md
    reject_next: mp.implement
    next: mp.end

  - id: mp.end
    type: end
    title: "Pronto para Merge"
    description: >-
      Execute `ft close --merge full` para arquivar os artefatos do ciclo,
      integrar a mudança e remover a worktree.
```

---

## 11. Onde olhar quando travar

| Dúvida | Fonte de verdade |
|---|---|
| Campos aceitos por um node | `ft/engine/graph.py` (`Node` + `load_graph`) |
| O que é erro vs warning | `ft/engine/process_validator.py` |
| Como cada tipo é executado | `ft/engine/runner.py` (loop principal e `_run_*`) |
| Assinatura exata de um validator | `ft/engine/validators/*.py` |
| Registro de validators | `VALIDATOR_REGISTRY` em `ft/engine/runner.py` |
| Perfis de contexto disponíveis | `ft/engine/context_profiles.py` |
| Contrato do template/catálogo | `ft/templates/catalog.py` |
| Exemplo ultraleve | `templates/tweak/process.yml` |
| Exemplo completo de manutenção | `templates/feature-fast/process.yml` |
| Exemplo sem produto | `templates/innovation/process.yml` |
| Exemplo com batch e lanes | `templates/mvp-builder-fast/process.yml` |
