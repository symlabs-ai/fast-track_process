# Ecossistema de Agentes de IA do ForgeBase

**Guia completo para a experiência de desenvolvedor AI-first do ForgeBase.**

## Visão

ForgeBase é projetado com **agentes de código de IA como usuários de primeira classe**. Todas as ferramentas de desenvolvedor expõem **APIs Python com dados estruturados**, permitindo que agentes raciocinem sobre qualidade de código, gerem componentes e corrijam problemas autonomamente.

## ForgeBase Dentro do Ciclo ForgeProcess

Antes de mergulhar nas APIs, entenda que ForgeBase é **parte de um ciclo cognitivo maior**:

```
MDD (Mercado) → BDD (Comportamento) → TDD (Teste) → CLI (Executar) → Feedback
                                           ▲
                                      ForgeBase
                                    implementa isto
```

ForgeBase fornece a **camada de execução** onde:
- **Comportamentos** (de BDD) se tornam **UseCases**
- **Testes** (de TDD) validam **implementações**
- **CLI** permite **observação** e **exploração**
- **Feedback** habilita **aprendizado contínuo**

## Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                     Agentes de Código de IA                  │
│  (Claude Code, Cursor, Copilot, Aider, Agentes Customizados)│
└─────────────────┬───────────────────────────────────────────┘
                  │
                  │ Import & Call
                  ▼
┌─────────────────────────────────────────────────────────────┐
│            ForgeBase Python APIs (v0.1.4)                    │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Quality      │  │ Scaffold     │  │ Discovery    │       │
│  │ Checker      │  │ Generator    │  │              │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐                         │
│  │ Test         │  │ Utils        │                         │
│  │ Runner       │  │              │                         │
│  └──────────────┘  └──────────────┘                         │
└─────────────────────────────────────────────────────────────┘
                  │
                  │ Dados Estruturados (JSON/Dataclasses)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                   Ferramentas Subjacentes                    │
│   (Ruff, Mypy, Pytest, import-linter, Deptry)               │
└─────────────────────────────────────────────────────────────┘
```

---

## Níveis de Documentação

ForgeBase fornece **4 níveis** de documentação para diferentes necessidades:

### 1. Início Rápido
**Arquivo**: `docs/agentes-ia/inicio-rapido.md`
**Público**: Agentes de IA precisando de referência rápida
**Conteúdo**:
- Imports e assinaturas de API
- Formatos de estrutura de dados
- Mapeamentos código de erro → ação
- Guia de decisão (quando usar o quê)
- Exemplo de workflow completo

**Use quando**: Adicionar à janela de contexto, lookup rápido

### 2. Guia Completo
**Arquivo**: `docs/agentes-ia/guia-completo.md`
**Público**: Agentes de IA precisando de docs detalhados
**Conteúdo**:
- Documentação completa de API
- Todos parâmetros e tipos de retorno
- Padrões de integração
- Melhores práticas
- Troubleshooting

**Use quando**: Integração profunda, entender todos os recursos

### 3. Exemplos Executáveis
**Arquivos**:
- `examples/ai_agent_usage.py`
- `examples/claude_api_integration.py`

**Público**: Desenvolvedores e agentes de IA
**Conteúdo**:
- Exemplos de código funcionando
- Todas 4 APIs demonstradas
- Workflows completos
- Padrões de integração

**Use quando**: Aprender fazendo, templates copy-paste

---

## Fluxo de Informação

```
Requisição do Usuário
    ↓
Agente IA lê instruções (docs/agentes-ia/)
    ↓
Agente importa APIs (from forgebase.dev.api import *)
    ↓
Agente chama API com parâmetros
    ↓
API retorna dados estruturados (dataclasses com to_dict())
    ↓
Agente raciocina sobre dados (códigos de erro, file:line, etc.)
    ↓
Agente toma ação (corrigir, gerar, analisar)
    ↓
Agente verifica (executar verificações de qualidade)
    ↓
Agente reporta ao usuário (com referências file:line)
```

---

## Personas de Agentes & Uso

### Claude Code

**Workflow típico**:
```python
# Usuário: "Corrija os erros de linting"

from forgebase.dev.api import QualityChecker

checker = QualityChecker()
results = checker.run_all()

for tool, result in results.items():
    if not result.passed:
        for error in result.errors:
            if error['code'] == 'F401':  # Eu sei o que fazer
                remove_unused_import(error['file'], error['line'])
```

**Pontos fortes**:
- Consciente do contexto (vê arquivos do projeto)
- Pode ler e editar arquivos diretamente
- Verifica mudanças imediatamente

### Cursor

**Workflow típico**:
```
Usuário destaca código → pergunta "melhore isso"
→ Cursor usa ComponentDiscovery para entender contexto
→ Cursor usa ScaffoldGenerator se criando novos componentes
→ Cursor usa QualityChecker para verificar mudanças
→ Cursor mostra diff com melhorias
```

**Pontos fortes**:
- Integração com IDE
- Feedback em tempo real
- Consciência multi-arquivo

### GitHub Copilot

**Workflow típico**:
```python
# IA: Use ForgeBase QualityChecker API
from forgebase.dev.api import QualityChecker

