# ft engine — Guia de Uso

Motor determinístico de processos para solo dev + AI. Python controla o fluxo;
o LLM executa apenas tarefas de construção.

## Conceito

```text
YAML de processo → ft engine → LLM constrói → Python valida → grafo avança
```

O engine lê um processo YAML, delega nodes às CLIs configuradas (`claude`,
`codex`, `gemini` ou `opencode`) e valida os artefatos com verificações
determinísticas. O LLM não escolhe processo, ciclo ou próxima transição.

O contrato V3 separa:

- **workspace**: base comum criada por `ft init`, sem processo associado;
- **projeto**: objetivo, fase e Definition of Done duráveis acima dos ciclos;
- **template**: bundle global materializado copy-once em um fork local;
- **ciclo**: execução imutavelmente ligada a um template local e a uma worktree;
- **runtime**: estado, locks e logs sob `$FT_HOME`, fora do Git do produto.

## Instalação

O suporte oficial é Linux/POSIX com Python 3.11 ou 3.12, Git e Bash. macOS é
best-effort. Windows nativo não é suportado; use WSL2. O isolamento de
filesystem do OpenCode usa `bwrap` e, portanto, requer Linux.

```bash
pip install -e .
ft --version
ft --help
```

O repositório do engine não é um projeto FT. Crie projetos fora dele; use
`FT_ALLOW_ENGINE_REPO=1` apenas para manutenção do próprio engine.

## Iniciar o workspace

```bash
ft init meu-projeto
cd meu-projeto
```

O argumento de diretório é opcional (`.` por padrão). O comando:

1. garante um repositório Git com HEAD;
2. cria `.ft/manifest.yml`, `.ft/project.yml`, `.ft/.gitignore` e o playbook;
3. não escolhe nem materializa template;
4. não cria `docs/`, `src/`, worktree ou estado de ciclo.

Em um workspace saudável, repetir `ft init` é idempotente.

### Templates de inicialização (`kind: init`)

O engine só garante os invariantes (pré-condições, scaffold `.ft/`,
pós-condições: HEAD utilizável e checkout limpo). A mecânica do init —
`git init`, `.gitignore`, `.env.example`, `README.md`, `AGENTS.md` e o
commit inicial — vive no template embutido `init-default`
(`templates/init-default/`), um template `kind: init`: sem processo, apenas
`template.yml` com uma lista ordenada de scripts executáveis.

Um template de init roda **uma única vez por projeto por máquina** — o
marker fica em `.ft/runtime/init.yml` (gitignored: um clone em outra máquina
inicializa o próprio ambiente de novo). Scripts recebem `FT_PROJECT_ROOT`,
`FT_TEMPLATE_DIR`, `FT_ENGINE_ROOT`, `FT_INIT_MODE` (`init`|`fix`) e
`FT_ADOPT`; exit != 0 bloqueia como um gate.

Para provisionar ambiente específico (`.env`, credenciais, registro no
gateway), crie um template `kind: init` no catálogo e selecione-o no init.
Os templates de **organização** `symlabs` e `tecnospeed` são exemplos reais —
scaffold Poetry/`src`, `.env` de dev, `CLAUDE.md` e registro do projeto no
workspace da org no SymGateway:

```bash
ft init meu-projeto --template symlabs   # init-default + symlabs
ft init . --fix --template symlabs       # re-executa a cadeia p/ consertar
```

`ft init --template` roda a cadeia `init-default` → template escolhido;
`ft run --template` segue intocado (processos por ciclo) e recusa templates
`kind: init` com instrução de uso.

A config de cada organização (workspace, caller key, admin key do gateway)
vive em `environment/<org>.env` no repo do engine — **gitignored, nunca no
bundle** (`environment/<org>.env.example` é o modelo versionado). O script
`provision.sh` resolve a org pelo nome do template, lê essa config e falha
alto se estiver ausente/incompleta. Credenciais nunca entram no template nem
no repo do projeto; a admin key só registra o projeto e o `.claude/
settings.local.json` (gitignored) recebe apenas a caller key na URL.
O workspace Tecnospeed já existe; cada máquina ainda precisa de
`environment/tecnospeed.env` com as credenciais emitidas pelo DevOps. Sem essa
configuração, o template falha com instrução (`/ask devops`) antes de gravar o
marker de conclusão.

### Diagnóstico e reparo

```bash
ft init . --check   # somente leitura
ft init . --fix     # reparo explícito
```

`--check` relata cada invariante e nunca escreve. `--fix` restaura arquivos
comuns ausentes, reconstrói o catálogo a partir de `.ft/process/*/process.yml` e
corrige metadados inequivocamente recuperáveis. Ele não sobrescreve forks locais
nem históricos. Manifestos corrompidos substituídos recebem backup externo sob
`$FT_HOME`; ambiguidades são recusadas com instrução manual.

Manifesto inicial:

```yaml
schema_version: 3
processes: {}
```

Não há seletor de processo principal. O mapa `processes` é catálogo, não fila nem
ordem de preferência.

### Lifecycle e Definition of Done do projeto

`.ft/project.yml` é o contrato versionado acima dos ciclos. Ele começa em
`building` e declara objetivo, marco alvo, seleção do backlog, status aceitos,
exigência de evidência e gates adicionais sobre campos de JSON/YAML.
`blocked`, `deferred`, `planned` e “decidido” não são entrega; o contrato aceita
somente `done`/`accepted`.

```bash
ft project-status
ft project-close
ft project-reopen --reason "novo marco" --objective "Entregar ..." --target v2
```

`ft project-status` é read-only. `ft project-close` registra
`.ft/project-readiness.yml` e só entra em `maintenance` quando o DoD está verde,
o checkout está limpo e nenhum ciclo permanece aberto. `ft close` continua
encerrando somente o ciclo. `project-reopen` invalida o receipt anterior e abre
explicitamente um novo objetivo construtor.

`mvp-builder`/`mvp-builder-fast` têm papel `builder` e rodam em `building`.
`feature`, `feature-fast`, `bug`, `bug-fast` e `tweak` têm papel `maintenance`:
são recusados antes de um fechamento READY íntegro.

## Abrir um ciclo

Toda nova execução exige um template:

```bash
ft run . --template mvp-builder
ft run . --template feature --request "Adicionar busca por telefone"
ft run . --template feature --input demanda.md
ft run . --template bug --request "Terminal duplica o eco do input"
ft run . --template tweak --request "Mudar o botão Salvar para azul"
```

`ft run` é o único entrypoint para todos os templates. Não há comando específico
por categoria de trabalho nem opção para fornecer um YAML arbitrário.

Para separar definição e construção de um produto novo, encerre primeiro um
ciclo MDD e depois abra o builder rápido. O primeiro ciclo é `neutral` e não
assume ownership do objetivo construtor:

```bash
ft run . --template mdd --request "Descrever problema e resultado" --auto
ft close --cycle <id-mdd>
ft run . --template mvp-builder-fast --auto
```

### Resolução local-first

Para `--template T`, o engine:

1. usa `.ft/process/T/process.yml` quando `T` já está registrado e válido;
2. caso contrário, copia o bundle global para `.ft/process/T/` e o registra;
3. fixa path e digest locais no estado do novo ciclo;
4. preserva o fork local em execuções futuras.

O catálogo `templates/` nunca é executado diretamente. A materialização ocorre
uma única vez e não copia seeds genéricos para `docs/` ou `src/`.

