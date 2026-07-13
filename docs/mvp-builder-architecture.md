# MVP Builder - Arquitetura de Projeto

## Objetivo

O Fast Track separa quatro categorias que não podem compartilhar o mesmo ciclo de
vida:

1. Código e testes do produto.
2. Fontes de verdade mantidas pelo usuário.
3. Processo e histórico versionados do Fast Track.
4. Runtime descartável da engine.

Essa separação impede que estado de uma execução seja copiado por `ft init`, mantém o
processo específico do projeto sob Git e deixa PRD, backlog e catálogo de features
fáceis de encontrar.

## Layout Canônico

```text
produto/
├── AGENTS.md
├── docs/
│   ├── PRD.md
│   ├── TECH_STACK.md
│   ├── ui_criteria.md
│   ├── PROJECT_BACKLOG.md
│   └── FEATURES.md
├── .ft/
│   ├── manifest.yml
│   ├── .gitignore
│   ├── process/
│   │   ├── mvp-builder/
│   │   │   ├── process.yml
│   │   │   ├── environment.yml
│   │   │   └── scripts/
│   │   └── feature/
│   │       ├── process.yml
│   │       ├── environment.yml
│   │       └── scripts/
│   └── cycles/
│       └── cycle-01/
│           ├── cycle.yml
│           ├── task_list.md
│           ├── acceptance-report.md
│           ├── handoff.md
│           ├── retro.md
│           └── screenshots/
├── project/
└── src/
```

O diretório iniciado por ponto continua sendo versionado normalmente pelo Git. O
`.ft/.gitignore` ignora apenas `runtime/`, `cache/`, `tmp/`, `logs/` e PIDs; processo e
ciclos nunca são ignorados.

## Fronteiras

### `docs/`: conhecimento humano

Um arquivo permanece visível quando o usuário precisa introduzi-lo, encontrá-lo,
revisá-lo ou mantê-lo como fonte de verdade. Isso inclui:

- `PRD.md`
- `TECH_STACK.md` ou `tech_stack.md`
- `ui_criteria.md`
- `PROJECT_BACKLOG.md`
- `FEATURES.md`
- contratos e dados de teste canônicos definidos pelo processo

`PROJECT_BACKLOG.md` registra mudanças desejadas e seu histórico. `FEATURES.md` é a
projeção das capacidades efetivamente entregues: IDs `FEAT-*` permanecem estáveis,
referenciam itens `PB-*` concluídos e conservam evolução, depreciação ou remoção.

### `.ft/process/<nome>/`: processos locais

Cada `process.yml` é uma cópia versionada do template e passa a pertencer ao
projeto. Ele pode evoluir para capturar nuances daquele domínio. O engine nunca o
substitui automaticamente por uma versão global. `default_process` escolhe o
processo usado sem flag; processos especializados, como `feature`, convivem no
mesmo catálogo.

`environment.yml` e `scripts/` acompanham o processo porque são mecanismos de
orquestração, não código do produto.

O node final de meta-melhoria não promove alterações diretamente para o engine.
Ele gera `docs/process-improvements.yml`, no qual cada achado é classificado como
local, candidato global ou rejeitado segundo uma régua determinística. Candidatos
globais permanecem `pending` até um mantenedor registrar a decisão e, em caso de
promoção, a referência do commit/path global validado. O `ft close` aplica esse
gate antes de arquivar e remover a worktree.

### `.ft/cycles/`: memória durável

Durante a worktree, os nodes podem produzir relatórios transitórios em `docs/` para que
validadores e LLMs trabalhem com paths simples. No `ft close`, a engine consulta o
`artifact_policy` do processo e move os artefatos específicos da execução para
`.ft/cycles/<id>/` antes do merge.

Cada ciclo contém `cycle.yml` com:

- processo e versão utilizados;
- engine e modelo LLM;
- steps concluídos e totais;
- resumo dos gates;
- índice dos artefatos arquivados;
- horário de fechamento.

Estado, locks e logs brutos não entram nesse histórico.

### `$FT_HOME`: runtime descartável

```text
~/.ft/
├── worktrees/<projeto>/<cycle>/state/
└── runtime/<projeto>/continuous/state/
```

`FT_HOME` pode redirecionar essa raiz. Nada daqui é copiado por templates ou mergeado
para a branch principal.

## Manifest

`.ft/manifest.yml` identifica o contrato do layout:

