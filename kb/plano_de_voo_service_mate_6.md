# Plano de Voo — ServiceMate 6

> Criado em: 2026-04-02
> Sessão de origem: fast-track (main, v0.8.3)
> Próxima sessão: ServiceMate 6 — teste end-to-end do processo v0.8.3

---

## 1. O que mudou no processo desde o SM5

| Mudança | Impacto esperado no SM6 |
|---------|------------------------|
| `gate_kb_review` — gate final que verifica pitfalls P0 da KB | Bloqueia se `interface_type=ui` + backend Python existir, ou sem HTTP server em `mixed` |
| KB-mode — lições injetadas nos prompts de `build` e `retro` | Coder receberá aviso explícito sobre HTTP server obrigatório em `mixed` |
| `gate_server_starts` — tenta subir uvicorn e bater em `/health` | Backend sem entry point bloqueia em `gate.delivery` |
| `type: retro` estruturado — checklist de integração obrigatório | Retro vai detectar problemas de integração, não só de documentação |
| `has_sections` accent-insensitive | Sem mais falsos negativos em seções com acento |

**Meta SM6: 8+/10 com zero intervenções manuais.**

---

## 2. Criar o projeto SM6

### 2.1 Criar diretório e PRD corrigido

```bash
mkdir -p ~/dev/projects/examples/service_mate_6
```

Copiar o PRD do SM5 e aplicar as correções antes de iniciar:

```bash
cp ~/dev/projects/examples/service_mate_5/project/docs/PRD.md \
   /tmp/prd_sm6_base.md
```

O PRD precisa de duas seções novas antes de ser usado:

**Seção 8.5 — Contrato de Navegação UI** (estava referenciada em prd_review mas não existia no SM5):

```markdown
## 8.5 Contrato de Navegação UI (regras invioláveis)

### Bottom Navigation Bar
- EXATAMENTE 5 itens, na ordem: **Home | Clientes | Serviços | Catálogo | Financeiro**
- TODOS os itens devem ter rótulo textual visível (proibido ícone sem texto)
- Assistente NÃO está no bottom nav

### Assistente (FAB flutuante)
- Implementado como FAB com `position: fixed`, `bottom: 80px`, `right: 16px`
- Presente em TODAS as telas via Layout global
- Posicionado ACIMA dos FABs de página (que ficam em `bottom: 16px`)

### Rotas obrigatórias
| Rota | Componente |
|------|------------|
| `/` ou `/home` | Dashboard/Home |
| `/clientes` | Lista de clientes |
| `/servicos` | Lista de serviços prestados |
| `/catalogo` | Catálogo de serviços com preços |
| `/financeiro` | Cobranças + resumo financeiro |

### Empty States
- Empty states NÃO duplicam o FAB da página
- Empty state tem call-to-action textual (ex: botão "Adicionar cliente")
```

**Seção 8.6 — Contrato de Integração HTTP** (novo — previne bug do SM5):

```markdown
## 8.6 Contrato de Integração HTTP (obrigatório)

O ServiceMate é uma aplicação **full-stack (interface_type: mixed)**:
- Backend: FastAPI rodando em `http://localhost:8000`
- Frontend: SvelteKit/React/Vite rodando em `http://localhost:5173`
- O frontend acessa o backend EXCLUSIVAMENTE via proxy `/api` configurado no Vite

### Entry point obrigatório
O arquivo `main.py` (ou `app.py`) na raiz do backend DEVE:
1. Instanciar `FastAPI()` ou equivalente
2. Registrar todos os routers (`/clientes`, `/servicos`, `/catalogo`, `/cobranças`)
3. Expor endpoint `GET /health` que retorna `{"status": "ok"}`
4. Ser inicializável com: `uvicorn main:app --host 0.0.0.0 --port 8000`

**Sem este arquivo, o sistema não funciona — o frontend receberá 404 em todas as chamadas.**

### Configuração Vite obrigatória
```js
// frontend/vite.config.js
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
```
```

### 2.2 Criar o PRD final do SM6

Criar `~/dev/projects/examples/service_mate_6/PRD.md` com o PRD do SM5 + seções 8.5 e 8.6 acima inseridas após a seção 8.2.

---

## 3. Inicializar e rodar o processo

### 3.1 Init

```bash
cd ~/dev/projects/examples/service_mate_6
python -m ft.cli.main \
  -p ~/dev/research/fast-track/process/fast_track/FAST_TRACK_PROCESS_V2.yml \
  init
```

Confirmar: **32 nodes** (31 do V2 + 1 `gate.kb_review` novo).

### 3.2 Rodar em modo MVP

```bash
cd ~/dev/projects/examples/service_mate_6
PYTHONUNBUFFERED=1 nohup python -m ft.cli.main \
  -p ~/dev/research/fast-track/process/fast_track/FAST_TRACK_PROCESS_V2.yml \
  continue --mvp > /tmp/sm6_run.log 2>&1 &
echo "PID: $!"
```

Monitorar:
```bash
tail -f /tmp/sm6_run.log
```

---

## 4. Gates críticos para acompanhar

| Gate | O que valida | Risco SM5 que cobre |
|------|-------------|---------------------|
| `gate.planning` | TASK_LIST + tech_stack + diagrams | — |
| `ft.plan.detect_interface` | Lê `interface_type` do tech_stack | — |
| `gate.delivery` → `gate_server_starts` | Tenta subir uvicorn; bate em `/health` | SM5: sem HTTP server |
| `gate.kb_review` | interface_type=ui + backend Python = BLOCK | SM5: interface_type errado |
| `gate.mvp` | Docs + testes | — |
| `gate.mvp.frontend` | Estrutura PWA | SM4: sem frontend |

**Se `gate.delivery` bloquear em `gate_server_starts`**: o coder esqueceu o `main.py`. Não forçar avanço — deixar o processo bloquear e analisar o log.

---

## 5. O que esperar do SM6

### Deve funcionar (corrigido pelo processo v0.8.3)
- ✅ `interface_type: mixed` corretamente detectado (gate_kb_review bloqueia `ui` + Python backend)
- ✅ KB-mode injeta lição do SM5 sobre HTTP server no prompt do coder (build nodes)
- ✅ `main.py` com FastAPI criado (gate_server_starts bloqueia sem ele)
- ✅ Vite proxy configurado (gate_kb_review verifica)
- ✅ PRD seção 8.5 presente → prd_review tem âncora válida
- ✅ PRD seção 8.6 presente → coder entende que `main.py` é obrigatório
- ✅ Retro com checklist de integração detecta se backend sobe

### Ainda pode falhar (não coberto ainda)
- ⚠️ Qualidade do frontend (Design/UX fora do PRD) — depende do LLM
- ⚠️ Rotas específicas (`/catalogo`, `/financeiro`) podem ficar incompletas
- ⚠️ Empty states podem duplicar FAB — validação é só no prd_review
- ⚠️ Cobertura de testes de integração (testes ainda são unitários)

---

## 6. Avaliação de referência

| Iteração | Nota | Principal problema |
|----------|------|--------------------|
| service_mate_4 (v0.7.x) | 4/10 | Sem frontend |
| service_mate_4 (v0.8.0) | 7/10 | Frontend ok, 4 bugs UX manuais |
| service_mate_5 (v0.8.2) | 6/10 | Sem HTTP server, interface_type errado |
| **service_mate_6 (v0.8.3)** | **meta: 8+/10** | HTTP server + interface_type garantidos pelos gates |
