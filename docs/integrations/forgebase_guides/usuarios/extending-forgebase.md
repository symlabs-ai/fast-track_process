# Extending ForgeBase

> "ForgeBase é extensível por design — adicione sem modificar o core."

Este guia ensina como estender ForgeBase com novos componentes, mantendo Clean Architecture e princípios de design.

---

## 🎯 Princípios de Extensão

ForgeBase segue o **Open-Closed Principle**: aberto para extensão, fechado para modificação.

### O Que Pode Ser Estendido?

✅ **Sem modificar o core:**
- Novos Adapters (Driving & Driven)
- Novos Repositories
- Novas Validações de Domínio
- Novos Observers (Logging, Metrics)
- Novos Decorators
- Custom DTOs

❌ **Requer modificação (evite se possível):**
- Base classes (EntityBase, UseCaseBase)
- Core exceptions
- Arquitetura fundamental

---

## 1. Adicionando um Novo Adapter

### 1.1 Driving Adapter (Primary)

Adapters que **disparam** a aplicação (CLI, HTTP, gRPC, GraphQL, etc.)

#### Exemplo: WebSocket Adapter

```python
# src/forgebase/adapters/websocket/websocket_adapter.py
"""
WebSocket adapter for real-time ForgeBase interactions.
"""

import asyncio
import websockets
from forgebase.adapters import AdapterBase
from forgebase.application import UseCaseBase


class WebSocketAdapter(AdapterBase):
    """
    WebSocket adapter for real-time UseCase execution.

    Allows clients to execute UseCases via WebSocket connections.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self._usecases = {}

    def name(self) -> str:
        return "WebSocketAdapter"

    def module(self) -> str:
        return "forgebase.adapters.websocket"

    def register_usecase(self, command: str, usecase: UseCaseBase):
        """Register a UseCase to be called via WebSocket."""
        self._usecases[command] = usecase

    async def handle_client(self, websocket, path):
        """Handle WebSocket client connection."""
        async for message in websocket:
            try:
                # Parse message (JSON expected)
                import json
                data = json.loads(message)

                command = data.get("command")
                input_data = data.get("input", {})

                if command not in self._usecases:
                    await websocket.send(json.dumps({
                        "error": f"Unknown command: {command}"
                    }))
                    continue

                # Execute UseCase
                usecase = self._usecases[command]
                output = usecase.execute(input_data)

                # Send response
                await websocket.send(json.dumps({
                    "success": True,
                    "output": output.to_dict() if hasattr(output, 'to_dict') else str(output)
                }))

            except Exception as e:
                await websocket.send(json.dumps({
                    "error": str(e)
                }))

    async def start(self):
        """Start WebSocket server."""
        async with websockets.serve(self.handle_client, self.host, self.port):
            print(f"WebSocket server listening on ws://{self.host}:{self.port}")
            await asyncio.Future()  # Run forever

    def run(self):
        """Run WebSocket server (blocking)."""
        asyncio.run(self.start())
```

**Uso:**

```python
adapter = WebSocketAdapter(port=8765)

adapter.register_usecase("create_user", create_user_usecase)
adapter.register_usecase("place_order", place_order_usecase)

adapter.run()
```

**Cliente:**

```javascript
const ws = new WebSocket('ws://localhost:8765');

ws.send(JSON.stringify({
    command: 'create_user',
    input: {
        name: 'Alice',
        email: 'alice@example.com'
    }
}));

ws.onmessage = (event) => {
    const response = JSON.parse(event.data);
    console.log(response.output);
};
```

### 1.2 Driven Adapter (Secondary)

Adapters que a aplicação **usa** (Notification, Payment, Cache, etc.)

#### Exemplo: Twilio SMS Adapter

```python
# src/infrastructure/notifications/twilio_sms_adapter.py
"""
Twilio SMS notification adapter.
"""

from twilio.rest import Client
from application.ports import NotificationServicePort


class TwilioSMSAdapter(NotificationServicePort):
    """
    SMS notification using Twilio.

    Implements NotificationServicePort for sending SMS messages.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str
    ):
        self.client = Client(account_sid, auth_token)
        self.from_number = from_number

    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        **options
    ) -> None:
        """Send SMS notification."""
        message = self.client.messages.create(
            body=f"{subject}\n\n{body}",
            from_=self.from_number,
            to=recipient
        )

        print(f"SMS sent: {message.sid}")

    def send_bulk(
        self,
        recipients: list[str],
        subject: str,
        body: str
    ) -> dict[str, bool]:
        """Send to multiple recipients."""
        results = {}
        for recipient in recipients:
            try:
                self.send(recipient, subject, body)
                results[recipient] = True
            except Exception as e:
                print(f"Failed to send to {recipient}: {e}")
                results[recipient] = False
        return results
```