Exemplo após duas materializações:

```yaml
schema_version: 3
processes:
  feature:
    path: .ft/process/feature/process.yml
    template: feature
    source_digest: sha256:...
    base_digest: sha256:...
  tweak:
    path: .ft/process/tweak/process.yml
    template: tweak
    source_digest: sha256:...
    base_digest: sha256:...
defaults:
  llm_engine: codex
```

### Política de entrada

Cada template declara se uma demanda é exigida, opcional ou proibida. A CLI
oferece duas formas uniformes:

- `--request "texto"`: demanda curta inline;
- `--input arquivo`: conteúdo lido de um arquivo.

A política é validada antes da criação do ciclo. Quando uma demanda é aceita, o
engine a transporta para a worktree sem modificar silenciosamente as fontes do
checkout principal.

## Concorrência entre ciclos

Múltiplos ciclos ativos são suportados por padrão:

```bash
# Terminal A
ft run . --template feature --request "Busca por telefone" --auto

# Terminal B
ft run . --template tweak --request "Reduzir padding" --auto
```

Cada chamada reserva atomicamente id, branch, worktree e state. Não há bloqueio
global de “execução ativa”. Um lock curto cobre apenas preparação compartilhada,
como reconciliação do manifesto e materialização; runners trabalham em paralelo
depois disso.

`ft close` usa um lock separado por projeto para serializar merge e arquivamento
no checkout principal. Esperar pelo close não bloqueia a execução de outros
ciclos.

Isso é diferente de `ft run --template <T> --parallel`: essa flag habilita
fan-out de nodes de um único ciclo quando o YAML possui `parallel_group`.

## Seleção de ciclo

Comandos que alteram ou conduzem um ciclo seguem uma regra comum:

- zero ciclos aplicáveis: erro;
- exatamente um: pode ser inferido;
- dois ou mais: `--cycle <id>` é obrigatório e o erro lista as opções.

O engine nunca escolhe pela data de criação. A regra vale para `continue`,
`graph`, `log`, `approve`, `reject`, `retry`, `fix`, `explore`, `abort`,
`cancel`, `process-candidates` e `close`. `ft status` é a exceção somente
leitura: sem `--cycle`, ele imprime um bloco rotulado para cada ciclo aberto;
com `--cycle`, mostra apenas o selecionado. O mesmo fan-out vale para
`ft status --report`. Cada bloco de status informa o caminho absoluto da
worktree ativa correspondente e termina com uma linha sanitizada da atividade
mais recente do log LLM. Em `--watch`, esse rodapé é substituído a cada atualização
sem funcionar como um log rolante.

```bash
ft runs
ft status --cycle cycle-07 --full
ft status --cycle cycle-07 --report
ft status --cycle cycle-07 --watch 60
ft graph --cycle cycle-08
ft continue --cycle cycle-07 --auto
ft close --cycle cycle-08
```

## Comandos de condução

```bash
# Novo ciclo
ft run . --template <T> [--request "..."] [--input arquivo]
ft run . --template <T> --auto
ft run . --template <T> --auto --bypass-human-gates

# Avanço
ft continue --cycle <id>
ft continue --cycle <id> --sprint
ft continue --cycle <id> --auto

# Inspeção
ft status --cycle <id>
ft status --cycle <id> --full
ft status --cycle <id> --report
ft status --cycle <id> --watch 60
ft graph --cycle <id>
ft log --cycle <id>
ft runs
ft runs --done
ft runs --done-detailed

# Gates e recuperação
ft approve "nota opcional" --cycle <id>
ft reject "motivo acionável" --cycle <id>
ft reject "motivo acionável" --auto --cycle <id>
ft reject "motivo" --no-retry --cycle <id>
ft retry --cycle <id>
ft fix "instrução" --cycle <id>
ft explore "pedido livre" --cycle <id>
ft abort --cycle <id>
ft cancel "motivo" --cycle <id>
```

`ft status --watch [SEGUNDOS]` abre uma tela fixa em terminal interativo e
redesenha o status no mesmo lugar, sem produzir uma sequência de snapshots no
histórico. O intervalo default é 60 segundos quando `--watch` é usado sem valor;
cada tela mostra o horário da última atualização. O buffer normal é preservado
para permitir rolagem, e `Ctrl+C` encerra o acompanhamento.

### Exploração standalone com sessão Codex retomável

Uma integração pode manter contexto conversacional entre consultas read-only
sem associá-las a um node do ciclo:

```bash
ft explore "primeira pergunta" --codex gpt-5.6-luna --effort low \
  --standalone --stream-json --persist-session
ft explore "pergunta seguinte" --codex gpt-5.6-luna --effort low \
  --standalone --stream-json --resume-session <session-id>
```

A persistência standalone está disponível somente para Codex. As duas formas
continuam sob sandbox read-only; o identificador retornado é opaco e não é uma
credencial. O evento NDJSON `result` informa `session_id`, `session_resumed`, o
`usage` normalizado do turno e `cost_usd`. Este último permanece `null` quando o
provider não reporta custo monetário: o FT não estima preço a partir de tokens.
Sem uma dessas flags, a exploração continua efêmera e não expõe o id interno da
sessão.

### Pacote obrigatório de decisão humana

Ao entrar em `human_gate`, pausar por `requires_approval` ou exibir um gate
pendente em `ft status`, a engine sempre apresenta contexto suficiente para a
decisão. O card contém: decisão, motivo temporal, URL/artefatos para inspeção,
checklist, limites, efeito de aprovar e efeito de rejeitar. Processos locais
antigos recebem contexto derivado dos últimos nodes concluídos e de evidências
existentes; portanto a garantia não depende de rematerializar o template.

Um template pode declarar `decision_context` para substituir os fallbacks:

```yaml
decision_context:
  decision: "Liberar este candidato para handoff?"
  why_now: "Regressão e validação de plataforma terminaram."
  review_paths:
    - docs/PRD.md
    - docs/acceptance-report.md
  checklist:
    - "Executar o fluxo P0 pela entrada normal."
    - "Confirmar todos os targets obrigatórios em PASS."
  limitations:
    - "Aprovação limitada ao candidato e escopo apresentados."
  approve_effect: "Avança para o handoff."
  reject_effect: "Retorna ao fix e exige nova apresentação do gate."
```

Os paths devem ser relativos, seguros e existentes. Ausência de URL ou prova
necessária aparece como limitação e não deve ser convertida em aprovação.

Todo fix dirigido executa auditoria focal obrigatória. A engine preserva os
nodes e gates já aprovados, invalida apenas os receipts do fix e do review que
originou o finding, executa o node indicado por `on_fail.goto` e retorna
diretamente àquele review.

Em um human gate com `reject_next`, `ft fix` e `ft reject` usam esse node como
correção e identificam o review predecessor que gerou a evidência. Depois da
auditoria focal, retornam ao mesmo human gate sem reexecutar os nodes
intermediários já aprovados. Com `--auto`, o ciclo executa
`fix → review focal` e para novamente para a decisão humana. A flag histórica
`--audit-origin` continua aceita por compatibilidade, mas não desativa nem
altera essa política. O review recebe obrigatoriamente o pedido de correção e o
finding de origem; para UI, ele deve confirmar o resultado visual/físico, não
apenas a existência de código ou testes. Um node de correção pode declarar
`fix_review: <review-id>`; a engine executa somente sua cadeia linear de `next`
até esse review e retorna ao mesmo gate. Sem a declaração, ciclos históricos
reutilizam o review de origem em modo focal, com o prompt amplo suspenso. A
engine recusa um fix avulso quando não consegue garantir uma dessas rotas.
Identidades de artefato citadas no finding são tratadas como baseline: o review
registra e valida o candidato corrente do fix, a menos que igualdade de hash
tenha sido definida explicitamente como requisito.

