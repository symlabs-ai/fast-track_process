# Guia de Testes ForgeBase

> "Testes não apenas validam comportamento — validam intenção."

Este guia ensina como escrever testes efetivos no ForgeBase, incluindo testes unitários tradicionais e **testes cognitivos** que validam coerência entre intenção e execução.

---

## Filosofia de Testes no ForgeBase

ForgeBase adota uma filosofia de testes em **três níveis**:

1. **Unit Tests** — Validam comportamento de componentes isolados
2. **Integration Tests** — Validam interação entre componentes
3. **Cognitive Tests** — Validam coerência entre intenção e execução

```
┌────────────────────────────────────────────┐
│          Cognitive Tests                   │  ← Valida INTENÇÃO
│  "O código faz o que PRETENDÍAMOS?"       │
└─────────────┬──────────────────────────────┘
              │
┌─────────────┴──────────────────────────────┐
│        Integration Tests                   │  ← Valida INTEGRAÇÃO
│  "Os componentes funcionam juntos?"        │
└─────────────┬──────────────────────────────┘
              │
┌─────────────┴──────────────────────────────┐
│           Unit Tests                       │  ← Valida COMPORTAMENTO
│  "Este componente funciona isoladamente?"  │
└────────────────────────────────────────────┘
```

---

## 1. Unit Tests (Testes Unitários)

### Estrutura Básica

```python
import unittest
from forgebase.domain import EntityBase, ValidationError


class TestUser(unittest.TestCase):
    """Testes unitários para entidade User."""

    def test_cria_usuario_com_dados_validos(self):
        """Testar criação de usuário com inputs válidos."""
        user = User(name="Alice", email="alice@example.com")

        self.assertEqual(user.name, "Alice")
        self.assertEqual(user.email, "alice@example.com")
        self.assertTrue(user.is_active)

    def test_rejeita_nome_vazio(self):
        """Testar que nome vazio levanta ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            User(name="", email="alice@example.com")

        self.assertIn("não pode ser vazio", str(ctx.exception).lower())
```

### O Que Testar?

#### Camada de Domínio

**Entities:**
- Validação de invariantes
- Métodos de negócio
- Igualdade baseada em ID
- Transições de estado

```python
class TestOrder(unittest.TestCase):
    def test_valida_total_positivo(self):
        """Testar que total negativo é rejeitado."""
        with self.assertRaises(ValidationError):
            Order(customer_id="123", items=[], total=-10)

    def test_mark_as_paid_muda_status(self):
        """Testar transição de status para pago."""
        order = Order(customer_id="123", items=[...], total=50)
        order.mark_as_paid()

        self.assertEqual(order.status, "paid")
        self.assertIsNotNone(order.paid_at)

    def test_nao_pode_modificar_pedido_pago(self):
        """Testar regra de negócio: pedidos pagos são imutáveis."""
        order = Order(customer_id="123", items=[...], total=50)
        order.mark_as_paid()

        with self.assertRaises(BusinessRuleViolation):
            order.add_item({"name": "Item"}, 10)
```

**ValueObjects:**
- Imutabilidade
- Igualdade estrutural
- Validação de formato
- Operações (se aplicável)

```python
class TestEmail(unittest.TestCase):
    def test_valida_formato(self):
        """Testar validação de formato de email."""
        with self.assertRaises(ValidationError):
            Email("email-invalido")

        # Válido
        email = Email("alice@example.com")
        self.assertEqual(str(email), "alice@example.com")

    def test_imutabilidade(self):
        """Testar que email é imutável."""
        email = Email("alice@example.com")

        with self.assertRaises(AttributeError):
            email.address = "bob@example.com"

    def test_igualdade(self):
        """Testar igualdade estrutural."""
        email1 = Email("alice@example.com")
        email2 = Email("alice@example.com")

        self.assertEqual(email1, email2)
        self.assertEqual(hash(email1), hash(email2))
```

#### Camada de Aplicação

**UseCases:**
- Validação de input DTOs
- Orquestração correta
- Output DTOs corretos
- Tratamento de erros

