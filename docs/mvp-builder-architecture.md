# Fast Track V3 — Arquitetura de Projeto

## Objetivo

O Fast Track separa cinco ciclos de vida:

1. código e testes do produto;
2. fontes de verdade mantidas pelo usuário;
3. processos locais versionados;
4. histórico durável de cada ciclo;
5. runtime e locks descartáveis fora do repositório.

No contrato V3, o workspace comum não pertence a nenhum template. `ft init`
estabelece os invariantes do repositório; somente `ft run --template T`
materializa um processo. Isso permite executar `feature`, `bug`, `tweak`,
`mvp-builder` ou qualquer outro template sem eleger um processo principal.

## Layout canônico

```text
produto/
├── .git/
├── AGENTS.md
├── docs/                              # criado/mantido pelo produto ou processo
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
│       ├── cycle-01/
│       │   ├── cycle.yml
│       │   ├── task_list.md
│       │   ├── acceptance-report.md
│       │   ├── handoff.md
│       │   └── retro.md
│       └── cycle-02/
├── project/                            # opcional; definido pelo produto
└── src/                                # opcional; definido pelo produto
```

`ft init` cria somente a base comum (`.ft/`, manifesto, ignore e playbook), não
`docs/`, `src/` nem uma entrada em `processes`. Esses diretórios aparecem quando
o usuário ou o template realmente precisa deles.

`.ft/.gitignore` ignora apenas runtime acidental, caches, temporários, logs e
PIDs. Processos e ciclos são versionados.

## Fronteiras

### `docs/`: conhecimento humano

Um arquivo permanece visível quando o usuário precisa introduzi-lo, encontrá-lo,
revisá-lo ou mantê-lo como fonte de verdade. Exemplos:

- `PRD.md`;
- `TECH_STACK.md` ou `tech_stack.md`;
- `ui_criteria.md`;
- `PROJECT_BACKLOG.md`;
- `FEATURES.md`;
- contratos e dados de teste canônicos definidos pelo processo.

`PROJECT_BACKLOG.md` registra mudanças desejadas. `FEATURES.md` projeta as
capacidades efetivamente entregues: IDs `FEAT-*` permanecem estáveis, referenciam
itens `PB-*` concluídos e conservam evolução, depreciação ou remoção.

### `.ft/process/<template>/`: forks locais

Cada bundle é uma cópia versionada do catálogo global e passa a pertencer ao
projeto. A resolução é local-first:

1. se o template está registrado e seu `process.yml` é válido, use esse path;
2. caso contrário, copie o bundle global uma única vez e registre-o;
3. nunca execute diretamente `templates/`;
4. nunca substitua automaticamente um fork local.

`environment.yml` e `scripts/` acompanham o processo porque são mecanismos de
orquestração. Materialização durante `run` não semeia diretórios de produto.

O node final de meta-melhoria pode produzir `docs/process-improvements.yml`.
Cada achado é `local`, `global_candidate` ou `rejected`. Candidatos globais
permanecem pendentes até decisão do mantenedor; promoção exige referência global
aplicada e testada.

### `.ft/cycles/`: memória durável

Durante o run, nodes podem produzir relatórios transitórios em `docs/` para
validadores e LLMs. No `ft close`, a política de artefatos move os outputs
específicos para `.ft/cycles/<id>/` antes do merge.

Cada `cycle.yml` registra, entre outros:

- template, path local e versão/digest usados;
- executor, modelo e effort;
- steps concluídos e totais;
- resumo dos gates;
- índice de artefatos arquivados;
- horário e resultado do fechamento.

Estado, locks e logs brutos nunca entram nesse histórico.

### `$FT_HOME`: runtime descartável

```text
$FT_HOME/
├── worktrees/<projeto>/<cycle>/
│   └── state/
├── locks/<projeto>/
└── migrations/<projeto>/<timestamp>/
```

`FT_HOME` pode redirecionar a raiz. Nada daqui é materializado por template ou
mergeado para a branch principal.

## Manifesto V3

`.ft/manifest.yml` identifica o workspace e cataloga processos locais sem
eleger um default:

```yaml
schema_version: 3
processes:
  mvp-builder:
    path: .ft/process/mvp-builder/process.yml
    template: mvp-builder
    source_digest: sha256:...
    base_digest: sha256:...
  feature:
    path: .ft/process/feature/process.yml
    template: feature
    source_digest: sha256:...
    base_digest: sha256:...
defaults:
  llm_engine: codex
  llm_model: gpt-5.6-sol
  llm_effort: max
```

Um workspace recém-inicializado usa `processes: {}`. Seletores top-level de
processo e metadados de origem do schema anterior não pertencem ao V3. APIs que
consomem o catálogo recebem o nome explicitamente ou iteram todas as entradas;
nunca inferem prioridade pela ordem YAML.