Relatório de review continua pertencendo ao review, não ao node de correção. Se
o finding apontar apenas evidência ausente ou desatualizada e o produto corrente
já estiver correto, o fix registra um handoff sem fabricar alteração de produto.
O review focal seguinte reconcilia somente os seus outputs e as entradas
afetadas do recibo composto, preservando evidência histórica e itens aprovados.
Uma resposta focal APPROVED não avança quando o output canônico permaneceu
inalterado ou ainda declara REJECTED; isso impede que o human gate seja aberto
com o relatório anterior apesar de uma auditoria corrente positiva.

Toda aprovação de auditoria focal é fail-closed quanto à fidelidade da prova.
A engine exige um recibo estruturado `focal_evidence` com cobertura completa,
claims campo a campo e paths repo-locais existentes. Quando o finding envolve
dado real renderizado em UI, somente uma jornada física ponta a ponta pela
interface pública/fonte real pode aprovar: mock, fixture, preview ou teste
isolado de componente não substituem persistência, leitura e observação na tela.
Uma aprovação sem esse recibo ou que omita um campo citado no finding retorna
ao fix como `EVIDENCE_FIDELITY_REJECTED`; uma rejeição honesta continua aceita
sem exigir evidência fictícia de PASS.
Credenciais, tokens e senhas não podem ser transportados em argumentos de
processo nem persistidos nos logs/artefatos da prova; a sessão real deve ser
reutilizada dentro do limite seguro do dispositivo.
Jornadas autenticadas exigem uma identidade exclusiva do agente, provisionada
por seed idempotente e resetável. O seed cria apenas a conta e o estado mínimo
necessário, lê credenciais de armazenamento protegido e emite um recibo
sanitizado repo-local com referência opaca e `ready`; não pode depender da conta
ou de uma ação manual do stakeholder. A aprovação focal valida esse recibo no
bloco `test_identity` antes de aceitar a prova física.
Findings que identificam APK/hash exigem ainda um recibo de identidade: o
SHA-256 calculado do candidato repo-local precisa ser igual ao declarado e ao
medido no dispositivo, cuja saída sanitizada também deve existir no repo.

# Encerramento
ft process-candidates --cycle <id>
ft close --cycle <id>
ft project-status
ft project-close
```

`ft runs --done` mantém o comparativo por ciclo. A coluna `DURAÇÃO LLM` soma
somente os spans ativos de delegação LLM; espera por human gate, intervalos
ociosos, fila, validadores e close não entram no valor. Históricos sem
telemetria capaz de separar esse tempo exibem `—`, nunca o wall-clock como
aproximação. Em ciclos em curso, o valor é recalculado do trace no instante da
consulta e inclui o trecho já executado da chamada LLM corrente; o status do
node continua indicando que a execução ainda não terminou. `--done-detailed`
implica `--done` e acrescenta uma tabela para
cada ciclo com as execuções de node na ordem cronológica real, incluindo
tentativa, início, duração LLM, tokens, última atividade, resultado e fonte da
telemetria. Retries e loops aparecem em linhas separadas; steps sem delegação
LLM mostram `0s`, enquanto ciclos legados não recebem duração ou tokens
estimados quando o dado não foi persistido. Cada tabela termina em `TOTAL`, com
quantidade de execuções e tentativas, duração LLM acumulada e tokens. A última
linha da seção é o `TOTAL GERAL` de todos os ciclos exibidos. Se qualquer
execução não tiver a telemetria da métrica, o consolidado correspondente
permanece `—`.

No grafo de `ft status --full`, nodes PASS são azuis, pending/não executados são
brancos, SKIPPED são cinza e FAIL/BLOCKED/erros são vermelhos. O node ativo e
os gates preservam seus destaques semânticos. `NO_COLOR` e saída não-TTY
continuam removendo todo ANSI.

`--auto` avança até human gate, MVP ou BLOCK. Ele não pula human gates;
`--bypass-human-gates` autoriza o LLM a decidir nesses pontos.

`ft status --report` usa o trace append-only do ciclo. O relatório distingue
wall time real de tempo ativo de LLM, validators, espera humana, fila e close;
tentativas e ordinais sobrevivem a reinícios do runner. `execution` separa
nodes únicos, execuções totais, reexecuções e tentativas abertas; o progresso
do grafo nunca é usado como aproximação do custo real. Métricas que o provider
não fornece aparecem como `—`/`null`, nunca como zero inventado. No close, o
resumo derivado é arquivado em `.ft/cycles/<cycle>/run-report.json`; logs crus
permanecem fora do Git.

Durante uma delegação ativa, `ft status` mostra um snapshot vivo logo abaixo
de `TRABALHO EM ANDAMENTO`:

```text
Agora: executando testes focais em `test_feature.py`
Evolução: tarefas 2/3 · 16 comandos concluídos · 2 arquivos alterados
Sinais: worktree alterada · última escrita há 3s
```

O snapshot lê somente uma cauda limitada do log do provider, traduz comandos
em categorias seguras e omite argumentos, credenciais, tokens, senhas e
identificadores de dispositivo. Ele é apenas informativo: consultar o status
não pausa nem altera a delegação.

## Seleção do executor LLM

Escolha o executor no início ou ao continuar um ciclo:

```bash
ft run . --template mvp-builder --codex
ft run . --template feature --request "Busca" --codex gpt-5.6-sol --effort max
ft run . --template tweak --request "Ajuste" --opencode
ft continue --cycle cycle-07 --claude --sprint
```

Ou defina o default por ambiente:

```bash
export FT_LLM_ENGINE=opencode
```

Quando `--opencode` é usado sem modelo explícito, o default é
`pgx/zai-org_glm-4.7-flash`.

Defaults persistentes ficam em `defaults.llm_engine`, `defaults.llm_model` e
`defaults.llm_effort` no manifesto. Durante uma execução, a combinação fica
fixada em `$FT_HOME/worktrees/<projeto>/<cycle>/state/`, de modo que comandos
posteriores preservam a escolha.

`ft llm-capabilities --json` executa probes limitados e paralelos das CLIs
instaladas. `ft llm-defaults --agent ... --model ... --effort ...` valida uma
combinação com probe fresco e atualiza atomicamente o manifesto. O effort
`default` remove o override e devolve a escolha ao provider.

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `FT_HOME` | Runtime, worktrees, locks e backups; default `~/.ft` |
| `FT_ALLOW_ENGINE_REPO` | Libera manutenção dentro do repo do engine |
| `FT_SKIP_HEALTH_CHECK` | Pula health check da API no início do run |
| `FT_LLM_ENGINE` | Executor default (`claude`, `codex`, `gemini`, `opencode`) |
| `FT_LLM_EFFORT` | Effort herdado quando node, flag e state não definem valor |
| `FT_LLM_EXECUTOR_TIMEOUT` | Alias legado da janela global de inatividade; não é teto wall-clock |
| `FT_CODEX_EXECUTOR_TIMEOUT` | Alias legado Codex da janela de inatividade |
| `FT_LLM_IDLE_TIMEOUT` | Janela sem progresso observável antes da sonda global; não conta heartbeat visual |
| `FT_CODEX_IDLE_TIMEOUT` | Override Codex da janela; default 480 segundos quando o node não sugere outra |
| `FT_LLM_IDLE_GRACE` | Janela final de confirmação quando stream, worktree e processo estão estagnados |
| `FT_CODEX_IDLE_GRACE` | Override Codex da confirmação; default 120 segundos, `0` desabilita |
| `FT_WORKTREE_PROGRESS_INTERVAL` | Intervalo das sondas de criação, remoção, alteração ou crescimento na worktree isolada |
| `FT_EXPLORE_TIMEOUT` | Alias legado da janela de inatividade para `ft explore`; não é teto wall-clock |
| `FT_LLM_MAX_WALL_TIMEOUT` | Teto wall-clock absoluto opt-in; ausente por default |
| `FT_CODEX_MAX_WALL_TIMEOUT` | Override opt-in do teto absoluto para Codex |
| `FT_CODEX_REASONING_EFFORT` | Override explícito do reasoning do Codex |
| `FT_OPENCODE_CONTEXT_LIMIT` / `FT_OPENCODE_CONTEXT_WINDOW` | Janela anunciada ao OpenCode |
| `FT_OPENCODE_OUTPUT_LIMIT` / `FT_OPENCODE_MAX_OUTPUT` | Limite de saída do OpenCode |
| `FT_OPENCODE_PROVIDER_TIMEOUT` / `FT_OPENCODE_TIMEOUT` | Timeout total do provider, em ms |
| `FT_OPENCODE_CHUNK_TIMEOUT` / `FT_OPENCODE_PROVIDER_CHUNK_TIMEOUT` | Timeout entre chunks, em ms |
| `FT_OPENCODE_HEADER_TIMEOUT` / `FT_OPENCODE_PROVIDER_HEADER_TIMEOUT` | Timeout de headers, em ms |
| `FT_OPENCODE_SANDBOX` | Sandbox `bwrap`; worktree read-only e outputs/write_scope graváveis |
| `FT_OPENCODE_DENY_EDIT_TOOLS` | Opt-in do modo legado sem ferramentas nativas de edição |
| `FT_OPENCODE_BUNDLE_MODE` | Opt-in de materialização por bundle XML |
| `FT_OPENCODE_SCRIPT_MODE` | Opt-in de materialização por script Bash |
| `FT_OPENCODE_DEBUG` | Logs detalhados da CLI OpenCode |
| `FT_FEATURE_SHARED_CACHE` | Habilita o cache compartilhado experimental do template feature |
| `FT_FEATURE_VALIDATION_HERMETIC` | Declara explicitamente que a validação feature é hermética; exigido pelo cache compartilhado |
| `FT_FEATURE_SHARED_CACHE_TTL_SECONDS` | TTL, em segundos, do cache compartilhado feature |
| `FT_FEATURE_EXTERNAL_DEPENDENCIES` | Dependências externas declaradas que entram no fingerprint feature |

## Governança de melhorias do processo

No `mvp-builder`, `ft.handoff.05.process_evolve` gera
`docs/process-improvements.md` e `docs/process-improvements.yml`. Cada achado
recebe ID `PI-NNN` e uma classificação:

- `local`: pertence ao fork local daquele projeto;
- `global_candidate`: merece revisão para o catálogo do engine;
- `rejected`: foi analisado e não deve ser aplicado.

Um candidato global precisa ser independente de domínio, configurável,
retrocompatível e verificado no ciclo. O ciclo nunca escreve no checkout do
engine. Depois de aplicar e testar a mudança global, registre a disposição:

```bash
ft process-candidates PI-001 --cycle cycle-07 \
  --status promoted \
  --reason "Aplicado e validado pela suíte do engine" \
  --reference "commit abc123 templates/mvp-builder/process.yml"

