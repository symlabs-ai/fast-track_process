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

Durante a implementação, testes rápidos e sondas são executados sem shell
intermediário com:

```bash
.ft/process/feature/scripts/product.sh focal -- python -m pytest tests/test_busca.py -q
```

Ao concluir cada implementação ou correção, o gate determinístico executa uma
única validação completa e grava seu receipt no caminho canônico:

```bash
.ft/process/feature/scripts/product.sh full --record docs/feature-validation.json
```

O receipt compacto persiste resultado, fingerprint, instante, product root,
comandos e contagem de arquivos; ele não serializa a lista de arquivos/hashes.
O fingerprint continua ligado às versões das ferramentas e aos hashes dos inputs
executáveis versionados/não ignorados do projeto e dos scripts do processo.
Documentos e CHANGELOG reconciliados depois do aceite não entram nesse snapshot.
Review usa `verify` para reaproveitar a evidência antes do aceite. Depois da
reconciliação documental, o gate final faz uma única verificação do receipt e
dos documentos reconciliados; qualquer mudança material exige outro `full`.

Em batches paralelos, o orquestrador pode prefixar a demanda com
`reserved_backlog_item: PB-NNN`. O discovery deve preservar essa reserva para
que duas features da mesma wave não disputem o mesmo ID.

## Contrato

- uma demanda e uma feature alvo por ciclo;
- perguntas iterativas antes de congelar o escopo;
- nenhum código antes do human gate de escopo;
- uma validação completa `make build` + `make test`, com receipt determinístico,
  obrigatória após cada implementação/correção;
- aceite humano antes de atualizar backlog e catálogo;
- reconciliação final obrigatória de `docs/PROJECT_BACKLOG.md`,
  `docs/FEATURES.md`, documentação canônica afetada e `CHANGELOG.md`;
- somente o PB/FEAT selecionado pode ser reconciliado;
- merge somente por `ft close`.

## Suporte do engine

Este template pertence ao entrypoint `feature` e não pode ser passado ao
`ft init`. O comando materializa a cópia aninhada uma única vez, fixa path e
digest no estado, segue novamente o grafo após rejeições e aplica o
`close_policy` restrito ao PB selecionado. O processo global é apenas fonte de
materialização e nunca é executado.

Os quatro nodes LLM usam perfis `feature_delta.*` próprios do processo incremental
em vez de herdar HyperMode do `mvp-builder`. O engine compõe apenas demanda,
contratos, feedback, diff e recortes focais aplicáveis a discovery, implementação,
review ou reconcile.

Consulte `examples/feature.md` para o formato produzido ao final do discovery.
