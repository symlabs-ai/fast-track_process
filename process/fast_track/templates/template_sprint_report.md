# Sprint Report — {{sprint_id}}

> Ciclo: {{cycle_id}}
> Sprint: {{sprint_id}}
> Status: completed | partial | blocked
> Data: {{date}}

---

## Resumo

- Tasks concluidas: {{done}} / {{total}}
- Tasks bloqueadas: {{blocked}}
- Testes passando: {{tests_passing}}
- Cobertura media: {{coverage}}%
- Commits: {{commit_count}}

## Resultado por Task

| Task | Titulo | Prioridade | gate.delivery | Commits | Notas |
|------|--------|------------|---------------|---------|-------|
| T-XX | [titulo] | P0 | PASS | abc1234 | — |
| T-YY | [titulo] | P1 | BLOCK | — | [motivo] |

## Gate Log (para ft_state.yml)

```yaml
gate_log:
  T-XX: {gate.delivery: PASS}
  T-YY: {gate.delivery: BLOCK}
```

## Testes

- Unit: {{unit_pass}} passando, {{unit_fail}} falhando
- Integration: {{int_pass}} passando, {{int_fail}} falhando
- Cobertura: {{coverage}}% (minima por arquivo: {{min_file_coverage}}%)

## Bloqueios (se houver)

### T-YY: [titulo]
- gate.delivery BLOCK: [motivo detalhado]
- Tentativas de fix: [N]
- Acao necessaria: [o que o ft_manager precisa decidir]

## Decisoes Tecnicas

[Decisoes relevantes tomadas durante a sprint que impactam sprints futuras]

## Arquivos Criados/Modificados

[Lista dos arquivos tocados, agrupados por dominio]
