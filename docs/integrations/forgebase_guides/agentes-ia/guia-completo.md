# ForgeBase - Guia Completo para Agentes de IA

> Referência completa das APIs programáticas do ForgeBase para agentes de código de IA.

## Visão Geral

ForgeBase fornece APIs Python programáticas projetadas especificamente para agentes de código de IA. Ao invés de parsear output de texto CLI, agentes de IA podem importar classes Python que retornam dados estruturados (dataclasses, dicts) adequados para análise programática.

## Por Que Isso Importa

### Método Antigo (CLI)
```python
import subprocess

# IA tem que parsear output de texto
result = subprocess.run(["python", "devtool.py", "lint"], capture_output=True)
output = result.stdout  # "Ruff check passou"

# IA tem que usar regex/parsear texto não estruturado
if "passed" in output:
    # Esperar que o formato não mude...
```

### Método Novo (API)
```python
from forgebase.dev.api import QualityChecker

# IA obtém dados estruturados
checker = QualityChecker()
result = checker.run_ruff()

# IA pode raciocinar sobre erros específicos
if not result.passed:
    for error in result.errors:
        if error['code'] == 'F401':  # Import não utilizado
            remove_import(error['file'], error['line'])
```

---

## APIs Disponíveis

### 1. QualityChecker

**Propósito**: Validação de qualidade de código (linting, type checking, arquitetura)

**Retorna**: `CheckResult` com erros estruturados

```python
from forgebase.dev.api import QualityChecker

checker = QualityChecker()

# Executar verificações individuais
ruff_result = checker.run_ruff()
mypy_result = checker.run_mypy()
arch_result = checker.run_import_linter()
deps_result = checker.run_deptry()

# Executar todas as verificações
results = checker.run_all()

# Analisar resultados
for tool, result in results.items():
    if not result.passed:
        print(f"{tool}: {result.error_count} erros")
        for error in result.errors:
            # IA pode agir em códigos de erro específicos
            fix_error(error)
```

**Estrutura CheckResult**:
```python
@dataclass
class CheckResult:
    tool: str                # "ruff", "mypy", etc.
    passed: bool             # True se sem erros
    errors: list[dict]       # Erros estruturados com file, line, message, code
    warnings: list[dict]     # Avisos não bloqueantes
    suggestions: list[str]   # Sugestões acionáveis por IA
    duration: float          # Tempo de execução
    exit_code: int
    raw_output: str          # Para debugging
```

**Estrutura de Erro** (exemplo Ruff):
```python
{
    "file": "user.py",
    "line": 42,
    "column": 10,
    "message": "Import não utilizado: `sys`",
    "code": "F401",
    "severity": "error"
}
```

---

### 2. ScaffoldGenerator

**Propósito**: Gerar código boilerplate

**Retorna**: `ScaffoldResult` com código gerado como string

```python
from forgebase.dev.api import ScaffoldGenerator

generator = ScaffoldGenerator()

# Gerar UseCase
result = generator.create_usecase(
    name="CreateOrder",
    input_type="CreateOrderInput",
    output_type="CreateOrderOutput",
    repository="OrderRepositoryPort"
)

if result.success:
    # IA pode modificar código antes de escrever
    code = result.code
    code = customize_for_context(code)

    # Escrever no caminho sugerido
    write_file(result.file_path, code)

# Gerar Entity
result = generator.create_entity(
    name="Order",
    attributes=["customer_id", "total", "items"]
)
```

**Estrutura ScaffoldResult**:
```python
@dataclass
class ScaffoldResult:
    component_type: str      # "usecase", "entity", etc.
    name: str                # Nome do componente
    code: str                # Código gerado
    file_path: str           # Caminho de arquivo sugerido
    success: bool
    error: str
    metadata: dict           # Imports, dependências, etc.
```

---

### 3. ComponentDiscovery

**Propósito**: Escanear codebase e catalogar componentes

**Retorna**: `DiscoveryResult` com todos componentes encontrados

```python
from forgebase.dev.api import ComponentDiscovery

discovery = ComponentDiscovery()
result = discovery.scan_project()

# IA analisa arquitetura
print(f"Entities: {len(result.entities)}")
print(f"UseCases: {len(result.usecases)}")
print(f"Repositories: {len(result.repositories)}")

# IA faz recomendações
if len(result.usecases) < len(result.entities):
    print("Considere criar mais UseCases")

# IA encontra componentes não utilizados
for entity in result.entities:
    if not any(entity.name in uc.imports for uc in result.usecases):
        print(f"{entity.name} não utilizado em nenhum UseCase")
```