**Uso:**

```python
# Configuration
sms_adapter = TwilioSMSAdapter(
    account_sid=config['twilio']['account_sid'],
    auth_token=config['twilio']['auth_token'],
    from_number=config['twilio']['from_number']
)

# Inject into UseCase
usecase = PlaceOrderUseCase(
    order_repository=order_repo,
    notification_service=sms_adapter  # Inject SMS adapter
)
```

---

## 2. Adicionando um Novo Repository

Repositories implementam `RepositoryBase[T]`.

### Exemplo: Redis Repository

```python
# src/infrastructure/repository/redis_repository.py
"""
Redis-based repository implementation.
"""

import json
import redis
from forgebase.infrastructure.repository import RepositoryBase
from typing import Type, TypeVar, Optional

T = TypeVar('T')


class RedisRepository(RepositoryBase[T]):
    """
    Redis implementation of RepositoryBase.

    Stores entities as JSON in Redis with configurable TTL.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        entity_type: Type[T],
        key_prefix: str = "entity",
        ttl: Optional[int] = None  # TTL in seconds
    ):
        self.client = redis_client
        self.entity_type = entity_type
        self.key_prefix = key_prefix
        self.ttl = ttl

    def _make_key(self, entity_id: str) -> str:
        """Create Redis key."""
        return f"{self.key_prefix}:{entity_id}"

    def save(self, entity: T) -> None:
        entity.validate()

        key = self._make_key(entity.id)
        data = json.dumps(entity.to_dict())

        if self.ttl:
            self.client.setex(key, self.ttl, data)
        else:
            self.client.set(key, data)

    def find_by_id(self, id: str) -> Optional[T]:
        key = self._make_key(id)
        data = self.client.get(key)

        if data is None:
            return None

        entity_dict = json.loads(data)
        return self.entity_type.from_dict(entity_dict)

    def find_all(self) -> list[T]:
        """Find all entities with this key prefix."""
        pattern = f"{self.key_prefix}:*"
        keys = self.client.keys(pattern)

        entities = []
        for key in keys:
            data = self.client.get(key)
            if data:
                entity_dict = json.loads(data)
                entities.append(self.entity_type.from_dict(entity_dict))

        return entities

    def delete(self, id: str) -> None:
        key = self._make_key(id)
        self.client.delete(key)

    def exists(self, id: str) -> bool:
        key = self._make_key(id)
        return self.client.exists(key) > 0
```

**Uso:**

```python
import redis

# Setup Redis
redis_client = redis.Redis(host='localhost', port=6379, db=0)

# Create repository
user_repo = RedisRepository(
    redis_client=redis_client,
    entity_type=User,
    key_prefix="user",
    ttl=3600  # 1 hour
)

# Use in UseCase
usecase = CreateUserUseCase(user_repository=user_repo)
```

---

## 3. Adicionando Validações Customizadas

### 3.1 Domain Validators

```python
# src/domain/validators/business_validators.py
"""
Custom business validators.
"""

from forgebase.domain import ValidationError
import re


class BusinessValidators:
    """Business-specific validation rules."""

    @staticmethod
    def cpf_format(cpf: str, field_name: str = "CPF") -> None:
        """Validate Brazilian CPF format."""
        # Remove formatting
        cpf = re.sub(r'[^\d]', '', cpf)

        if len(cpf) != 11:
            raise ValidationError(f"Invalid {field_name} length")

        # CPF validation algorithm
        # (simplified - add full validation)
        if cpf == cpf[0] * 11:
            raise ValidationError(f"Invalid {field_name}")

        # Calculate check digits
        # ...

    @staticmethod
    def credit_card_format(card_number: str) -> None:
        """Validate credit card using Luhn algorithm."""
        card_number = re.sub(r'[^\d]', '', card_number)

        if len(card_number) not in [13, 14, 15, 16, 19]:
            raise ValidationError("Invalid card number length")

        # Luhn algorithm
        digits = [int(d) for d in card_number]
        checksum = 0

        for i in range(len(digits) - 2, -1, -2):
            digits[i] *= 2
            if digits[i] > 9:
                digits[i] -= 9

        checksum = sum(digits)

        if checksum % 10 != 0:
            raise ValidationError("Invalid card number (failed Luhn check)")
```