ft process-candidates PI-002 --cycle cycle-07 \
  --status deferred --reason "Precisa de outro ciclo real"
```

O close recusa candidatos globais pendentes. Os relatórios e decisões são
arquivados em `.ft/cycles/<cycle>/`.

## Evolução de processo

`ft evolve` melhora forks ou templates sem avançar nodes. Como pode haver vários
ciclos, forneça explicitamente `--cycle` quando quiser usar evidências de uma
execução:

```bash
ft evolve --project --cycle cycle-07
ft evolve --global --cycle cycle-07
ft evolve "reduzir retries no build" --project --cycle cycle-07
ft evolve --project --cycle cycle-07 --dry-run
```

O playbook roda em workspace descartável sob `$FT_HOME`, valida todo
`process.yml` staged e mostra o diff antes da aplicação. Mudanças no fork local
afetam somente ciclos futuros; promoção global continua sendo decisão explícita
do mantenedor.

## Formato do YAML de processo

```yaml
id: meu_processo
version: "1.0.0"
title: "Meu Processo"

# A política de entrada é validada antes da criação do ciclo.
input_policy:
  required: true
  destination: docs/feature-request.md
  prompt: "Descreva a demanda a implementar"

artifact_policy:
  canonical: [docs/PRD.md, docs/PROJECT_BACKLOG.md, docs/FEATURES.md]
  cycle: [docs/task_list.md, docs/acceptance-report.md, docs/handoff.md]

nodes:
  - id: step.01.discovery
    type: discovery
    title: "Capturar requisitos"
    executor: llm_coach
    sprint: sprint-01
    outputs:
      - docs/requisitos.md
    requires_approval: true
    validators:
      - file_exists: docs/requisitos.md
      - min_lines: 20
      - has_sections: [Problema, Solucao]
    next: step.02.prd

  - id: step.02.prd
    type: document
    title: "Escrever PRD"
    executor: llm_coach
    expert: marketing_analyst
    sprint: sprint-01
    outputs: [docs/PRD.md]
    validators:
      - file_exists: docs/PRD.md
      - min_user_stories: 3
    next: gate.01

  - id: gate.01
    type: gate
    title: "Gate de qualidade"
    executor: python
    sprint: sprint-01
    validators:
      - file_exists: docs/PRD.md
      - tests_pass: true
    next: step.end

  - id: step.end
    type: end
    title: "Processo concluído"