```python
class TestCreateUserUseCase(unittest.TestCase):
    def setUp(self):
        self.fake_repo = FakeRepository()
        self.usecase = CreateUserUseCase(user_repository=self.fake_repo)

    def test_cria_usuario(self):
        """Testar criação de usuário com sucesso."""
        output = self.usecase.execute(CreateUserInput(
            name="Alice",
            email="alice@example.com"
        ))

        self.assertIsNotNone(output.user_id)
        self.assertEqual(output.name, "Alice")

        # Verificar persistência
        self.assertEqual(self.fake_repo.count(), 1)

    def test_rejeita_email_duplicado(self):
        """Testar regra de negócio: email deve ser único."""
        # Criar primeiro usuário
        self.usecase.execute(CreateUserInput(
            name="Alice",
            email="alice@example.com"
        ))

        # Tentar duplicar
        with self.assertRaises(BusinessRuleViolation) as ctx:
            self.usecase.execute(CreateUserInput(
                name="Bob",
                email="alice@example.com"
            ))

        self.assertIn("já existe", str(ctx.exception))
```

#### Camada de Infraestrutura

**Repositories:**
- Operações CRUD
- Métodos de consulta
- Tratamento de erros

```python
class TestJSONRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_path = os.path.join(self.temp_dir, "data.json")
        self.repo = JSONRepository(
            file_path=self.file_path,
            entity_type=User
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_save_and_find(self):
        """Testar salvar e recuperar."""
        user = User(name="Alice", email="alice@example.com")
        self.repo.save(user)

        found = self.repo.find_by_id(user.id)

        self.assertIsNotNone(found)
        self.assertEqual(found.id, user.id)
        self.assertEqual(found.name, "Alice")

    def test_persistencia_entre_instancias(self):
        """Testar que dados persistem entre instâncias de repository."""
        user = User(name="Alice", email="alice@example.com")
        self.repo.save(user)

        # Criar nova instância do repo
        new_repo = JSONRepository(
            file_path=self.file_path,
            entity_type=User
        )

        found = new_repo.find_by_id(user.id)
        self.assertIsNotNone(found)
```

### Padrões de Teste

#### Arrange-Act-Assert (AAA)

```python
def test_exemplo(self):
    """Teste seguindo padrão AAA."""
    # Arrange: Setup
    user = User(name="Alice", email="alice@example.com")
    order = Order(customer_id=user.id, items=[], total=50)

    # Act: Executar
    order.mark_as_paid()

    # Assert: Verificar
    self.assertEqual(order.status, "paid")
    self.assertIsNotNone(order.paid_at)
```

#### Given-When-Then (Estilo BDD)

```python
def test_exemplo_estilo_bdd(self):
    """
    Dado um pedido pendente
    Quando marcar como pago
    Então status deve ser 'paid' e paid_at deve estar preenchido
    """
    # Given (Dado)
    order = Order(customer_id="123", items=[], total=50, status="pending")

    # When (Quando)
    order.mark_as_paid()

    # Then (Então)
    self.assertEqual(order.status, "paid")
    self.assertIsNotNone(order.paid_at)
```

---

## 2. Integration Tests (Testes de Integração)

Testes de integração validam a interação entre múltiplos componentes.

### Exemplo: UseCase + Repository

```python
class TestCreateUserIntegration(unittest.TestCase):
    """Testes de integração para fluxo CreateUser."""

    def setUp(self):
        # Componentes reais (não fakes)
        self.temp_dir = tempfile.mkdtemp()
        self.repo = JSONRepository(
            file_path=os.path.join(self.temp_dir, "users.json"),
            entity_type=User
        )
        self.logger = LogService(name="test")
        self.metrics = TrackMetrics()

        self.usecase = CreateUserUseCase(
            user_repository=self.repo,
            logger=self.logger,
            metrics=self.metrics
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_fluxo_completo(self):
        """Testar fluxo completo de criação de usuário."""
        # Executar
        output = self.usecase.execute(CreateUserInput(
            name="Alice",
            email="alice@example.com"
        ))

        # Verificar todas as camadas
        # 1. Output DTO correto
        self.assertIsNotNone(output.user_id)

        # 2. Persistido no repository
        user = self.repo.find_by_id(output.user_id)
        self.assertIsNotNone(user)

        # 3. Métricas coletadas
        report = self.metrics.report()
        self.assertIn('create_user', str(report))
```

