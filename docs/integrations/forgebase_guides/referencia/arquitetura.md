# 🧱 PRD — Modularização do Núcleo do ForgeBase

## 📘 Visão Geral

Este documento descreve o plano de modularização do **núcleo do ForgeBase**, o componente técnico central do **Forge Framework**.
O objetivo é estabelecer uma estrutura estável, extensível e observável, garantindo coerência entre o **raciocínio arquitetural (ForgeProcess)** e sua **execução técnica (ForgeBase)**.

A modularização visa consolidar o ForgeBase como uma *infraestrutura cognitiva* — um ambiente que não apenas executa código, mas também entende, mede e explica seu próprio funcionamento.

> *“O núcleo do ForgeBase é o corpo onde o pensamento do ForgeProcess se manifesta e se autoavalia.”*

---

## 🧩 1. Objetivos do Projeto

### 🎯 Objetivos Principais

* Estabelecer **uma arquitetura Clean + Hexagonal** consistente em todos os módulos.
* Padronizar a **estrutura de pastas e importações** para reduzir acoplamento e ambiguidade.
* Garantir **testabilidade e observabilidade nativas** em todos os níveis do sistema.
* Fornecer uma base sólida para extensões cognitivas (CLI, API, LLM, agentes IA).
* Permitir a evolução contínua sem perda de coerência arquitetural.

### 🚧 Escopo Inicial

* Modularização do diretório `forgebase/` (núcleo).
* Definição das classes base (`EntityBase`, `UseCaseBase`, `PortBase`, `AdapterBase`).
* Implementação das convenções de importação modular.
* Introdução dos módulos `observability/` e `testing/` como componentes de primeiro nível.

---

## 🧠 2. Filosofia Arquitetural

O ForgeBase deve refletir os princípios cognitivos do Forge Framework:

1. **Reflexividade:** o sistema deve compreender e medir suas próprias operações.
2. **Autonomia:** o domínio deve permanecer isolado da infraestrutura.
3. **Extensibilidade:** qualquer nova funcionalidade deve ser adicionada via adapters e ports, sem alterar o núcleo.
4. **Rastreabilidade:** cada execução deve ser audível e vinculada à intenção que a originou.
5. **Consistência Cognitiva:** código e documentação são espelhos um do outro.

---

## ⚙️ 3. Estrutura Modular Proposta

A seguir está a estrutura de referência para o núcleo do ForgeBase:

```
forgebase/
├─ __init__.py
│
├─ domain/                          # Núcleo de entidades e invariantes
│  ├─ __init__.py
│  ├─ entity_base.py                # Classe base para entidades
│  ├─ value_object_base.py          # Objetos imutáveis de domínio
│  ├─ exceptions.py                 # Exceções e erros de domínio
│  └─ validators/                   # Regras e invariantes
│     ├─ __init__.py
│     └─ rules.py
│
├─ application/                     # Casos de uso e orquestração cognitiva
│  ├─ __init__.py
│  ├─ usecase_base.py               # Classe base para UseCases
│  ├─ port_base.py                  # Classe base para Ports (contratos cognitivos)
│  ├─ dto_base.py                   # DTOs padronizados
│  ├─ error_handling.py             # Guardas e tratamento de exceções
│  └─ decorators/                   # Decoradores para métricas e feedback
│     ├─ __init__.py
│     └─ track_metrics.py
│
├─ adapters/                        # Interfaces e conectores externos
│  ├─ __init__.py
│  ├─ adapter_base.py               # Classe base para Adapters
│  ├─ cli/                          # Interface CLI
│  │  ├─ __init__.py
│  │  └─ cli_adapter.py
│  ├─ http/                         # Interface REST / HTTP
│  │  ├─ __init__.py
│  │  └─ http_adapter.py
│  └─ ai/                           # Adapters cognitivos (LLM, agentes)
│     ├─ __init__.py
│     └─ llm_adapter.py
│
├─ infrastructure/                  # Serviços técnicos e persistência
│  ├─ __init__.py
│  ├─ repository/                   # Persistência e armazenamento
│  │  ├─ __init__.py
│  │  ├─ repository_base.py
│  │  ├─ json_repository.py
│  │  └─ sql_repository.py
│  ├─ logging/                      # Logging e tracing estruturado
│  │  ├─ __init__.py
│  │  └─ logger_port.py
│  ├─ configuration/                # Configurações e carregamento
│  │  ├─ __init__.py
│  │  └─ config_loader.py
│  └─ security/                     # Sandbox e autenticação
│     ├─ __init__.py
│     └─ sandbox.py
│
├─ observability/                   # Núcleo de feedback e métricas
│  ├─ __init__.py
│  ├─ log_service.py                # Serviço de logging estruturado
│  ├─ track_metrics.py              # Métricas e telemetria
│  ├─ tracer_port.py                # Interface para tracing distribuído
│  └─ feedback_manager.py           # Integração Process ↔ Base
│
├─ testing/                         # Estrutura de testes cognitivos
│  ├─ __init__.py
│  ├─ forge_test_case.py            # Classe base para testes
│  ├─ fakes/                        # Implementações falsas para simulações
│  │  ├─ __init__.py
│  │  └─ fake_logger.py
│  └─ fixtures/                     # Dados simulados para regressão
│     ├─ __init__.py
│     └─ sample_data.py
│
└─ core_init.py                     # Inicialização cognitiva e bootstrap
```

