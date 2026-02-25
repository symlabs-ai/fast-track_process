# Início Rápido com ForgeBase

> "Forjar é transformar pensamento em estrutura."

Este guia vai levá-lo do zero até sua primeira aplicação funcional em ~30 minutos.

---

## Pré-requisitos

- **Python 3.11+** instalado
- **pip** para gerenciar pacotes
- **Git** para clonar o repositório
- Editor de código (VS Code, PyCharm, etc.)

---

## Instalação

### Opção 1: Instalação via pip (produção)

```bash
# Instalar última versão do main
pip install git+https://github.com/symlabs-ai/forgebase.git

# Verificar instalação
python -c "from forgebase.dev.api import QualityChecker; print('ForgeBase instalado!')"
```

### Opção 2: Instalação para desenvolvimento

```bash
# Clonar e instalar em modo editável
git clone https://github.com/symlabs-ai/forgebase.git
cd forgebase

# Criar ambiente virtual
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# ou: .venv\Scripts\activate  # Windows

# Instalar com dependências de desenvolvimento
pip install -e ".[dev]"
```

### Opção 3: Com dependências opcionais

```bash
# Com suporte a SQL (SQLAlchemy)
pip install "forgebase[sql] @ git+https://github.com/symlabs-ai/forgebase.git"

# Com todas as dependências
pip install "forgebase[all] @ git+https://github.com/symlabs-ai/forgebase.git"
```

---

## Seu Primeiro UseCase

Vamos criar uma aplicação simples de gerenciamento de tarefas usando ForgeBase.

### Estrutura do Projeto

```bash
mkdir minha-app-forge
cd minha-app-forge

# Criar estrutura de diretórios
mkdir -p src/domain
mkdir -p src/application
mkdir -p src/infrastructure
mkdir -p tests

touch src/__init__.py
touch src/domain/__init__.py
touch src/application/__init__.py
touch src/infrastructure/__init__.py
```

### 1. Camada de Domínio: Entidade Task

```python
# src/domain/task.py
"""Entidade de domínio: Task."""

from datetime import datetime
from forgebase.domain import EntityBase, ValidationError


class Task(EntityBase):
    """
    Entidade Task.

    Uma tarefa tem título, descrição opcional e status de conclusão.
    Tarefas são criadas como incompletas por padrão.
    """

    def __init__(
        self,
        title: str,
        description: str = "",
        id: str | None = None,
        completed: bool = False,
        created_at: datetime | None = None
    ):
        super().__init__(id=id)
        self.title = title
        self.description = description
        self.completed = completed
        self.created_at = created_at or datetime.now()
        self.validate()

    def validate(self) -> None:
        """Validar invariantes da tarefa."""
        if not self.title or not self.title.strip():
            raise ValidationError("Título da tarefa não pode ser vazio")

        if len(self.title) > 200:
            raise ValidationError("Título muito longo (máximo 200 caracteres)")

    def complete(self) -> None:
        """Marcar tarefa como concluída."""
        self.completed = True

    def uncomplete(self) -> None:
        """Marcar tarefa como incompleta."""
        self.completed = False

    def __str__(self) -> str:
        status = "✓" if self.completed else "○"
        return f"{status} {self.title}"
```

### 2. Camada de Aplicação: DTOs e Port

```python
# src/application/task_dtos.py
"""DTOs para gerenciamento de Task."""

from forgebase.application import DTOBase


class CreateTaskInput(DTOBase):
    """Input para criar uma tarefa."""

    def __init__(self, title: str, description: str = ""):
        self.title = title
        self.description = description

    def validate(self) -> None:
        if not self.title:
            raise ValueError("Título é obrigatório")

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CreateTaskInput':
        return cls(
            title=data.get("title", ""),
            description=data.get("description", "")
        )


class CreateTaskOutput(DTOBase):
    """Output da criação de tarefa."""

    def __init__(self, task_id: str, title: str, created_at: str):
        self.task_id = task_id
        self.title = title
        self.created_at = created_at

    def validate(self) -> None:
        pass

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CreateTaskOutput':
        return cls(
            task_id=data["task_id"],
            title=data["title"],
            created_at=data["created_at"]
        )
```

