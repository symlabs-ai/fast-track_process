# ft engine — Fast Track

Motor determinístico de processos para solo dev + AI. O pacote se chama
`ft-engine`, mas o comando instalado é `ft`.

Versão atual: **0.13.6**.

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

```bash
pip install -e .
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

`ft init` não recebe opções de seleção de template, não copia processo e não
semeia `docs/` ou `src/`. Ele prepara `.ft/`, o manifesto, ignores e o
repositório Git. Repeti-lo em um workspace saudável é idempotente.

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
ft run . --template mvp-builder --auto
ft run . --template feature --request "Adicionar busca por telefone" --codex
ft run . --template feature --input demanda.md --claude
ft run . --template bug --request "Terminal duplica o eco do input" --codex
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
principal. `--parallel` em `ft run` é outra coisa: fan-out de nodes de um único
processo quando o YAML declara um `parallel_group`.

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
ft graph --cycle <id>
ft approve "nota opcional" --cycle <id>
ft reject "motivo objetivo" --cycle <id>
ft fix "instrução" --cycle <id>
ft retry --cycle <id>
ft abort --cycle <id>
ft close --cycle <id>
ft runs

ft llm-capabilities --json
ft llm-defaults --agent codex --model gpt-5.6-sol --effort max --json
```

Quando há exatamente um ciclo aplicável, comandos de acompanhamento podem
inferi-lo. Com dois ou mais, `--cycle` é obrigatório e o erro lista as opções; o
engine nunca escolhe pela data de criação.

`--auto` avança até human gate, MVP ou BLOCK. Ele não pula human gates;
`--bypass-human-gates` delega essas decisões ao LLM.

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
| `feature` | Evolução incremental de uma capacidade existente |
| `bug` | Correção focal com regressão RED→GREEN |
| `tweak` | Mudança pequena e de baixo risco |
| `mvp-builder` | Processo completo recomendado para um MVP |
| `fast-track-v2` | Processo histórico V2 |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `symgateway` | Ambiente com integração SymGateway opt-in |

Integrações externas pertencem ao processo local. O engine chama scripts apenas
ao lado do template materializado, em `.ft/process/<nome>/scripts/`.

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
python -m pytest -q
```
