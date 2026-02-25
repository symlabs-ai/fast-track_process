# E2E Validation — Ciclo [XX]

> **Status**: [Pendente | Em Execucao | Aprovado | Rejeitado]
>
> **Data de Execucao**: [AAAA-MM-DD]
>
> **Executado por**: [Nome do Stakeholder]

---

## Objetivo

Validar a implementacao completa do **Ciclo [XX]**, incluindo:

- Todos os **ValueTracks** implementados
- Todos os **SupportTracks** de suporte
- Integracoes com modulos de terceiros **reais** (nao mocks)

---

## Pre-requisitos

### 1. Ambiente

```bash
# Ativar ambiente virtual do projeto
source .venv/bin/activate

# Verificar CLI instalada
[CLI_NAME] --version
```

### 2. Credenciais

Crie o arquivo `.env.e2e` na raiz do projeto com as seguintes variaveis:

```bash
# Credenciais de integracao (OBRIGATORIAS)
API_KEY=sua_api_key_aqui
API_SECRET=sua_api_secret_aqui

# Configuracoes opcionais
API_ENVIRONMENT=sandbox
LOG_LEVEL=INFO
```

> **IMPORTANTE**: Nunca commite o arquivo `.env.e2e` no repositorio!

### 3. Servicos Externos

Verifique que os seguintes servicos estao acessiveis:

- [ ] [Servico 1] — [URL ou descricao]
- [ ] [Servico 2] — [URL ou descricao]

---

## Execucao

### Rodar Todos os Testes

```bash
cd tests/e2e/cycle-[XX]
./run-all.sh
```

### Rodar Track Especifico

```bash
# ValueTrack
./vt-[XX]-[nome]/run.sh

# SupportTrack
./st-[XX]-[nome]/run.sh
```

### Rodar Feature Especifica

```bash
./vt-[XX]-[nome]/[NN]-[feature].sh
```

---

## Tracks Incluidos

### ValueTracks

| ID | Nome | Features | Status |
|----|------|----------|--------|
| VT-01 | [Nome] | [N] features | [ ] Pendente |
| VT-02 | [Nome] | [N] features | [ ] Pendente |

### SupportTracks

| ID | Nome | Features | Status |
|----|------|----------|--------|
| ST-01 | [Nome] | [N] features | [ ] Pendente |

---

## Resultado Esperado

Ao executar `./run-all.sh`, voce vera:

```
═══════════════════════════════════════════════════════
  E2E VALIDATION - CICLO [XX]
  Data: [DATA_HORA]
═══════════════════════════════════════════════════════

▶ Executando: vt-01-[nome]
  Feature: [Descricao]
  ✓ PASS: [Cenario 1]
  ✓ PASS: [Cenario 2]
✓ vt-01-[nome]: PASSED

▶ Executando: vt-02-[nome]
  ...
✓ vt-02-[nome]: PASSED

▶ Executando: st-01-[nome]
  ...
✓ st-01-[nome]: PASSED

═══════════════════════════════════════════════════════
  RESULTADO FINAL - CICLO [XX]
  Tracks PASSED: 3
  Tracks FAILED: 0
  Evidencia: evidence/run-all_[TIMESTAMP].log
═══════════════════════════════════════════════════════
```

---

## Evidencias

Os logs de execucao sao salvos automaticamente em:

```
tests/e2e/cycle-[XX]/evidence/
├── run-all_[TIMESTAMP].log      # Log completo
├── vt-01_[TIMESTAMP].log        # Log por track (se executado individualmente)
└── ...
```

Estes arquivos servem como **prova formal** de validacao do ciclo.

---

## Troubleshooting

### Erro: "CLI nao encontrada"

```bash
pip install -e .
```

### Erro: "Variaveis nao definidas"

Verifique se o arquivo `.env.e2e` existe e contem todas as variaveis obrigatorias.

### Erro: "Servico inacessivel"

Verifique conectividade com os servicos externos e se as credenciais estao corretas.

---

## Aprovacao

Apos execucao bem-sucedida:

- [ ] Todos os tracks passaram (0 falhas)
- [ ] Logs de evidencia gerados
- [ ] Stakeholder validou o comportamento

**Assinatura do Stakeholder**: _________________________ Data: _________
