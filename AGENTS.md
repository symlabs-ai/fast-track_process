# AGENTS.md — Conduzindo um projeto com o ft engine

> Playbook para um agente ou humano conduzir um projeto Fast Track de ponta a
> ponta com o contrato V3. Python orquestra grafo, gates, validadores e
> worktrees; o LLM constrói; você decide nos human gates e encerra os ciclos.
> O `ft init` copia este arquivo para a raiz do projeto.

## Regra zero — nunca opere no repo do engine

O repositório `fast-track` contém o engine e o catálogo global de templates; ele
nunca é um projeto FT. O guard global recusa comandos de projeto nesse repo.

- Crie ou inicialize projetos fora deste diretório.
- Nunca clone o engine para “virar projeto”.
- Para desenvolver o próprio engine, use `FT_ALLOW_ENGINE_REPO=1` somente nos
  comandos de manutenção que realmente precisam desse acesso.

## Modelo mental V3

Há duas operações independentes:

1. `ft init [dir]` prepara a base comum e saudável do repositório: Git com HEAD,
   `.ft/`, manifesto V3, ignores e este playbook. Ele não escolhe nem copia
   template e não cria `docs/` ou `src/` de produto.
2. `ft run <dir> --template <T>` seleciona, materializa e executa um template em
   um novo ciclo isolado. Não existe processo principal ou default.

```text
ft init meu-projeto                      # base comum, sem processo
  → adicionar/commitar conhecimento      # fontes do projeto, se necessárias
ft run . --template mvp-builder --auto   # ciclo A
ft run . --template tweak --request ...  # ciclo B, pode coexistir com A
  → status/graph/approve/reject/fix       # selecionar ciclo se houver ambiguidade
ft close --cycle <id>                     # merge e arquivamento serializados
```

Cada ciclo roda em worktree externa em
`$FT_HOME/worktrees/<projeto>/<cycle>/`. O checkout principal permanece limpo
até o `ft close`. Ciclos são descartáveis e múltiplos ciclos podem estar ativos
ao mesmo tempo, inclusive usando templates diferentes.

## 0. Inicializar ou diagnosticar o repositório

```bash
ft init meu-projeto
cd meu-projeto

ft init --check   # diagnóstico somente leitura
ft init           # repetição idempotente quando o ambiente está saudável
ft init --fix     # reparo explícito e conservador
```

`ft init` aceita o diretório como argumento opcional e nenhuma opção de seleção
de template. Ao concluir, o projeto possui repositório Git com HEAD e a base
comum do Fast Track. Uma repetição saudável não altera arquivos.

`--check` apenas relata invariantes ausentes ou inconsistentes e não escreve no
projeto. `--fix` pode reconstruir manifesto e catálogo a partir dos processos
locais válidos, restaurar arquivos comuns ausentes e corrigir metadados seguros.
Ele nunca sobrescreve forks locais nem históricos; antes de substituir metadados
corrompidos, guarda backup fora do repositório, sob `$FT_HOME`.

Manifesto inicial:

```yaml
schema_version: 3
processes: {}
```

O manifesto não contém seletor de processo default. Após um template ser
materializado, ele aparece no mapa `processes`, mas nenhum deles ganha prioridade
implícita.

## 1. Preparar conhecimento

Templates leem as fontes do projeto existentes no checkout. Antes de iniciar um
ciclo, crie os documentos que o template escolhido exige e faça commit:

- produto novo: em geral `docs/PRD.md` e `docs/TECH_STACK.md`;
- produto existente: preserve `docs/PROJECT_BACKLOG.md` como mudanças desejadas e
  `docs/FEATURES.md` como catálogo do que já foi entregue;
- demanda em arquivo: passe `--input demanda.md`;
- demanda curta: passe `--request "descrição objetiva"`;
- hipótese pronta, quando suportada: passe `--hipotese hipotese.md`.

> Faça commit antes de rodar. A worktree nasce de um commit, nunca de mudanças
> não commitadas.

## 2. Escolher e rodar qualquer template

