# Template `feature`

Processo incremental para implementar exatamente uma nova capacidade, evolução
ou melhoria em um produto FT já existente.

## Uso

```bash
ft feature "Adicionar busca por telefone" --template feature --claude
# ou
ft feature --input demanda.md --template feature --claude
# ou, para responder à demanda no prompt
ft feature --template feature --claude
```

Na primeira invocação, o engine copia este diretório para
`.ft/process/feature/`. A partir daí, somente a cópia local versionada é
executada. O conteúdo global nunca é usado como processo runtime e nunca
sobrescreve o fork local.

Toda a demanda, discovery, implementação, testes e aceite acontecem numa
worktree externa. Depois do aceite:

```bash
ft close --merge full
```

arquiva os artefatos do ciclo, faz merge no checkout principal e remove a
worktree. `ft abort` descarta tudo sem merge.

## Pré-requisitos

- projeto já inicializado pelo FT;
- `project/Makefile` ou `src/Makefile` com `test`, `build`, `run` e `url`;
- `docs/PRD.md`;
- `docs/PROJECT_BACKLOG.md` válido;
- `docs/FEATURES.md` válido;
- demanda gravada pelo comando em `docs/feature-request.md` dentro da worktree.

O helper local `scripts/product.sh` detecta exatamente um desses diretórios pelo
Makefile. A presença simultânea dos dois é tratada como ambígua e bloqueia o
preflight, evitando que o processo altere o produto errado.

## Contrato

- uma demanda e uma feature alvo por ciclo;
- perguntas iterativas antes de congelar o escopo;
- nenhum código antes do human gate de escopo;
- `make test` e `make build` obrigatórios em cada implementação/correção;
- aceite humano antes de atualizar backlog e catálogo;
- somente o PB/FEAT selecionado pode ser reconciliado;
- merge somente por `ft close`.

## Suporte do engine

Este template pertence ao entrypoint `feature` e não pode ser passado ao
`ft init`. O comando materializa a cópia aninhada uma única vez, fixa path e
digest no estado, segue novamente o grafo após rejeições e aplica o
`close_policy` restrito ao PB selecionado. O processo global é apenas fonte de
materialização e nunca é executado.

Consulte `examples/feature.md` para o formato produzido ao final do discovery.
