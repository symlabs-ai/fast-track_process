# Tech Stack — ForgeProcess Fast Track

**Versão:** 1.0
**Data:** 2026-04-01
**Processo:** fast_track_v2 / ft.plan.02.tech_stack
**Status:** Proposta para aprovação do stakeholder

---

## 1. Visão Geral

O ForgeProcess Fast Track é um motor de orquestração de processo para solo dev + AI. A stack é escolhida com três critérios principais:

1. **Determinismo** — sem frameworks que introduzam magia ou comportamento implícito (RNF-01)
2. **Rastreabilidade** — tudo em texto/YAML auditável via git (RNF-02)
3. **Minimalismo** — nenhuma dependência que não seja diretamente necessária (RNF-04)

---

## 2. Runtime Principal

| Componente | Tecnologia | Versão mínima |
|------------|------------|---------------|
| Linguagem | Python | 3.11+ |
| Gerenciador de pacotes | pip + `pyproject.toml` | — |
| Formato de estado | YAML | (via PyYAML) |

**Justificativa Python 3.11+:**
- Suporte nativo a `tomllib` (leitura de `pyproject.toml` sem dependência extra)
- `match/case` para dispatch determinístico de nodes por tipo
- Melhorias de performance (10–60% vs 3.10) relevantes para o loop de gate
- Tipagem com `Self` e `TypeVarTuple` para modelos de estado mais expressivos

---

## 3. Dependências de Produção

```toml
[project.dependencies]
PyYAML = ">=6.0"
anthropic = ">=0.25"   # Claude Agent SDK
```

### 3.1 PyYAML `>=6.0`

- Leitura e escrita de `engine_state.yml` e `FAST_TRACK_PROCESS_V2.yml`
- Único formato de estado persistente do motor (RF-01 a RF-05)
- Versão 6.x usa `SafeLoader` por padrão — sem execução de código arbitrário

### 3.2 Anthropic SDK / Claude Agent SDK `>=0.25`

- Interface com os 5 agentes symbiotas (ft_manager, ft_gatekeeper, ft_coach, ft_acceptance, forge_coder)
- Modelo padrão: `claude-sonnet-4-6` (custo/capacidade equilibrado para gates e TDD)
- Modelo de revisão (Sprint Expert Gate): `claude-opus-4-6` (análise mais profunda)
- Sem dependência de outros AI providers — stack de AI homogênea

---

## 4. Dependências de Desenvolvimento e Teste

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.9",
]
```

### 4.1 pytest `>=8.0` + pytest-cov `>=5.0`

- Framework de teste para todos os ciclos TDD (RF-07, RF-14)
- `pytest-cov` gera relatório de cobertura lido pelo gate de cobertura (RF-08)
- Threshold obrigatório: `--cov-fail-under=80` nos gates automáticos
- Saída em XML (`--cov-report=xml`) para parsing programático pelo motor

### 4.2 ruff `>=0.4`

- Linter e formatter unificado (substitui `pylint` + `black` + `isort`)
- Gate de Delivery (`ft.delivery.01.self_review`) exige lint limpo antes de avançar
- Configuração mínima em `pyproject.toml` — zero arquivos de config extras

### 4.3 mypy `>=1.9`

- Verificação estática de tipos no motor (`ft_engine/`)
- Obrigatório para o módulo de estado: `engine_state.py`, `validators.py`
- Agentes (código de prompt/LLM) ficam fora do escopo do mypy

---

## 5. Estrutura de Pastas

```
fast-track/
├── pyproject.toml            # config central: deps, ruff, mypy, pytest
├── ft_engine/                # motor determinístico
│   ├── __init__.py
│   ├── engine.py             # loop principal: boot → dispatch → advance
│   ├── state.py              # leitura/escrita engine_state.yml
│   ├── validators.py         # validators dos nodes (file_exists, min_lines, etc.)
│   ├── gates.py              # lógica PASS/BLOCK e gate_log
│   └── agents.py             # interface com Claude SDK por agente
├── tests/
│   ├── unit/                 # testes unitários (mocked)
│   └── e2e/                  # cenários E2E (AC-01 a AC-05)
├── process/
│   └── fast_track/
│       └── FAST_TRACK_PROCESS_V2.yml
└── project/
    ├── state/
    │   └── engine_state.yml  # estado persistente do processo ativo
    └── docs/                 # artifacts produzidos pelos agentes
```

**Regras de isolamento (RNF-05):**

| Agente | Paths permitidos de escrita |
|--------|----------------------------|
| ft_coach | `project/docs/` |
| ft_gatekeeper | `project/state/engine_state.yml` (gate_log apenas) |
| ft_acceptance | `project/docs/` |
| forge_coder | `ft_engine/`, `tests/`, `project/docs/` |
| ft_manager | `project/state/engine_state.yml` |

---

## 6. Configuração Central (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=ft_engine --cov-report=xml --cov-fail-under=80"

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "UP"]

[tool.mypy]
strict = true
files = ["ft_engine/"]
```

---

## 7. Controle de Versão e Commits

- **Repositório:** Git (já existente)
- **Branch principal:** `main`
- **Padrão de commits:** Conventional Commits

```
<type>(<scope>): <descrição>

feat(engine): implementar suporte a múltiplos ciclos (RF-04)
fix(gate): corrigir blocked_reason em gate de cobertura (RF-03)
test(tdd): adicionar testes red para gate E2E (RF-09)
chore: bump version to 0.8.0
```

- Commits atômicos por step TDD são parte do contrato do processo (PRD §10)
- Cada sprint fecha com ao menos um commit de `test:` (red) e um de `feat:` (green)

---

## 8. Fora do Escopo desta Stack

| Item | Motivo |
|------|--------|
| Docker / containers | Delegado ao DevOps (`~/dev/devops`) |
| CI/CD pipeline | Delegado ao DevOps (Gitea Actions) |
| Banco de dados | Estado é YAML — sem DB necessário |
| Frontend / UI | Motor é CLI puro |
| Ferramentas de PM externas | Fora do escopo do processo (PRD §3.2) |
| Secrets / credenciais | Gerenciados pelo SymVault via DevOps |

---

## 9. Decisões de Design Registradas

| ID | Decisão | Alternativa Considerada | Justificativa |
|----|---------|------------------------|---------------|
| TD-01 | YAML para estado persistente | JSON, SQLite | YAML é legível por humanos e agentes; auditável via git diff |
| TD-02 | ruff em vez de pylint+black | pylint, flake8 | Menos configuração, mais rápido, uma só ferramenta |
| TD-03 | SDK Anthropic direto | LangChain, LlamaIndex | Sem camada de abstração — determinismo e rastreabilidade (RNF-01/02) |
| TD-04 | Python puro (sem async) | asyncio, FastAPI | Motor é sequencial por design; async introduziria não-determinismo |
| TD-05 | Sem ORM ou dataclasses para estado | Pydantic, dataclasses | `dict` + YAML é suficiente e mais fácil de serializar/deserializar |

---

## 10. Aprovação

| Papel | Status |
|-------|--------|
| Stakeholder (solo dev) | ⏳ Pendente |
| ft_gatekeeper (gate.planning) | ⏳ Pendente — aguarda aprovação do stakeholder |