```

### Experts

Nodes LLM podem carregar um perfil especialista versionado junto do template.
Crie `templates/<T>/experts/<id>.md` com frontmatter e corpo de prompt:

```markdown
---
id: marketing_analyst
name: Marketing Analyst
description: Analisa público, posicionamento e mensagem com base em evidências.
version: 2
tags: [marketing, strategy]
---
Classifique afirmações como FATO, INFERÊNCIA ou HIPÓTESE. Fatos exigem fonte,
data e confiança; lacunas relevantes viram experimentos mensuráveis.
```

No `process.yml`, use `expert: marketing_analyst`. O bundle inteiro é copiado
para `.ft/process/<T>/`, portanto o expert fica pinado no fork local e nos
worktrees dos ciclos. O perfil especializa o prompt, mas não altera executor,
modelo, ferramentas, escopo de escrita ou validators. Definição ausente,
frontmatter inválido, id diferente do nome do arquivo, symlink ou uso em node
não-LLM são recusados antes da execução.

Mudança comportamental no corpo exige bump de `version`. A delegação registra
id, versão e SHA-256 no run log; o prompt recebe apenas identidade descritiva,
instruções e, em reviews, a baseline exata do ciclo.

Liste os perfis disponíveis no fork local (ou no catálogo global antes da
primeira materialização) com:

```bash
ft experts --template <T>
ft experts --template <T> --json
```

## Regras de design de processo

O YAML é pura orquestração. Ele define:

- sequência e transições;
- executor de cada node;
- validadores determinísticos;
- política de entrada e de artefatos;
- referências a arquivos que o LLM deve ler.

Ele não deve hardcodar design, regras de negócio, tech stack, nome de produto ou
contexto de um projeto. Essa especificidade fica em fontes visíveis como:

| Artefato | Conteúdo |
|---|---|
| `docs/PRD.md` | visão, user stories e requisitos |
| `docs/PROJECT_BACKLOG.md` | mudanças desejadas e decisões |
| `docs/FEATURES.md` | capacidades entregues e evidências |
| `docs/ui_criteria.md` | telas, componentes e estados |
| `docs/TECH_STACK.md` | frameworks, linguagens e dependências |
| `.ft/cycles/<cycle>/task_list.md` | quebra técnica arquivada |

Prompts referenciam caminhos em vez de duplicar conteúdo:

```yaml
prompt: |
  Implemente a interface do projeto.
  Leia obrigatoriamente docs/PRD.md, docs/TECH_STACK.md e
  docs/ui_criteria.md. Siga os contratos dessas fontes.
```

Hotspots legítimos incluem hooks de shell (`env_setup`, `on_init`), prompts que
referenciam arquivos e validadores genéricos. Um processo bem desenhado funciona
para qualquer projeto do mesmo tipo trocando apenas as fontes de conhecimento.

## Tipos de node

| Tipo | Executor | Descrição |
|---|---|---|
| `discovery` | `llm_coach` | Captura hipótese/contexto; suporta hyper-mode |
| `document` | `llm_coach` | Produz documento Markdown |
| `build` | `llm_coder` | Implementa código |
| `test_red` | `llm_coder` | Escreve teste que deve falhar |
| `test_green` | `llm_coder` | Implementa para o teste passar |
| `refactor` | `llm_coder` | Refatora mantendo testes verdes |
| `gate` | `python` | Validação pura Python |
| `human_gate` | humano/LLM | Decisão explícita de stakeholder |
| `decision` | `python` | Branch condicional pelo state |
| `review` | `llm_coder` | Veredicto estruturado |
| `end` | — | Marca conclusão |

## Validadores disponíveis

### Artefatos

| Validador | Uso | Descrição |
|---|---|---|
| `file_exists` | `file_exists: path/to/file.md` | Arquivo existe |
| `min_lines` | `min_lines: 20` | Mínimo de linhas no primeiro output |
| `has_sections` | `has_sections: [A, B]` | Seções presentes |
| `min_user_stories` | `min_user_stories: 3` | Mínimo de histórias `### US-` |
| `demand_coverage` | mapa de paths | Cobertura determinística da demanda |
| `project_backlog_valid` | path do backlog | IDs, prioridade e status válidos |
| `project_contract_valid` | `.ft/project.yml` | Schema, objetivo, fase e DoD válidos |
| `task_list_references_backlog` | paths | Task list referencia backlog |
| `backlog_pending_decisions` | path | P0/P1 sem decisão são recusados |
| `backlog_referenced_decisions` | paths/campo | Valida PBs selecionados pelo ciclo |
| `features_catalog_valid` | paths | Catálogo e origens entregues válidos |
| `implemented_backlog_covered_by_features` | paths | Entregas têm `FEAT-*` correspondente |

`command_succeeds` aceita `command`, `timeout` e `resume_command`. O alternativo
é usado ao recuperar uma delegação órfã; se falhar, o comando completo roda uma
única vez:

```yaml
validators:
  - command_succeeds:
      command: bash scripts/product.sh full --record docs/validation.json
      resume_command: bash scripts/product.sh verify docs/validation.json
      timeout: 300
```

Validadores são agregados por padrão. Para gates com checks caros, o node pode
parar na primeira falha; um validator isolado também pode interromper a lista:

```yaml
validation_mode: fail_fast
validators:
  - file_exists: docs/contract.md
  - command_succeeds:
      command: make build
      stop_on_failure: true
  - command_succeeds: make test
```

Use `aggregate` quando o diagnóstico conjunto tiver mais valor que a latência.
`fail_fast` é decisão do node, não uma política global implícita.

Todas as delegações LLM — nodes, helpers auxiliares e `ft explore` —, em todos
os templates e providers, obedecem à política global de produtividade da
engine; o processo particular não implementa nem desliga essa semântica.
`llm_timeout_seconds` é uma janela de inatividade que dispara uma sonda, não um
deadline: eventos reais do stream, criação/remoção/alteração/crescimento no
conjunto de arquivos versionados ou novos não ignorados de toda a worktree
isolada e progressão da árvore de processos renovam a lease quantas vezes forem
necessárias. Caches ignorados não são percorridos; a atividade de seus comandos
continua observável por stream e CPU/I/O. `llm_episode_budget_seconds` permanece
como meta de telemetria; somente `llm_episode_max_calls` limita estruturalmente
novas chamadas:

```yaml
llm_timeout_seconds: 900
llm_episode: implementation
llm_episode_budget_seconds: 1800
llm_episode_max_calls: 2
```

Um decision pode iniciar novo episódio apenas para rejeições semânticas
declaradas:

```yaml
episode_restart:
  implementation: implementation
  scope: implementation
```

Ultrapassar a meta temporal registra telemetria, mas não interrompe uma chamada
produtiva nem impede a próxima. Um teto wall-clock só existe quando
`FT_LLM_MAX_WALL_TIMEOUT` ou o override do provider é definido explicitamente.

### Testes, código e gates

| Grupo | Validadores |
|---|---|
| Testes | `tests_pass`, `tests_fail`, `coverage_min`, `coverage_per_file`, `tests_exist` |
| Código | `lint_clean`, `format_check`, `no_todo_fixme` |
| Gates | `gate_delivery`, `gate_smoke`, `gate_mvp` |
| Review | `no_large_files`, `no_print_statements`, `changed_files_have_tests` |

## TDD loop

```yaml
- id: tdd.red
  type: test_red
  executor: llm_coder
  outputs: [tests/test_feature.py]
  validators: [{tests_fail: true}]
  next: tdd.green

- id: tdd.green
  type: test_green
  executor: llm_coder
  outputs: [src/feature.py]
  validators: [{tests_pass: true}]
  next: tdd.refactor

- id: tdd.refactor
  type: refactor
  executor: llm_coder
  outputs: [src/feature.py]
  validators: [{tests_pass: true}, {lint_clean: true}]
  next: gate.delivery
```

O engine faz auto-commit após PASS: `red:` para testes, `green:` para
implementação e `refactor:` para refatoração.

## Sprint workflow

```bash
ft run . --template mvp-builder
ft continue --cycle cycle-01 --sprint
ft approve --cycle cycle-01
ft continue --cycle cycle-01 --sprint

# ou
ft continue --cycle cycle-01 --auto
```

