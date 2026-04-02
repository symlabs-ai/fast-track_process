# environment/ — Configuração Local do Workspace

Esta pasta contém configurações específicas da instalação local do Fast Track.
Os arquivos `*.md` (exceto este README) são **gitignored** — cada usuário mantém o seu.

O `setup_env.sh` cria os arquivos a partir dos templates `*.example.md` na primeira execução.

## Arquivos

| Arquivo | Template | Descrição |
|---------|----------|-----------|
| `gateway.md` | `gateway.example.md` | Como registrar projetos no gateway LLM deste workspace |

## Como funciona

O `ft init` lê `environment/gateway.md` e injeta as instruções no primeiro node do processo,
evitando o ciclo "gateway bloqueou → chamar DevOps → reiniciar".

Se `gateway.md` não existir, `ft init` avisa mas não bloqueia.
