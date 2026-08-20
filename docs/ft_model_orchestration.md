# Orquestração adaptativa de modelos no Fast Track

Status: **experimental**
Responsável: mantenedores do Fast Track
Última validação documental: **2026-08-08**
Fonte operacional: `process.yml`, `ft llm-capabilities`, `ft status --report` e `ft runs --done-detailed`

## Objetivo

Usar o menor modelo e esforço que produzam uma entrega validada, escalando
somente quando a complexidade, o risco ou a evidência justificarem. O objetivo
é reduzir custo total por resultado aceito — inclusive retries, tempo de
validação e regressões — sem reduzir as garantias do processo.

Este manual é uma política de autoria e condução de processos; não muda o
comportamento do engine. O FT já oferece os controles necessários por node:
`llm_engine`, `llm_model` e `llm_effort`.

## Regra obrigatória para agentes

Quando um agente for solicitado a criar, alterar, revisar ou explicar a
orquestração de um processo FT, ele deve usar este manual como referência.
Deve compartilhar este caminho com quem fez a pergunta e, antes de propor uma
rota, informar:

1. o tipo e o risco de cada node afetado;
2. a combinação executor/modelo/effort escolhida e sua justificativa;
3. os validadores ou evidências que tornam a delegação segura;
4. as condições objetivas de escalonamento; e
5. como custo e eficiência serão comparados.

Não trate esta matriz como catálogo estático: execute `ft llm-capabilities
--json` no ambiente que realizará o ciclo. Uma combinação indisponível ou não
confirmada não pode ser inventada, substituída silenciosamente ou usada como
fallback implícito.

## Princípios de roteamento

1. **Use Python antes de LLM.** Gates, validação, transformação determinística,
   coleta de métricas e seleção de paths pertencem a nodes `python` quando
   possível.
2. **Roteie por node, não pelo tamanho da demanda.** Uma mesma feature pode
   ter discovery econômico, implementação equilibrada e revisão profunda.
3. **Escalone por evidência.** Teste falho, hipóteses concorrentes, impacto
   transversal, risco de segurança ou duas tentativas sem progresso justificam
   subir modelo ou effort; ansiedade não.
4. **Preserve afinidade.** Uma execução, seu retry e sua sessão usam a mesma
   rota. A troca ocorre na fronteira de um novo node ou episódio declarado,
   com a decisão registrada no YAML e no trace; nunca no meio de uma chamada.
5. **Paralelize somente trabalho independente.** Leitura, pesquisa e review
   podem ser lanes paralelas. Escritas na mesma área, migrations e decisões que
   dependem entre si devem ter owner único e fan-in explícito.
6. **O modelo não substitui controles.** `write_scope`, permissões, validators,
   testes, human gates e receipts continuam autoritativos. Uma revisão de outro
   modelo é diversidade adicional, não prova independente de correção.

## Matriz inicial

Use os nomes exatos retornados por `ft llm-capabilities`. As famílias abaixo
expressam papéis; não pressupõem equivalência de qualidade entre fornecedores.

| Situação do node | Rota inicial Codex | Rota inicial Claude | Proteção exigida |
| --- | --- | --- | --- |
| Busca, inventário, classificação ou síntese com saída delimitada | Luna / Low | Haiku / padrão disponível | somente leitura; paths e formato de saída explícitos |
| Implementação ou bug reproduzível de escopo conhecido | Terra / Medium | Sonnet / Medium | `outputs`, `write_scope` e testes focais |
| Integração, refatoração multi-módulo ou diagnóstico com algumas hipóteses | Terra / High | Sonnet / High | contrato, testes de integração e critérios de parada |
| Arquitetura, concorrência, autorização, dados financeiros ou debugging ambíguo | Sol / High ou XHigh | Opus / High ou XHigh | revisão somente leitura e validação determinística proporcional ao risco |
| Auditoria independente de mudança crítica | Sol / High | Opus / High | `write_scope` restrito ao parecer; evidências e baseline obrigatórias |
| Problema único excepcional | Sol / Max | Opus / Max | justificativa registrada e comparação com rota anterior |
| Várias frentes realmente independentes | Sol / Ultra para coordenação, com lanes menores | Fable ou equipe de subagentes, se disponível | `parallel_group`, ownership sem sobreposição e fan-in |

`Max`, `Ultra`, Fable e qualquer alias dependem da conta e da CLI instalada;
não são defaults. Para volume previsível, aumente lanes ou use modelos
econômicos com validators, em vez de elevar o modelo principal.

## Como declarar no processo

