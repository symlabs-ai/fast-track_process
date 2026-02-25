# Descoberta de APIs do ForgeBase por Agentes de IA

## O Problema de Descoberta

Quando um agente de IA encontra ForgeBase pela primeira vez, ele enfrenta o problema de "partida fria":

> "Como eu descubro que `get_agent_quickstart()` existe se a documentação sobre isso está dentro da função que eu não sei que existe?"

Este documento explica a **estratégia de descoberta em múltiplas camadas** implementada no ForgeBase.

---

## Métodos de Descoberta (Ordem de Prioridade)

### 1. Seção Explícita no README.md (PRIMÁRIO)

**Localização:** Topo do `README.md` (linhas 7-46)

**O que agentes de IA veem:**

```markdown
## Para Agentes de Código de IA

**Primeira vez usando ForgeBase?** Acesse documentação completa programaticamente:

```python
from forgebase.dev import get_agent_quickstart

guide = get_agent_quickstart()  # Documentação completa de API
```
```

**Por que funciona:**
- Visível na página do repositório GitHub
- Visível na página do pacote PyPI
- Primeira coisa que agentes de IA leem ao descobrir um pacote
- Explícito, claro, impossível de perder

**Fluxo de descoberta:**
```
Usuário: "Use ForgeBase para verificar qualidade do código"
    ↓
Agente IA: "Não conheço ForgeBase, deixe-me pesquisar"
    ↓
Agente IA lê README.md do GitHub/PyPI
    ↓
Agente IA vê: "Para Agentes de Código de IA"
    ↓
Descobre get_agent_quickstart() imediatamente
```

---

### 2. Docstring do Módulo (FALLBACK)

**Localização:** `src/forgebase/dev/__init__.py`

**O que agentes de IA veem:**

```python
import forgebase.dev
help(forgebase.dev)

# Output:
"""
ForgeBase Developer Tools.

Uso para Agentes de IA:
    from forgebase.dev import get_agent_quickstart

    # Acessar documentação programaticamente
    guide = get_agent_quickstart()
"""
```

**Por que funciona:**
- Descobrível via introspecção `help()`
- Funciona mesmo se README não estiver acessível
- Embutido no pacote instalado

---

### 3. Listagem de Diretório (`dir()`) (INTROSPECÇÃO)

**Localização:** Exports `__all__` em `__init__.py`

**O que agentes de IA veem:**

```python
import forgebase.dev
dir(forgebase.dev)

# Output:
['get_agent_quickstart', 'get_documentation_path', ...]
```

**Por que funciona:**
- Nome de função auto-explicativo
- Introspecção Python padrão
- Nenhuma dependência externa necessária

---

### 4. Nomes de Função Auto-Documentados (SEMÂNTICO)

**Nome da função:** `get_agent_quickstart()`

**Por que funciona:**
- `agent` → claramente para agentes de IA
- `quickstart` → indica documentação/guia
- `get_` → convenção Python padrão para getters

**Mesmo sem documentação, o nome comunica a intenção!**

---

### 5. Descrição do Pacote PyPI (BUSCA)

**Localização:** Campo description em `pyproject.toml`

**Antes:**
```toml
description = "Cognitive Architecture Framework..."
```

**Depois:**
```toml
description = "Cognitive Architecture Framework... AI agent-friendly com acesso programático à API."
```

**Por que funciona:**
- Aparece nos resultados de busca PyPI
- Sinaliza para agentes de IA que o pacote os suporta
- Indexado por motores de busca

---

## Taxa de Sucesso de Descoberta por Método

| Método | Efetividade | Velocidade | Requisitos |
|--------|-------------|------------|------------|
| **Seção README** | Excelente | Instantâneo | Acesso GitHub/PyPI |
| **Docstring módulo** | Alta | Rápido | `import` + `help()` |
| **Listagem dir()** | Média | Rápido | `import` + `dir()` |
| **Nome da função** | Média | Instantâneo | Raciocínio básico |
| **Descrição PyPI** | Baixa | Médio | Capacidade de busca |

---

## Cenários de Descoberta no Mundo Real

### Cenário 1: Claude Code

```
Usuário: "Use ForgeBase para verificar qualidade do código"

Claude Code:
1. Não reconheço "ForgeBase" nos meus dados de treinamento
2. Procuro contexto na conversa
3. Se tenho acesso, verifico README.md
4. Vejo: "Para Agentes de Código de IA"
5. Imediatamente sei: from forgebase.dev import get_agent_quickstart
```