**Estrutura DiscoveryResult**:
```python
@dataclass
class DiscoveryResult:
    entities: list[ComponentInfo]
    usecases: list[ComponentInfo]
    repositories: list[ComponentInfo]
    ports: list[ComponentInfo]
    value_objects: list[ComponentInfo]
    adapters: list[ComponentInfo]
    total_files_scanned: int
    scan_duration: float
```

**Estrutura ComponentInfo**:
```python
@dataclass
class ComponentInfo:
    name: str
    type: str                # 'entity', 'usecase', etc.
    file_path: str
    line_number: int
    base_class: str
    imports: list[str]
    docstring: str
```

---

### 4. TestRunner

**Propósito**: Executar testes com resultados estruturados

**Retorna**: `TestResult` com informações detalhadas de teste

```python
from forgebase.dev.api import TestRunner

runner = TestRunner()

# Executar tipos específicos de teste
unit_result = runner.run_unit_tests()
integration_result = runner.run_integration_tests()
property_result = runner.run_property_tests()
contract_result = runner.run_contract_tests()

# Executar todos os testes
results = runner.run_all()

# IA analisa falhas
for test_type, result in results.items():
    if not result.passed:
        for failure in result.failures:
            # IA pode raciocinar sobre tipos de falha
            if "AssertionError" in failure.error_type:
                fix_assertion(failure.file, failure.line)
            elif "TypeError" in failure.error_type:
                add_type_hints(failure.file, failure.line)
```

**Estrutura TestResult**:
```python
@dataclass
class TestResult:
    test_type: str           # "unit", "integration", etc.
    passed: bool
    total: int
    passed_count: int
    failed_count: int
    skipped_count: int
    failures: list[TestFailure]
    duration: float
    coverage: float
    exit_code: int
    raw_output: str
```

**Estrutura TestFailure**:
```python
@dataclass
class TestFailure:
    test_name: str
    file: str
    line: int
    message: str
    traceback: str
    error_type: str          # "AssertionError", "TypeError", etc.
```

---

## Exemplo de Workflow Completo para Agente de IA

```python
from forgebase.dev.api import (
    QualityChecker,
    ComponentDiscovery,
    TestRunner,
    ScaffoldGenerator
)

def ai_agent_workflow():
    """Workflow completo de agente de IA."""

    # 1. Analisar codebase atual
    discovery = ComponentDiscovery()
    components = discovery.scan_project()

    if len(components.usecases) < 5:
        # 2. Gerar componente faltante
        generator = ScaffoldGenerator()
        result = generator.create_usecase("UpdateOrder")

        # 3. Customizar código gerado
        code = result.code
        code = add_custom_logic(code)
        write_file(result.file_path, code)

    # 4. Verificar qualidade
    checker = QualityChecker()
    quality_results = checker.run_all()

    # 5. Corrigir problemas
    for tool, result in quality_results.items():
        if not result.passed:
            for error in result.errors:
                auto_fix_error(error)

    # 6. Executar testes
    runner = TestRunner()
    test_results = runner.run_all()

    # 7. Corrigir testes falhando
    for test_type, result in test_results.items():
        if not result.passed:
            for failure in result.failures:
                fix_test(failure)

    # 8. Verificar que tudo passa
    final_quality = checker.run_all()
    final_tests = runner.run_all()

    all_passed = (
        all(r.passed for r in final_quality.values()) and
        all(r.passed for r in final_tests.values())
    )

    if all_passed:
        # 9. Pronto para commit
        create_commit("feat: Código gerado por IA com validação completa")
```

---

## Resumo de Estruturas de Dados

Todas as APIs retornam **dataclasses** que podem ser:
- Convertidas para dict: `result.to_dict()`
- Serializadas para JSON
- Analisadas programaticamente
- Usadas para tomada de decisão por IA

Nenhum parsing de texto não estruturado necessário!

---

## Começando

### Instalação
```bash
pip install -e ".[dev]"
```

### Uso Básico
```python
# Importar APIs
from forgebase.dev.api import (
    QualityChecker,
    ScaffoldGenerator,
    ComponentDiscovery,
    TestRunner,
)

# Usar diretamente
checker = QualityChecker()
results = checker.run_all()

# IA toma decisões baseadas em dados estruturados
if not all(r.passed for r in results.values()):
    fix_quality_issues(results)
```