**Uso:**

```python
class BrazilianCustomer(EntityBase):
    def __init__(self, name: str, cpf: str):
        super().__init__()
        self.name = name
        self.cpf = cpf
        self.validate()

    def validate(self) -> None:
        BusinessValidators.cpf_format(self.cpf)
```

### 3.2 Composite Validators

```python
class CompositeValidator:
    """Combine multiple validators."""

    def __init__(self, *validators):
        self.validators = validators

    def validate(self, value: any, field_name: str = "Field") -> None:
        """Run all validators."""
        for validator in self.validators:
            validator(value, field_name)


# Usage
email_validator = CompositeValidator(
    lambda v, f: DomainValidators.not_empty(v, f),
    lambda v, f: DomainValidators.email_format(v, f),
    lambda v, f: DomainValidators.max_length(v, 100, f)
)

email_validator.validate("alice@example.com", "Email")
```

---

## 4. Adicionando Observabilidade Customizada

### 4.1 Custom Logger Implementation

```python
# src/infrastructure/logging/custom_logger.py
"""
Custom logger implementation.
"""

import logging
from forgebase.infrastructure.logging import LoggerPort


class ElasticsearchLogger(LoggerPort):
    """
    Logger that sends logs to Elasticsearch.
    """

    def __init__(self, es_client, index_name: str):
        self.es_client = es_client
        self.index_name = index_name

    def _log(self, level: str, message: str, **context):
        """Send log to Elasticsearch."""
        document = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
            **context
        }

        self.es_client.index(
            index=self.index_name,
            body=document
        )

    def debug(self, message: str, **context) -> None:
        self._log("DEBUG", message, **context)

    def info(self, message: str, **context) -> None:
        self._log("INFO", message, **context)

    def warning(self, message: str, **context) -> None:
        self._log("WARNING", message, **context)

    def error(self, message: str, **context) -> None:
        self._log("ERROR", message, **context)

    def critical(self, message: str, **context) -> None:
        self._log("CRITICAL", message, **context)
```

### 4.2 Custom Metrics Exporter

```python
# src/observability/exporters/datadog_exporter.py
"""
DataDog metrics exporter.
"""

from datadog import statsd
from forgebase.observability import TrackMetrics


class DataDogMetricsExporter:
    """Export ForgeBase metrics to DataDog."""

    def __init__(self, metrics: TrackMetrics, prefix: str = "forgebase"):
        self.metrics = metrics
        self.prefix = prefix

    def export(self):
        """Export all metrics to DataDog."""
        report = self.metrics.report()

        for metric_name, metric_data in report.items():
            full_name = f"{self.prefix}.{metric_name}"

            if metric_data['type'] == 'counter':
                statsd.increment(full_name, metric_data['value'])

            elif metric_data['type'] == 'gauge':
                statsd.gauge(full_name, metric_data['value'])

            elif metric_data['type'] == 'histogram':
                for value in metric_data['values']:
                    statsd.histogram(full_name, value)
```

---

## 5. Adicionando Decorators Customizados

### 5.1 Retry Decorator

```python
# src/application/decorators/retry.py
"""
Retry decorator for resilient operations.
"""

import time
from functools import wraps


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Retry decorator with exponential backoff.

    :param max_attempts: Maximum number of retry attempts
    :param delay: Initial delay between retries (seconds)
    :param backoff: Multiplier for delay on each retry
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise  # Last attempt, re-raise

                    print(f"Attempt {attempt + 1} failed: {e}. Retrying in {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff

        return wrapper
    return decorator
```

**Uso:**

```python
class CreateUserUseCase(UseCaseBase):
    @retry(max_attempts=3, delay=1.0)
    @track_metrics(metric_name="create_user")
    def execute(self, input_dto: CreateUserInput) -> CreateUserOutput:
        # May fail due to transient issues (network, etc.)
        ...
```