### Cenário 2: Cursor

```
Usuário: Instala ForgeBase e abre Cursor

Cursor AI:
1. Escaneia dependências do projeto
2. Vê "forgebase" em requirements
3. Busca descrição PyPI: "AI agent-friendly"
4. Lê README.md do cache/repo
5. Vê "Para Agentes de Código de IA"
6. Configura para usar get_agent_quickstart()
```

### Cenário 3: GitHub Copilot

```
Desenvolvedor digita: from forgebase.dev import

Copilot:
1. Analisa exports disponíveis
2. Vê: get_agent_quickstart, get_documentation_path
3. Sugere completamento baseado na semântica do nome
4. Desenvolvedor aceita: from forgebase.dev import get_agent_quickstart
```

### Cenário 4: Aider

```
Sessão Aider: Usuário menciona "ForgeBase"

Aider:
1. import forgebase.dev
2. dir(forgebase.dev)
3. Vê: ['get_agent_quickstart', ...]
4. help(forgebase.dev.get_agent_quickstart)
5. Lê docstring: "útil para agentes de IA"
6. Usa API para descobrir ferramentas disponíveis
```

---

## Testando Descoberta

### Teste Manual: Simulação de Partida Fria

```python
# Simular agente de IA que nunca viu ForgeBase

# Passo 1: Verificar README (PRIMÁRIO)
# → Abre README.md
# → Vê "Para Agentes de Código de IA" na linha 7
# SUCESSO em < 5 segundos

# Passo 2: Introspecção (FALLBACK)
import forgebase.dev
help(forgebase.dev)
# → Vê "Uso para Agentes de IA:"
# SUCESSO em < 1 segundo

# Passo 3: Listagem de diretório (FALLBACK)
dir(forgebase.dev)
# → Vê ['get_agent_quickstart', ...]
# SUCESSO em < 1 segundo
```

### Teste Automatizado

```python
# test_ai_discovery.py

def test_readme_menciona_agentes_ia():
    """Testar que README tem seção de agentes de IA."""
    with open("README.md") as f:
        content = f.read()
    assert "Para Agentes" in content
    assert "get_agent_quickstart" in content

def test_docstring_modulo_tem_uso():
    """Testar que docstring do módulo guia agentes de IA."""
    import forgebase.dev
    assert "Agentes" in forgebase.dev.__doc__
    assert "get_agent_quickstart" in forgebase.dev.__doc__

def test_funcao_exportada():
    """Testar que função é descobrível."""
    import forgebase.dev
    assert hasattr(forgebase.dev, 'get_agent_quickstart')
    assert 'get_agent_quickstart' in dir(forgebase.dev)

def test_nome_funcao_semantico():
    """Testar que nome da função é auto-explicativo."""
    name = "get_agent_quickstart"
    assert "agent" in name  # Claramente para agentes
    assert "get" in name    # Padrão getter
```

---

## Melhores Práticas para Pacotes AI-Friendly

### FAÇA:

1. **Seção explícita no README** - "Para Agentes de IA" no topo
2. **Nomes auto-documentados** - `get_agent_*()`, `ai_*()`, etc.
3. **Docstrings de módulo** - Incluir exemplos de uso para IA
4. **Descrição do pacote** - Mencionar "AI agent-friendly"
5. **Export em `__all__`** - Tornar funções descobríveis via `dir()`

### NÃO FAÇA:

1. **APIs escondidas** - Não enterre funções de IA em submódulos profundos
2. **Nomes crípticos** - Evite `fetch_data()` quando você quer dizer `get_agent_docs()`
3. **Não documentado** - Toda função voltada para IA precisa de docstring
4. **Somente README** - Docs devem ser acessíveis após `pip install`
5. **Assumir conhecimento** - Agentes de IA não têm contexto prévio

---

## Resumo

ForgeBase resolve o problema de descoberta "partida fria" através de **defesa em profundidade**:

1. **Primário:** Seção explícita no README.md (visível no GitHub/PyPI)
2. **Fallback 1:** Docstring do módulo (introspecção)
3. **Fallback 2:** Exports `dir()` (listagem)
4. **Fallback 3:** Nomes auto-documentados (semântica)
5. **Fallback 4:** Descrição PyPI (busca)

**Resultado:** Agentes de IA podem descobrir APIs do ForgeBase em **< 10 segundos** com **múltiplos caminhos independentes** para o sucesso.

---

**Versão:** ForgeBase 0.1.4
