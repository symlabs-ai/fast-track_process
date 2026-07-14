# Fluxo do processo Tweak

```text
tweak.preflight (determinístico, <10 s)
  └─ demanda fora dos limites → BLOCK + orientação para template feature
  └─ demanda pequena → tweak.implement
       └─ begin determinístico isola o recibo desta tentativa/retry
       └─ 1 delegação LLM, budget total 600 s
       └─ 1 check focal real (máx. 60 s, recibo ligado ao diff)
       └─ limite de diff + make build + limite de diff (máx. 120 s)
       └─ falha → BLOCK, sem retry/auto-fix
       └─ sucesso → tweak.acceptance (humano)
            ├─ reject → tweak.implement (somente por decisão humana)
            └─ approve → tweak.end → ft close --merge full
```

Não existem nodes de discovery, review ou reconcile. O processo não mantém
backlog/catálogo porque uma mudança que exige esse trabalho já é uma feature.
Com `ft run . --template tweak --parallel`, grupos paralelos declarados no YAML
reutilizam exatamente este mesmo grafo. Outros ciclos continuam independentes.