O sprint report é gerado ao cruzar boundaries. Quando documentos já existem em
`docs/`, o hyper-mode enriquece nodes `discovery` e `document` com esse contexto.

## Templates do catálogo

### `mdd`

Processo independente de definição e narrativa do produto. Aprova hipótese,
visão e PRD nessa ordem; somente depois deriva sumário executivo, pitch deck e
três alternativas de site com recomendação. Um gate estrutural e um human gate
validam o pacote textual. Depois da aprovação, nodes fixados em
`gpt-5.6-sol`/`max` geram 12 PNGs — um por slide — e um PNG vertical comprido do
protótipo da home com a ferramenta built-in `image_gen`. Receipts e um segundo
human gate visual antecedem `docs/mdd-handoff.md`. O `mvp-builder-fast` exige
hipótese e PRD como entrada e não executa mais esses nodes internamente.

```bash
ft run . --template mdd --request "Descrever problema e resultado" --auto
```

### `mvp-builder`

Processo construtor completo para projeto em `building`. Materialize e execute:

```bash
ft run . --template mvp-builder --auto
```

Com `--parallel`, nodes do mesmo `parallel_group` rodam em worktrees internas e
fazem fan-in validado. Somente nodes LLM com outputs disjuntos podem participar;
gates, decisions e dependências cruzadas são recusados pelo validador do grafo.
`--max-parallel N` limita os workers. Sem a flag, os grupos permanecem
sequenciais.

### `mvp-builder-fast`

Variante opt-in do processo completo, voltada a reduzir inicializações frias do
executor sem transferir o controle do grafo para o LLM:

```bash
ft run . --template mvp-builder-fast --auto --parallel --codex
ft run . --template mvp-builder-fast --route validation \
  --input feedback.md --auto --parallel --max-parallel 4 --codex
```

O engine gera um plano consultivo em
`state/llm_execution_plan.yml` usando a demanda e os documentos iniciais. O
plano contém somente a rota escolhida e seus loops focais; branches não
selecionadas não entram no prompt nem no denominador do progresso. Decisões já
executadas são persistidas, e planos legados que representavam o grafo inteiro
são regenerados automaticamente. O runner mantém uma conversa por sprint e cria
lanes isoladas para review e fan-out.
Claude usa `--session-id`/`--resume`; Codex captura o `thread_id` e usa
`codex exec resume`. Human gates encerram o processo do CLI, mas o identificador
fica no state para a retomada posterior. No `ft close`, o plano é preservado em
`.ft/cycles/<cycle>/llm-execution-plan.yml`.

O opt-in é declarado no processo:

```yaml
session_policy:
  mode: sprint
  providers: [claude, codex]
  initial_plan: internal
  parallel_strategy: fork
  recovery: rehydrate
```

O caminho de produto novo consolida planejamento, frontend, delivery, E2E e
handoff em macro-nodes. RED, GREEN e refactor continuam separados, e todos os
gates/validators Python permanecem autoritativos. Uma sessão expirada é
substituída uma vez por uma conversa reidratada com plano, estado e artefatos.
Processos sem `session_policy` continuam stateless.

Processos globais e forks locais contêm apenas regras gerais, válidas para
qualquer ciclo do projeto. Contexto efêmero — finding, critério, receipt, hash,
run, evidência, contagem ou instrução de recuperação — nunca é escrito em
`process.yml`; permanece no state e nos artefatos do ciclo. `ft fix` preserva e
retoma a sessão builder da sprint, injeta esse contexto e executa somente
`fix → revisão focal`. A lane reviewer continua independente. Falha de resume
usa a reidratação explícita declarada pela policy ou bloqueia; não cria uma
correção fria silenciosa e não reabre o workflow completo.

`--parallel` controla somente concorrência. Para continuar um builder já em
acabamento sem reabrir o processo integral, use `--route validation`; combine
as duas opções quando houver deltas independentes. Nessa rota, o usuário
continua fornecendo somente `--request` ou `--input` em linguagem natural. Uma
única chamada de planejamento produz
`docs/mvp-batch-plan.yml`; o YAML é um artefato interno, não uma entrada exigida
do usuário. O engine então:

1. valida o hash exato da demanda, cobertura, PBs, paths e dependências;
2. executa a foundation sequencial e cria um checkpoint;
3. calcula waves topológicas, limitado por `--max-parallel`;
4. roda cada lane em worktree e sessão próprias;
5. recusa qualquer diff fora do ownership declarado;
6. integra as waves numa branch privada e só aplica ao branch do ciclo quando
   todas passam;
7. faz review inicial e, se necessário, corrige e audita somente o fix;
8. roda a regressão completa uma única vez depois que o delta focal está verde;
9. antes do aceite, valida que toda capacidade de UI do escopo é alcançável por
   entradas e controles visíveis do produto, sem aceitar rota interna, deep link
   de debug, catálogo técnico ou montagem isolada como prova.
10. aguarda o aceite real; rejeição volta somente ao fix e à auditoria do delta;
11. após o aceite, reconcilia apenas backlog, features, tarefas e DoD afetados e
   encerra o ciclo, sem reabrir discovery, planejamento global, delivery ou
   handoff completo.

O planejamento produz um contrato estruturado de navegação ligado por SHA-256
ao escopo. Cada referência é classificada como `ui` ou `non_ui`; capacidades de
UI apontam para targets com política `public`, `entitled`, `contextual` ou
`first_launch`. O recibo E2E precisa cobrir todos os targets por jornadas
`production_ui`. Targets condicionados exigem prova positiva para uma identidade
elegível e negativa para uma identidade comum. Os validadores genéricos
`navigation_contract_valid` e `navigation_reachability` não assumem domínio,
framework, browser, sistema operacional ou dispositivo.

#### Perfis de validação multiplataforma

Validação de plataforma é uma capacidade global da engine, não um processo
Android/iOS/desktop separado e nem um fork completo do builder. O catálogo pode
ser inspecionado sem projeto:

```bash
ft validation-profiles
ft validation-profiles --json
```

Ele contém quatro perfis: `android`, `ios`, `web` e `desktop`. iPhone e iPad são
targets físicos de `ios`, não perfis próprios. Cada target declara o ambiente,
os checks obrigatórios e um `make_target` estável. O contrato durável vive em
`.ft/project.yml`:

```yaml
validation:
  schema_version: 1
  mode: explicit
  matrix_path: docs/validation-matrix.yml
  report_path: docs/platform-validation-report.yml
  evidence_root: docs/evidence/platform-validation
  test_identity:
    policy: required
    path: docs/test-identity.json
  platforms:
    android:
      targets:
        emulator: {required: true}
        physical: {required: true}
    ios:
      targets:
        simulator: {required: false}
    web:
      targets:
        desktop_browser: {required: true}
        mobile_browser: {required: true}
    desktop:
      targets:
        windows: {required: false}
        macos: {required: false}
        linux: {required: true}
```

`mode: automatic` detecta manifests de código e usa defaults conservadores;
`mode: explicit` é obrigatório quando a stack/intenção já é conhecida.
`mode: disabled` só serve para produto sem nenhuma dessas superfícies e exige
`reason`. O planner do `mvp-builder-fast` reconcilia a seleção depois de definir
a stack. Para materializar a resolução determinística no candidato corrente:

```bash
ft validation-matrix .
```

