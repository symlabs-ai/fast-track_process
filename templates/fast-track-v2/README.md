# Template: Fast Track V2

Template de processo para o motor Fast Track. Inclui o ciclo completo MDD-to-MVP com 43 nós, 10 fases e validators determinísticos.

## Fases

1. **MDD** — Market Driven Development (hipótese, PRD, validação)
2. **Planning** — Task list, tech stack, diagramas, test data, API contract
3. **Frontend** — Scaffold, implementação, review visual
4. **TDD** — Red/green cycle
5. **Delivery** — Entrypoint, self-review, refactor
6. **Acceptance** — CLI validation
7. **Smoke** — Server starts + health check
8. **E2E** — CLI validation + browser com dados reais
9. **Feedback** — Retro + audit ForgeBase
10. **Handoff** — PRD rewrite, SPEC, plano de voo

## Uso

```bash
ft init --template fast-track-v2
ft run .
```

## Customização

Copie e edite `FAST_TRACK_PROCESS.yml` no seu projeto:

```
meu-produto/
  process/
    FAST_TRACK_PROCESS.yml   ← customize aqui
```

Valide após editar:

```bash
ft validate
```
