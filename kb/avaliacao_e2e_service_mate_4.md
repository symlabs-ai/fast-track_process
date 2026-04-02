# Avaliação E2E — service_mate_4

> Data: 2026-04-02
> Processo: Fast Track V2 (FAST_TRACK_PROCESS_V2.yml)
> Projeto-teste: service_mate_4 (PRD do ServiceMate v2.2.0)
> Nota: **4/10**

---

## O que funcionou

- Engine rodou 22/22 nodes sem travar (após bugfixes aplicados durante a run)
- Docs gerados (PRD, TASK_LIST, SPEC.md) foram de qualidade razoável
- TDD completo: 283 testes passando, lint limpo, gate MVP aprovado
- Hyper-mode absorveu o PRD existente corretamente

## O que falhou de forma crítica

- **Entregou metade do produto**: PRD pediu PWA + backend, engine entregou só backend
- O processo não lê `interface_type` e não tem sprint de frontend — qualquer PRD com UI vai falhar da mesma forma
- O smoke report já admitia "CLI adapter não implementado" — sinal claro que o processo não cobre o escopo completo
- Gate MVP "aprovado" com entrega incompleta = falsa sensação de conclusão (pior do que bloquear)

---

## Causa Raiz

`FAST_TRACK_PROCESS_V2.yml` não tem:
1. Detecção de `interface_type` no PRD/tech_stack
2. Sprint de frontend (PWA/UI)
3. Decision node que ramifica o fluxo por tipo de interface
4. Validators que checam entrega de frontend quando PRD exige

---

## Ação Necessária

Redesenhar o processo V2 com:
- Decision node após `gate.planning` que lê `interface_type` do tech_stack.md
- `sprint-frontend`: scaffold → build → gate (ativado quando `interface_type != cli_only`)
- Gate MVP expandido: verificar presença de `frontend/` quando interface_type exige UI