---

## 3. Cognitive Tests (Testes Cognitivos)

**Testes cognitivos** validam que a execução cumpre a intenção original. Usam `ForgeTestCase`.

### ForgeTestCase: Assertions Cognitivas

```python
from forgebase.testing import ForgeTestCase
from forgebase.testing.fakes import FakeRepository, FakeLogger, FakeMetricsCollector


class TestCreateUserCognitive(ForgeTestCase):
    """Testes cognitivos para UseCase CreateUser."""

    def setUp(self):
        # Fakes para testes rápidos e isolados
        self.fake_repo = FakeRepository()
        self.fake_logger = FakeLogger()
        self.fake_metrics = FakeMetricsCollector()

        self.usecase = CreateUserUseCase(
            user_repository=self.fake_repo,
            logger=self.fake_logger,
            metrics=self.fake_metrics
        )

    def test_cria_usuario_com_validacao_cognitiva(self):
        """
        Teste cognitivo validando:
        - Intenção corresponde ao resultado
        - Métricas coletadas
        - Logs estruturados
        - Performance aceitável
        """
        # 1. Captura intenção
        intent = "Criar usuário chamado Alice com email alice@example.com"

        # 2. Executa
        output = self.usecase.execute(CreateUserInput(
            name="Alice",
            email="alice@example.com"
        ))

        # 3. Validações tradicionais
        self.assertEqual(output.name, "Alice")
        self.assertEqual(output.email, "alice@example.com")

        # 4. COGNITIVO: Valida intenção
        actual = f"Criado usuário {output.name} com email {output.email}"
        self.assert_intent_matches(
            expected=intent,
            actual=actual,
            threshold=0.75  # 75% similaridade mínima
        )

        # 5. COGNITIVO: Valida métricas
        self.assert_metrics_collected({
            'create_user.duration': lambda v: v > 0,
            'create_user.count': lambda v: v == 1
        })

        # 6. COGNITIVO: Valida logs estruturados
        logs = self.fake_logger.get_logs(level='info')
        self.assertTrue(any('user' in log['message'].lower() for log in logs))

        # 7. COGNITIVO: Valida performance
        self.assert_performance_within(
            lambda: self.usecase.execute(CreateUserInput(...)),
            max_duration_ms=50.0
        )

        # 8. Valida persistência
        self.assertEqual(self.fake_repo.count(), 1)
```

### Intent Tracking em Testes

```python
from forgebase.integration import IntentTracker


class TestWithIntentTracking(ForgeTestCase):
    def test_valida_coerencia(self):
        """Teste com rastreamento completo de intenção."""
        tracker = IntentTracker()

        # Captura intenção
        intent_id = tracker.capture_intent(
            description="Criar usuário Alice",
            expected_outcome="Usuário criado com sucesso"
        )

        # Executa
        output = self.usecase.execute(CreateUserInput(...))

        # Registra execução
        tracker.record_execution(
            intent_id=intent_id,
            actual_outcome=f"Usuário {output.user_id} criado",
            success=True
        )

        # Valida coerência
        report = tracker.validate_coherence(intent_id)

        # Asserções
        self.assertIn(report.coherence_level, [
            CoherenceLevel.PERFECT,
            CoherenceLevel.HIGH
        ])
        self.assertGreaterEqual(report.similarity_score, 0.80)
```

---

## Test Doubles: Fakes vs Mocks

ForgeBase prefere **Fakes** sobre Mocks.

### Fakes (Recomendado)

Implementações funcionais in-memory para testes.

```python
# Usando Fakes
def test_com_fakes(self):
    fake_repo = FakeRepository()  # Implementação real, in-memory
    usecase = CreateUserUseCase(user_repository=fake_repo)

    output = usecase.execute(CreateUserInput(...))

    # Verificar estado
    self.assertEqual(fake_repo.count(), 1)
    user = fake_repo.find_by_id(output.user_id)
    self.assertIsNotNone(user)
```

**Vantagens:**
- Mais próximo do comportamento real
- Detecta bugs em interações
- Fácil inspecionar estado
- Menos frágil

### Mocks (Use com Moderação)