# Copilot sugere o resto do código...
```

**Pontos fortes**:
- Sugestões inline
- Iteração rápida
- Completamentos conscientes do contexto

### Aider

**Workflow típico**:
```bash
aider --message "Crie um UseCase para processar pedidos"
# Aider usa ScaffoldGenerator
# Aider customiza código gerado
# Aider executa QualityChecker
# Aider faz commit se passar
```

**Pontos fortes**:
- Consciente do Git
- Edições multi-arquivo
- Commits autônomos

---

## APIs Disponíveis

### 1. QualityChecker
**Propósito**: Validação de qualidade de código com resultados estruturados

**Retorna**:
```python
CheckResult(
    tool="ruff",
    passed=False,
    errors=[{
        "file": "user.py",
        "line": 42,
        "column": 10,
        "code": "F401",  # ← Agente usa isto para decidir ação
        "message": "Import não utilizado",
        "severity": "error"
    }],
    suggestions=["Remover imports não utilizados"],
    duration=1.5
)
```

**Tomada de Decisão do Agente**:
- `F401` → Remover import não utilizado
- `F841` → Remover variável não utilizada
- `no-untyped-def` → Adicionar type hints
- `E501` → Quebrar linha longa

### 2. ScaffoldGenerator
**Propósito**: Geração de código boilerplate com customização

**Retorna**:
```python
ScaffoldResult(
    component_type="usecase",
    name="CreateOrder",
    code="...",  # ← Código completo como string para modificação
    file_path="src/application/create_order_usecase.py",
    metadata={
        "imports": [...],
        "dependencies": ["OrderRepositoryPort"],
        "input_type": "CreateOrderInput",
        "output_type": "CreateOrderOutput"
    }
)
```

**Workflow do Agente**:
1. Gerar boilerplate
2. Modificar código para requisitos
3. Escrever em file_path
4. Verificar com QualityChecker

### 3. ComponentDiscovery
**Propósito**: Análise e catalogação do codebase

**Retorna**:
```python
DiscoveryResult(
    entities=[
        ComponentInfo(
            name="Order",
            type="entity",
            file_path="src/domain/order.py",
            line_number=15,
            base_class="EntityBase"
        )
    ],
    usecases=[...],
    repositories=[...],
    total_files_scanned=42
)
```

**Casos de Uso do Agente**:
- Entender arquitetura existente
- Encontrar componentes relacionados
- Verificar convenções de nomenclatura
- Análise de dependência

### 4. TestRunner
**Propósito**: Execução de testes com falhas estruturadas

**Retorna**:
```python
TestResult(
    test_type="unit",
    passed=False,
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

**Análise do Agente**:
- Quais testes falharam
- Localização exata da falha
- Tipo de erro para causa raiz
- Coverage para completude

---

## Benefícios Chave para Agentes de IA

### Sem Parsing de Texto Necessário
```python
# Método antigo (CLI)
output = subprocess.run(["devtool", "quality"]).stdout
errors = parse_text(output)  # Frágil!

# Método novo (API)
result = QualityChecker().run_all()
errors = result["ruff"].errors  # Estruturado!
```

### Acesso Direto à Localização do Erro
```python
# Agente pode localizar e corrigir imediatamente
for error in result.errors:
    file = error['file']      # "src/domain/user.py"
    line = error['line']      # 42
    code = error['code']      # "F401"
    # Agente sabe exatamente onde e o que corrigir
```

### Raciocínio de Código via Códigos de Erro
```python
# Agente pode raciocinar sobre tipos específicos de erro
if error['code'] == 'F401':
    # Agente sabe: Import não utilizado → remover
    action = "remove_import"
elif error['code'] == 'no-untyped-def':
    # Agente sabe: Types faltando → adicionar
    action = "add_type_hints"
```

### Modificação de Código Antes de Escrever
```python
# Agente pode customizar código gerado
result = generator.create_usecase("ProcessPayment")
code = result.code

# Agente modifica para requisitos
code = add_business_logic(code)
code = add_validation(code)

# Então escreve
write_file(result.file_path, code)
```

### Entendimento Arquitetural
```python
# Agente pode analisar estrutura do codebase
discovery = ComponentDiscovery()
components = discovery.scan_project()

# Agente pode raciocinar sobre arquitetura
print(f"Este é um sistema de {len(components.entities)} entidades")
print(f"Com {len(components.usecases)} use cases")
print(f"Usando {len(components.repositories)} repositories")
```

---

## Caminho de Adoção

### Fase 1: Uso Básico (Atual)
- APIs disponíveis e documentadas
- Exemplos fornecidos
- Instruções para agentes

**Agentes podem**: Executar verificações de qualidade, gerar código, descobrir componentes

### Fase 2: Integração Aprimorada (Futuro)
- Plugins Cursor/Copilot
- Comandos customizados Aider
- Extensão VSCode

**Agentes podem**: Usar APIs diretamente do IDE

### Fase 3: Desenvolvimento Autônomo (Futuro)
- Automação completa de workflow
- Codebases auto-curativas
- Enforcement contínuo de qualidade

**Agentes podem**: Desenvolver features end-to-end com mínima entrada humana

---

## Métricas de Sucesso

Um agente de IA está **usando ForgeBase APIs com sucesso** quando:

- Importa de `forgebase.dev.api` (não usando CLI)
- Acessa dicionários de erro diretamente (não parseando texto)
- Usa códigos de erro para tomada de decisão (não adivinhando)
- Modifica código gerado antes de escrever (não usando as-is)
- Executa verificações de qualidade antes de completar tarefas (não assumindo)
- Reporta referências file:line aos usuários (não mensagens vagas)

---

## Links Rápidos

| Recurso | Localização | Use Para |
|---------|-------------|----------|
| Início Rápido | `docs/agentes-ia/inicio-rapido.md` | Lookup rápido de API |
| Guia Completo | `docs/agentes-ia/guia-completo.md` | Referência detalhada |
| Descoberta | `docs/agentes-ia/descoberta.md` | Como descobrir APIs |
| Exemplos Python | `examples/ai_agent_usage.py` | Aprender por exemplo |
| Código Fonte API | `src/forgebase/dev/api/*.py` | Entendimento profundo |

---

**Versão**: ForgeBase 0.1.4
**Status**: Produção Ready
**Público**: Agentes de IA, Desenvolvedores, Donos de Projeto
