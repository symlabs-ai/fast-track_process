# Documentação ForgeBase

> "Forjar é transformar pensamento em estrutura."

Esta documentação está organizada em três seções principais, dependendo do seu perfil de uso.

---

## Navegação Rápida

### Para Usuários Humanos
Desenvolvedores que querem usar ForgeBase em seus projetos.

| Documento | Descrição |
|-----------|-----------|
| [ForgeBase Rules](usuarios/forgebase-rules.md) | **Guia completo de regras e práticas** |
| [Início Rápido](usuarios/inicio-rapido.md) | Instalação e primeiro uso |
| [Receitas](usuarios/receitas.md) | Padrões e exemplos práticos |
| [Guia de Testes](usuarios/guia-de-testes.md) | Como escrever testes cognitivos |
| [CLI First](usuarios/cli-first.md) | Filosofia de desenvolvimento CLI First |
| [Ambiente e Scripts](usuarios/ambiente_e_scripts.md) | Setup de ambiente e ferramentas |
| [Extensão](usuarios/extending-forgebase.md) | Como estender ForgeBase |

### Para Agentes de IA
Agentes de IA (Claude Code, Cursor, Aider, etc.) que interagem com ForgeBase.

| Documento | Descrição |
|-----------|-----------|
| [Início Rápido](agentes-ia/inicio-rapido.md) | APIs programáticas para AI agents |
| [Guia Completo](agentes-ia/guia-completo.md) | Referência completa de APIs |
| [Descoberta](agentes-ia/descoberta.md) | Como agents descobrem ForgeBase |
| [Ecossistema](agentes-ia/ecossistema.md) | Integração com diferentes agents |

### Referência Técnica
Documentação de arquitetura e decisões de design.

| Documento | Descrição |
|-----------|-----------|
| [Arquitetura](referencia/arquitetura.md) | Estrutura modular e princípios |
| [ForgeProcess](referencia/forge-process.md) | Ciclo cognitivo completo |
| [ForgeProcess Visual](referencia/forge-process-visual.md) | Guia visual do ciclo |
| [Acesso à Documentação](referencia/acesso-documentacao.md) | Como docs são distribuídas |
| [Guia de Documentação](referencia/documentation_guide.md) | Padrões de docstrings |

---

## Acesso Programático (AI Agents)

```python
from forgebase.dev import get_agent_quickstart

# Obter guia completo de APIs para AI agents
guia = get_agent_quickstart()
print(guia)
```

---

## Versão

- **ForgeBase**: v0.1.4
- **Documentação**: 2025-12
