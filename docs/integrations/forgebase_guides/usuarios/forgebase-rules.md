# ForgeBase Rules — Guia Completo para Desenvolvedores

> "Forjar é transformar pensamento em estrutura."

Este documento consolida todas as regras, práticas e considerações para desenvolver com ForgeBase.

---

## 1. Arquitetura de Camadas

### Estrutura Obrigatória

```
src/
├── domain/              # Núcleo de negócio (PURO)
├── application/         # Casos de uso e orquestração
├── infrastructure/      # Implementações concretas
└── adapters/            # Interfaces externas (CLI, HTTP)
```

### Regras de Dependência

```
Domain ← Application ← Infrastructure
                    ← Adapters
```

| Camada | Pode importar | NÃO pode importar |
|--------|---------------|-------------------|
| **Domain** | Nada externo | Application, Infrastructure, Adapters |
| **Application** | Domain, Ports (abstrações) | Infrastructure, Adapters |
| **Infrastructure** | Domain, Application | Adapters |
| **Adapters** | Domain, Application | Infrastructure (via Ports) |

### Classes Base

| Componente | Camada | Base Class | Propósito |
|------------|--------|------------|-----------|
| Entidade | Domain | `EntityBase` | Objetos com identidade |
| Value Object | Domain | `ValueObjectBase` | Objetos imutáveis |
| UseCase | Application | `UseCaseBase` | Orquestração de lógica |
| Port | Application | `PortBase` | Contratos/abstrações |
| Adapter | Adapters | `AdapterBase` | Implementações de I/O |

---

## 2. CLI First

### Princípio

**Todo UseCase deve ser validado via CLI antes de ser exposto via API HTTP.**

```
UseCase → CLIAdapter → Validado? → HTTPAdapter/WebUI
```

### Fluxo de Desenvolvimento

```
1. Escrever UseCase
2. Expor via CLIAdapter
3. Testar no terminal
4. Automatizar testes CLI
5. Só então criar HTTPAdapter/API
```

### Exemplo

```python
# 1. UseCase
class CreateOrderUseCase(UseCaseBase):
    def execute(self, input): ...

# 2. CLI
cli = CLIAdapter(usecases={'create_order': CreateOrderUseCase(repo)})

# 3. Teste manual
# $ python cli.py exec create_order --json '{"items": [...]}'

# 4. Só depois: HTTP
@app.post("/orders")
def create_order(req):
    return usecase.execute(req)
```

### Benefícios

- Testabilidade automática
- Debug rápido sem servidor
- Scripts de manutenção
- CI/CD simplificado

---

## 3. Exceções de Domínio

### Hierarquia

```python
from forgebase.domain.exceptions import (
    DomainException,        # Base para todas
    ValidationError,        # Dados inválidos (formato, tipo)
    InvariantViolation,     # Regra de negócio quebrada
    BusinessRuleViolation,  # Operação não permitida
    EntityNotFoundError,    # Entidade não existe
    DuplicateEntityError,   # Entidade duplicada
)
```

### Quando Usar Cada Uma

| Exceção | Quando usar | Exemplo |
|---------|-------------|---------|
| `ValidationError` | Dados inválidos | Email sem @ |
| `InvariantViolation` | Estado inválido | Total negativo |
| `BusinessRuleViolation` | Operação proibida | Usuário inativo comprando |
| `EntityNotFoundError` | Busca sem resultado | Pedido não existe |
| `DuplicateEntityError` | Conflito de unicidade | Email já cadastrado |

### Exemplo

```python
class CreateOrderUseCase(UseCaseBase):
    def execute(self, input):
        if not input.items:
            raise ValidationError("Pedido deve ter itens")

        user = self.user_repo.find(input.user_id)
        if not user:
            raise EntityNotFoundError(f"Usuário {input.user_id} não encontrado")

        if not user.is_active:
            raise BusinessRuleViolation("Usuário inativo não pode criar pedidos")
```

### Regra de Ouro

**Nunca use `Exception` genérico.** Sempre use exceções de domínio específicas.

---

## 4. Observabilidade

### Logging Estruturado

```python
from forgebase.observability.log_service import LogService

log = LogService(service_name="meu-app", environment="production")
log.add_console_handler()
log.add_file_handler("logs/app.log")

# Log com contexto estruturado
log.info("Pedido criado", order_id="123", user_id="456", total=99.90)

# Com correlation ID (rastrear request)
with log.correlation_context("req-abc-123"):
    log.info("Processando request")
    # todos os logs terão correlation_id
```

### Métricas

```python
from forgebase.observability.track_metrics import TrackMetrics

metrics = TrackMetrics()

# Contador
metrics.increment("orders.created", status="success")

# Timer (mede duração)
with metrics.timer("usecase.create_order_ms"):
    result = usecase.execute(input)

# Histogram
metrics.histogram("db.query_ms", 23.5, table="orders")

# Relatório
print(metrics.report())  # p50, p95, p99
```

### Padrão em UseCase

```python
class CreateOrderUseCase(UseCaseBase):
    def __init__(self, repo, log, metrics):
        self.repo = repo
        self.log = log
        self.metrics = metrics

    def execute(self, input):
        with self.metrics.timer("usecase.create_order_ms"):
            try:
                # lógica
                order = Order.create(input)
                self.repo.save(order)

                self.log.info("Pedido criado", order_id=order.id)
                self.metrics.increment("orders.created", status="success")
                return order

            except DomainException as e:
                self.log.warning("Erro de domínio", error=str(e))
                self.metrics.increment("orders.created", status="domain_error")
                raise
            except Exception as e:
                self.log.error("Erro inesperado", error=str(e))
                self.metrics.increment("orders.created", status="error")
                raise
```

