# Avaliação E2E — service_mate_5

> Data: 2026-04-02
> Processo: Fast Track V2 (FAST_TRACK_PROCESS_V2.yml v0.8.x)
> Projeto-teste: service_mate_5
> Nota: **6/10**

---

## O que funcionou

- Engine completou 30/30 nodes sem travar
- PRD, TASK_LIST, tech_stack, hipótese — todos gerados com qualidade
- TDD: 55 testes backend passando, lint limpo, gate MVP aprovado
- Decision node `interface_type` funcionou: detectou `mixed` e ativou sprint de frontend
- Frontend scaffolded (React + Vite + PWA manifest)
- Hyper-mode enriqueceu prompts com docs existentes corretamente
- Gateway SymGateway identificou o projeto via cwd do subprocesso

## O que falhou de forma crítica

### Bug principal: backend sem servidor HTTP

- O processo gerou domínio + casos de uso (Python puro) + frontend (React), mas **nenhum servidor HTTP**
- Não havia `main.py` com FastAPI/Flask — o frontend chamava `/api/*` e recebia 404 sempre
- 55 testes unitários passaram porque testavam lógica isolada; a stack completa nunca foi testada
- Gate MVP aprovou a entrega com integração completamente quebrada

### Retro cega para gaps de integração

- A retro automática (`type: document`) só via o que os docs diziam
- Não rodava checklist de integração real (subir servidor, testar endpoint, bater frontend no backend)
- Detectou problemas visuais de frontend (Layout.jsx, BottomNav) mas não o problema raiz

### Frontend PRD review com âncora inválida

- Checklist referenciava "PRD seção 8.5" que não existia no PRD gerado
- Review resultou em REJECTED mas o critério não era verificável

---

## Causa Raiz

O processo não tinha nenhum gate que:
1. Verificasse se existe entry point HTTP quando `interface_type != cli_only`
2. Tentasse subir o servidor e confirmar que responde
3. Exigisse que a retro analisasse integração (não apenas documentos)

---

## Correções Aplicadas (v0.8.2)

### 1. `gate_server_starts` (validators/gates.py)
- Novo gate em `gate.delivery`
- Busca entry points: `backend/main.py`, `src/main.py`, `main.py`, `app.py`, `server.py`
- Verifica presença de FastAPI/Flask/Starlette no arquivo
- Sobe uvicorn na porta 18765, aguarda 3s, bate em `/health` e `/`
- Skip automático se `interface_type = cli_only`

### 2. `has_sections` accent-insensitive (validators/artifacts.py)
- Validador normalizava unicode via `unicodedata.normalize("NFD")`
- `Hipótese` passava a bater com `Hipotese` — sem mais falsos negativos

### 3. `type: retro` (runner.py + FAST_TRACK_PROCESS_V2.yml)
- Novo tipo de node que injeta no prompt: activity log, gate_log, blocked_reason, completed_nodes
- Checklist de integração **obrigatório** na retro:
  - [ ] Backend sobe e responde em `/health`
  - [ ] Frontend conecta ao backend (sem 404/CORS)
  - [ ] Fluxo E2E principal funciona ponta-a-ponta
- Seção obrigatória "Gaps de Detecção" — o que o processo deveria ter pego e não pegou
- `min_lines: 50` (antes era 10)

### 4. Registro de projeto no gateway de produção
- `service_mate_5` registrado no DB da produção (`symrouter` PostgreSQL)
- API key `palhano` vinculada ao projeto em `project_api_keys`
- Gateway extrai projeto do path cwd do subprocesso (via system prompt)

---

## Lições para Próximos Projetos

| Lição | Aplicação |
|---|---|
| Unit tests passando ≠ stack funcionando | Sempre checar subida real do servidor após TDD |
| Retro precisa ver estado real, não só docs | `type: retro` injeta activity log |
| Checklist com âncora inválida = critério inválido | PRD deve ter seção 8.5 antes do frontend review |
| `interface_type = mixed` exige backend HTTP + frontend integrado | `gate_server_starts` bloqueia agora |
| Gateway é sempre produção | `symgateway.symlabs.ai` — nunca staging |

---

## Débitos Abertos (service_mate_5)

| # | Débito | Prioridade |
|---|---|---|
| DT-01 | Criar `Layout.jsx` com FAB do Assistente (`position: fixed`) | P0 |
| DT-02 | Remover Assistente do BottomNav → FAB no Layout | P0 |
| DT-03 | Implementar rota e tela `/catalogo` | P0 |
| DT-04 | Adicionar seção 8.5 ao PRD (Contrato de Navegação UI) | P1 |
| DT-05 | Criar servidor HTTP (`main.py` FastAPI) conectando domínio ao frontend | P0 |

---

## Comparativo SM4 → SM5

| Aspecto | SM4 | SM5 |
|---|---|---|
| Nodes completados | 22/22 | 30/30 |
| Frontend gerado | ❌ | ✅ (com gaps) |
| Backend HTTP | ✅ | ❌ (bug principal) |
| Testes | 283 pass | 55 pass |
| Gate server_starts | ❌ ausente | ✅ adicionado pós-SM5 |
| Retro com integração | ❌ | ✅ adicionado pós-SM5 |
| Nota | 4/10 | 6/10 |

SM5 foi melhor que SM4 (frontend existe, processo mais completo), mas revelou que entrega de stack integrada ainda não é garantida pelo processo. As correções aplicadas em v0.8.2 devem elevar SM6+ para 8+/10.