```python
from unittest.mock import Mock

# Usando Mocks
def test_com_mocks(self):
    mock_repo = Mock(spec=UserRepositoryPort)
    mock_repo.find_by_email.return_value = None  # Sem duplicata

    usecase = CreateUserUseCase(user_repository=mock_repo)

    output = usecase.execute(CreateUserInput(...))

    # Verificar interações
    mock_repo.save.assert_called_once()
    mock_repo.find_by_email.assert_called_with("alice@example.com")
```

**Use quando:**
- Testar interações específicas
- Simular erros difíceis de reproduzir
- Verificar chamadas exatas

---

## Coverage

### Rodando Testes com Coverage

```bash
# Executar todos os testes com coverage
pytest --cov=forgebase --cov-report=html

# Abrir relatório
open htmlcov/index.html
```

### Metas de Coverage

| Camada | Meta |
|--------|------|
| **Domain Layer** | ≥95% |
| **Application Layer** | ≥90% |
| **Infrastructure Layer** | ≥80% |
| **Adapters Layer** | ≥70% |

---

## Executando Testes

### Executar Todos os Testes

```bash
pytest
```

### Executar Arquivo Específico

```bash
pytest tests/unit/domain/test_user.py
```

### Executar Teste Específico

```bash
pytest tests/unit/domain/test_user.py::TestUser::test_cria_usuario
```

### Executar por Markers

```bash
# Apenas testes rápidos
pytest -m "not slow"

# Testes de integração
pytest -m integration

# Testes cognitivos
pytest -m cognitive
```

### Output Verbose

```bash
pytest -v
pytest -vv  # Extra verbose
```

### Parar na Primeira Falha

```bash
pytest -x
```

---

## Boas Práticas

### 1. Uma Asserção por Teste (Ideal)

```python
# Bom
def test_cria_usuario(self):
    output = usecase.execute(input_dto)
    self.assertIsNotNone(output.user_id)

def test_persiste_usuario(self):
    usecase.execute(input_dto)
    self.assertEqual(repo.count(), 1)

# Aceitável para testes cognitivos
def test_validacao_cognitiva(self):
    # Múltiplas asserções relacionadas OK
    self.assert_intent_matches(...)
    self.assert_metrics_collected(...)
    self.assert_performance_within(...)
```

### 2. Nomes de Testes são Documentação

```python
# Bom: Descreve comportamento
def test_rejeita_preco_negativo(self):
    ...

def test_marca_pedido_como_pago_quando_pagamento_sucede(self):
    ...

# Ruim: Não descritivo
def test_preco(self):
    ...

def test_pedido(self):
    ...
```

### 3. setUp() para Contexto Compartilhado

```python
class TestCreateUser(unittest.TestCase):
    def setUp(self):
        # Setup compartilhado
        self.fake_repo = FakeRepository()
        self.usecase = CreateUserUseCase(user_repository=self.fake_repo)

    def test_caso_1(self):
        # Usa self.usecase
        ...

    def test_caso_2(self):
        # Usa self.usecase
        ...
```

### 4. tearDown() para Cleanup

```python
def setUp(self):
    self.temp_dir = tempfile.mkdtemp()

def tearDown(self):
    shutil.rmtree(self.temp_dir)
```

### 5. Docstrings em Testes

```python
def test_cria_usuario(self):
    """
    Testar que CreateUserUseCase cria um usuário com sucesso.

    Dado input válido (nome e email)
    Quando execute() é chamado
    Então usuário deve ser criado e persistido
    """
    ...
```

---

## Debugging de Testes

### Print Debugging

```python
def test_exemplo(self):
    output = usecase.execute(input_dto)
    print(f"Output: {output.to_dict()}")  # Debug
    self.assertEqual(output.name, "Alice")
```

### pdb (Python Debugger)

```python
def test_exemplo(self):
    import pdb; pdb.set_trace()  # Breakpoint
    output = usecase.execute(input_dto)
    self.assertEqual(output.name, "Alice")
```

### pytest --pdb

```bash
# Entrar no debugger em caso de falha
pytest --pdb
```

---

## Recursos Adicionais

- **[Receitas](receitas.md)** — Exemplos práticos
- **[Início Rápido](inicio-rapido.md)** — Tutorial inicial

---

**Teste com Propósito!**

*"Cada teste documenta intenção e valida coerência."*
