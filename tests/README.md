# Tests — ForgeProcess

```
tests/
├── README.md          # Este arquivo
└── e2e/               # Validacao E2E CLI-first (gate obrigatorio)
```

---

## E2E CLI-First (`tests/e2e/`)

### O que e?

Validacao **end-to-end** via scripts shell que testam a CLI do produto com **integracoes reais**.
Gate obrigatorio do Fast Track: ciclo nao fecha sem E2E passando.

### Estrutura

```
tests/e2e/
├── shared/                  # Scripts utilitarios reutilizaveis
│   ├── colors.sh            # Cores para output do terminal
│   ├── setup.sh             # Configuracao de ambiente
│   ├── teardown.sh          # Limpeza pos-testes
│   └── assertions.sh        # Funcoes de assercao
│
├── template/                # Templates para novos ciclos
│   ├── README.template.md
│   ├── run-all.template.sh
│   ├── track-run.template.sh
│   └── feature.template.sh
│
└── cycle-XX/                # Testes por ciclo
    ├── README.md
    ├── run-all.sh           # Executa TODOS os testes
    └── evidence/            # Logs de execucao (auto-gerados)
```

### Como executar

```bash
# Executar todos os testes do ciclo
./tests/e2e/cycle-01/run-all.sh

# Executar feature especifica
./tests/e2e/cycle-01/01-feature-name.sh
```

### Quando usar

| Momento | Acao |
|---------|------|
| **Todas as tasks done** | Criar scripts E2E para features implementadas |
| **ft.e2e.01.cli_validation** | Executar `run-all.sh` — gate obrigatorio |

---

## Testes unitarios e de integracao

Testes unitarios e de integracao ficam em `tests/` (diretamente ou em subdiretorios por modulo).
Executar com pytest:

```bash
pytest tests/ -v
```

---

## Referencias

- Templates: `tests/e2e/template/`
- Guia de testes: `docs/integrations/forgebase_guides/usuarios/guia-de-testes.md`
- Fast Track spec: `process/fast_track/FAST_TRACK_PROCESS.md`
