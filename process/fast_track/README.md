# Fast Track — Quick Start

> Solo dev + AI. 18 steps. Valor > cerimônia, com sprints técnicas.

## Como começar

```bash
# 1. Copie o template PRD
cp process/fast_track/templates/template_prd.md project/docs/PRD.md

# 2. Inicie o processo
# O ft_coach guia você pela hipótese e PRD

# 3. Após PRD aprovado, crie a task list
cp process/fast_track/templates/template_task_list.md project/docs/TASK_LIST.md

# 4. O forge_coder executa TDD + delivery sprint a sprint

# 5. Ao fim de cada sprint, rode o Sprint Expert Gate (/ask fast-track)

# 6. Smoke + E2E CLI gate fecham o ciclo
```

## Estrutura

```
process/fast_track/
  FAST_TRACK_PROCESS.yml    # Definição do processo
  FAST_TRACK_PROCESS.md     # Spec legível
  FAST_TRACK_IDS.md         # Step IDs canônicos
  SUMMARY_FOR_AGENTS.md     # Resumo para LLMs
  state/                     # (migrado para project/state/)
  # ft_state.yml agora em project/state/ft_state.yml
  templates/
    template_prd.md          # PRD consolidado
    template_task_list.md    # Task list
    template_sprint_review.md # Review de sprint via especialista
    template_retro_note.md   # Retro note
```

## Referências

- [Fast Track YAML](FAST_TRACK_PROCESS.yml)
- [Fast Track Spec](FAST_TRACK_PROCESS.md)
- [Step IDs](FAST_TRACK_IDS.md)
- [Summary for Agents](SUMMARY_FOR_AGENTS.md)