O default do ciclo deve ser a rota mais frequente. Declare override no node
quando o papel for diferente. O exemplo usa Codex; substitua somente por uma
combinação validada no ambiente.

```yaml
- id: feature.discovery
  type: discovery
  executor: codex
  llm_engine: codex
  llm_model: gpt-5.6-luna
  llm_effort: low
  outputs: [docs/feature-map.md]
  write_scope: [docs/feature-map.md]
  prompt: |
    Mapeie os pontos de entrada, invariantes e testes existentes.
    Cite arquivos e símbolos; não proponha nem altere a arquitetura.

- id: feature.implement
  type: build
  executor: codex
  llm_engine: codex
  llm_model: gpt-5.6-terra
  llm_effort: medium
  outputs: [src/feature.py, tests/test_feature.py]
  validators:
    - tests_pass: true

- id: feature.review_security
  type: review
  executor: codex
  llm_engine: codex
  llm_model: gpt-5.6-sol
  llm_effort: high
  expert: code_reviewer
  outputs: [docs/review/security.md]
  write_scope: [docs/review/security.md]
```

O modelo de revisão não corrige o produto. Se encontrar defeito, a rota retorna
ao node focal de correção e executa os validators novamente. Para nodes Claude,
use o executor e os identificadores aceitos no probe; aliases só são aceitáveis
quando a reprodutibilidade não exigir versão fixada.

## Escalonamento e troca de rota

Comece na rota inicial da matriz. Antes de criar um override mais caro, registre
no node ou na instrução de `ft fix` pelo menos um dos sinais abaixo:

- validação falhou por causa não local ou há duas hipóteses plausíveis;
- duas tentativas válidas não produziram progresso verificável;
- a mudança alcança autenticação, autorização, concorrência, persistência,
  migração destrutiva, faturamento ou contrato público;
- o discovery comprovou que há mais módulos, invariantes ou dependências que o
  escopo inicial; ou
- a revisão encontrou um risco que exige novo plano.

A promoção é localizada: `Terra/Medium → Terra/High → Sol/High`, por exemplo.
Não eleve todo o ciclo para Sol, Opus ou Max por causa de um único node. Depois
de resolver o bloqueio, o próximo node volta à rota definida para seu próprio
papel.

Nunca altere `process.yml` com IDs de ciclos, findings, hashes, receipts ou
instruções de recuperação concretas. Isso pertence ao state do ciclo e a `ft
fix`; o processo deve continuar reutilizável.

## Experimento de custo e eficiência

Esta política só continua se melhorar uma métrica sem deteriorar qualidade.
Compare durante duas semanas, ou até haver pelo menos 10 entregas aceitas por
classe de tarefa, a baseline atual contra a matriz acima.

1. Antes de iniciar, registre template, commit-base, classe, risco, critérios de
   aceite, rota por node e se o trabalho aceita paralelismo.
2. Mantenha constantes template, validators, permissões e critérios de aceite.
   Não compare uma rota barata sem testes contra uma rota forte com revisão.
3. Ao fechar cada ciclo, arquive `ft status --cycle <id> --report` e consulte
   `ft runs --done-detailed`. Esses artefatos informam tempo ativo de LLM,
   tokens quando o provider os expõe, retries, validators, fila e wall time.
4. Calcule por classe: taxa de primeira aprovação, ciclos de correção, tempo até
   verde, regressões posteriores, tokens por entrega aceita e duração LLM por
   entrega aceita. Custo monetário exige tabela de preços datada do provider;
   não o estime quando a telemetria não o fornecer.
5. Adote uma rota apenas se mantiver ou aumentar a taxa de aceite e reduzir ao
   menos uma métrica de custo/tempo de forma material. Se aumentar regressões,
   reduza o escopo do piloto ou reverta a rota daquela classe.

## Checklist de autoria e orientação

- [ ] Rodei `ft llm-capabilities --json` no ambiente alvo.
- [ ] Cada node LLM tem papel, `outputs` e `write_scope` explícitos.
- [ ] Cada rota econômica possui validação determinística suficiente.
- [ ] A rota de alto risco inclui revisão e evidência proporcionais.
- [ ] A troca de modelo ocorre apenas entre nodes/episódios e tem gatilho objetivo.
- [ ] Lanes paralelas não escrevem nos mesmos recursos.
- [ ] O processo passa em `ft validate --template <nome>` e `ft lint-process`.
- [ ] A coleta por ciclo usa `ft status --report` e não inventa custos ausentes.

## Referências

- [Autoria de processos](ft_process_authoring.md)
- [Uso do engine e telemetria](ft_engine_usage.md)
- [Playbook copiado aos projetos](../AGENTS.md)
