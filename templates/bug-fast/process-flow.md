# Fluxo do processo bug-fast

```text
bug.preflight
  → bug.diagnose_fix       (LLM builder: RED → correção → GREEN; build+test)
  → bug.review             (LLM reviewer independente)
  → bug.review_decision
      approved ───────────────→ bug.acceptance
      fix → bug.fix_prepare → bug.fix → bug.fix_review → decisão
                aprovado → bug.fix_full_validate ───────→ bug.acceptance
                path novo → bug.fix_full_review_validate → bug.review
      scope ──────────────────→ bug.scope_block (migrar para feature-fast)
  → bug.acceptance         (humano)
  → bug.reconcile          (Python, sem LLM)
  → bug.final_gate         (receipt + review, sem repetir suíte)
  → bug.end
```

Na correção, a auditoria cobre somente o delta desde
`docs/bug-fix-baseline.yml` e os B-* originais. `full_review` é usado apenas
quando o fix toca path fora do conjunto inicialmente revisado. Rejeição no
aceite volta à correção RED→GREEN e torna obrigatória uma nova review.
Loops de fix executam somente o GREEN e a auditoria focal; build+test completo
é renovado uma vez, depois que o fix for aprovado e antes do aceite.
Quando um path novo exige review completa, a renovação ocorre antes dessa
review; uma rejeição posterior substitui a âncora focal obsoleta.
