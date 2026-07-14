# Fluxo do processo bug

```text
bug.preflight
  → bug.diagnose_fix    (LLM: RED → correção → GREEN)
  → bug.acceptance      (humano)
  → bug.reconcile       (LLM documental curta, #BUG)
  → bug.final_gate      (verify sem repetir suíte)
  → bug.end
```

Rejeição no aceite volta a `bug.diagnose_fix`. O teste RED já comprovado fica
congelado; a correção precisa produzir um novo GREEN com o mesmo comando.
