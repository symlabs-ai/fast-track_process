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

Há três níveis independentes:

1. `ft init [dir]` prepara a base comum e saudável do repositório: Git com HEAD,
   `.ft/`, manifesto V3, contrato conservador `.ft/project.yml`, ignores e este
   playbook. Ele não escolhe nem copia template de processo e não cria `docs/`
   ou `src/` de produto. Opcionalmente, `--template` encadeia um template de
   inicialização (`kind: init`).
2. `ft run <dir> --template <T>` seleciona, materializa e executa um template em
   um novo ciclo isolado. Não existe processo principal ou default.
3. O projeto fica em `building` até `ft project-close` provar o Definition of
   Done global e registrar um receipt READY. `ft close` encerra apenas um ciclo;
   não declara o produto entregue.

```text
ft init meu-projeto                      # base comum, sem processo
  → adicionar/commitar conhecimento      # fontes do projeto, se necessárias
ft run . --template mdd --request ...    # definição + narrativa em ciclo próprio
ft close --cycle <id-mdd>                # promover conhecimento aprovado
ft run . --template mvp-builder --auto   # ciclo A
ft run . --template tweak --request ...  # ciclo B, pode coexistir com A
  → status/graph/approve/reject/fix       # selecionar ciclo se houver ambiguidade
ft close --cycle <id>                     # merge e arquivamento do ciclo
ft project-status                         # avaliar o DoD global
ft project-close                          # building → maintenance, somente READY
```

Cada ciclo roda em worktree externa em
`$FT_HOME/worktrees/<projeto>/<cycle>/`. O checkout principal permanece limpo
até o `ft close`. Ciclos são descartáveis e múltiplos ciclos podem estar ativos
ao mesmo tempo, inclusive usando templates diferentes.

O objetivo durável, o marco de entrega e o DoD ficam em `.ft/project.yml`.
P0/P1 `blocked`, `deferred`, `planned` ou apenas “decidido” continuam pendentes:
somente `done`/`accepted` com evidência satisfazem o fechamento. Gates adicionais
apontam para campos verificáveis de arquivos JSON/YAML. O receipt versionado
fica em `.ft/project-readiness.yml`.

O mesmo contrato declara `validation`: a matriz global de Android, iOS, web e
desktop aplicável ao produto. Use `ft validation-profiles` para inspecionar o
catálogo e `ft validation-matrix .` para materializar a seleção corrente. iPhone
e iPad são targets físicos de iOS. Target obrigatório sem receipt aprovado
bloqueia tanto o processo quanto `ft project-close`; não crie processos móveis
separados nem aceite simulador como substituto silencioso de aparelho físico.
Todo target de UI inclui o check `mockup_watermark`: cada tela, página ou janela
do produto deve renderizar uma marca d'água discreta com o identificador exato
do mockup correspondente (`S01`, `S02`, ...). O receipt inventaria todas as
telas e exige screenshot real por tela; overlay da captura, referência genérica,
tela sem mockup ou inventário incompleto reprova a validação.

## Manual de orquestração de modelos

Ao ser perguntado sobre como orquestrar, escolher, trocar ou medir modelos e
efforts em um processo FT, use e informe o manual
[`docs/ft_model_orchestration.md`](docs/ft_model_orchestration.md). Ele é a
política experimental obrigatória: exige roteamento por node, probe de
capabilities no ambiente alvo, escalonamento por evidência e comparação de
custo/eficiência com a telemetria real do ciclo. Não responda com preferência
genérica de modelo nem altere a rota silenciosamente.

### Roteamento objetivo do Laguna no PGX

Quando `ft llm-capabilities --json` confirmar os cinco perfis OpenCode no
ambiente executor, aplique esta política:

- `pgx/laguna-2k`: discovery, inventário, tarefas mecânicas, uso intensivo de
  tools e respostas curtas;
- `pgx/laguna-4k`: implementação curta e delimitada;
- `pgx/laguna-8k`: implementação comum e rota padrão do ciclo;
- `pgx/laguna-16k`: revisão, arquitetura e artefatos que comprovadamente
  exijam mais de 8K tokens de saída;