O digest cobre `process.yml`, `environment.yml` e arquivos semânticos em
`scripts/`, incluindo path e modo de permissão. Apenas caches inequivocamente
gerados são ignorados.

## Política de artefatos

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

Itens `canonical` permanecem em `docs/`; itens `cycle` vão para o diretório do
ciclo. Se um path aparecer nas duas listas, a classificação canônica prevalece.

## Ciclo de vida

```text
ft init [dir]
  -> garante repositório Git com HEAD
  -> cria/reconcilia somente a base comum V3
  -> não escolhe template, não cria processo e não cria runtime

ft run <dir> --template <T> [--request ... | --input arquivo]
  -> exige workspace V3 saudável e checkout apto a originar worktree
  -> resolve T local-first e materializa copy-once quando necessário
  -> aloca id/branch/worktree atomicamente
  -> fixa path e digest do processo no estado do ciclo
  -> executa exclusivamente na worktree externa

ft close --cycle <id>
  -> adquire o lock de merge do projeto
  -> valida artefatos e governança do processo fixado
  -> arquiva outputs em .ft/cycles/<id>/
  -> faz o merge escolhido
  -> remove worktree/branch quando solicitado
```

Não existe fallback de execução no checkout principal. Sem Git ou HEAD válido,
`run` falha com orientação para reparar/inicializar o workspace.

## Concorrência

Múltiplos ciclos são o estado normal, não uma exceção:

```text
checkout principal ── snapshot A ── worktree cycle-07 (feature)
                  └── snapshot B ── worktree cycle-08 (tweak)
                  └── snapshot C ── worktree cycle-09 (bug)
```

Existem dois escopos de lock:

- **preparação**: seção crítica curta para reconciliar manifesto, materializar um
  template e reservar id/branch/worktree sem colisões;
- **close**: serializa alterações no checkout principal, incluindo merge e
  arquivamento.

Nenhum lock cobre a execução completa. Depois da preparação, runners avançam em
paralelo. Um close em espera não bloqueia nodes de outros ciclos.

Comandos que operam sobre estado seguem seleção determinística: zero ciclos é
erro; um pode ser inferido; dois ou mais exigem `--cycle` e listam as opções. Não
há fallback baseado na data de criação.

Fan-out de nodes dentro de um processo (`ft run --template <T> --parallel`) é
ortogonal à concorrência de ciclos.

## Entrada de demanda

`ft run` oferece formas uniformes:

```bash
ft run . --template feature --request "Adicionar busca por telefone"
ft run . --template feature --input demanda.md
```

O bundle declara se exige, aceita ou rejeita uma demanda. Essa política é
validada antes de criar o ciclo. O engine transporta a entrada para a worktree,
mas não inventa seeds genéricos em `docs/` ou `src/`.

## Diagnóstico e reparo

```bash
ft init . --check
ft init .
ft init . --fix
```

- `--check` é totalmente read-only e relata cada invariante;
- init saudável é idempotente;
- `--fix` restaura arquivos comuns ausentes e reconstrói o catálogo a partir de
  bundles locais válidos;
- reparo nunca sobrescreve processo ou histórico;
- manifesto corrompido recebe backup externo antes da substituição;
- casos ambíguos são recusados com ação manual explícita.

Reparo V3 não substitui migração de layout legado.

## Migração V2 → V3

O CLI detecta layouts antigos, mas nunca os executa nem cria V3 ao lado deles. A
migração deve ocorrer sem runtime em mutação:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

Ela:

1. valida contenção, YAML, symlinks e colisões antes de escrever;
2. move `process/` ou o bundle flat para `.ft/process/<nome>/` quando necessário;
3. preserva todos os processos locais e seus digests;
4. converte o manifesto para schema V3 e remove o seletor default sem promover
   substituto;
5. importa `docs/archive/<ciclo>/` para `.ft/cycles/<ciclo>/` sem sobrescrever
   histórico;
6. atualiza referências atuais inequívocas, sem reescrever `.ft/cycles/`;
7. move runtime legado para backup inativo em `$FT_HOME/migrations/`.

## Invariantes

- Todo workspace V3 é um repositório Git com HEAD.
- `ft init` nunca materializa template ou runtime.
- Toda nova execução exige `--template`.
- Todo processo executável vive em `.ft/process/<template>/process.yml`, está
  registrado e não atravessa symlink.
- Não existe processo default, principal ou inferido.
- A engine sempre executa o fork local fixado no ciclo.
- Cada ciclo usa worktree externa própria.
- Preparação e close são atomicamente protegidos, mas runners não compartilham
  um lock de longa duração.
- `ft close` não mergeia estado ou logs brutos.
- Histórico de ciclo permanece versionado depois da remoção da worktree.
