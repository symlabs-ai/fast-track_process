# ft engine — Fast Track

Motor determinístico de processos para solo dev + AI. O pacote se chama
`ft-engine`, mas o comando instalado é `ft`.

A versão instalada é exibida por `ft --version`; a fonte canônica do pacote é
[`ft/__about__.py`](ft/__about__.py).

## O que é

O Fast Track executa processos definidos em YAML. Python controla grafo, estado,
gates, worktrees e validadores; o LLM constrói artefatos somente quando um node
delega trabalho.

No contrato V3, inicializar um repositório e escolher um processo são ações
separadas:

- `ft init [dir]` cria apenas a base comum do Fast Track e garante Git com HEAD;
- `ft run <dir> --template <T>` materializa e executa o template escolhido;
- não existe processo principal ou default;
- ciclos com templates diferentes podem coexistir no mesmo repositório.

Projetos reais ficam fora deste repositório. Este repo é o engine e o catálogo
global; o guard bloqueia comandos de projeto aqui, exceto para manutenção
explícita com `FT_ALLOW_ENGINE_REPO=1`.

## Instalação local

Plataforma suportada: Linux/POSIX, Python 3.11 ou 3.12, Git e Bash. macOS é
best-effort; Windows nativo não é suportado (use WSL2). O sandbox de filesystem
do OpenCode também requer `bwrap` no Linux.

```bash
pip install -e .
ft --version
ft --help
```

## Criar um projeto

```bash
ft init meu-projeto
cd meu-projeto

# Adicione as fontes exigidas pelo template e faça commit.
mkdir -p docs
$EDITOR docs/PRD.md docs/TECH_STACK.md
git add -A && git commit -m "docs: seed product context"

ft run . --template mvp-builder --auto
```

`ft init` não seleciona nem copia template de **processo** e não semeia `docs/`
ou `src/`. A opção `--template` existe apenas para encadear um template de
inicialização (`kind: init`) depois da base comum. O comando prepara `.ft/`, o
manifesto, o contrato conservador `.ft/project.yml`, ignores e o repositório
Git. Repeti-lo em um workspace saudável é idempotente.

```bash
ft init --check   # diagnóstico somente leitura
ft init --fix     # reparo conservador e explícito
```

`--fix` reconstrói metadados e o catálogo a partir de processos locais válidos,
sem sobrescrever forks nem ciclos. Metadados corrompidos substituídos recebem
backup fora do projeto, sob `$FT_HOME`.

Manifesto inicial:

```yaml
schema_version: 3
processes: {}
```

## Rodar qualquer processo

`--template` é obrigatório para abrir um ciclo. Todos os tipos de trabalho usam
o mesmo entrypoint:

```bash
ft run . --template mdd --request "Definir o produto" --auto
# após os gates: ft close --cycle <id-mdd>
ft run . --template mvp-builder --auto
ft run . --template mvp-builder-fast --auto --parallel
ft run . --template mvp-builder-fast --route validation --input feedback.md --auto --parallel
ft run . --template feature --request "Adicionar busca por telefone" --codex
ft run . --template feature-fast --request "Adicionar busca por telefone" --codex
ft run . --template feature --input demanda.md --claude
ft run . --template bug --request "Terminal duplica o eco do input" --codex
ft run . --template bug-fast --request "Terminal duplica o eco do input" --codex
ft run . --template tweak --request "Mudar o botão Salvar para azul" --codex
```

Não há entrypoint especializado por categoria, opção para apontar um YAML
arbitrário ou execução sem seleção explícita de template.

Na primeira chamada, o template global é copiado para
`.ft/process/<template>/` e registrado no manifesto. Chamadas seguintes usam e
preservam esse fork local. O engine nunca executa `templates/` diretamente.

Cada template define a política de entrada. `--request` recebe uma demanda
curta; `--input` recebe um arquivo. Combinações ausentes ou incompatíveis falham
antes de criar worktree ou estado.

Templates construtores (`mvp-builder` e `mvp-builder-fast`) rodam enquanto o
projeto está em `building`. `feature`, `feature-fast`, `bug`, `bug-fast` e
`tweak` são manutenção e só rodam depois de um `ft project-close` READY.
`ft close` encerra um ciclo; não declara o projeto entregue.

