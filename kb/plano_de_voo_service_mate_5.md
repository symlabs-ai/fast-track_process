# Plano de Voo — ServiceMate 5

> Criado em: 2026-04-02
> Sessão de origem: fast-track (main, post v0.8.1)
> Próxima sessão: ServiceMate 5 — primeira iteração com processo v0.8.x

---

## 1. Fechar a sessão atual

### 1.1 Commitar mudanças pendentes no fast-track

Há mudanças não commitadas no repositório `~/dev/research/fast-track`:

```bash
cd ~/dev/research/fast-track
git add ft/engine/runner.py process/fast_track/FAST_TRACK_PROCESS_V2.yml tests/engine/test_graph.py
git commit -m "feat: activity logging in runner + prd_review node + 31 nodes"
```

### 1.2 Bump de versão

Após commit, fazer `/push patch` para registrar como v0.8.2.

### 1.3 Encerrar servidores do service_mate_4

Os servidores locais do service_mate_4 ainda podem estar rodando:

```bash
pkill -f "uvicorn src.main:app"
pkill -f "vite"
```

---

## 2. O que foi feito nesta sessão

### Fast Track Engine (v0.8.0 → v0.8.1 → pendente v0.8.2)

| Mudança | Arquivo | Status |
|---------|---------|--------|
| Sprint-03-frontend com detecção de `interface_type` | `FAST_TRACK_PROCESS_V2.yml` | ✅ commitado |
| `_auto_approve` em modo `--mvp` | `runner.py` | ✅ commitado |
| `gate_frontend` valida `frontend/index.html` | `gates.py` | ✅ commitado |
| Prompt scaffold: `index.html` na raiz + proxy rewrite | `FAST_TRACK_PROCESS_V2.yml` | ✅ commitado |
| Diretriz de navegação no bottom nav | `FAST_TRACK_PROCESS_V2.yml` | ✅ commitado |
| Node `ft.frontend.03.prd_review` (review de arquitetura) | `FAST_TRACK_PROCESS_V2.yml` | ⚠️ pendente commit |
| Activity logging (`_log_activity` + `servicemate_log.md`) | `runner.py` | ⚠️ pendente commit |

### ServiceMate 4 — UI refinamentos (não commitados no produto)

As mudanças abaixo foram feitas diretamente em `~/dev/projects/examples/service_mate_4/frontend/src/`:

| Mudança | Arquivo |
|---------|---------|
| Bottom nav: Agenda→Home, Assistente→FAB, Catálogo adicionado | `Layout.jsx` |
| Paleta verde: primary `#006D5B`, secondary `#2E7D32` | `theme.js` |
| AppBar Home: "Service Mate" centralizado com ícone `Handyman` | `AgendaPage.jsx` |
| Empty state de Clientes sem botão duplicado | `ClientesPage.jsx` |
| FAB Assistente em `bottom: 136px` (acima dos FABs de página) | `Layout.jsx` |

### PRD do ServiceMate atualizado

O arquivo `~/dev/projects/examples/service_mate_4/PRD.md` foi atualizado com:
- Seção 4.1: paleta verde, padrão de FABs
- Seção 4.5: bottom nav correto (Home, Clientes, Serviços, Catálogo, Financeiro)
- Seção 4.6: empty states sem botão duplicado
- **Seção 8.5**: Contrato de Navegação UI (regras invioláveis para o LLM)
- Seção 9.1: spec completa da aba Home

---

## 3. Criar o ServiceMate 5

### 3.1 Criar a pasta

```bash
mkdir -p ~/dev/projects/examples/service_mate_5
cp ~/dev/projects/examples/service_mate_4/PRD.md ~/dev/projects/examples/service_mate_5/PRD.md
```

**Copiar apenas o PRD** — nenhum código, nenhum estado anterior.

### 3.2 Inicializar o processo

```bash
cd ~/dev/projects/examples/service_mate_5
python -m ft.cli.main -p ~/dev/research/fast-track/process/fast_track/FAST_TRACK_PROCESS_V2.yml init
```

Confirmar que o output mostra **31 nodes** e **sprint-03-frontend** na lista.

### 3.3 Rodar o processo completo

```bash
cd ~/dev/projects/examples/service_mate_5
PYTHONUNBUFFERED=1 nohup python -m ft.cli.main \
  -p ~/dev/research/fast-track/process/fast_track/FAST_TRACK_PROCESS_V2.yml \
  continue --mvp > /tmp/sm5_run.log 2>&1 &
echo "PID: $!"
```

Monitorar com:
```bash
tail -f /tmp/sm5_run.log
```

---

## 4. O que esperar do ServiceMate 5

Com as melhorias do processo v0.8.x, o ServiceMate 5 deve:

- ✅ Detectar `interface_type: ui` do `tech_stack.md` automaticamente
- ✅ Executar sprint-03-frontend (scaffold + implement)
- ✅ `frontend/index.html` criado na raiz (não em `public/`)
- ✅ `vite.config.js` com proxy + rewrite correto
- ✅ Bottom nav com Catálogo como 5° item (não escondido)
- ✅ Assistente como FAB flutuante (não no bottom nav)
- ✅ Node `ft.frontend.03.prd_review` validando conformidade com o PRD
- ✅ `servicemate_log.md` gerado automaticamente com log de atividade

### Pontos de atenção

1. **PRD seção 8.5** — o LLM vai ler o Contrato de Navegação. Se gerar bottom nav errado, o `prd_review` node vai rejeitar (REJECTED) e bloquear o processo.
2. **Paleta verde** — o PRD especifica `#006D5B`. O LLM deve usar isso no `theme.js`.
3. **AppBar Home centralizado** — o PRD descreve o padrão de espaçador espelhado. Verificar se foi seguido antes de avançar para TDD.

---

## 5. Avaliação de referência

| Iteração | Nota | Principal problema |
|----------|------|--------------------|
| service_mate_4 (v0.7.x) | 4/10 | Sem frontend — processo não tinha sprint UI |
| service_mate_4 (v0.8.0) | 7/10 | Frontend entregue, mas 4 bugs manuais + UX fraca |
| service_mate_5 (v0.8.x) | meta: 8+/10 | Com PRD reforçado + prd_review node + logging |

Meta para o service_mate_5: **8/10 ou mais**, com zero intervenções manuais.