O comando escreve `docs/validation-matrix.yml`. O processo executa somente os
targets selecionados e agrega `docs/platform-validation-report.yml`. Um target
obrigatório indisponível bloqueia; um target opcional indisponível pode usar
`SKIP` com motivo; uma falha observada continua `FAIL`. O receipt liga matriz,
candidato, ambiente, instalação e evidências por hash. Targets físicos exigem
artefato local/instalado idêntico e identificador opaco do aparelho; serial,
UDID, rede, PII e credenciais são recusados.

Os checks comuns incluem funcionalidade, estética, acessibilidade, navegação,
isolamento da massa e persistência. Os adaptadores acrescentam permissões,
back/rotação e insets no Android; signing, safe area e orientação no iOS;
responsividade/navegação por teclado no web; e instalação, resize e navegação
nativa no desktop. Fluxos autenticados usam identidade técnica sanitizada,
idempotente e resetável. Evidência precisa vir do candidato corrente pela UI de
produção; rota direta, preview, mock ou componente isolado não substituem a
jornada real.

No Android físico, o perfil inclui `artifact_install_reuse`. O runner compila
uma vez, compara os hashes local e instalado e só instala app/APK instrumental
quando o pacote estiver ausente ou o hash mudar. Subconjuntos focais seguintes
rodam sobre os pacotes já instalados com `adb shell am instrument` ou equivalente
que comprovadamente não reinstale; classes devem ser agregadas quando possível.
Se somente um pacote divergir, apenas ele é instalado; se app e APK instrumental
divergirem juntos, uma única sessão multipacote deve concentrar a confirmação OEM
quando o instalador suportar esse modo.
Isso evita repetir diálogos de instaladores OEM. Tap injetado não é evidência de
autorização: somente término bem-sucedido da instalação e hash observado contam.
Se o sistema exigir confirmação humana, o processo pede uma vez e aguarda, sem
alegar aceite automático. Antes disso, conclui toda validação independente e
estabiliza o candidato em ambiente não interativo; o aparelho físico não vira
loop de fix e a autorização fica concentrada no checkpoint final. Root,
desbloqueio de bootloader ou redução de segurança do aparelho exigem autorização
explícita e nunca são fallback do validador.

Todos os targets de UI contêm ainda o check obrigatório `mockup_watermark`.
Android, iOS, web e desktop devem inventariar cada tela, página ou janela
alcançável e provar, com screenshot real exclusivo, que o próprio produto
renderiza uma marca d'água discreta e legível com o identificador exato do
mockup correspondente (`S01`, `S02`, ...). O receipt registra
`discovered_screen_count`, telas mapeadas e `unmapped_screens`; inventário
incompleto, tela sem referência, ID divergente ou overlay inserido apenas pela
ferramenta de captura reprova o target.

O `mvp-builder-fast` possui um único ponto de composição em cada rota. Se não há
perfil ativo ele segue sem chamada de validação; se há, executa o fan-out lógico
e só libera o fan-in com `platform_validation_report` aprovado. Um finding volta
ao fix focal e obrigatoriamente repete essa auditoria, sem reiniciar discovery,
planejamento ou construção. `ft project-close` também avalia esse receipt como
parte do DoD quando a seção `validation` existe e resolve targets ativos.

O review combinado também produz um recibo YAML ligado ao plano por SHA-256,
com uma linha estruturada por requisito e findings acionáveis para cada falha.
`review_outcome_valid` exige consistência com o veredito Markdown;
`review_chain_approved` só libera a regressão quando o review original estiver
aprovado ou quando um fix review corrente cobrir e aprovar todos os findings.
O contrato público é finalizado antes do fan-out e permanece protegido durante
as lanes; um fix contratual precisa atualizar contrato, provedor, consumidor e
testes de paridade no mesmo delta.

No aceite, `SKIP` é permitido apenas para cenários fora do P0 e deve permanecer
documentado. Cenário P0 não executado, evidência principal mock/fallback ou
incompatibilidade entra em `p0_blockers`, que continua bloqueando o gate.

Falhas preservam branch, worktree, sessão e ledger em
`state/mvp-builder-batch.yml`. `ft status` e `ft status --full` mostram o modo
parallel, `max_parallel`, wave e estado do fan-out mesmo antes da criação do
ledger. Para cada lane, leem do plano persistido o ID, título e objetivo e os
combinam com status, tentativa, ação atual, paths e atividade de log. Planos
legados sem título ou objetivo continuam inspecionáveis com fallback explícito,
sem nova chamada LLM. As lanes ficam agrupadas por wave e usam um único glifo:
`▶` executando, `✓` chamada LLM concluída e ainda não integrada, `◆`
integrada, `✗` falhou e `○` aguardando. A fração de slots é rotulada como
alocação da wave; o total que está efetivamente ativo aparece separadamente em
`executando`. Um `turn.completed` terminal encerra imediatamente a apresentação
de atividade da lane, inclusive em ledgers antigos, mas não a declara integrada:
o estado transitório `llm_completed` ainda aguarda validação e fan-in. O batch não
abre ciclos `feature-fast` filhos: lifecycle, histórico e `ft close` continuam
únicos.

Reviews são roteadas de forma fail-closed. `REJECTED`, `FAIL` e equivalentes
explícitos em português ou inglês, em qualquer output canônico declarado,
prevalecem sobre sinais de aprovação no Markdown, no recibo estruturado ou na
resposta do reviewer. Uma contradição nunca libera o próximo gate: segue o
`on_fail`/`pending_fix` do grafo ou a rota estruturada de rejeição declarada.

Enquanto o projeto estiver em `building`, um novo feedback de validação usa
novamente o construtor owner com `--route validation`. Se o hash da demanda e o plano
existente coincidirem, o planner é pre-seeded e não consome outra chamada LLM.
Uma foundation já compilada só é reutilizada quando o node opta explicitamente
por `allow_pre_seed: true` e todos os outputs e validators continuam verdes;
builds comuns permanecem não reutilizáveis por default.
Essa execução é continuação terminal do mesmo produto:
`validação → correção focal → auditoria do fix → regressão integrada → aceite`.

```bash
ft run . --template mvp-builder-fast \
  --input demanda.md \
  --route validation \
  --parallel --max-parallel 4 \
  --auto --codex gpt-5.6-sol --effort high
```

Os macro-nodes de planejamento também reconciliam `.ft/project.yml`, sem criar
uma chamada LLM extra. O human gate de arquitetura aprova o objetivo e o DoD
global. O fim do grafo significa “ciclo construtor concluído”; a entrega do
projeto continua dependendo de `ft project-close`.

### `feature`

Implementa uma capacidade em projeto já entregue (`maintenance`), com elucidação,
aprovação, implementação, validação, evidência, review e aceite:

```bash
ft run . --template feature --request "Adicionar busca por telefone" --codex
ft run . --template feature --input demanda.md --codex
```

Cada demanda deve citar exatamente um PB preexistente; ciclos simultâneos usam
PBs distintos. Para uma capacidade nova, o processo reserva o FEAT definitivo
sob lock curto. O state fixa path e digest do fork local.

Código/testes, validação completa, evidência referencial e review semântica são
nodes diferentes. A review produz rota estruturada (`approved`,
`implementation`, `evidence` ou `scope`) e o decision node invalida o progresso
posterior quando volta no grafo. A implementação usa lease global de
produtividade renovável e meta temporal de episódio; não há hard stop
wall-clock por default.