## Ciclos em paralelo

Cada `ft run` aloca atomicamente seu próprio id, branch, worktree e estado. Não
existe bloqueio global de ciclo ativo nem flag para contorná-lo:

```bash
# Execute em terminais distintos.
ft run . --template feature --request "Adicionar busca por telefone" --auto
ft run . --template tweak --request "Reduzir padding do cabeçalho" --auto
```

Os runners avançam em paralelo mesmo usando templates diferentes. Um lock curto
protege a preparação compartilhada; um lock de close serializa merges no checkout
principal. `--parallel` em `ft run` é outra coisa: paralelismo dentro de um
único ciclo. Ele nunca escolhe a rota do processo. No `mvp-builder-fast`,
`--route validation` seleciona a continuação terminal e `--parallel` permite
decompor a demanda natural em foundation sequencial e lanes dinâmicas isoladas.
Contexto e progresso consideram apenas essa branch; o fechamento é
`validação → correção → auditoria somente do fix → regressão integrada → aceite`,
sem reiniciar o builder quando o stakeholder pede ajustes.

As worktrees ficam em:

```text
$FT_HOME/worktrees/<projeto>/<cycle>/
```

Ao fechar, artefatos específicos são arquivados em `.ft/cycles/<cycle>/`. Fontes
humanas como PRD, stack, backlog e catálogo de features permanecem em `docs/`.

## Comandos principais

```bash
ft run . --template <T> [--request "..."] [--input arquivo]
ft run . --template <T> --auto
ft continue --cycle <id>
ft continue --cycle <id> --sprint
ft continue --cycle <id> --auto
ft status --cycle <id> --full
ft status --cycle <id> --watch 60
ft graph --cycle <id>
ft approve "nota opcional" --cycle <id>
ft reject "motivo objetivo" --cycle <id>
ft reject "motivo objetivo" --auto --cycle <id>
ft fix "instrução" --cycle <id>
ft retry --cycle <id>
ft abort --cycle <id>
ft close --cycle <id>
ft runs
ft runs --done
ft runs --done-detailed  # execuções de step, incluindo retries/loops
ft project-status
ft project-close
ft project-reopen --reason "novo marco" --objective "Entregar ..." --target v2

ft llm-capabilities --json
ft llm-defaults --agent codex --model gpt-5.6-sol --effort max --json
```

Quando há exatamente um ciclo aplicável, comandos de acompanhamento podem
inferi-lo. Com dois ou mais, `--cycle` é obrigatório e o erro lista as opções; o
engine nunca escolhe pela data de criação.

`ft status --watch [SEGUNDOS]` abre uma tela fixa de monitoramento e redesenha o
status no mesmo lugar, sem acumular linhas como um log. Sem informar o intervalo,
usa 60 segundos. Cada tela mostra o horário da última atualização. `Ctrl+C`
encerra o monitoramento. O buffer normal do terminal é preservado para permitir
rolagem durante o acompanhamento. O status também informa o caminho absoluto da
worktree ativa do ciclo. A última linha mostra a atividade sanitizada mais recente
do log LLM como um tail fixo, substituído a cada atualização.

O DoD global versionado em `.ft/project.yml` exige `done`/`accepted` com
evidência para o escopo selecionado e pode declarar gates JSON/YAML adicionais.
Itens `blocked`/`deferred` continuam pendentes mesmo quando possuem decisão.
Somente `ft project-close`, com checkout limpo e nenhum ciclo aberto, registra
`.ft/project-readiness.yml` READY e muda a fase para `maintenance`.

`--auto` avança até human gate, MVP ou BLOCK. Ele não pula human gates;
`--bypass-human-gates` delega essas decisões ao LLM.

Todo gate humano é apresentado como um pacote de decisão: o que está sendo
decidido, por que agora, onde avaliar, checklist, limites e consequências de
aprovar ou rejeitar. `ft status --cycle <id>` repete o mesmo contexto; processos
locais antigos recebem um fallback derivado do grafo e das evidências existentes.