- `pgx/laguna-32k`: geração excepcional e manual, nunca rota default.

O sufixo indica a reserva máxima de saída, não uma diferença de qualidade do
modelo. Os perfis compartilham a mesma janela total; reservar mais saída reduz
o espaço útil de prompt. Fixe a escolha por node em `llm_model`. Retry preserva
a rota. Escalone `2K → 4K → 8K → 16K → 32K` somente na fronteira de um novo
node ou episódio, após truncamento, validação incompleta ou complexidade
comprovada. Se o perfil não estiver disponível, bloqueie: não faça fallback
silencioso.

## 0. Inicializar ou diagnosticar o repositório

```bash
ft init meu-projeto
cd meu-projeto

ft init --check   # diagnóstico somente leitura
ft init           # repetição idempotente quando o ambiente está saudável
ft init --fix     # reparo explícito e conservador
```

`ft init` aceita o diretório como argumento opcional. Ele não oferece seleção de
processo; `--template` aceita somente um template `kind: init` para provisionar
o ambiente depois da base comum. Ao concluir, o projeto possui repositório Git
com HEAD e a base comum do Fast Track. Uma repetição saudável não altera
arquivos.

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

- produto novo com `mvp-builder-fast`: rode e encerre primeiro o template `mdd`,
  que produz hipótese, visão, PRD, sumário executivo, pitch deck, proposta de
  site, um PNG por slide, um PNG vertical do protótipo do site e handoff. Em
  seguida, o builder gera e submete à revisão humana os protótipos raster do
  produto, derivados de PRD, brief e critérios visuais, antes do planejamento
  técnico e do código;
- produto novo com `mvp-builder`: o próprio construtor ainda pode conduzir sua
  fase MDD; `docs/PRD.md` e `docs/TECH_STACK.md` existentes são reaproveitados;
- produto existente: preserve `docs/PROJECT_BACKLOG.md` como mudanças desejadas e
  `docs/FEATURES.md` como catálogo do que já foi entregue;
- antes da construção, revise `.ft/project.yml`: objetivo maior, alvo, escopo
  P0/P1 e gates que precisam estar verdes para o projeto estar entregue;
- demanda em arquivo: passe `--input demanda.md`;
- demanda curta: passe `--request "descrição objetiva"`;
- hipótese pronta, quando suportada: passe `--hipotese hipotese.md`.

> Faça commit antes de rodar. A worktree nasce de um commit, nunca de mudanças
> não commitadas.

## 2. Escolher e rodar qualquer template

`--template` é obrigatório em toda nova execução:

```bash
ft run . --template mvp-builder --auto
ft run . --template mdd --request "Definir o problema e o produto" --codex
ft run . --template feature --request "Adicionar busca por telefone" --codex
ft run . --template feature --input demanda.md --claude
ft run . --template bug --request "Terminal duplica o eco do input" --codex
ft run . --template bug-fast --request "Terminal duplica o eco do input" --codex
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
| `feature-fast` | Feature de manutenção com sessões persistentes e auditoria do delta |
| `bug-fast` | Bug de manutenção em duas chamadas LLM e fix focal |
| `tweak` | Mudança pequena e de baixo risco em projeto entregue |
| `mdd` | Definição de produto e pacote executivo antes da construção |
| `mvp-builder-fast` | Construtor rápido: protótipos raster do produto após o PRD, plano interno, sessões persistentes e macro-nodes |
| `fastfy` | Adoção de repositório legado na base canônica Fast Track |
| `material_design_pwa` | Evolução de UI existente para Material Design 3 e PWA |

Templates de inicialização (`kind: init`) — usados por `ft init --template`,
recusados pelo run:

| Template | Uso |
|---|---|
| `init-default` | Base de todo projeto: git, .gitignore, .env.example, commit inicial |
| `symlabs` | Ambiente da org Symlabs: Poetry/src, .env, CLAUDE.md, registro no SymGateway |
| `tecnospeed` | Ambiente da org Tecnospeed; exige credenciais locais emitidas pelo DevOps |

Cada template declara sua política de entrada. `--request` e `--input` são
formas genéricas; o engine recusa combinações ausentes ou incompatíveis antes de
criar o ciclo.

Também declara seu papel no projeto. `mvp-builder` e `mvp-builder-fast` aceitam
somente `building`; o primeiro construtor usado passa a ser o owner do objetivo.
`feature`, `feature-fast`, `bug`, `bug-fast` e `tweak` aceitam somente
`maintenance`, com receipt READY íntegro. Isso impede usar manutenção para
completar silenciosamente um projeto que o construtor deixou pendente.

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

`--parallel` em `ft run` continua sendo apenas paralelismo dentro de um único
ciclo; ele não escolhe uma rota. Nos processos comuns ele honra
`parallel_group`. No `mvp-builder-fast`, selecione a continuação terminal com
`--route validation`; combine `--parallel` quando a demanda em linguagem natural
dever virar foundation sequencial e lanes isoladas:

```bash
ft run . --template mvp-builder-fast --route validation \
  --input feedback.md --parallel --max-parallel 4 --auto