`--template` é obrigatório em toda nova execução:

```bash
ft run . --template mvp-builder --auto
ft run . --template feature --request "Adicionar busca por telefone" --codex
ft run . --template feature --input demanda.md --claude
ft run . --template bug --request "Terminal duplica o eco do input" --codex
ft run . --template tweak --request "Mudar o botão Salvar para azul" --codex
```

Não existem entrypoints especializados por categoria, opção para apontar um YAML
arbitrário ou execução sem seleção explícita. Os templates `feature`, `bug`,
`tweak`, `mvp-builder` e demais templates compatíveis usam o mesmo entrypoint.

Na primeira seleção de `T`, o engine copia o catálogo global para
`.ft/process/T/` e registra o path no manifesto. Execuções posteriores usam
somente esse fork local. A materialização é copy-once: o catálogo global nunca
substitui customizações locais, e o engine nunca executa `templates/` diretamente.
Materializar um template durante `run` não semeia `docs/` ou `src/`.

Templates principais:

| Template | Uso |
|---|---|
| `base` | Grafo mínimo para projetos que querem compor o próprio processo |
| `feature` | Evolução incremental de uma capacidade em produto existente |
| `bug` | Correção focal com diagnóstico e regressão RED→GREEN |
| `tweak` | Mudança pequena, focal e de baixo risco |
| `mvp-builder` | Processo completo recomendado para construir um MVP |
| `fast-track-v2` | Processo histórico V2 |
| `ft-ui-prototype` | Prototipagem rápida de UI |
| `symgateway` | Ambiente com integração SymGateway opt-in |

Cada template declara sua política de entrada. `--request` e `--input` são
formas genéricas; o engine recusa combinações ausentes ou incompatíveis antes de
criar o ciclo.

### Concorrência entre ciclos

Inicie execuções independentes em terminais distintos ou em background:

```bash
ft run . --template feature --request "Busca por telefone" --auto
ft run . --template tweak --request "Reduzir padding do cabeçalho" --auto
```

Não há bloqueio global de “ciclo ativo” nem flag especial para contorná-lo: cada
chamada aloca atomicamente um novo id, branch, worktree e estado. Um lock curto protege a
preparação comum do projeto; depois disso os runners avançam em paralelo. O
merge/arquivamento do `ft close` usa outro lock por projeto para impedir que dois
closes alterem o checkout principal simultaneamente.

`--parallel` em `ft run` continua significando fan-out de nodes declarados como
paralelizáveis dentro de um único processo. Isso é diferente de iniciar vários
ciclos independentes.

### Modos de avanço

```bash
ft run . --template <T>                  # interativo
ft run . --template <T> --auto           # até human gate, MVP ou BLOCK
ft run . --template <T> --auto --bypass-human-gates
ft run . --template <T> --codex gpt-5.6-sol --effort max

ft continue --cycle <id>                 # um node
ft continue --cycle <id> --sprint        # uma sprint
ft continue --cycle <id> --auto          # até gate, MVP ou BLOCK
```

`--auto` sozinho nunca pula human gates. `--bypass-human-gates` delega a decisão
ao LLM e deve ser usado deliberadamente.

## 3. Selecionar e monitorar ciclos

```bash
ft runs
ft status --cycle <id>
ft status --cycle <id> --full
ft status --cycle <id> --report
ft graph --cycle <id>
ft log --cycle <id>
```

Regra única de seleção:

- nenhum ciclo aplicável: erro claro;
- exatamente um: o comando pode inferi-lo;
- mais de um: `--cycle` é obrigatório e o erro lista as opções.

O engine nunca escolhe pela data de criação. A regra vale para comandos de avanço,
inspeção, gate, recuperação e encerramento.

## 4. Human gates

```bash
ft approve --cycle <id>
ft approve "mensagem para o próximo node" --cycle <id>
ft reject "motivo acionável" --cycle <id>
ft reject "motivo" --no-retry --cycle <id>
ft fix "instrução de correção" --cycle <id>
ft explore "pedido livre" --cycle <id>
```

