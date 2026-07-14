# Template `bug`

Processo focal de `ft run --template bug` para corrigir um defeito reproduzível
com teste de regressão RED→GREEN.

```bash
ft run . --template bug \
  --request "Terminal duplica o comando ao ecoar input" --codex
```

Vários bugs independentes podem ocupar ciclos paralelos:

```bash
ft run . --template bug --request "bug A" --codex
ft run . --template bug --request "bug B" --codex
ft run . --template bug --request "bug C" --codex
```

Cada comando cria sua própria worktree e fixa sua própria cópia local do
template. A coordenação de dependências entre demandas continua sendo uma
decisão do condutor.

## Caminho feliz

1. preflight determinístico, sem build/test;
2. uma delegação de código: diagnóstico, teste RED, correção e mesmo teste GREEN;
3. uma validação completa `build + test`;
4. aceite humano;
5. uma delegação documental curta para PB, FEAT e `CHANGELOG.md` com `#BUG`;
6. gate final reaproveitando o receipt, sem repetir a suíte.

São duas chamadas LLM, um gate humano e nenhuma fase de discovery, perguntas,
scope gate ou review independente. A meta operacional é p50 de até 10 minutos
e p95 de até 20 minutos, fora espera humana e a duração intrínseca da suíte.

Use `--template feature` quando não houver reprodução determinística, quando o
pedido representar comportamento novo ou envolver contrato público,
auth/security, migrations, dados, dependências, infraestrutura ou mudança
transversal.

Entradas de changelog seguem esta convenção:

- bug: `#BUG` como primeiro token textual;
- feature: `#FEAT` como primeiro token textual;
- tweak: nenhuma entrada obrigatória.
