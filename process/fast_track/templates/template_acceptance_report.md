# Acceptance Report — Cycle XX

## Interface testada
- Tipo: [CLI | API | UI | Mixed]
- Ferramenta: [Playwright | httpx | requests | shell | Chrome automation]
- URL/endpoint base: [...]

## Mapeamento ACs → Testes

| US | AC | Descrição (Given/When/Then) | Test file | Status |
|----|-----|---------------------------|-----------|--------|
| US-01 | AC-01.1 | Given ... When ... Then ... | test_us01_ac01.py:test_happy_path | PASS / FAIL |
| US-01 | AC-01.2 | Given ... When ... Then ... | test_us01_ac02.py:test_edge_case | PASS / FAIL |

## Value Tracks cobertos

| Track | Fluxo testado | Test file | Status |
|-------|--------------|-----------|--------|
| vt-01 | [descrição do fluxo] | test_vt01_flow.py | PASS / FAIL |

## Resumo
- Total ACs: X
- Cobertos: Y (Z%)
- Pendentes: [listar ACs não cobertos, se houver — meta é 0]
- Value Tracks testados: A / B
- Status: **APROVADO** / **REPROVADO**

## Observações
[Comportamentos inesperados, edge cases detectados, notas de ambiente]
