# ForgeBase - Guia Rápido para Agentes de IA

**Público-alvo**: Agentes de código de IA (Claude Code, Cursor, GitHub Copilot, Aider, etc.)

## Referência Rápida

### Importar Tudo que Você Precisa

```python
from forgebase.dev.api import (
    QualityChecker,
    ScaffoldGenerator,
    ComponentDiscovery,
    TestRunner
)
```

### Acessar Este Guia Programaticamente

```python
# Agentes de IA podem carregar este guia programaticamente (mesmo após pip install)
from forgebase.dev import get_agent_quickstart

guide = get_agent_quickstart()
print(guide)  # Conteúdo markdown completo deste arquivo
```

**Por que isso importa para agentes de IA:**
- Funciona mesmo quando instalado via `pip install git+...`
- Não precisa de internet
- Sempre sincronizado com a versão instalada
- Pode ser parseado para descoberta de APIs

---

## API 1: Verificação de Qualidade

**Quando usar**: Antes de commits, durante code review, corrigindo erros de linting

```python
checker = QualityChecker()

# Executar ferramenta específica
ruff_result = checker.run_ruff()
mypy_result = checker.run_mypy()

# Ou executar tudo
results = checker.run_all()

# Acessar erros estruturados
for tool, result in results.items():
    if not result.passed:
        for error in result.errors:
            # Cada erro tem: file, line, column, code, message
            print(f"Corrigir {error['file']}:{error['line']} - {error['code']}")
```

**Estrutura de Dados**:
```python
CheckResult(
    tool="ruff",
    passed=False,
    errors=[
        {
            "file": "user.py",
            "line": 42,
            "column": 10,
            "code": "F401",           # Use isto para determinar a correção!
            "message": "Import não utilizado",
            "severity": "error"
        }
    ],
    suggestions=["Remover imports não utilizados"],  # Acionável por IA
    duration=1.5,
    exit_code=1
)
```

**Códigos de Erro Comuns**:

| Código | Significado | Ação |
|--------|-------------|------|
| `F401` | Import não utilizado | Remover |
| `F841` | Variável não utilizada | Remover ou usar |
| `E501` | Linha muito longa | Reformatar |
| `no-untyped-def` | Type hints faltando | Adicionar |
| `import-outside-toplevel` | Import fora do topo | Mover |

---

## API 2: Geração de Código

**Quando usar**: Criar novos UseCases, Entities, Repositories

```python
generator = ScaffoldGenerator()

# Gerar UseCase
result = generator.create_usecase(
    name="CreateOrder",
    input_type="CreateOrderInput",
    output_type="CreateOrderOutput",
    repository="OrderRepositoryPort"
)

# Modificar código antes de escrever (RECURSO CHAVE para IA!)
if result.success:
    code = result.code
    # IA pode customizar o código
    code = code.replace("# TODO: Implementar", "# Implementação customizada")

    # Escrever no caminho sugerido
    with open(result.file_path, 'w') as f:
        f.write(code)

# Gerar Entity
entity_result = generator.create_entity(
    name="Order",
    attributes=["customer_id", "total", "items"]
)
```

**Estrutura de Dados**:
```python
ScaffoldResult(
    component_type="usecase",
    name="CreateOrder",
    code="...",              # Código gerado completo como string
    file_path="src/...",     # Localização sugerida
    success=True,
    metadata={
        "imports": [...],    # Dependências para verificar
        "input_type": "...",
        "output_type": "..."
    }
)
```

---

## API 3: Descoberta de Componentes

**Quando usar**: Antes de modificar arquitetura, entender dependências

```python
discovery = ComponentDiscovery()
result = discovery.scan_project()

# Acessar componentes descobertos
for entity in result.entities:
    print(f"Entity: {entity.name} em {entity.file_path}:{entity.line_number}")

for usecase in result.usecases:
    print(f"UseCase: {usecase.name}")
    print(f"  Base: {usecase.base_class}")
    print(f"  Imports: {usecase.imports}")
```

**Estrutura de Dados**:
```python
ComponentInfo(
    name="Order",
    type="entity",
    file_path="src/domain/order.py",
    line_number=15,
    base_class="EntityBase",
    imports=["from forgebase.domain.entity_base import EntityBase"],
    docstring="Entidade de domínio Order"
)
```

---

## API 4: Execução de Testes

**Quando usar**: Executar testes, analisar falhas, verificar coverage

