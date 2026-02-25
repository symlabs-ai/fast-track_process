# ForgeBase - Receitas Práticas

> Soluções prontas para casos comuns de desenvolvimento

Este cookbook fornece soluções práticas para casos de uso frequentes no ForgeBase. Cada receita é autocontida e pode ser copiada/adaptada para seu projeto.

---

## Índice

1. [Como Criar uma Entity](#1-como-criar-uma-entity)
2. [Como Criar um ValueObject](#2-como-criar-um-valueobject)
3. [Como Criar um UseCase](#3-como-criar-um-usecase)
4. [Como Criar um Port](#4-como-criar-um-port)
5. [Como Criar um Adapter](#5-como-criar-um-adapter)
6. [Como Adicionar Observabilidade](#6-como-adicionar-observabilidade)
7. [Como Escrever Testes Cognitivos](#7-como-escrever-testes-cognitivos)
8. [Como Integrar com ForgeProcess](#8-como-integrar-com-forgeprocess)
9. [Como Criar um Repository Customizado](#9-como-criar-um-repository-customizado)
10. [Como Adicionar Validações Customizadas](#10-como-adicionar-validações-customizadas)
11. [Como Usar Dependency Injection](#11-como-usar-dependency-injection)
12. [Como Adicionar um Novo Adapter](#12-como-adicionar-um-novo-adapter)

---

## 1. Como Criar uma Entity

**Problema**: Preciso criar uma entidade de domínio com identidade e invariantes.

**Solução**:

```python
from forgebase.domain import EntityBase, ValidationError, BusinessRuleViolation
from datetime import datetime


class Order(EntityBase):
    """
    Entidade Order.

    Representa um pedido com itens, total e status.

    Regras de Negócio:
        - Pedido deve ter pelo menos um item
        - Total deve ser positivo
        - Uma vez pago, pedido não pode ser modificado
    """

    def __init__(
        self,
        customer_id: str,
        items: list[dict],
        total: float,
        id: str | None = None,
        status: str = "pending",
        paid_at: datetime | None = None
    ):
        super().__init__(id=id)
        self.customer_id = customer_id
        self.items = items
        self.total = total
        self.status = status
        self.paid_at = paid_at
        self.validate()

    def validate(self) -> None:
        """Validar invariantes do pedido."""
        if not self.customer_id:
            raise ValidationError("Pedido deve ter um cliente")

        if not self.items:
            raise ValidationError("Pedido deve ter pelo menos um item")

        if self.total <= 0:
            raise ValidationError("Total do pedido deve ser positivo")

        if self.status not in ["pending", "paid", "shipped", "cancelled"]:
            raise ValidationError(f"Status inválido: {self.status}")

    def mark_as_paid(self) -> None:
        """Marcar pedido como pago."""
        if self.status == "cancelled":
            raise BusinessRuleViolation("Não é possível pagar pedido cancelado")

        if self.paid_at is not None:
            raise BusinessRuleViolation("Pedido já foi pago")

        self.status = "paid"
        self.paid_at = datetime.now()

    def add_item(self, item: dict, price: float) -> None:
        """Adicionar item ao pedido."""
        if self.paid_at is not None:
            raise BusinessRuleViolation("Não é possível modificar pedido pago")

        self.items.append(item)
        self.total += price

    def __str__(self) -> str:
        return f"Order {self.id} ({self.status}) - R${self.total:.2f}"
```

**Pontos-Chave**:
- Herda de `EntityBase`
- `validate()` verifica invariantes
- Métodos de negócio (`mark_as_paid`, `add_item`)
- Exceções apropriadas (`ValidationError` vs `BusinessRuleViolation`)

---

## 2. Como Criar um ValueObject

**Problema**: Preciso de um objeto imutável que representa um valor (não tem identidade).

**Solução**:

```python
from forgebase.domain import ValueObjectBase, ValidationError
import re


class EmailAddress(ValueObjectBase):
    """
    Value object para endereço de email.

    Valor imutável representando um email validado.
    """

    def __init__(self, address: str):
        super().__init__()
        self.address = address
        self.validate()
        self._freeze()  # Torna imutável

    def validate(self) -> None:
        """Validar formato do email."""
        if not self.address:
            raise ValidationError("Endereço de email não pode ser vazio")

        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, self.address):
            raise ValidationError(f"Formato de email inválido: {self.address}")

    def to_dict(self) -> dict:
        return {"address": self.address}

    @classmethod
    def from_dict(cls, data: dict) -> 'EmailAddress':
        return cls(address=data["address"])

    def __str__(self) -> str:
        return self.address

    def __eq__(self, other) -> bool:
        if not isinstance(other, EmailAddress):
            return False
        return self.address == other.address

    def __hash__(self) -> int:
        return hash(self.address)


class Money(ValueObjectBase):
    """Value object para valores monetários."""

    def __init__(self, amount: float, currency: str = "BRL"):
        super().__init__()
        self.amount = amount
        self.currency = currency
        self.validate()
        self._freeze()

    def validate(self) -> None:
        if self.amount < 0:
            raise ValidationError("Valor não pode ser negativo")

        if self.currency not in ["USD", "EUR", "BRL"]:
            raise ValidationError(f"Moeda não suportada: {self.currency}")

    def add(self, other: 'Money') -> 'Money':
        """Somar dois valores monetários."""
        if self.currency != other.currency:
            raise ValueError("Não é possível somar moedas diferentes")
        return Money(self.amount + other.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount:.2f}"
```

**Pontos-Chave**:
- Herda de `ValueObjectBase`
- `_freeze()` garante imutabilidade
- Igualdade baseada em valor, não identidade
- Hashable (pode usar em sets/dicts)

---

## 3. Como Criar um UseCase

**Problema**: Preciso implementar um caso de uso de aplicação.

**Solução**:

```python
from forgebase.application import UseCaseBase, DTOBase


class PlaceOrderInput(DTOBase):
    """Input para colocar um pedido."""

    def __init__(self, customer_id: str, items: list[dict]):
        self.customer_id = customer_id
        self.items = items

    def validate(self) -> None:
        if not self.customer_id:
            raise ValueError("ID do cliente é obrigatório")
        if not self.items:
            raise ValueError("Pelo menos um item é obrigatório")

    def to_dict(self) -> dict:
        return {"customer_id": self.customer_id, "items": self.items}

    @classmethod
    def from_dict(cls, data: dict) -> 'PlaceOrderInput':
        return cls(
            customer_id=data["customer_id"],
            items=data["items"]
        )


class PlaceOrderOutput(DTOBase):
    """Output da criação do pedido."""

    def __init__(self, order_id: str, total: float, status: str):
        self.order_id = order_id
        self.total = total
        self.status = status

    def validate(self) -> None:
        pass

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "total": self.total,
            "status": self.status
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PlaceOrderOutput':
        return cls(
            order_id=data["order_id"],
            total=data["total"],
            status=data["status"]
        )


class PlaceOrderUseCase(UseCaseBase):
    """
    Colocar um novo pedido.

    Orquestra:
        1. Validar que cliente existe
        2. Calcular total do pedido
        3. Criar entidade de pedido
        4. Persistir pedido
        5. Enviar notificação de confirmação
    """

    def __init__(
        self,
        order_repository: OrderRepositoryPort,
        customer_repository: CustomerRepositoryPort,
        notification_service: NotificationServicePort
    ):
        self.order_repository = order_repository
        self.customer_repository = customer_repository
        self.notification_service = notification_service

    def execute(self, input_dto: PlaceOrderInput) -> PlaceOrderOutput:
        """Executar criação do pedido."""
        # 1. Validar input
        input_dto.validate()

        # 2. Validar que cliente existe
        customer = self.customer_repository.find_by_id(input_dto.customer_id)
        if customer is None:
            raise BusinessRuleViolation(
                f"Cliente não encontrado: {input_dto.customer_id}"
            )

        # 3. Calcular total
        total = sum(item["price"] * item["quantity"] for item in input_dto.items)

        # 4. Criar entidade de pedido
        order = Order(
            customer_id=customer.id,
            items=input_dto.items,
            total=total
        )

        # 5. Validar regras de negócio
        order.validate()

        # 6. Persistir
        self.order_repository.save(order)

        # 7. Enviar notificação
        self.notification_service.send(
            recipient=customer.email,
            subject="Confirmação de Pedido",
            body=f"Seu pedido {order.id} foi realizado!"
        )

        # 8. Retornar output
        return PlaceOrderOutput(
            order_id=order.id,
            total=order.total,
            status=order.status
        )

    def _before_execute(self) -> None:
        """Hook antes da execução."""
        pass

    def _after_execute(self) -> None:
        """Hook após a execução."""
        pass

    def _on_error(self, error: Exception) -> None:
        """Hook em caso de erro."""
        pass
```

**Pontos-Chave**:
- Herda de `UseCaseBase`
- Input/Output DTOs explícitos
- Dependências injetadas via construtor
- Orquestração clara (passos numerados)
- Hooks para observabilidade

---

## 4. Como Criar um Port

**Problema**: Preciso definir um contrato de comunicação externa.

**Solução**:

```python
from abc import ABC, abstractmethod


class NotificationServicePort(ABC):
    """
    Port para envio de notificações.

    Implementações podem usar email, SMS, push notifications, etc.
    """

    @abstractmethod
    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **options
    ) -> None:
        """Enviar uma notificação."""
        pass

    @abstractmethod
    def send_bulk(
        self,
        recipients: list[str],
        subject: str,
        body: str
    ) -> dict[str, bool]:
        """
        Enviar notificação para múltiplos destinatários.

        Retorna:
            Dict mapeando destinatário para status de sucesso
        """
        pass


class PaymentGatewayPort(ABC):
    """Port para processamento de pagamentos."""

    @abstractmethod
    def charge(
        self,
        amount: float,
        currency: str,
        payment_method: str,
        customer_id: str
    ) -> dict:
        """
        Processar um pagamento.

        Retorna:
            Dict com transaction_id, status, etc.
        """
        pass

    @abstractmethod
    def refund(
        self,
        transaction_id: str,
        amount: float
    ) -> dict:
        """Processar um reembolso."""
        pass
```

**Pontos-Chave**:
- Herda de `ABC`
- Métodos `@abstractmethod`
- Docstring explicando contrato
- Foco em **o quê**, não **como**
- Type hints claros

---

## 5. Como Criar um Adapter

**Problema**: Preciso implementar um port com tecnologia específica.

**Solução**:

```python
import smtplib
from email.mime.text import MIMEText


class EmailNotificationAdapter(NotificationServicePort):
    """
    Implementação de email do NotificationServicePort.

    Usa SMTP para enviar notificações por email.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_address: str
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_address = from_address

    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **options
    ) -> None:
        """Enviar notificação por email."""
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = recipient

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)

    def send_bulk(
        self,
        recipients: list[str],
        subject: str,
        body: str
    ) -> dict[str, bool]:
        """Enviar para múltiplos destinatários."""
        results = {}
        for recipient in recipients:
            try:
                self.send(recipient, subject, body)
                results[recipient] = True
            except Exception:
                results[recipient] = False
        return results


class ConsoleNotificationAdapter(NotificationServicePort):
    """
    Implementação de console (para testes/dev).

    Imprime notificações no stdout ao invés de enviar.
    """

    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **options
    ) -> None:
        print(f"[NOTIFICAÇÃO]")
        print(f"  Para: {recipient}")
        print(f"  Assunto: {subject}")
        print(f"  Corpo: {body}")

    def send_bulk(
        self,
        recipients: list[str],
        subject: str,
        body: str
    ) -> dict[str, bool]:
        for recipient in recipients:
            self.send(recipient, subject, body)
        return {r: True for r in recipients}
```

**Pontos-Chave**:
- Implementa o Port
- Recebe configuração via construtor
- Detalhes de implementação encapsulados
- Múltiplos adapters para mesmo port

---

## 6. Como Adicionar Observabilidade

**Problema**: Preciso de logs, métricas e tracing no meu UseCase.

**Solução**:

```python
from forgebase.observability import LogService, TrackMetrics
from forgebase.application.decorators import track_metrics


class PlaceOrderUseCase(UseCaseBase):
    def __init__(
        self,
        order_repository: OrderRepositoryPort,
        logger: LogService | None = None,
        metrics: TrackMetrics | None = None
    ):
        self.order_repository = order_repository
        self.logger = logger or LogService(name="place_order")
        self.metrics = metrics or TrackMetrics()

    @track_metrics(
        metric_name="place_order",
        track_duration=True,
        track_errors=True
    )
    def execute(self, input_dto: PlaceOrderInput) -> PlaceOrderOutput:
        # Log estruturado
        self.logger.info(
            "Criando pedido",
            customer_id=input_dto.customer_id,
            items_count=len(input_dto.items)
        )

        try:
            # ... lógica ...

            # Métricas customizadas
            self.metrics.increment("orders.placed", tags={"customer": input_dto.customer_id})
            self.metrics.gauge("order.total", total, tags={"customer": input_dto.customer_id})

            self.logger.info(
                "Pedido criado com sucesso",
                order_id=order.id,
                total=order.total
            )

            return PlaceOrderOutput(...)

        except Exception as e:
            self.logger.error(
                "Falha ao criar pedido",
                error=str(e),
                customer_id=input_dto.customer_id
            )
            self.metrics.increment("orders.errors")
            raise
```

**Com Context Manager para Performance:**

```python
def execute(self, input_dto):
    with self.metrics.measure("place_order.total"):
        # ... lógica ...

        with self.metrics.measure("place_order.validate_customer"):
            customer = self.customer_repository.find_by_id(...)

        with self.metrics.measure("place_order.create_order"):
            order = Order(...)

        with self.metrics.measure("place_order.persist"):
            self.order_repository.save(order)

    return output
```

**Pontos-Chave**:
- Logger e metrics injetados
- `@track_metrics` decorator para auto-instrumentação
- Logs estruturados (key-value pairs)
- Context managers para durations
- Error tracking

---

## 7. Como Escrever Testes Cognitivos

Veja o [Guia de Testes](guia-de-testes.md) para documentação completa sobre testes cognitivos.

---

## 8. Como Integrar com ForgeProcess

**Problema**: Preciso sincronizar specs YAML com código Python.

**Solução**:

### Passo 1: Criar Spec YAML

```yaml
# specs/place_order.yaml
version: "1.0"
usecase:
  name: PlaceOrder
  description: Criar um novo pedido de cliente

  inputs:
    - name: customer_id
      type: str
      required: true
    - name: items
      type: list
      required: true

  outputs:
    - name: order_id
      type: str
    - name: total
      type: float
    - name: status
      type: str

  business_rules:
    - Cliente deve existir
    - Pedido deve ter pelo menos um item
    - Total deve ser calculado corretamente
```

### Passo 2: Gerar Código

```python
from forgebase.integration import YAMLSync

sync = YAMLSync()

# Parse YAML
spec = sync.parse_yaml("specs/place_order.yaml")

# Gerar código skeleton
code = sync.generate_code(spec)

# Escrever em arquivo
with open("src/application/place_order_usecase.py", "w") as f:
    f.write(code)
```

### Passo 3: Validar Consistência

```python
# Detectar drift
drift = sync.detect_drift(PlaceOrderUseCase, spec)

if drift:
    print("Drift detectado:")
    for issue in drift:
        print(f"  - {issue}")
else:
    print("Código corresponde à spec YAML")
```

---

## 9. Como Criar um Repository Customizado

**Problema**: Preciso implementar um repository para tecnologia específica (MongoDB, Redis, etc.).

**Solução**:

```python
from forgebase.infrastructure.repository import RepositoryBase
from pymongo import MongoClient


class MongoDBRepository(RepositoryBase[T]):
    """Implementação MongoDB do RepositoryBase."""

    def __init__(
        self,
        connection_string: str,
        database: str,
        collection: str,
        entity_type: Type[T]
    ):
        self.client = MongoClient(connection_string)
        self.db = self.client[database]
        self.collection = self.db[collection]
        self.entity_type = entity_type

    def save(self, entity: T) -> None:
        entity.validate()

        doc = {
            "_id": entity.id,
            **entity.to_dict()
        }

        self.collection.replace_one(
            {"_id": entity.id},
            doc,
            upsert=True
        )

    def find_by_id(self, id: str) -> T | None:
        doc = self.collection.find_one({"_id": id})
        if doc is None:
            return None

        return self.entity_type.from_dict(doc)

    def find_all(self) -> list[T]:
        docs = self.collection.find()
        return [self.entity_type.from_dict(doc) for doc in docs]

    def delete(self, id: str) -> None:
        self.collection.delete_one({"_id": id})

    def exists(self, id: str) -> bool:
        return self.collection.count_documents({"_id": id}) > 0

    def close(self):
        """Limpar conexão."""
        self.client.close()


# Uso
repository = MongoDBRepository(
    connection_string="mongodb://localhost:27017",
    database="my_app",
    collection="orders",
    entity_type=Order
)
```

**Pontos-Chave**:
- Implementa `RepositoryBase[T]`
- Generic type para entidade
- Métodos CRUD completos
- Cleanup resources (close)

---

## 10. Como Adicionar Validações Customizadas

**Problema**: Preciso de validações reutilizáveis no domínio.

**Solução**:

```python
# src/domain/validators.py
from forgebase.domain import ValidationError


class DomainValidators:
    """Validadores de domínio reutilizáveis."""

    @staticmethod
    def not_empty(value: str, field_name: str = "Campo") -> None:
        """Validar que string não está vazia."""
        if not value or not value.strip():
            raise ValidationError(f"{field_name} não pode ser vazio")

    @staticmethod
    def min_length(value: str, min_len: int, field_name: str = "Campo") -> None:
        """Validar tamanho mínimo."""
        if len(value) < min_len:
            raise ValidationError(
                f"{field_name} deve ter pelo menos {min_len} caracteres"
            )

    @staticmethod
    def max_length(value: str, max_len: int, field_name: str = "Campo") -> None:
        """Validar tamanho máximo."""
        if len(value) > max_len:
            raise ValidationError(
                f"{field_name} deve ter no máximo {max_len} caracteres"
            )

    @staticmethod
    def in_range(value: float, min_val: float, max_val: float, field_name: str = "Campo") -> None:
        """Validar que valor está no intervalo."""
        if not (min_val <= value <= max_val):
            raise ValidationError(
                f"{field_name} deve estar entre {min_val} e {max_val}"
            )

    @staticmethod
    def positive(value: float, field_name: str = "Campo") -> None:
        """Validar que valor é positivo."""
        if value <= 0:
            raise ValidationError(f"{field_name} deve ser positivo")

    @staticmethod
    def email_format(value: str, field_name: str = "Email") -> None:
        """Validar formato de email."""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, value):
            raise ValidationError(f"Formato de {field_name} inválido: {value}")


# Uso em Entity
class Product(EntityBase):
    def __init__(self, name: str, price: float, description: str = ""):
        super().__init__()
        self.name = name
        self.price = price
        self.description = description
        self.validate()

    def validate(self) -> None:
        DomainValidators.not_empty(self.name, "Nome do produto")
        DomainValidators.max_length(self.name, 100, "Nome do produto")
        DomainValidators.positive(self.price, "Preço do produto")

        if self.description:
            DomainValidators.max_length(self.description, 500, "Descrição")
```

**Pontos-Chave**:
- Validators centralizados e reutilizáveis
- Mensagens de erro consistentes
- Fácil testar isoladamente
- Composição em entities

---

## 11. Como Usar Dependency Injection

**Problema**: Preciso conectar dependências sem acoplamento.

**Solução**:

```python
# main.py com DI Container
from forgebase.infrastructure.configuration import ConfigLoader


def setup_dependencies(config: dict) -> dict:
    """Configurar dependências a partir da configuração."""

    # Logger
    logger = LogService(
        name="my-app",
        level=config.get("log_level", "INFO")
    )

    # Metrics
    metrics = TrackMetrics()

    # Repositories
    order_repository = JSONRepository(
        file_path=config.get("orders_file", "data/orders.json"),
        entity_type=Order,
        logger=logger
    )

    customer_repository = JSONRepository(
        file_path=config.get("customers_file", "data/customers.json"),
        entity_type=Customer,
        logger=logger
    )

    # Use cases
    place_order_usecase = PlaceOrderUseCase(
        order_repository=order_repository,
        customer_repository=customer_repository,
        logger=logger,
        metrics=metrics
    )

    return {
        "logger": logger,
        "metrics": metrics,
        "order_repository": order_repository,
        "customer_repository": customer_repository,
        "place_order_usecase": place_order_usecase
    }


def main():
    # Carregar config
    config = ConfigLoader.load("config.yaml")

    # Configurar DI
    deps = setup_dependencies(config)

    # Usar use case
    usecase = deps["place_order_usecase"]

    # Executar
    output = usecase.execute(PlaceOrderInput(...))
    print(f"Pedido criado: {output.order_id}")


if __name__ == "__main__":
    main()
```

**Pontos-Chave**:
- Centraliza wiring em um lugar
- Configuration-driven
- Fácil trocar implementações

---

## 12. Como Adicionar um Novo Adapter

**Problema**: Preciso adicionar um novo tipo de adapter (ex: gRPC, GraphQL).

**Solução**:

```python
# src/adapters/grpc/grpc_adapter.py
import grpc
from concurrent import futures
from forgebase.adapters import AdapterBase


class GRPCAdapter(AdapterBase):
    """
    Adapter gRPC para expor UseCases via gRPC.

    Mapeia métodos de serviço gRPC para execuções de UseCase.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 50051,
        max_workers: int = 10
    ):
        self.host = host
        self.port = port
        self.max_workers = max_workers
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        self._usecases = {}

    def name(self) -> str:
        return "GRPCAdapter"

    def module(self) -> str:
        return "forgebase.adapters.grpc"

    def register_usecase(
        self,
        service_name: str,
        method_name: str,
        usecase: UseCaseBase
    ):
        """Registrar um UseCase para ser exposto via gRPC."""
        key = f"{service_name}.{method_name}"
        self._usecases[key] = usecase

    def start(self):
        """Iniciar servidor gRPC."""
        self.server.add_insecure_port(f"{self.host}:{self.port}")
        self.server.start()
        print(f"Servidor gRPC escutando em {self.host}:{self.port}")

    def stop(self):
        """Parar servidor gRPC."""
        self.server.stop(grace=5)


# Uso
adapter = GRPCAdapter(port=50051)

adapter.register_usecase(
    service_name="OrderService",
    method_name="PlaceOrder",
    usecase=place_order_usecase
)

adapter.start()
```

**Pontos-Chave**:
- Herda de `AdapterBase`
- Implementa `name()` e `module()`
- Encapsula tecnologia específica (gRPC)
- Registra UseCases dinamicamente

---

## Próximos Passos

Explore mais:
- **[Início Rápido](inicio-rapido.md)** — Tutorial completo
- **[Guia de Testes](guia-de-testes.md)** — Testes cognitivos detalhados

---

**Feliz Forjamento!**

*"Cada receita é uma história de transformar pensamento em estrutura."*
