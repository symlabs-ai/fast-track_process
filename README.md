# ft engine — Fast Track

Motor determinístico de processos para solo dev + AI. O pacote se chama
`ft-engine`, mas o comando instalado é `ft`.

Versão atual: **0.13.2**.

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
ft init meu-projeto --template fast-track-v3
cd meu-projeto
git init
git add -A
git commit -m "chore: bootstrap fast track"
ft run . --auto
```

O ciclo roda em worktree externo:

```text
~/.ft/worktrees/<projeto>/cycle-NN/
```

A raiz do projeto permanece limpa até `ft close` fazer o merge escolhido.

## Comandos principais

```bash
ft run .                       # iniciar ciclo
ft run . --auto                # avançar automaticamente até human gate/MVP/BLOCK
ft continue                    # avançar um node
ft continue --sprint           # avançar uma sprint
ft continue --auto             # avançar até o próximo human gate/MVP/BLOCK
ft status --full               # status + grafo
ft graph                       # grafo com status
ft approve "nota opcional"     # aprovar human gate
ft reject "motivo objetivo"    # rejeitar e reenviar com feedback
ft fix "instrução"             # corrigir pending_fix
ft close                       # encerrar ciclo e escolher merge
```

Use `--codex`, `--claude [modelo]`, `--gemini [modelo]` ou `--opencode [modelo]`
para escolher o executor LLM. O default de `--opencode` é
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
| `base` | Estrutura mínima com `process/process.yml`, `docs/` e `src/` |
| `fast-track-v3` | Processo completo recomendado para MVP |
| `fast-track-v2` | Processo V2 legado |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `symgateway` | Exemplo de ambiente com scripts de integração SymGateway |

Integrações externas pertencem ao projeto/template de ambiente. O engine chama
scripts em `process/scripts/` e não precisa conhecer o provedor.

## Documentação

- Guia do engine: [`docs/ft_engine_usage.md`](docs/ft_engine_usage.md)
- Arquitetura V3: [`docs/V3_ARCHITECTURE.md`](docs/V3_ARCHITECTURE.md)
- Playbook de condução: [`AGENTS.md`](AGENTS.md)
- Templates: [`templates/`](templates/)
- Processo legado V2: [`process/fast_track/`](process/fast_track/)

## Validação local

```bash
python -m pytest -q
FT_ALLOW_ENGINE_REPO=1 ft --process templates/fast-track-v3/process.yml validate
FT_ALLOW_ENGINE_REPO=1 ft --process templates/ft-ui-prototype/process.yml validate
```