```

O engine calcula as waves, valida o diff real e faz um único fan-in/close. O
plano e o progresso incluem somente a branch escolhida. Ao final, a rota é
`validação → correção focal → auditoria somente do fix → regressão integrada →
aceite`; uma rejeição não retorna a MDD, planning, delivery ou handoff completo.
Um plano existente com o mesmo hash é reutilizado sem nova chamada LLM. Isso é
diferente de iniciar vários ciclos independentes.

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
ft runs --done                         # DURAÇÃO LLM acumulada, inclusive em curso
ft runs --done-detailed                # steps cronológicos com duração ativa de LLM
ft status --cycle <id>
ft status --cycle <id> --full
ft status --cycle <id> --report
ft status --cycle <id> --watch 60       # tela fixa, sem acumular linhas
ft graph --cycle <id>
ft log --cycle <id>
```

Enquanto uma delegação LLM estiver ativa, `ft status` atualiza o bloco
`TRABALHO EM ANDAMENTO` com a ação corrente, a evolução observável e os sinais
de produtividade. O resumo é derivado do log e da supervisão da worktree,
omite argumentos sensíveis e não interfere na execução. Logs novos mantêm um
sidecar só de hashes/timestamps, portanto “nesta ação há” usa o instante real
sem copiar prompt, comando ou segredo. O cabeçalho informa o caminho absoluto da
worktree ativa para facilitar inspeção direta. O rodapé é sempre uma linha
sanitizada da atividade mais recente do log LLM; no modo `--watch`, ela é
substituída no lugar como um tail fixo.

`--watch [SEGUNDOS]` redesenha o status no mesmo lugar em um terminal
interativo; sem valor, o intervalo é 60 segundos. O modo preserva o buffer normal
para permitir rolagem, mostra o horário da última atualização e encerra ao
receber `Ctrl+C`.

`--done-detailed` implica `--done`. A seção de cada ciclo mostra toda execução
real de node na ordem em que começou, sem colapsar retries, correções ou loops,
e atribui duração e tokens ao respectivo step. Históricos anteriores ao tracing
estruturado usam o `cycle-log.md` e exibem `—` onde não existe medição confiável.
A última linha de cada tabela totaliza execuções, tentativas, duração e tokens;
a última linha de toda a seção é o `TOTAL GERAL` dos ciclos exibidos. Qualquer
parcela sem telemetria deixa a métrica consolidada como `—`, sem soma parcial.
Em `ft status --full`, PASS aparece em azul, pending/não executado em branco,
SKIPPED em cinza e erro em vermelho; ativo e gate mantêm destaque próprio.

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

Todo human gate e todo node com `requires_approval` deve ser apresentado como
um pacote de decisão, nunca apenas como “rode `ft approve`”. A engine mostra,
na entrada do gate e em `ft status`, sete blocos obrigatórios: decisão, por que
agora, onde avaliar (URL e artefatos existentes), checklist verificável, limites
conhecidos, efeito de aprovar e efeito de rejeitar. Forks antigos recebem um
fallback derivado do grafo, do estado e das evidências recentes.

Templates podem tornar o pacote específico sem implementar UI própria:

```yaml
decision_context:
  decision: "Qual decisão o stakeholder está tomando?"
  why_now: "Quais verificações terminaram e por que o processo parou aqui?"
  review_paths: [docs/PRD.md, docs/acceptance-report.md]
  checklist:
    - "Ação observável e critério de aprovação."
  limitations:
    - "O que esta aprovação não cobre."
  approve_effect: "Próxima etapa e mudança de estado."
  reject_effect: "Rota de correção e condição para reapresentar o gate."
```

`review_paths` aceita somente paths relativos e seguros; itens inexistentes não
são apresentados como prova. Uma rejeição deve informar onde ocorreu, passos,
resultado esperado e observado. Se a URL ou a evidência necessária estiver
ausente ou inacessível, o humano ainda não tem base para aprovar.

Além do pacote técnico apresentado pelo próprio `ft`, o agente condutor deve
traduzir cada human gate no chat para uma pergunta curta, concreta e fácil de
entender. A mensagem deve explicar em linguagem simples qual decisão precisa
ser tomada, resumir as opções e seus impactos e terminar com uma pergunta
direta, como `Você aprova ...?`, indicando também como rejeitar ou pedir
ajustes. A saída do engine complementa essa pergunta, mas não a substitui. O
agente deve decompor gates com vários achados, critérios ou decisões e explicar
sucintamente cada item em separado: qual é o problema, qual risco ele cria e o
que será feito para resolvê-lo, sempre em linguagem não técnica. Não esconda
decisões diferentes sob um resumo único nem presuma que termos internos como
`receipt`, `allowlist`, fingerprint, lane ou terminalidade sejam
autoexplicativos. Cada item que possa ser aceito ou rejeitado independentemente
deve terminar em sua própria pergunta numerada e respondível. Ao final, permita
ao usuário `aprovar todas` ou indicar os números que deseja rejeitar ou ajustar;
uma pergunta agregada não substitui essas perguntas individuais. O
agente não pode aprovar, rejeitar nem usar `--bypass-human-gates` sem uma
resposta explícita do usuário à pergunta apresentada no chat.

Quando só há um ciclo aplicável, `--cycle` pode ser omitido. Rejeições devem ter
motivo objetivo porque o texto vira contexto do retry.

## 5. Bloqueios e recuperação

### Generalidade do processo e contexto do ciclo

`process.yml`, tanto no catálogo global quanto no fork local do projeto,
descreve somente comportamento reutilizável por qualquer ciclo daquele projeto.
É proibido gravar no processo contexto de uma tentativa concreta, incluindo
IDs de finding ou critério, contagens PASS/FAIL, nomes de runs, hashes, paths de
evidência, instruções `RECOVERY` ou decisões tomadas durante um ciclo.

Esse contexto pertence exclusivamente ao state do ciclo, `cycle_memory`,
receipts/evidências focais e à instrução passada a `ft fix`. Uma correção
dirigida retoma a mesma sessão builder da sprint com todo o histórico anterior,
executa somente o fix e sua revisão focal e então segue o grafo. Ela não reseta
a sessão, não reescreve o processo e não volta ao workflow completo. Se o
provider não puder retomar a conversa, a engine deve declarar a reidratação ou
bloquear; nunca abrir silenciosamente uma correção sem contexto.

| Situação | Ação |
|---|---|
| node bloqueado, repetir igual | `ft retry --cycle <id>` |
| review em `pending_fix`, mas a causa externa mudou | `ft retry --cycle <id>` repete somente o review e descarta o encaminhamento ao fix |
| correção dirigida | `ft fix "o que corrigir" --cycle <id>` executa o fix e repete obrigatoriamente somente o review que encontrou o defeito |
| stakeholder rejeitou evidência em human gate | `ft reject "motivo" --auto --cycle <id>` executa `reject_next`, renova o review focal e retorna ao mesmo gate |
| descartar sem merge | `ft abort --cycle <id>` |
| cancelar com justificativa | `ft cancel "motivo" --cycle <id>` |

Leia `ft status --cycle <id>` antes de retentar. Smart retry detecta erros
idênticos e bloqueia cedo.