Quando só há um ciclo aplicável, `--cycle` pode ser omitido. Rejeições devem ter
motivo objetivo porque o texto vira contexto do retry.

## 5. Bloqueios e recuperação

| Situação | Ação |
|---|---|
| node bloqueado, repetir igual | `ft retry --cycle <id>` |
| correção dirigida | `ft fix "o que corrigir" --cycle <id>` |
| descartar sem merge | `ft abort --cycle <id>` |
| cancelar com justificativa | `ft cancel "motivo" --cycle <id>` |

Leia `ft status --cycle <id>` antes de retentar. Smart retry detecta erros
idênticos e bloqueia cedo.

## 6. Encerrar o ciclo

```bash
ft close --cycle <id>
ft close --cycle <id> --merge full
ft close --cycle <id> --merge docs
ft close --cycle <id> --merge selective --merge-paths "path/a path/b"
ft close --cycle <id> --keep-worktree
```

O lock de close serializa merges do mesmo projeto. Um ciclo aguardando esse lock
não impede outros runners de continuar trabalhando.

Antes do close, revise aprendizados estruturados do processo:

```bash
ft process-candidates --cycle <id>
ft process-candidates PI-001 --cycle <id> --status promoted \
  --reason "Aplicado e testado no engine" --reference "commit/path"
```

Não marque `promoted` sem atualizar e testar a referência global. O ciclo altera
apenas seu fork local.

### Verificação pós-close obrigatória

O ciclo testa na worktree; caches e dependências do checkout promovido podem
estar defasados. Antes de demonstrar ao stakeholder:

1. reinstale dependências alteradas;
2. limpe caches de build antigos;
3. reinicie backend e frontend no checkout promovido;
4. confirme HTTP 200 nas rotas principais;
5. exercite de fato a capacidade entregue.

Depois, feedback do stakeholder vira backlog/PRD no checkout principal e um novo
ciclo usa o template adequado.

## Migração e reparo

Projetos com `process/`, bundle flat `.ft/process/process.yml` ou manifesto V2
precisam de migração explícita:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

A migração preserva todos os processos e ciclos, converte o manifesto para schema
V3 e remove apenas o conceito de default. Nunca cria um layout novo ao lado do
legado. Execute sem runtime em mutação; colisões e symlinks são recusados antes
de qualquer movimento.

Use `ft init --check` para diagnóstico cotidiano e `ft init --fix` para reparos
seguros do workspace V3. Migração de layout e reparo não são sinônimos.

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `FT_HOME` | Runtime, worktrees, locks e backups; default `~/.ft` |
| `FT_ALLOW_ENGINE_REPO` | Libera manutenção no repo do engine |
| `FT_SKIP_HEALTH_CHECK` | Pula health check da API no `ft run` |
| `FT_LLM_ENGINE` | Executor default (`claude`, `codex`, `gemini`, `opencode`) |
| `FT_LLM_EFFORT` | Effort herdado quando não há override |
| `FT_CODEX_REASONING_EFFORT` | Override de reasoning do Codex |
| `FT_LLM_EXECUTOR_TIMEOUT` | Timeout geral de delegação, em segundos |
| `FT_CODEX_EXECUTOR_TIMEOUT` | Timeout específico de delegações Codex |
| `FT_OPENCODE_SANDBOX` | Sandbox de filesystem do OpenCode |
| `SYM_GATEWAY_PROJECT_KEY` / `SYM_GATEWAY_ADMIN_KEY` | Scripts SymGateway opt-in |

## Referências

No projeto:

- catálogo local: `processes` em `.ft/manifest.yml`;
- processos versionados: `.ft/process/<template>/process.yml`;
- histórico versionado: `.ft/cycles/<cycle>/`;
- runtime externo: `$FT_HOME/worktrees/<projeto>/<cycle>/state/`.

No engine:

- guia completo: `docs/ft_engine_usage.md`;
- arquitetura: `docs/mvp-builder-architecture.md`;
- catálogo global: `templates/`.