```python
# src/application/task_repository_port.py
"""Port de repositório para entidade Task."""

from abc import ABC, abstractmethod
from src.domain.task import Task


class TaskRepositoryPort(ABC):
    """
    Port para persistência de Task.

    Define o contrato para armazenar e recuperar tarefas,
    sem especificar detalhes de implementação.
    """

    @abstractmethod
    def save(self, task: Task) -> None:
        """Salvar uma tarefa."""
        pass

    @abstractmethod
    def find_by_id(self, task_id: str) -> Task | None:
        """Buscar tarefa por ID."""
        pass

    @abstractmethod
    def find_all(self) -> list[Task]:
        """Buscar todas as tarefas."""
        pass

    @abstractmethod
    def delete(self, task_id: str) -> None:
        """Remover uma tarefa."""
        pass

    @abstractmethod
    def exists(self, task_id: str) -> bool:
        """Verificar se tarefa existe."""
        pass
```

### 3. Camada de Aplicação: UseCase

```python
# src/application/create_task_usecase.py
"""UseCase para criar uma nova tarefa."""

from forgebase.application import UseCaseBase
from src.domain.task import Task
from src.application.task_dtos import CreateTaskInput, CreateTaskOutput
from src.application.task_repository_port import TaskRepositoryPort


class CreateTaskUseCase(UseCaseBase):
    """
    Criar uma nova tarefa.

    Orquestra a criação de uma entidade tarefa e sua persistência.

    Exemplo::

        usecase = CreateTaskUseCase(task_repository=repository)
        output = usecase.execute(CreateTaskInput(
            title="Aprender ForgeBase",
            description="Seguir o guia de início rápido"
        ))
        print(f"Tarefa criada: {output.task_id}")
    """

    def __init__(self, task_repository: TaskRepositoryPort):
        self.task_repository = task_repository

    def execute(self, input_dto: CreateTaskInput) -> CreateTaskOutput:
        """
        Executar criação de tarefa.

        :param input_dto: Dados da tarefa
        :return: Informações da tarefa criada
        """
        # Validar input
        input_dto.validate()

        # Criar entidade de domínio
        task = Task(
            title=input_dto.title,
            description=input_dto.description
        )

        # Validar regras de domínio
        task.validate()

        # Persistir
        self.task_repository.save(task)

        # Retornar output
        return CreateTaskOutput(
            task_id=task.id,
            title=task.title,
            created_at=task.created_at.isoformat()
        )
```

### 4. Camada de Infraestrutura: Repositório In-Memory

```python
# src/infrastructure/in_memory_task_repository.py
"""Implementação in-memory do TaskRepositoryPort."""

from src.application.task_repository_port import TaskRepositoryPort
from src.domain.task import Task


class InMemoryTaskRepository(TaskRepositoryPort):
    """
    Repositório de tarefas in-memory.

    Armazena tarefas em um dicionário. Útil para testes e prototipagem.
    """

    def __init__(self):
        self._storage: dict[str, Task] = {}

    def save(self, task: Task) -> None:
        task.validate()
        self._storage[task.id] = task

    def find_by_id(self, task_id: str) -> Task | None:
        return self._storage.get(task_id)

    def find_all(self) -> list[Task]:
        return list(self._storage.values())

    def delete(self, task_id: str) -> None:
        if task_id in self._storage:
            del self._storage[task_id]

    def exists(self, task_id: str) -> bool:
        return task_id in self._storage

    def count(self) -> int:
        """Retornar número de tarefas."""
        return len(self._storage)
```

### 5. Executando Seu UseCase

```python
# main.py
"""Ponto de entrada da aplicação."""

from src.application.create_task_usecase import CreateTaskUseCase
from src.application.task_dtos import CreateTaskInput
from src.infrastructure.in_memory_task_repository import InMemoryTaskRepository


def main():
    # Configurar dependências
    repository = InMemoryTaskRepository()

    # Criar use case
    usecase = CreateTaskUseCase(task_repository=repository)

    # Executar
    output = usecase.execute(CreateTaskInput(
        title="Aprender ForgeBase",
        description="Completar o guia de início rápido"
    ))

    # Exibir resultado
    print(f"Tarefa criada!")
    print(f"  ID: {output.task_id}")
    print(f"  Título: {output.title}")
    print(f"  Criada em: {output.created_at}")


if __name__ == "__main__":
    main()
```