Revisão de fix não é opcional. A engine invalida apenas os receipts do fix e
do review afetado, preserva os demais nodes aprovados e impede que o human gate
reapareça antes da nova auditoria. A flag histórica `--audit-origin` ainda é
aceita por compatibilidade, mas esse comportamento já é o padrão obrigatório.
O pedido de correção e o finding original são injetados no review focal; em
mudança de UI, a revisão deve comprovar o resultado visual/físico solicitado,
não apenas presença de código ou testes verdes.
Quando o node de correção declara `fix_review`, a engine reabre somente a cadeia
linear `next → fix_review` e volta ao mesmo human gate. Em forks históricos sem
essa declaração, o review de origem é reutilizado com o prompt amplo suspenso:
a delegação audita somente o finding e seu veredito focal, sem refazer o fluxo
completo. Se não existir nenhuma rota segura de revisão, o fix é recusado.
Hashes citados no pedido identificam a baseline; o review deve auditar e
registrar o artefato corrente produzido pelo fix, salvo quando a igualdade de
hash tiver sido declarada explicitamente como requisito.

O fix não assume ownership dos relatórios, receipts ou capturas do review de
origem. Se o defeito for somente evidência ausente/desatualizada e o produto
corrente já cumprir o requisito, o fix registra um handoff sem inventar mudança
de código. Em seguida, o review focal atualiza somente seus próprios outputs e
as partes afetadas do recibo composto, preservando todo o restante e qualquer
evidência histórica. A engine recusa a aprovação se o review retornar APPROVED
mas deixar o output canônico inalterado ou ainda marcado como REJECTED; assim o
human gate nunca recebe um relatório velho depois de um fix aprovado.

Uma aprovação focal exige o recibo estruturado `focal_evidence` injetado pela
engine. Ele deve decompor o finding em claims e ligar cada uma a evidência
repo-local existente. Se o finding tratar de dados reais mostrados em UI, a
prova obrigatória é a jornada interface pública/fonte real → persistência →
leitura → tela física, com comparação campo a campo. Mock, fixture, preview ou
teste isolado de componente pode apoiar diagnóstico, mas nunca aprovar esse
tipo de finding. Omissão de campo citado ou evidência de fidelidade inferior
resulta em `EVIDENCE_FIDELITY_REJECTED` e retorna somente ao fix focal.
Token, senha e credencial de sessão nunca podem ser passados em argumento de
processo nem registrados em log/evidência; a prova deve reutilizar o estado
autenticado seguro do dispositivo sem exteriorizar o segredo.
Toda jornada autenticada deve começar com um usuário dedicado do agente. O
projeto fornece um seed idempotente, isolado e resetável que cria somente essa
identidade e o estado mínimo do percurso; credenciais vêm de secret store ou
arquivo protegido, nunca do Git. O preflight produz recibo sanitizado com uma
referência opaca, ambiente e estado `ready`, sem e-mail, telefone, token ou
senha. Ausência desse usuário é falha do setup do teste — nunca motivo para usar
conta pessoal do stakeholder, aceitar mock ou aprovar parcialmente o fluxo.
Quando o finding citar APK ou hash, a aprovação também exige igualdade entre o
SHA-256 do artefato local corrente, o hash declarado e o hash medido no
dispositivo, com saída sanitizada preservada no repositório.

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

Encerrar o ciclo não encerra o projeto. Se o DoD global continuar bloqueado,
rode outro ciclo do mesmo template construtor. Depois do último `ft close` e da
verificação pós-close, atualize e commite backlog/evidências e então:

```bash
ft project-status
ft project-close
```

`ft project-close` só muda `.ft/project.yml` de `building` para `maintenance`
quando todos os itens selecionados estão `done`/`accepted` com evidência, todos
os gates estruturados estão verdes, não há worktree de ciclo aberta e o checkout
está limpo. Caso contrário, persiste um receipt BLOCKED e lista os impedimentos.

Para iniciar deliberadamente um novo objetivo maior em produto já entregue:

```bash
ft project-reopen --reason "novo marco de produto" \
  --objective "Entregar ..." --target "v2"
```