---

## 5. Qualidade de Código

### Comandos Obrigatórios

```bash
# Antes de cada commit
python devtool.py quality

# Ou individualmente:
python devtool.py lint        # Ruff + Mypy
python devtool.py check-arch  # Validar camadas
python devtool.py test        # Testes
python devtool.py check-deps  # Dependências
```

### O que é Validado

| Ferramenta | Valida |
|------------|--------|
| **Ruff** | Linting, formatação, imports |
| **Mypy** | Type hints, tipos |
| **import-linter** | Boundaries entre camadas |
| **deptry** | Dependências não usadas |
| **pytest** | Testes passando |

---

## 6. Checklist para Nova Feature

### Antes de Começar

- [ ] Entendi o requisito de negócio?
- [ ] Identifiquei qual camada será afetada?
- [ ] Preciso de nova entidade, UseCase, ou Port?

### Durante Desenvolvimento

- [ ] Lógica de negócio está no Domain?
- [ ] UseCase só orquestra, não implementa I/O?
- [ ] Ports são abstrações, Adapters são implementações?
- [ ] Usei exceções de domínio específicas?
- [ ] Adicionei logs estruturados?
- [ ] Adicionei métricas relevantes?

### Antes de Commit

- [ ] `python devtool.py quality` passa?
- [ ] Funciona via CLI?
- [ ] Testes cobrem casos de sucesso e erro?
- [ ] Não introduzi dependências circulares?

---

## 7. Anti-patterns a Evitar

### ❌ UseCase acessando banco diretamente

```python
# ERRADO
class CreateOrderUseCase:
    def execute(self, input):
        conn = sqlite3.connect("db.sqlite")  # ❌ I/O direto
        conn.execute("INSERT INTO orders...")
```

```python
# CORRETO
class CreateOrderUseCase:
    def __init__(self, order_repo: OrderRepositoryPort):  # ✅ Via Port
        self.order_repo = order_repo

    def execute(self, input):
        order = Order.create(input)
        self.order_repo.save(order)  # ✅ Abstração
```

### ❌ Entidade importando infraestrutura

```python
# ERRADO
from infrastructure.database import Database  # ❌

class Order(EntityBase):
    def save(self):
        Database.insert(self)  # ❌ Entidade não faz I/O
```

```python
# CORRETO
class Order(EntityBase):
    # Entidade é pura, sem I/O
    pass

# Repository faz o I/O
class OrderRepository:
    def save(self, order: Order):
        self.db.insert(order)
```

### ❌ Lógica de negócio em Adapter

```python
# ERRADO
@app.post("/orders")
def create_order(req):
    if req.total < 0:  # ❌ Validação no adapter
        raise HTTPException(400, "Invalid total")
    # lógica aqui...  # ❌ Lógica no adapter
```

```python
# CORRETO
@app.post("/orders")
def create_order(req):
    try:
        return usecase.execute(req)  # ✅ Delega para UseCase
    except ValidationError as e:
        raise HTTPException(400, str(e))
```

### ❌ Exception genérico

```python
# ERRADO
if not user:
    raise Exception("User not found")  # ❌ Genérico
```

```python
# CORRETO
if not user:
    raise EntityNotFoundError(f"User {user_id} not found")  # ✅ Específico
```

### ❌ HTTP First

```python
# ERRADO: Criar API sem validar UseCase
@app.post("/orders")
def create_order(req):
    # Lógica direto aqui, sem testar antes
```

```python
# CORRETO: CLI First
cli.run(['exec', 'create_order', '--json', '...'])  # ✅ Testar primeiro
# Depois criar HTTP
```

---

## 8. Estrutura de Testes

### Organização

```
tests/
├── unit/           # Testes isolados (mocks)
├── integration/    # Testes com dependências reais
├── property_based/ # Testes com Hypothesis
├── contract_tests/ # Testes de contrato entre camadas
└── cli/            # Testes end-to-end via CLI
```

### Exemplo de Teste Cognitivo

```python
def test_order_creation_validates_minimum_items():
    """
    DADO um pedido sem itens
    QUANDO tento criar o pedido
    ENTÃO deve falhar com ValidationError

    Intenção: Garantir que pedidos vazios são rejeitados
    """
    usecase = CreateOrderUseCase(mock_repo)

    with pytest.raises(ValidationError) as exc:
        usecase.execute(CreateOrderInput(items=[]))

    assert "pelo menos um item" in str(exc.value)
```

---

## 9. Comandos Úteis

```bash
# Desenvolvimento
python devtool.py scaffold usecase CreateOrder  # Gerar boilerplate
python devtool.py discover                       # Listar componentes
python devtool.py test unit                      # Só testes unitários

# Qualidade
python devtool.py quality   # Suite completa
python devtool.py lint      # Só linting
python devtool.py check-arch # Só arquitetura

# CLI da aplicação
python cli.py list                              # Listar UseCases
python cli.py exec create_order --json '{...}' # Executar UseCase
```

---

## 10. Referências

- [Início Rápido](inicio-rapido.md) — Instalação
- [CLI First](cli-first.md) — Filosofia CLI First detalhada
- [Guia de Testes](guia-de-testes.md) — Testes cognitivos
- [ForgeProcess](../referencia/forge-process.md) — Ciclo cognitivo completo
- [Arquitetura](../referencia/arquitetura.md) — Modularização do núcleo

---

**Versão:** ForgeBase 0.1.5
**Atualizado:** 2025-12
