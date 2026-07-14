# Template `bug`

Processo focal do comando `ft feature` para corrigir um defeito reproduzível
com teste de regressão RED→GREEN.

```bash
ft feature "Terminal duplica o comando ao ecoar input" --template bug --codex
```

Vários bugs usam o orquestrador paralelo já existente:

```bash
ft feature "bug A" "bug B" "bug C" --parallel --template bug --codex
```

O planner agrupa bugs por áreas e dependências; worktrees sem sobreposição
rodam juntas e as demais viram waves sequenciais. Não existe um orquestrador
alternativo para `bug`.

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
