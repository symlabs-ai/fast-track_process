# Template `bug-fast`

Processo focal de `ft run --template bug-fast` para corrigir um defeito
reproduzível com sessão persistente, prova RED→GREEN e review independente.

```bash
ft run . --template bug-fast \
  --request "Terminal duplica o comando ao ecoar input" \
  --codex gpt-5.6-sol --effort high
```

## Caminho feliz

1. preflight determinístico, sem build/test;
2. uma chamada builder: diagnóstico, teste RED, correção mínima e GREEN;
3. uma validação completa `build + test`, registrada em receipt;
4. uma chamada reviewer, em sessão separada, auditando somente o diff;
5. aceite humano;
6. reconciliação Python de PB, FEAT, changelog e resultado;
7. gate final que reaproveita receipt e review, sem repetir a suíte.

O caminho feliz usa exatamente duas chamadas LLM. `initial_plan: disabled`
elimina uma chamada de planejamento; builder e reviewer ocupam sessões
persistentes independentes dentro da sprint.

Quando a review encontra um defeito, `bug-fast` ancora seus achados B-*, reusa
a sessão do builder para um fix focal e audita somente o delta. A suíte completa
e o receipt são renovados uma única vez depois da aprovação focal, antes do
aceite; tentativas intermediárias executam apenas GREEN e auditoria. O restante
do julgamento permanece fechado. Se o delta
escapar dos paths originais, passa por review completa; se exigir contrato,
capacidade, auth/security, migration, dados, dependência, infraestrutura ou
mudança transversal, o ciclo bloqueia e orienta:

```bash
ft abort --cycle <id>
ft run . --template feature-fast --request "..." --codex
```

O aceite humano nunca é pulado por `--auto`. Rejeição do stakeholder volta ao
RED→GREEN e exige nova review.

Entradas de changelog seguem a convenção `#BUG` como primeiro token textual.
Não há delegação documental final: a reconciliação é idempotente, aditiva e
limitada aos IDs declarados em `docs/bug-report.md`.

## Medição

Para comparar com `bug`, use `ft status --report`, o log e os traces do ciclo.
Separe:

- wall clock do ciclo;
- espera em human gate;
- tempo ativo e quantidade de chamadas LLM;
- retries/timeouts;
- duração de RED/GREEN e da única suíte completa;
- tempo da reconciliação determinística.
