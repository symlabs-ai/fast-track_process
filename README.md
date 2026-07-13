# ft engine — Fast Track

Motor determinístico de processos para solo dev + AI. O pacote se chama
`ft-engine`, mas o comando instalado é `ft`.

Versão atual: **0.13.5**.

## O que é

O Fast Track executa um processo definido em YAML: o Python controla grafo,
estado, gates, worktrees e validadores; o LLM apenas constrói artefatos quando
um node delega trabalho.

Projetos reais ficam fora deste repositório. Este repo é o template/engine e o
guard bloqueia `ft init`/`ft run .` aqui, exceto com `FT_ALLOW_ENGINE_REPO=1`
para desenvolvimento do próprio engine.

## Instalação local

```bash
pip install -e .
ft --help
```

## Criar um projeto

```bash
ft init meu-projeto --template mvp-builder
cd meu-projeto
git init
git add -A
git commit -m "chore: bootstrap fast track"
ft run . --auto
```

`ft init` não escolhe um processo implicitamente: `--template` é obrigatório e
`ft init --help` mostra os nomes compatíveis com esse entrypoint no catálogo
instalado. Templates de outros comandos, como `feature`, não são aceitos no init.
Se `.ft/manifest.yml` já existir, uma nova chamada a `ft init` falha.

Um projeto pode manter vários processos locais. O manifesto registra o default e
os processos nomeados; quando um entrypoint recebe `--template`, a primeira
invocação materializa o template aplicável sob `.ft/process/<template>/` e as seguintes
preservam a cópia versionada. O runtime nunca executa arquivos de `templates/`
diretamente.

```yaml
schema_version: 2
default_process: mvp-builder
processes:
  mvp-builder:
    path: .ft/process/mvp-builder/process.yml
    template: mvp-builder
    entrypoint: init
```

## Evoluir uma feature

Num produto FT já inicializado e commitado:

```bash
ft feature "Adicionar busca por telefone" --template feature --claude
# ou: ft feature --input demanda.md --template feature --codex
```

Na primeira chamada, o template é copiado para `.ft/process/feature/`; chamadas
seguintes preservam esse fork local. A demanda existe apenas na worktree do
ciclo. Perguntas, aprovação de escopo, implementação, review e aceite são
conduzidos pelo grafo. Ao final, `ft close` valida somente o PB selecionado, faz
merge full e remove worktree/branch.

O ciclo roda em worktree externo:

```text
~/.ft/worktrees/<projeto>/cycle-NN/
```

A raiz do projeto permanece limpa até `ft close` fazer o merge escolhido.
Ao fechar, os artefatos específicos da execução são arquivados em
`.ft/cycles/<cycle>/`; PRD, stack, critérios de UI, backlog e catálogo de features
permanecem em `docs/`. O backlog descreve mudanças desejadas; `docs/FEATURES.md`
descreve as capacidades efetivamente entregues.

## Comandos principais

```bash
ft run .                       # iniciar ciclo
ft feature "demanda" --template feature  # evoluir capacidade existente
ft run . --auto                # avançar automaticamente até human gate/MVP/BLOCK
ft continue                    # avançar um node
ft continue --sprint           # avançar uma sprint
ft continue --auto             # avançar até o próximo human gate/MVP/BLOCK
ft status --full               # status + grafo
ft llm-capabilities --json     # modelos/efforts anunciados pelas CLIs locais
ft llm-defaults --agent codex --model gpt-5.6-sol --effort max --json
ft graph                       # grafo com status
ft approve "nota opcional"     # aprovar human gate
ft reject "motivo objetivo"    # rejeitar e reenviar com feedback
ft fix "instrução"             # corrigir pending_fix
ft process-candidates          # revisar melhorias candidatas ao processo global
ft close                       # encerrar ciclo e escolher merge
```

O template `mvp-builder` classifica cada aprendizado de processo como `local`,
`global_candidate` ou `rejected`. `ft close` bloqueia enquanto houver candidato
global pendente; o mantenedor registra `promoted`, `deferred` ou `rejected` com
`ft process-candidates PI-NNN --status ... --reason "..."`. Promoções exigem
uma referência ao commit/path global que recebeu e validou a mudança.

Use `--codex`, `--claude [modelo]`, `--gemini [modelo]` ou `--opencode [modelo]`
para escolher o executor LLM e `--effort` para selecionar um nível compatível.
Os defaults persistentes vivem em `.ft/manifest.yml`; `ft llm-capabilities`
descobre as opções pelas CLIs instaladas e `ft llm-defaults` valida e grava uma
combinação sem editar o manifest por fora do engine. O default de `--opencode` é
`pgx/zai-org_glm-4.7-flash`. Também é possível definir `FT_LLM_ENGINE=opencode`.
Para esse modelo default, o `ft` anuncia ao OpenCode uma janela de contexto de
200k tokens e saída de 32k tokens; sobrescreva com `FT_OPENCODE_CONTEXT_LIMIT`
e `FT_OPENCODE_OUTPUT_LIMIT` se o servidor expuser limites diferentes.
Por padrão, execuções OpenCode rodam em sandbox de filesystem via `bwrap`: o
worktree fica read-only e apenas outputs/write_scope do node são writable
(`FT_OPENCODE_SANDBOX=0` desabilita).

## Templates

| Template | Uso |
|----------|-----|
| `base` | Estrutura mínima com `.ft/process/base/process.yml`, `docs/` e `src/` |
| `feature` | Evolução incremental de uma única feature em produto FT existente |
| `mvp-builder` | Processo completo recomendado para construir um MVP do zero |
| `fast-track-v2` | Processo V2 legado |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `symgateway` | Exemplo de ambiente com scripts de integração SymGateway |

Integrações externas pertencem ao projeto/template de ambiente. O engine chama
scripts exclusivamente ao lado do processo selecionado,
`.ft/process/<nome>/scripts/`.

Projetos do layout anterior (`process/` ou `.ft/process/process.yml`) devem ser
migrados explicitamente, sem ciclo/runtime ativo:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

Use `--cycle-id <id>` para atribuir os relatórios soltos ao último ciclo conhecido.
O migrador também importa `docs/archive/<ciclo>/` e retira runtime legado do repo,
preservando-o como backup inativo em `$FT_HOME/migrations/`.
Referências atuais ao processo são atualizadas; o conteúdo arquivado dos ciclos é
preservado byte a byte. O preflight recusa colisões, manifestos inválidos e
symlinks antes de mover qualquer fonte.

O CLI detecta processos legados apenas para exigir `ft migrate-layout`; nunca os
executa nem cria o layout v2 em paralelo.

## Documentação

- Guia do engine: [`docs/ft_engine_usage.md`](docs/ft_engine_usage.md)
- Arquitetura do MVP Builder: [`docs/mvp-builder-architecture.md`](docs/mvp-builder-architecture.md)
- Playbook de condução: [`AGENTS.md`](AGENTS.md)
- Templates: [`templates/`](templates/)
- Processo legado V2: [`process/fast_track/`](process/fast_track/)

## Validação local

```bash
python -m pytest -q
FT_ALLOW_ENGINE_REPO=1 ft --process templates/mvp-builder/process.yml validate
FT_ALLOW_ENGINE_REPO=1 ft --process templates/feature/process.yml validate
FT_ALLOW_ENGINE_REPO=1 ft --process templates/ft-ui-prototype/process.yml validate
```
