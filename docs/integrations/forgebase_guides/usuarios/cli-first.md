# CLI First — Filosofia de Desenvolvimento

> "Se não funciona no terminal, não funciona em lugar nenhum."

## O Princípio

**CLI First** significa que todo UseCase deve ser validado via linha de comando antes de ser exposto via API HTTP, WebSocket, ou qualquer outra interface.

```
UseCase → CLIAdapter → Validado? → HTTPAdapter/WebUI
```

## Por que CLI First?

| Benefício | Descrição |
|-----------|-----------|
| **Testabilidade** | CLI é trivial de automatizar em scripts e CI/CD |
| **Rapidez** | Testar sem subir servidor, banco, frontend |
| **Isolamento** | Valida lógica de negócio sem dependências de UI |
| **Automação** | Integração com pipelines, cron jobs, scripts |
| **Debug** | Output direto, sem camadas de abstração |
| **Documentação viva** | `--help` documenta o que o sistema faz |

## Fluxo de Desenvolvimento

```
1. Escrever UseCase (domain + application)
2. Expor via CLIAdapter
3. Testar manualmente no terminal
4. Automatizar testes CLI
5. Validar com usuário/stakeholder via CLI
6. Só então criar HTTPAdapter/API/UI
```

## Implementação no ForgeBase

### 1. Criar o UseCase

```python
# src/application/usecases/create_order.py
from forgebase.application import UseCaseBase
from dataclasses import dataclass

@dataclass
class CreateOrderInput:
    customer_id: str
    items: list[dict]

@dataclass
class CreateOrderOutput:
    order_id: str
    total: float

class CreateOrderUseCase(UseCaseBase[CreateOrderInput, CreateOrderOutput]):
    def __init__(self, order_repo, product_repo):
        self.order_repo = order_repo
        self.product_repo = product_repo

    def execute(self, input: CreateOrderInput) -> CreateOrderOutput:
        # Validação
        if not input.items:
            raise ValidationError("Pedido deve ter pelo menos um item")

        # Lógica de negócio
        order = Order.create(
            customer_id=input.customer_id,
            items=input.items
        )

        self.order_repo.save(order)

        return CreateOrderOutput(
            order_id=order.id,
            total=order.total
        )
```

### 2. Expor via CLIAdapter

```python
# src/cli.py
from forgebase.adapters.cli import CLIAdapter
from application.usecases.create_order import CreateOrderUseCase
from infrastructure.repositories import OrderRepository, ProductRepository

# Inicializar dependências
order_repo = OrderRepository()
product_repo = ProductRepository()

# Registrar UseCases
cli = CLIAdapter(usecases={
    'create_order': CreateOrderUseCase(order_repo, product_repo),
    'list_orders': ListOrdersUseCase(order_repo),
    'cancel_order': CancelOrderUseCase(order_repo),
})

if __name__ == '__main__':
    cli.run()
```

### 3. Testar no Terminal

```bash
# Listar comandos disponíveis
python src/cli.py list

# Criar pedido
python src/cli.py exec create_order --json '{
  "customer_id": "cust-123",
  "items": [
    {"product_id": "prod-1", "quantity": 2},
    {"product_id": "prod-2", "quantity": 1}
  ]
}'

# Output (JSON estruturado)
{
  "result": {
    "order_id": "ord-abc-123",
    "total": 150.00
  }
}
```

### 4. Automatizar Testes

```python
# tests/cli/test_create_order_cli.py
import subprocess
import json

def test_create_order_via_cli():
    """Teste end-to-end via CLI."""
    result = subprocess.run(
        [
            'python', 'src/cli.py', 'exec', 'create_order',
            '--json', '{"customer_id": "test", "items": [{"product_id": "p1", "quantity": 1}]}'
        ],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert 'order_id' in output['result']

def test_create_order_empty_items_fails():
    """Pedido sem itens deve falhar."""
    result = subprocess.run(
        [
            'python', 'src/cli.py', 'exec', 'create_order',
            '--json', '{"customer_id": "test", "items": []}'
        ],
        capture_output=True,
        text=True
    )

    assert result.returncode == 1
    assert 'pelo menos um item' in result.stderr
```

### 5. Só Depois: HTTP/API

```python
# src/api.py
from fastapi import FastAPI
from application.usecases.create_order import CreateOrderUseCase, CreateOrderInput

app = FastAPI()

@app.post("/orders")
def create_order(input: CreateOrderInput):
    # Mesmo UseCase, diferente adapter
    usecase = CreateOrderUseCase(order_repo, product_repo)
    result = usecase.execute(input)
    return {"order_id": result.order_id, "total": result.total}
```

## Padrão de Projeto

```
src/
├── domain/              # Entidades, regras de negócio
├── application/
│   └── usecases/        # UseCases puros
├── infrastructure/      # Repositórios, configs
├── adapters/
│   ├── cli/             # CLI (primeiro!)
│   └── http/            # HTTP (depois)
└── cli.py               # Entry point CLI
```

## Checklist CLI First

Antes de criar API HTTP, valide:

- [ ] UseCase funciona via CLI?
- [ ] Erros retornam mensagens claras?
- [ ] Output é JSON estruturado?
- [ ] `--help` documenta os parâmetros?
- [ ] Testes CLI passam?
- [ ] Stakeholder validou o comportamento?

## Benefícios em Produção

### Scripts de Manutenção

```bash
# Cron job para limpeza
0 2 * * * python cli.py exec cleanup_old_orders --json '{"days": 90}'

# Script de migração
python cli.py exec migrate_users --json '{"batch_size": 100}'
```

### CI/CD

```yaml
# .github/workflows/test.yml
- name: Test CLI
  run: |
    python cli.py exec health_check
    python cli.py exec create_order --json '{"customer_id": "test", "items": [...]}'
```

### Debug em Produção

```bash
# SSH no servidor
python cli.py exec get_order --json '{"order_id": "ord-123"}'
python cli.py exec retry_payment --json '{"order_id": "ord-123"}'
```

## Anti-patterns

### ❌ Errado: HTTP First

```python
# Criar API sem validar UseCase
@app.post("/orders")
def create_order(req):
    # Lógica misturada com HTTP
    # Difícil de testar
    # Difícil de reusar
    pass
```

### ✅ Correto: CLI First

```python
# 1. UseCase puro
class CreateOrderUseCase:
    def execute(self, input): ...

# 2. CLI valida
cli.run(['exec', 'create_order', '--json', '...'])

# 3. HTTP reusa
@app.post("/orders")
def create_order(req):
    return usecase.execute(req)
```

## Integração com ForgeProcess

CLI First é a **Fase 4** do ciclo cognitivo ForgeProcess:

```
MDD → BDD → TDD → CLI → Feedback
                   ↑
              Você está aqui
```

Antes de entregar para usuários (Feedback), valide via CLI.

---

**Referências:**
- [CLIAdapter](../referencia/arquitetura.md) — Implementação do adapter
- [ForgeProcess](../referencia/forge-process.md) — Ciclo completo
- [UseCaseBase](../referencia/arquitetura.md) — Base para casos de uso