```yaml
schema_version: 2
default_process: mvp-builder
processes:
  mvp-builder:
    path: .ft/process/mvp-builder/process.yml
    template: mvp-builder
    entrypoint: init
    source_digest: sha256:...
    base_digest: sha256:...
  feature:
    path: .ft/process/feature/process.yml
    template: feature
    entrypoint: feature
defaults:
  llm_engine: opencode
  llm_model: pgx/zai-org_glm-4.7-flash
```

O schema canônico é `2`; as chaves top-level `process`, `template` e
`origin_template` são legadas. O digest cobre `process.yml`, `environment.yml` e
arquivos semânticos em `scripts/`, incluindo path e modo de permissão. Apenas
caches inequivocamente gerados são ignorados.

## Política de Artefatos

Cada processo pode declarar sua classificação:

```yaml
artifact_policy:
  canonical:
    - docs/PRD.md
    - docs/PROJECT_BACKLOG.md
    - docs/FEATURES.md
    - docs/ui_criteria.md
  cycle:
    - docs/task_list.md
    - docs/screenshots/
    - docs/acceptance-report.md
    - docs/handoff.md
    - docs/retro.md
```

Itens `canonical` permanecem em `docs/`. Itens `cycle` são movidos para o diretório do
ciclo. Se um path aparecer nas duas listas, a classificação canônica prevalece.

## Ciclo de Vida

```text
ft init --template <template>
  -> cria docs/, src/ e .ft/ versionável
  -> copia template para .ft/process/<template>/process.yml
  -> registra processes.<template> e default_process no manifest v2
  -> NÃO cria engine_state.yml

ft run
  -> commita snapshot de docs/ + .ft/process/
  -> cria worktree externa ou runtime continuous
  -> inicializa estado somente no FT_HOME

ft close
  -> valida backlog e, quando declarado pelo processo, sua projeção em FEATURES
  -> arquiva outputs de ciclo em .ft/cycles/<id>/
  -> cria cycle.yml
  -> commita o arquivo do ciclo
  -> faz merge
  -> remove worktree e branch temporárias
```

`--merge none` descarta o ciclo e não cria histórico. Merge `selective` inclui o
registro do ciclo, mesmo quando apenas alguns paths de produto são escolhidos.
Nos modos por cópia (`docs`, `selective` e fallback sem Git), o manifesto do
checkout principal nunca é substituído pelo snapshot da worktree; defaults LLM e
sua revisão permanecem vivos.

## Run Modes

No modo `isolated`, a execução ocorre em
`$FT_HOME/worktrees/<projeto>/<cycle>/`. No modo `continuous`, o código permanece no
checkout principal, mas o estado vai para `$FT_HOME/runtime/<projeto>/continuous/`.

Nenhum modo cria `state/` ou `runs/` no repositório.

## Migração

O CLI detecta o layout anterior, mas nunca o executa nem cria v2 ao lado dele. A
migração é explícita e deve ocorrer sem ciclo/runtime ativo:

```bash
ft migrate-layout . --dry-run
ft migrate-layout . --cycle-id cycle-08-claude
```

Ela:

1. valida o grafo legado, o manifesto v2 candidato, contenção, symlinks e todas as
   colisões sem alterar o projeto;
2. move `process/` ou o bundle flat `.ft/process/process.yml` para
   `.ft/process/<nome>/`;
3. normaliza o YAML principal para `.ft/process/<nome>/process.yml`;
4. atualiza referências a scripts e ao processo;
5. cria o manifesto schema v2 e a política de ignore;
6. importa `docs/archive/<ciclo>/` para `.ft/cycles/<ciclo>/` sem sobrescrever
   histórico durável;
7. arquiva relatórios soltos em `.ft/cycles/<cycle-id>/`;
8. atualiza referências inequívocas ao processo nos arquivos atuais do projeto, sem
   reescrever o conteúdo histórico em `.ft/cycles/`;
9. move `state/`, `runs/` e marcadores de servidor legados para um backup inativo em
   `$FT_HOME/migrations/<projeto>/<timestamp>/`, fora do repositório.

Depois da migração, `process/` deixa de ser reconhecido pela CLI.

## Invariantes

- `ft init` nunca cria runtime.
- Templates não podem conter `state`, `engine_state.yml`, `llm_logs`, `runs` ou
  diretórios `cycle-*`.
- Todo processo executável usa `.ft/process/<nome>/process.yml`, está registrado no
  manifesto e não atravessa symlinks.
- A engine sempre executa o fork local; o catálogo global só materializa a primeira
  cópia.
- O PRD nunca fica oculto.
- `ft close` não mergeia estado ou logs brutos.
- O histórico de ciclo é versionado e auditável depois da remoção da worktree.