A reabertura volta a `building`, invalida o receipt anterior e libera um novo
owner construtor. Não a use para feature/bug/tweak de manutenção.

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
| `FT_LLM_EXECUTOR_TIMEOUT` | Alias legado global da janela de inatividade; não limita wall-clock produtivo |
| `FT_CODEX_EXECUTOR_TIMEOUT` | Alias legado Codex da janela de inatividade |
| `FT_LLM_IDLE_TIMEOUT` | Janela global sem progresso observável antes da sonda de stream, worktree e processo |
| `FT_CODEX_IDLE_TIMEOUT` | Override Codex da janela de inatividade; default 480 segundos quando o node não sugere outra |
| `FT_LLM_IDLE_GRACE` | Janela final de confirmação após uma sonda sem produtividade |
| `FT_CODEX_IDLE_GRACE` | Override da confirmação Codex; default 120 segundos |
| `FT_WORKTREE_PROGRESS_INTERVAL` | Intervalo global das sondas de criação, remoção, alteração ou crescimento na worktree isolada |
| `FT_EXPLORE_TIMEOUT` | Alias legado da janela de inatividade para `ft explore`; não é teto wall-clock |
| `FT_LLM_MAX_WALL_TIMEOUT` | Teto wall-clock absoluto opt-in; ausente por default |
| `FT_CODEX_MAX_WALL_TIMEOUT` | Override opt-in do teto absoluto para Codex |
| `FT_OPENCODE_SANDBOX` | Sandbox de filesystem do OpenCode |
| `SYM_GATEWAY_PROJECT_KEY` / `SYM_GATEWAY_ADMIN_KEY` | Scripts SymGateway opt-in |

A supervisão de produtividade é uma política da engine e vale para nodes,
helpers de delegação e `ft explore`, em todos os templates e providers. Um
valor de timeout no node apenas sugere a janela de inatividade: stream real,
arquivos versionados ou novos não ignorados em qualquer ponto da worktree e
progressão de CPU/I/O/processos renovam essa janela indefinidamente. A engine só
interrompe após todos esses sinais permanecerem estagnados durante a janela e a
confirmação final.

## Referências

No projeto:

- catálogo local: `processes` em `.ft/manifest.yml`;
- objetivo, fase e DoD global: `.ft/project.yml`;
- último receipt determinístico: `.ft/project-readiness.yml`;
- processos versionados: `.ft/process/<template>/process.yml`;
- histórico versionado: `.ft/cycles/<cycle>/`;
- runtime externo: `$FT_HOME/worktrees/<projeto>/<cycle>/state/`.

No engine:

- guia completo: `docs/ft_engine_usage.md`;
- arquitetura: `docs/mvp-builder-architecture.md`;
- catálogo global: `templates/`.

<!-- symlabs-symgateway:start -->
## Symlabs — LLMs via SymGateway por default

Todo uso de OpenAI ou Anthropic neste repositório deve atravessar o
SymGateway, salvo a exceção FT explícita abaixo. Não crie bypass ad hoc.

- **Codex/OpenAI:** use o profile `symgateway-dev`, com
  `SYMGATEWAY_API_KEY` e `SYMGATEWAY_PROJECT_SLUG` carregados pelo ambiente do
  projeto.
- **Exceção de imagem built-in:** somente um node de processo Fast Track que
  declare `codex_auth: chatgpt` pode usar o provider OpenAI direto. A exceção
  cobre o node inteiro, deve exigir login ChatGPT e existe apenas para uma
  capability built-in requerida, como `$imagegen`/`image_gen`, indisponível em
  custom providers. Sem login ou capability, bloqueie; não improvise fallback.
- **Claude/Anthropic:** use `ANTHROPIC_BASE_URL` e `ANTHROPIC_API_KEY` definidos
  em `.claude/settings.local.json`. Não execute `claude auth login` e não use
  credencial ou endpoint direto da Anthropic.
- Segredos permanecem apenas em `.envrc.private` e
  `.claude/settings.local.json`, ambos ignorados pelo Git. Nunca copie chaves
  para arquivos versionados, argumentos de processo, logs ou documentação.
- Fora da exceção declarativa acima, se o roteamento do SymGateway estiver
  ausente ou indisponível, interrompa a execução e corrija a configuração.
<!-- symlabs-symgateway:end -->