---

## 🧩 4. Classes Fundamentais

### `EntityBase`

* Representa entidades do domínio.
* Mantém invariantes e regras locais.
* Deve ser totalmente independente de infraestrutura.

```python
class EntityBase:
    def __init__(self):
        self._id = None
```

### `UseCaseBase`

* Define o contrato para casos de uso (ValueTracks).
* Deve conter a lógica de orquestração e integração entre domínio e adapters.

```python
class UseCaseBase:
    def execute(self, *args, **kwargs):
        raise NotImplementedError
```

### `PortBase`

* Interface abstrata para comunicação entre módulos internos e externos.
* Garante rastreabilidade e documentação do contrato.

```python
class PortBase:
    def info(self):
        return {"port": self.__class__.__name__, "module": self.__module__}
```

### `AdapterBase`

* Implementa o contrato dos ports e adiciona instrumentação de feedback.

```python
class AdapterBase:
    def __init__(self):
        self.name = self.__class__.__name__
```

---

## 🧭 5. Convenções de Implementação

1. **Imports organizados**
   Sempre usar a sintaxe modular clara:

   ```python
   from forgebase.domain import EntityBase
   from forgebase.application import UseCaseBase, PortBase
   from forgebase.adapters import AdapterBase
   ```

2. **Isolamento absoluto do domínio**
   Nenhum módulo fora de `domain/` deve modificar entidades ou regras internas.

3. **Observabilidade padrão**
   Cada UseCase, Port e Adapter deve emitir métricas automaticamente por meio do decorator `track_metrics`.

4. **Feedback como contrato**
   Toda execução relevante deve gerar feedback técnico e semântico (logs, métricas, exceções tratadas).

5. **Testes cognitivos**
   Os testes devem documentar o raciocínio técnico e cobrir casos de intenção, não apenas de execução.

---

## 🧮 6. Requisitos Técnicos

* Compatibilidade com Python 3.11+.
* Padronização de docstrings com formato reST.
* Instrumentação via `forgecore.observability`.
* Execução modular independente (cada módulo deve poder ser testado isoladamente).
* Mapeamento YAML ↔ Código (sincronização Process ↔ Base).

---

## 📈 7. Critérios de Sucesso

| Categoria           | Indicador                | Meta                                                       |
| ------------------- | ------------------------ | ---------------------------------------------------------- |
| **Modularização**   | Importação unificada     | 100% dos módulos compatíveis com `from forgebase.[module]` |
| **Testabilidade**   | Cobertura de testes      | ≥ 90% do núcleo                                            |
| **Observabilidade** | Métricas automáticas     | 100% dos UseCases e Ports instrumentados                   |
| **Desacoplamento**  | Dependências cruzadas    | 0 dependências proibidas entre camadas                     |
| **Evolutividade**   | Adição de novos Adapters | Nenhuma modificação necessária no núcleo                   |

---

## 🔍 8. Roadmap de Implementação

| Fase                   | Entregáveis                  | Descrição                                                              |
| ---------------------- | ---------------------------- | ---------------------------------------------------------------------- |
| **1. Planejamento**    | Estrutura de diretórios      | Definir pastas e classes base                                          |
| **2. Implementação**   | Classes base + Imports       | Criar e validar `EntityBase`, `UseCaseBase`, `PortBase`, `AdapterBase` |
| **3. Observabilidade** | Módulo `observability` ativo | Integrar métricas, logs e tracing                                      |
| **4. Testabilidade**   | Núcleo validado              | Criar testes cognitivos automáticos                                    |
| **5. Sincronização**   | ForgeProcess ↔ ForgeBase     | Habilitar sync de artefatos via YAML                                   |

---

## ✅ Conclusão

A modularização do núcleo do ForgeBase representa o passo definitivo rumo à maturidade arquitetural do Forge Framework.
Ela consolida o ForgeBase como uma infraestrutura **reflexiva, modular e observável**, pronta para operar como o ambiente central da inteligência cognitiva do Forge.

> *“O código do ForgeBase é o corpo de uma mente que pensa em software.”*