Todo `ft fix` dirigido renova obrigatoriamente o review que encontrou o defeito.
Quando a correção nasce em human gate, `ft fix`/`ft reject` executam
`fix → review focal` e retornam ao mesmo gate, sem reabrir etapas intermediárias
já aprovadas. A flag antiga `--audit-origin` permanece aceita apenas para
compatibilidade. O review focal sempre recebe o pedido e o finding que deve
auditar; correções de UI exigem confirmação do resultado visual/físico. Nodes
de correção podem declarar `fix_review`: nesse caso só a cadeia focal até esse
review é reaberta. Em processos históricos, a engine suspende o prompt amplo do
review de origem durante a auditoria do fix.

O template `mvp-builder` classifica aprendizados de processo como `local`,
`global_candidate` ou `rejected`. Antes do close, o mantenedor decide candidatos
pendentes com `ft process-candidates --cycle <id>` e registra a referência global
quando promover uma mudança.

## Executors LLM

Use `--codex`, `--claude [modelo]`, `--gemini [modelo]` ou
`--opencode [modelo]`, além de `--effort`, para escolher uma combinação
compatível. Defaults persistentes vivem em `.ft/manifest.yml`.
`ft llm-capabilities` descobre opções pelas CLIs instaladas e `ft llm-defaults`
valida a combinação antes de gravá-la.

Por padrão, OpenCode roda em sandbox de filesystem via `bwrap`: o worktree fica
read-only e somente outputs/write_scope do node são graváveis. Use as variáveis
`FT_OPENCODE_CONTEXT_LIMIT`, `FT_OPENCODE_OUTPUT_LIMIT` e
`FT_OPENCODE_SANDBOX` para ajustar a integração.

## Templates

| Template | Uso |
|---|---|
| `base` | Processo mínimo para composição local |
| `feature` | Evolução incremental de manutenção após entrega |
| `feature-fast` | Feature de manutenção com sessões persistentes e auditoria do delta |
| `bug` | Correção focal em projeto entregue, com regressão RED→GREEN |
| `bug-fast` | Bug de manutenção em duas chamadas LLM e fix focal |
| `tweak` | Mudança pequena em projeto entregue |
| `mdd` | Definição, pacote executivo, 12 PNGs do pitch, protótipo vertical do site e handoff |
| `mvp-builder` | Construtor completo durante a fase `building` |
| `mvp-builder-fast` | Construtor rápido com plano interno, sessões e macro-nodes |
| `fast-track-v2` | Processo histórico V2 |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `fastfy` | Adoção de repositório legado na base canônica Fast Track |
| `material_design_pwa` | Evolução de UI existente para Material Design 3 e PWA |

Templates de inicialização (`kind: init`) — usados por `ft init --template`,
recusados pelo run:

| Template | Uso |
|---|---|
| `init-default` | Base de todo projeto: git, .gitignore, .env.example, commit inicial |
| `symlabs` | Ambiente da org Symlabs: Poetry/src, .env, CLAUDE.md, registro no SymGateway |
| `tecnospeed` | Ambiente da org Tecnospeed; exige credenciais locais emitidas pelo DevOps |

Integrações externas pertencem ao processo local: nos runs, o engine chama
scripts apenas ao lado do template materializado
(`.ft/process/<nome>/scripts/`); no init, apenas os scripts declarados no
`template.yml` de um template `kind: init`.

## Migração

Layouts anteriores (`process/`, `.ft/process/process.yml` ou manifesto V2) são
migrados explicitamente, sem ciclo/runtime em mutação:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

O migrador preserva processos, forks e histórico, converte o manifesto para V3 e
remove somente o conceito de default. Runtime legado recebe backup inativo sob
`$FT_HOME/migrations/`. O preflight recusa colisões e symlinks antes de mover
qualquer fonte.

## Documentação

- Guia do engine: [`docs/ft_engine_usage.md`](docs/ft_engine_usage.md)
- Arquitetura: [`docs/mvp-builder-architecture.md`](docs/mvp-builder-architecture.md)
- Playbook de condução: [`AGENTS.md`](AGENTS.md)
- Catálogo global: [`templates/`](templates/)

## Validação local

```bash
python -m ruff check ft tests
python -m pytest -q
python -m pip wheel --no-deps --wheel-dir dist .
```