### 5.2 Cache Decorator

```python
# src/application/decorators/cache.py
"""
Cache decorator for UseCases.
"""

from functools import wraps
import hashlib
import json


class CacheDecorator:
    """Cache UseCase results."""

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self.cache = {}

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key
            key = self._make_key(args, kwargs)

            # Check cache
            if key in self.cache:
                cached_value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    return cached_value

            # Execute and cache
            result = func(*args, **kwargs)
            self.cache[key] = (result, time.time())

            return result

        return wrapper

    def _make_key(self, args, kwargs) -> str:
        """Create cache key from arguments."""
        data = json.dumps((args, kwargs), sort_keys=True, default=str)
        return hashlib.md5(data.encode()).hexdigest()


# Usage
cache = CacheDecorator(ttl=300)

class GetUserUseCase(UseCaseBase):
    @cache
    def execute(self, user_id: str) -> UserOutput:
        # Cached for 5 minutes
        ...
```

---

## 6. Plugin System (Avançado)

Para extensibilidade máxima, crie um sistema de plugins:

```python
# src/forgebase/plugins/plugin_manager.py
"""
Plugin system for ForgeBase.
"""

import importlib
from typing import Dict, Any


class Plugin:
    """Base class for plugins."""

    def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize plugin with configuration."""
        pass

    def on_usecase_execute(self, usecase_name: str, input_data: Any) -> None:
        """Hook called before UseCase execution."""
        pass

    def on_usecase_complete(self, usecase_name: str, output_data: Any) -> None:
        """Hook called after successful UseCase execution."""
        pass

    def on_usecase_error(self, usecase_name: str, error: Exception) -> None:
        """Hook called when UseCase fails."""
        pass


class PluginManager:
    """Manages ForgeBase plugins."""

    def __init__(self):
        self.plugins = []

    def load_plugin(self, plugin_path: str, config: Dict[str, Any]) -> None:
        """
        Load a plugin from module path.

        :param plugin_path: Module path (e.g., "my_plugins.analytics_plugin")
        :param config: Plugin configuration
        """
        module = importlib.import_module(plugin_path)
        plugin_class = getattr(module, "Plugin")

        plugin = plugin_class()
        plugin.initialize(config)

        self.plugins.append(plugin)

    def trigger(self, hook_name: str, *args, **kwargs) -> None:
        """Trigger a hook on all plugins."""
        for plugin in self.plugins:
            if hasattr(plugin, hook_name):
                method = getattr(plugin, hook_name)
                method(*args, **kwargs)
```

**Exemplo de Plugin:**

```python
# my_plugins/analytics_plugin.py
"""
Analytics plugin for tracking UseCase execution.
"""

from forgebase.plugins import Plugin


class AnalyticsPlugin(Plugin):
    """Send analytics to external service."""

    def initialize(self, config):
        self.api_key = config['api_key']
        self.endpoint = config['endpoint']

    def on_usecase_complete(self, usecase_name, output_data):
        """Track successful execution."""
        import requests

        requests.post(self.endpoint, json={
            "event": "usecase_executed",
            "usecase": usecase_name,
            "timestamp": time.time()
        }, headers={
            "Authorization": f"Bearer {self.api_key}"
        })
```

**Uso:**

```python
plugin_manager = PluginManager()

plugin_manager.load_plugin("my_plugins.analytics_plugin", {
    "api_key": "...",
    "endpoint": "https://analytics.example.com/events"
})

# In core_init or UseCase execution
plugin_manager.trigger("on_usecase_complete", usecase_name, output)
```

---

## 🎯 Checklist de Extensão

Ao adicionar uma extensão, verifique:

- [ ] Segue Clean Architecture (dependências corretas)
- [ ] Implementa interface apropriada (Port)
- [ ] Type hints completos
- [ ] Docstrings reST presentes
- [ ] Testes escritos (unit + integration)
- [ ] Exemplos de uso documentados
- [ ] README atualizado se aplicável
- [ ] Não modifica código core do ForgeBase

---

## Recursos Adicionais

- **[Receitas](receitas.md)** — Receitas práticas
- **[ForgeBase Rules](forgebase-rules.md)** — Regras e convenções

---

**Estenda com Propósito! 🔌**

*"Extensibilidade sem sacrificar coesão é a marca de boa arquitetura."*