```python
runner = TestRunner()

# Executar suite específica
unit_result = runner.run_unit_tests()
integration_result = runner.run_integration_tests()

# Ou executar tudo
results = runner.run_all()

# Analisar falhas
for test_type, result in results.items():
    if not result.passed:
        for failure in result.failures:
            print(f"Falhou: {failure.test_name}")
            print(f"  Arquivo: {failure.file}:{failure.line}")
            print(f"  Erro: {failure.error_type}")
            print(f"  Mensagem: {failure.message}")
```

**Estrutura de Dados**:
```python
TestResult(
    test_type="unit",
    passed=False,
    total=100,
    passed_count=95,
    failed_count=5,
    failures=[
        TestFailure(
            test_name="test_create_order",
            file="tests/test_order.py",
            line=42,
            error_type="AssertionError",
            message="Esperado 200, obtido 404"
        )
    ],
    coverage=85.5
)
```

---

## Exemplo de Workflow Completo

```python
from forgebase.dev.api import *

# 1. Descobrir o que existe
discovery = ComponentDiscovery()
components = discovery.scan_project()
print(f"Encontradas {len(components.entities)} entidades")

# 2. Gerar novo componente
generator = ScaffoldGenerator()
result = generator.create_usecase("ProcessPayment")
if result.success:
    # Customizar antes de escrever
    with open(result.file_path, 'w') as f:
        f.write(result.code)

# 3. Verificar qualidade
checker = QualityChecker()
quality_results = checker.run_all()

# 4. Corrigir erros automaticamente
for tool, result in quality_results.items():
    if not result.passed:
        for error in result.errors:
            if error.get("code") == "F401":  # Import não utilizado
                # IA remove o import de error['file'] na linha error['line']
                pass

# 5. Executar testes
runner = TestRunner()
test_results = runner.run_all()
if test_results["unit"].passed:
    print("Pronto para commit")
```

---

## Guia de Decisão para Agentes de IA

### Quando Usar Qual API?

| Tarefa | API | Método |
|--------|-----|--------|
| "Verificar qualidade do código" | QualityChecker | `run_all()` |
| "Corrigir erros de linting" | QualityChecker | `run_ruff()` → analisar erros |
| "Adicionar type hints" | QualityChecker | `run_mypy()` → encontrar hints faltando |
| "Criar um UseCase" | ScaffoldGenerator | `create_usecase()` |
| "Criar uma Entity" | ScaffoldGenerator | `create_entity()` |
| "Que entidades existem?" | ComponentDiscovery | `scan_project()` → entities |
| "Quais são as dependências?" | ComponentDiscovery | `scan_project()` → verificar imports |
| "Executar testes" | TestRunner | `run_all()` ou tipo específico |
| "Por que os testes estão falhando?" | TestRunner | `run_*()` → analisar falhas |

### Mapeamento Código de Erro → Ação

```python
ERROR_ACTIONS = {
    "F401": "Remover import não utilizado",
    "F841": "Remover variável não utilizada",
    "E501": "Quebrar linha longa",
    "no-untyped-def": "Adicionar type hints",
    "import-error": "Verificar dependências",
    "AttributeError": "Verificar atributos do objeto",
    "NameError": "Verificar se variável existe",
}
```

---

## Boas Práticas para Agentes de IA

### FAÇA:
1. **Importar diretamente** - Use APIs Python, não chamadas CLI via subprocess
2. **Verificar códigos de erro** - Use `error['code']` para determinar ação
3. **Modificar código gerado** - Customize antes de escrever arquivos
4. **Usar dados estruturados** - Acesse `file`, `line`, `column` diretamente
5. **Executar qualidade antes de commit** - Sempre verifique com `run_all()`

### NÃO FAÇA:
1. **Não parsear output de CLI** - Use APIs ao invés
2. **Não adivinhar localizações de arquivo** - Use API de descoberta
3. **Não escrever código sem verificar** - Execute verificações de qualidade primeiro
4. **Não ignorar códigos de erro** - Eles dizem exatamente o que corrigir
5. **Não usar subprocess** - Importe e chame diretamente

---

## Dicas Profissionais

1. **Use `to_dict()` para logging**: Todos os resultados têm `.to_dict()` para JSON
2. **Verifique `success` primeiro**: Todas as operações têm um booleano `success`
3. **Use metadata**: ScaffoldResult inclui imports e dependências
4. **Operações em lote**: Métodos `run_all()` executam tudo de uma vez
5. **Multiplataforma**: Todas as APIs funcionam no Windows, macOS, Linux

---

## Documentação Adicional

- **[Guia Completo](guia-completo.md)** — Referência completa de APIs
- **[Descoberta](descoberta.md)** — Como agentes descobrem ForgeBase
- **[Ecossistema](ecossistema.md)** — Integração com diferentes agentes

---

**Versão**: ForgeBase 0.1.4
**Para**: Agentes de Código de IA