Execute:

```bash
python main.py
```

Output esperado:

```
Tarefa criada!
  ID: 550e8400-e29b-41d4-a716-446655440000
  Título: Aprender ForgeBase
  Criada em: 2025-11-03T10:30:00
```

---

## Testando Seu UseCase

```python
# tests/test_create_task_usecase.py
"""Testes para CreateTaskUseCase."""

import unittest
from src.application.create_task_usecase import CreateTaskUseCase
from src.application.task_dtos import CreateTaskInput
from src.infrastructure.in_memory_task_repository import InMemoryTaskRepository


class TestCreateTaskUseCase(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryTaskRepository()
        self.usecase = CreateTaskUseCase(task_repository=self.repository)

    def test_cria_tarefa(self):
        """Testar que tarefa é criada com sucesso."""
        # Executar
        output = self.usecase.execute(CreateTaskInput(
            title="Tarefa de teste",
            description="Descrição de teste"
        ))

        # Asserções
        self.assertIsNotNone(output.task_id)
        self.assertEqual(output.title, "Tarefa de teste")

        # Verificar persistência
        self.assertEqual(self.repository.count(), 1)
        task = self.repository.find_by_id(output.task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.title, "Tarefa de teste")

    def test_rejeita_titulo_vazio(self):
        """Testar que título vazio é rejeitado."""
        with self.assertRaises(ValueError):
            self.usecase.execute(CreateTaskInput(title=""))


if __name__ == "__main__":
    unittest.main()
```

Execute os testes:

```bash
python -m pytest tests/
```

---

## Usando as APIs Programáticas

ForgeBase oferece APIs para automatização:

```python
from forgebase.dev.api import (
    QualityChecker,
    ScaffoldGenerator,
    ComponentDiscovery,
    TestRunner
)

# 1. Verificar qualidade do código
checker = QualityChecker()
results = checker.run_all()
for tool, result in results.items():
    status = "OK" if result.passed else "ERRO"
    print(f"{tool}: {status}")

# 2. Gerar boilerplate
generator = ScaffoldGenerator()
result = generator.create_usecase(
    name="DeleteTask",
    input_type="DeleteTaskInput",
    output_type="DeleteTaskOutput"
)
print(f"Código gerado em: {result.file_path}")

# 3. Descobrir componentes
discovery = ComponentDiscovery()
components = discovery.scan_project()
print(f"Encontrados: {len(components.entities)} entidades")

# 4. Executar testes
runner = TestRunner()
results = runner.run_all()
```

---

## Próximos Passos

1. **Adicionar mais UseCases**: UpdateTask, DeleteTask, ListTasks
2. **Trocar repositório**: Use `JSONRepository` ou `SQLRepository`
3. **Adicionar observabilidade**: Logging e métricas
4. **Ler a documentação completa**: Veja [docs/](../README.md)

---

## Problemas Comuns

### Import Error: "No module named 'forgebase'"

```bash
# Instale ForgeBase
pip install git+https://github.com/symlabs-ai/forgebase.git

# Ou adicione ao PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```

### ValidationError: "Título da tarefa não pode ser vazio"

Isso é esperado! Entidades de domínio validam suas invariantes. Sempre passe dados válidos:

```python
# Vai falhar
task = Task(title="")

# Correto
task = Task(title="Título válido")
```

### git: command not found

```bash
# Ubuntu/Debian
sudo apt-get install git

# macOS
brew install git
```

---

## Recursos Adicionais

- [Receitas](receitas.md) — Padrões e exemplos práticos
- [Guia de Testes](guia-de-testes.md) — Como escrever testes cognitivos
- [ForgeProcess](../referencia/forge-process.md) — Ciclo cognitivo completo
- [Arquitetura](../referencia/arquitetura.md) — Estrutura do framework

---

**Feliz Forjamento!**

*"Cada linha de código carrega intenção, medição e capacidade de auto-explicação."*
