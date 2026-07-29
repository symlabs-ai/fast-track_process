# Template `feature-fast`

Processo incremental para implementar exatamente uma nova capacidade, evolução
ou melhoria em um produto FT já existente, preservando conversas LLM por sprint.

## Uso

```bash
ft run . --template feature-fast --request "Adicionar busca por telefone" --claude
# ou
ft run . --template feature-fast --input demanda.md --claude
# ou, para responder à demanda no prompt
ft run . --template feature-fast --claude
```

Na primeira invocação, o engine copia este diretório para
`.ft/process/feature-fast/`. A partir daí, somente a cópia local versionada é
executada. O conteúdo global nunca é usado como processo runtime e nunca
sobrescreve o fork local.

## Sessões persistentes

O engine cria um plano interno consultivo antes do grafo e mantém afinidade de
conversa por sprint para Claude e Codex. `discovery` ocupa a sessão de escopo;
`implement`, `fix` e `evidence` compartilham a sessão de build; as reviews usam
lanes isoladas para preservar independência; e `reconcile` usa a sessão de aceite.
Human gates, validators Python, receipts e rotas de rejeição continuam
autoritativos. Se o provider perder uma sessão, o engine reidrata uma nova
conversa uma única vez usando plano, estado e artefatos atuais.

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
.ft/process/feature-fast/scripts/product.sh focal -- python -m pytest tests/test_busca.py -q
```

O preflight grava uma `baseline attestation` própria. Depois da implementação,
`feature.product_validate` grava outro receipt, específico do resultado. O
comando `ensure` primeiro tenta verificar o receipt local e só executa a suíte
completa quando ele está ausente ou inválido:

```bash
.ft/process/feature-fast/scripts/product.sh ensure --record docs/feature-validation.json
```

O receipt compacto persiste resultado, fingerprint, instante, product root,
comandos e contagem de arquivos; ele não serializa a lista de arquivos/hashes.
O fingerprint continua ligado às versões das ferramentas e aos hashes dos inputs
executáveis versionados/não ignorados do projeto e dos scripts do processo.
Documentos e CHANGELOG reconciliados depois do aceite não entram nesse snapshot.
O node `evidence` não altera código: ele referencia ACs, testes, comandos e
artefatos existentes. O gate de evidência prova integridade referencial; a
review continua responsável por julgar a suficiência semântica. Seu primeiro
veredicto de implementação rejeitado segue para `fix_prepare`, que congela a
review, seus F-* e o commit revisado. `fix` corrige somente esses achados,
`fix_validate` confirma o delta focal e `fix_review` audita apenas esse delta.
Somente depois da aprovação, `fix_full_validate` renova o receipt completo e as
lanes impactadas uma vez, antes do aceite. Tentativas intermediárias não
repetem build+test.
Lacunas de prova voltam a `evidence`, contradições de escopo voltam a
`discovery` e aprovações seguem para o aceite. Se contrato, AC ou workset forem
ampliados, a auditoria focal exige `full_review` e retorna ao caminho completo.

Antes da suíte completa, uma pré-revisão semântica focal verifica todos os ACs,
testes, compatibilidade e escopo. Demandas claras com mais de 6 ACs são
recusadas até serem divididas em fatias verticais de 4–6 ACs. O workset inicial
é expandido pelo delta real e por testes/pares semanticamente relacionados.

Review usa `verify` para reaproveitar a evidência antes do aceite. Cada
pré-review e review completa recebe um ID determinístico ligado ao impacto,
evidência e receipts atuais; artefatos de uma iteração anterior não são aceitos.
`feature-workset.yml` pode declarar `receipt_dependencies` para lanes caras.
O processo compara seus paths antes/depois: uma lane física impactada exige
novo receipt; uma lane inalterada registra `reuse` e não repete o ensaio.
Essa mesma checagem ocorre depois de um fix focal: o receipt físico anterior
continua válido enquanto nenhuma dependência declarada tiver sido alterada.
Depois da
reconciliação documental, o gate final faz uma única verificação do receipt e
dos documentos reconciliados; qualquer mudança material exige outro `full`.
Na reconciliação, o LLM propõe apenas a linha do PB, a linha da FEAT, uma entrada
`#FEAT` e eventuais documentos adicionais. O engine valida os IDs e aplica essas
operações preservando deterministicamente o restante das tabelas.

Features independentes podem ocupar ciclos paralelos, mas cada demanda deve
referenciar um PB preexistente e distinto. Para `type=new`, o processo reserva o
FEAT definitivo sob lock curto; IDs reservados nunca são reutilizados. No close,
conflitos conservadores e aditivos em CHANGELOG, backlog e catálogo são
reconciliados automaticamente; qualquer conflito ambíguo permanece manual.

Cache compartilhado é experimental, desligado por padrão e só funciona quando
a validação foi declarada hermética:

```bash
FT_FEATURE_SHARED_CACHE=1 FT_FEATURE_VALIDATION_HERMETIC=1 \
  .ft/process/feature-fast/scripts/product.sh ensure --record docs/feature-validation.json
```

A chave inclui identidade do projeto, fingerprint executável, comandos,
processo, toolchain (inclusive o pacote editable de `ft`), dependências externas
declaradas, TTL e single-flight por lock. Sem a declaração hermética, `ensure`
permanece estritamente local.

Quando múltiplos ciclos disputam CPU, memória ou serviços locais não herméticos,
o recurso experimental `product_full` pode serializar somente as validações
completas do mesmo repositório (o lock vive no Git common-dir compartilhado por
todas as worktrees):

```bash
FT_FEATURE_PRODUCT_FULL_RESOURCE=1 \
  .ft/process/feature-fast/scripts/product.sh ensure --record docs/feature-validation.json
```

O recurso fica desligado por padrão e é independente do cache: ele não reutiliza
resultados, apenas evita duas execuções completas concorrentes. A ausência de
`flock` ou de um projeto Git válido falha explicitamente quando o opt-in está
ativo.

## Contrato

- uma demanda e uma feature alvo por ciclo;
- no máximo 6 ACs por ciclo claro; demandas maiores viram fatias verticais;
- perguntas iterativas antes de congelar o escopo;
- nenhum código antes do human gate de escopo;
- implementação, pré-review, validação do produto, evidência e review são
  etapas separadas;
- uma validação completa `make build` + `make test`, com receipt determinístico,
  obrigatória após cada episódio de implementação/correção;
- correções rejeitadas pela review usam fix focal + receipt completo + auditoria
  somente do delta; expansão de contrato, AC ou workset volta à review completa;
- aceite humano antes de atualizar backlog e catálogo;
- reconciliação final obrigatória de `docs/PROJECT_BACKLOG.md`,
  `docs/FEATURES.md`, documentação canônica afetada e `CHANGELOG.md`;
- somente o PB/FEAT selecionado pode ser reconciliado;
- merge somente por `ft close`.

## Suporte do engine

Este template pertence ao entrypoint universal `run`. O `ft init` não seleciona
templates. `ft run --template feature-fast` materializa a cópia aninhada uma única
vez, fixa path e digest no estado, segue novamente o grafo após rejeições e aplica o
`close_policy` restrito ao PB selecionado. O processo global é apenas fonte de
materialização e nunca é executado.

Os oito nodes LLM usam perfis `feature_delta.*` próprios do processo incremental
em vez de herdar HyperMode do `mvp-builder`. O engine compõe apenas demanda,
contratos, feedback, diff e recortes focais aplicáveis a discovery, implementação,
fix, review ou reconcile.

Consulte `examples/feature.md` para o formato produzido ao final do discovery.