### Uso Avançado
Veja `examples/ai_agent_usage.py` para exemplos abrangentes.

---

## CLI Ainda Disponível

O CLI `devtool.py` ainda está disponível para:
- Desenvolvedores humanos
- Scripts shell
- Pipelines CI/CD
- Compatibilidade

Mas agentes de IA devem usar as APIs Python para:
- Dados estruturados
- Sem overhead de subprocess
- Melhor tratamento de erros
- Análise programática

---

## Referência de API

Para documentação completa de API, veja:
- `forgebase/dev/api/quality.py` - QualityChecker
- `forgebase/dev/api/scaffold.py` - ScaffoldGenerator
- `forgebase/dev/api/discovery.py` - ComponentDiscovery
- `forgebase/dev/api/testing.py` - TestRunner

Todas as classes têm docstrings abrangentes com type hints.

---

## Melhores Práticas para Agentes de IA

### 1. Sempre Verificar Success
```python
result = generator.create_usecase("CreateOrder")
if not result.success:
    handle_error(result.error)
```

### 2. Usar Erros Estruturados
```python
for error in result.errors:
    # Não: parsear string de mensagem de erro
    # Sim: usar error['code'], error['file'], error['line']
    if error['code'] == 'F401':
        remove_unused_import(error['file'], error['line'])
```

### 3. Aproveitar Sugestões
```python
for suggestion in result.suggestions:
    # IA pode executar sugestões
    execute_suggestion(suggestion)
```

### 4. Converter para Dict para Serialização
```python
result_dict = result.to_dict()
save_to_file("analysis.json", json.dumps(result_dict))
```

### 5. Iterar e Validar
```python
# Gerar → Verificar → Corrigir → Repetir
while not all_quality_checks_passed():
    fix_next_issue()
    recheck()
```

---

## Integração com Claude Code

Claude Code (e outros assistentes de IA) podem usar estas APIs diretamente:

```python
# Claude Code pode fazer isso:
from forgebase.dev.api import QualityChecker

checker = QualityChecker()
result = checker.run_ruff()

# Claude analisa dados estruturados
for error in result.errors:
    # Claude sabe exatamente o que corrigir
    fix_code(error['file'], error['line'], error['code'])
```

Sem mais parsing de output de texto ou adivinhar formatos!

---

## Configuração do Projeto

### Tipos de Arquivos a Criar

| Tipo | Localização | Base |
|------|-------------|------|
| Entity | `src/domain/` | `EntityBase` |
| ValueObject | `src/domain/` | `ValueObjectBase` |
| UseCase | `src/application/` | `UseCaseBase` |
| Port | `src/application/` | `PortBase` |
| Adapter | `src/adapters/` | `AdapterBase` |
| Repository | `src/infrastructure/` | `RepositoryBase` |

### Convenções de Nomenclatura

| Componente | Padrão |
|------------|--------|
| Entity | `NomeEntity` ou apenas `Nome` |
| UseCase | `VerbNomeUseCase` (ex: `CreateOrderUseCase`) |
| Port | `NomePort` (ex: `OrderRepositoryPort`) |
| Adapter | `NomeAdapter` (ex: `EmailNotificationAdapter`) |
| DTO Input | `VerbNomeInput` (ex: `CreateOrderInput`) |
| DTO Output | `VerbNomeOutput` (ex: `CreateOrderOutput`) |

---

## Tratamento de Erros

### Códigos de Erro Comuns

| Código | Fonte | Ação |
|--------|-------|------|
| `F401` | Ruff | Remover import não utilizado |
| `F841` | Ruff | Remover variável não utilizada |
| `E501` | Ruff | Quebrar linha longa |
| `no-untyped-def` | Mypy | Adicionar type hints |
| `import-error` | Mypy | Verificar dependências |
| `DEP001` | Deptry | Adicionar dependência faltante |
| `DEP002` | Deptry | Remover dependência não utilizada |

### Exemplo de Auto-Correção

```python
def auto_fix_error(error: dict) -> None:
    """Corrigir automaticamente um erro baseado no código."""
    code = error.get("code", "")
    file = error.get("file", "")
    line = error.get("line", 0)

    if code == "F401":
        # Remover linha com import não utilizado
        remove_line(file, line)

    elif code == "E501":
        # Reformatar linha longa
        reformat_long_line(file, line)

    elif code == "no-untyped-def":
        # Adicionar type hints
        add_type_hints(file, line)
```

---

**Versão**: ForgeBase 0.1.4
**Para**: Agentes de Código de IA
