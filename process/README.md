# ForgeProcess

Processo de desenvolvimento para solo dev + AI.
18 steps, 9 fases, 3 symbiotas. Valor > cerimônia.

## Estrutura

```
process/
├── README.md                          # Este arquivo
├── fast_track/
│   ├── FAST_TRACK_PROCESS.yml         # Definição do processo
│   ├── FAST_TRACK_PROCESS.md          # Spec legível
│   ├── FAST_TRACK_IDS.md              # Step IDs canônicos
│   ├── SUMMARY_FOR_AGENTS.md          # Resumo para LLMs
│   ├── README.md                      # Quick-start
│   ├── state/
│   │   └── ft_state.yml               # Estado do processo
│   └── templates/
│       ├── template_prd.md            # PRD consolidado
│       ├── template_task_list.md      # Task list
│       └── template_retro_note.md     # Retro note
└── symbiotes/
    ├── ft_coach/
    │   └── prompt.md                  # Coach: MDD, planning, feedback
    └── forge_coder/
        └── prompt.md                  # Coder: TDD, delivery, E2E
```

## Flow

```
MDD (hipótese -> PRD -> validação)
  -> Planning (task list)
  -> Loop: TDD (red-green) + Delivery (integrate, review, commit)
  -> E2E CLI gate (obrigatório)
  -> Feedback (retro note)
  -> Novo ciclo ou encerrar
```

## Como começar

```bash
cp process/fast_track/templates/template_prd.md project/docs/PRD.md
```

Inicie com `ft.mdd.01.hipotese` — o ft_coach guia o processo.

## Para Agentes / LLMs

Leia `process/fast_track/SUMMARY_FOR_AGENTS.md` para um resumo compacto.
Estado em `process/fast_track/state/ft_state.yml`.

## Princípios

1. **PRD como fonte única** — Sem documentos satélite
2. **TDD Red-Green obrigatório** — Teste falhando antes de código
3. **E2E CLI gate obrigatório** — Ciclo não fecha sem E2E passando
4. **ACs substituem BDD** — Given/When/Then no PRD, sem .feature files
5. **Self-review** — Checklist automatizado, sem reviewers formais
6. **CLI-first e offline** — Validar via CLI, sem rede externa no MVP
