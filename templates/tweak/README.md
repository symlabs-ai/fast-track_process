# Tweak

Processo ultraleve de `ft run --template tweak` para alterações pequenas e
locais: cor, espaçamento, texto, ícone ou comportamento simples sem mudança de
contrato.

```bash
ft run . --template tweak \
  --request "Mude a cor do botão Salvar para azul" --codex
```

Meta operacional: **3–10 minutos** no caminho feliz. O grafo faz um preflight
determinístico, uma única delegação de implementação, um build rápido e um gate
humano de aceite. Não há discovery, planejamento, review, reconcile, suíte
completa ou E2E por padrão. Retries e auto-fixes automáticos estão desligados.
A delegação tem budget total de 1800 segundos (30 min), inclusive backoff
interno: o modelo/effort escolhido pelo usuário é preservado, mas não pode
ocupar uma hora.
`--parallel` continua disponível para grupos paralelos declarados dentro do
grafo. Ciclos `tweak` distintos também podem rodar simultaneamente.

## Limites

- uma mudança coerente de aparência, copy ou comportamento local;
- até 4 arquivos de produto/teste;
- até 160 linhas adicionadas + removidas;
- até 256 kB por arquivo e 256 kB de patch total;
- sem dependências, lockfiles, migrations, auth, contratos, infraestrutura,
  documentação canônica ou alterações no próprio processo;
- no máximo um teste/check focal de até 60 segundos durante a implementação;
  seu comando, resultado e diff são registrados automaticamente;
- o gate confere o escopo, roda somente `make build` e confere o escopo outra
  vez, com budget total de 120 segundos.

Os commits automáticos do engine desativam hooks e assinatura (`hooksPath`
neutro, `--no-verify --no-gpg-sign`) somente neste template. Isso evita repetir
pipelines amplas/interativas; a salvaguarda do tweak é o check focal comprovado
+ build rápido + limites determinísticos acima.

O preflight ou o validator bloqueia o ciclo quando a demanda não cabe nesses
limites. Nesse caso, descarte a worktree e rode o processo normal:

```bash
ft abort
ft run . --template feature --request "<mesma demanda>" --codex
```

O tweak não cria PB/FEAT nem altera PROJECT_BACKLOG, FEATURES ou CHANGELOG. Seu
registro durável é o commit integrado e os artefatos em `.ft/cycles/<ciclo>/`.