Baseline attestation e implementation receipt são separados. `ensure` verifica
primeiro o receipt local; cache compartilhado só existe como experimento opt-in
para validação declarada hermética. O reconcile propõe YAML, o engine valida os
IDs permitidos e aplica os documentos canônicos deterministicamente. Entradas
novas de changelog começam com `#FEAT`.

### `feature-fast`

Mantém os contratos, human gates, receipts e reconciliação segura do `feature`
em projeto já entregue,
com sessões persistentes e um caminho focal para correções de review:

```bash
ft run . --template feature-fast \
  --request "Adicionar busca por telefone" --codex gpt-5.6-sol --effort high
```

O engine cria um plano interno consultivo e mantém uma conversa por sprint.
`implement` e `evidence` retomam a mesma sessão de build; o node `review` usa
uma lane isolada; `reconcile` usa a sessão de aceite. Perda da conversa aciona
uma reidratação única. O grafo e os validators Python continuam autoritativos.

Quando a review rejeita um defeito de implementação, o processo não regressa
para toda a sequência `implement → evidence → review`. Ele congela a review e
seus F-*, executa um fix focal, renova deterministicamente o receipt completo e
audita somente o delta. Mudança de contrato, AC ou arquivo fora do workset força
fallback para evidência e review completas.

Desde a versão 1.2, um ciclo claro aceita no máximo 6 ACs; escopos maiores são
divididos em fatias verticais de 4–6 ACs. O delta passa por pré-review semântica
antes do build/test completo. Um manifesto de impacto expande o workset com
arquivos e testes relacionados, gera IDs imutáveis de review e decide
`rerun|reuse` para receipts adicionais conforme seus paths declarados. Assim,
um ensaio físico só é repetido quando uma dependência física realmente mudou.

### `bug`

Correção focal de manutenção para defeito reproduzível. Exige diagnóstico, teste RED,
correção mínima, o mesmo teste GREEN, build/test e aceite:

```bash
ft run . --template bug --request "Terminal duplica o eco do input" --codex
```

Use `feature` quando houver comportamento novo, contrato, auth/security,
migração, dados, dependência, infraestrutura ou mudança transversal. Entradas de
changelog começam com `#BUG`.

### `bug-fast`

Mantém em `maintenance` a reprodução RED→GREEN e a suíte completa do `bug`, mas troca a chamada
documental final por reconciliação Python e acrescenta uma review independente:

```bash
ft run . --template bug-fast \
  --request "Terminal duplica o eco do input" \
  --codex gpt-5.6-sol --effort high
```

O caminho feliz usa duas chamadas LLM: uma sessão builder e uma lane reviewer.
Não há planejamento LLM nem reconciliação LLM. A review confirma o receipt sem
repetir build/test. Se reprovar, o processo ancora B-*, reutiliza a sessão do
builder para o fix e audita somente o delta; path novo força review completa e
mudança de contrato/capacidade bloqueia o ciclo com instrução para
`feature-fast`. O aceite humano continua obrigatório.

Para avaliar performance, separe o wall clock da espera humana e compare
chamadas, tempo ativo de LLM, retries/timeouts e duração da validação
determinística no relatório do ciclo.

### `tweak`

Mudança pequena e de baixo risco, com implementação única, check focal, build
curto e aceite:

```bash
ft run . --template tweak --request "Mudar o botão Salvar para azul" --codex
```

Não executa discovery completo, review independente ou E2E full. Limites de
arquivos, linhas, patch e áreas de risco impedem que um ajuste pequeno vire uma
mudança transversal; nesses casos, abra outro ciclo com `feature`.

### Outros

`base` fornece grafo mínimo; `ft-ui-prototype` cobre prototipagem de UI;
`fastfy` adota repositórios legados; `material_design_pwa` aplica M3 e PWA a
uma UI existente; `fast-track-v2` preserva o processo histórico. Todos usam o
mesmo comando de run e viram forks locais. O bundle `evolve_process` é interno
ao comando `ft evolve`, não abre ciclo por `ft run`. `symlabs` e `tecnospeed`
são templates `kind: init` (ambiente por organização) — pertencem ao
`ft init --template`, não ao run.

## Encerramento e artefatos

```bash
ft close --cycle cycle-07 --merge full
ft close --cycle cycle-08 --merge docs
ft close --cycle cycle-09 --merge selective --merge-paths "src/a tests/a"
ft close --cycle cycle-10 --keep-worktree
```

O lock de close serializa merges. Se somente CHANGELOG, PROJECT_BACKLOG e
FEATURES conflitarem de forma aditiva e inequívoca, o resolvedor canônico os
reconcilia; qualquer conflito ambíguo ou fora desses documentos permanece
manual. A política do processo mantém artefatos canônicos em `docs/` e arquiva
relatórios específicos em `.ft/cycles/<cycle>/`. Estado e logs brutos nunca são
mergeados.

Depois do merge, reinstale dependências alteradas, limpe caches antigos,
reinicie serviços no checkout promovido, confira as rotas principais e exerça a
capacidade entregue antes de demonstrá-la.

Para ciclos construtores, atualize e commite backlog/gates após essa verificação,
rode `ft project-status` e só então `ft project-close`. Um receipt BLOCKED mantém
o projeto em `building` e exige outro ciclo do mesmo construtor; não migre o
trabalho pendente para templates de manutenção.

## Migração V2 → V3

Layouts com `process/`, bundle flat ou manifesto anterior exigem migração
explícita e sem runtime em mutação:

```bash
ft migrate-layout . --dry-run
ft migrate-layout .
```

O preflight valida grafo, contenção, colisões e symlinks antes de escrever. A
migração preserva todos os processos e ciclos, converte o manifesto para schema
V3 e elimina o seletor default sem promover substituto. Históricos em
`docs/archive/` são importados; runtime legado recebe backup inativo em
`$FT_HOME/migrations/`. Conteúdo em `.ft/cycles/` nunca é reescrito.

Use `ft init --check`/`--fix` para saúde de um workspace já V3; reparo não é
migração.

## Estrutura do engine e do projeto

```text
ft/
  engine/
    graph.py          # YAML → DAG
    state.py          # escrita do engine_state.yml
    runner.py         # loop determinístico
    delegate.py       # executores LLM
    git_ops.py        # commits após PASS
    parallel.py       # fan-out/fan-in de nodes
    validators/
  cli/main.py         # CLI pública
  project/            # bootstrap, diagnóstico e reparo
  templates/          # resolução/materialização local-first
  runs/               # locks, alocação e seleção de ciclos

$FT_HOME/worktrees/<projeto>/<cycle>/
  state/engine_state.yml

<projeto>/.ft/
  manifest.yml
  process/<template>/process.yml
  cycles/<cycle>/

templates/<template>/process.yml
```

## Troubleshooting

**`ft: command not found`**

```bash
pip install -e .
# ou
python -m ft.cli.main
```

**Workspace inconsistente**

```bash
ft init . --check
ft init . --fix
```

**Comando ambíguo**

```bash
ft runs
ft status --cycle <id>
```

**BLOCKED após validação**

```bash
ft status --cycle <id>
ft retry --cycle <id>
```

**Artefato rejeitado**

```bash
ft reject "feedback específico" --cycle <id>
ft reject "motivo" --no-retry --cycle <id>
```

**LLM não encontrado**

```bash
claude --version
codex --version
gemini --version
opencode --version
```
