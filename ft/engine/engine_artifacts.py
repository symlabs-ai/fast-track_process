"""O que a engine escreve na árvore do projeto durante um ciclo.

Esta é a única declaração desse fato. Ela existia espalhada por sete lugares
— três validadores de template, o receipt do produto, o auto-commit, o
validador de paths e o supervisor — e as cópias divergiram exatamente como
cópias divergem: cada uma esqueceu um caso diferente, e o mesmo bug foi
corrigido três vezes em commits separados (`b8f389b`, `35cf015`, `7b7ac50`),
sempre por causa de `<projeto>_log.md`.

O sintoma era pior que o incômodo. Com o produto na raiz, o log que a engine
grava a cada transição de nó entrava na lane do produto: `impact_prepare`
registrava o fingerprint, o simples ato de registrar aquela passagem mudava o
conteúdo hasheado, e o gate seguinte se tornava inalcançável para sempre. Um
artefato da engine contado como produto trava o ciclo.

Duas armadilhas que as cópias antigas caíram e que esta não repete:

- `cycle-*` e `*.log` eram palpites sobre o nome do log. A engine escreve
  `<projeto>_log.md`, então os palpites nunca casaram e o filtro não filtrava
  nada. Aqui a regra é o sufixo real.
- Nem toda cópia lembrava dos arquivos de serviço (`.serve.pid`, `.serve_url`
  e afins), que a engine cria ao subir o produto para o stakeholder olhar.
  Num template eles contavam contra o orçamento de arquivos da mudança.

**Este módulo é copiado verbatim** para `scripts/engine_artifacts.py` de cada
template, porque um bundle materializado em `.ft/process/` não consegue
importar `ft.engine`. As cópias são geradas, não mantidas à mão:
`tests/test_engine_artifacts_sync.py` compara byte a byte e falha na
divergência. Só depende da stdlib por causa disso.

O que este módulo NÃO responde: se um caminho é produto. `docs/`, `CHANGELOG.md`
e os artefatos do próprio ciclo são escritos pelo LLM ou pelo processo, não
pela engine, e cada template decide sobre eles com a sua própria regra — as
perguntas são diferentes e juntá-las seria trocar duplicação por confusão.
"""

from __future__ import annotations

from pathlib import PurePosixPath

#: Diretórios de topo inteiramente da engine.
ENGINE_ROOT_DIRS = ("state", "runs")

#: Subdiretórios de `.ft/` que são runtime. `.ft/process/` e `.ft/cycles/` não
#: estão aqui: o primeiro é o bundle executável, o segundo é história durável.
ENGINE_FT_SUBDIRS = ("runtime", "cache", "tmp", "logs")

#: Diretórios temporários que a engine cria em qualquer profundidade.
ENGINE_DIR_COMPONENTS = (".node-runs-tmp", ".process-yaml-tmp")

#: `.tmp/` só é da engine dentro da saída de teste; um `.tmp/` qualquer no
#: produto continua sendo do produto.
ENGINE_NESTED_TMP_ANCESTOR = "test-results"
ENGINE_NESTED_TMP_DIR = ".tmp"

#: O log de atividade que a engine grava a cada transição de nó. O nome real é
#: `<projeto>_log.md`, na raiz. Quem sabe o nome do projeto passa `project_name`
#: e ganha a regra exata; sem ele resta o sufixo, que é aproximação.
ENGINE_LOG_SUFFIX = "_log.md"

#: Arquivos de serviço criados ao subir o produto para o stakeholder.
ENGINE_SERVICE_NAMES = (
    ".serve_url",
    ".presented_artifact",
    ".presentation.pid",
    ".presentation.log",
)

#: `.serve.pid`, `.serve_backend.pid`, `.serve.log` — prefixo mais sufixo.
ENGINE_SERVICE_PREFIX = ".serve"
ENGINE_SERVICE_SUFFIXES = (".pid", ".log")


def is_engine_artifact(relative: str, *, project_name: str | None = None) -> bool:
    """O caminho foi escrito pela engine, e não por quem trabalha no produto?

    `relative` é POSIX e relativo à raiz do projeto. Um caminho absoluto ou
    com `..` não é classificável e volta False: quem chama já rejeitou, ou
    deveria — dizer "é da engine" sobre um caminho que escapa da raiz seria
    conceder o benefício da dúvida ao caso errado.

    `project_name` é o nome da pasta do projeto. Passe-o sempre que a raiz for
    conhecida: o log vira o arquivo exato que a engine escreve
    (`<projeto>_log.md`, na raiz) em vez do sufixo. O parâmetro só aperta,
    nunca afrouxa — sem ele, um `atacante_log.md` plantado pelo LLM seria
    perdoado como artefato da engine e escaparia do orçamento da mudança.
    """
    if not relative or relative.startswith("/"):
        return False
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or ".." in parts:
        return False

    if parts[0] in ENGINE_ROOT_DIRS:
        return True
    if parts[0] == ".ft" and len(parts) > 1 and parts[1] in ENGINE_FT_SUBDIRS:
        return True
    if any(part in ENGINE_DIR_COMPONENTS for part in parts):
        return True
    if ENGINE_NESTED_TMP_DIR in parts:
        anterior = parts[: parts.index(ENGINE_NESTED_TMP_DIR)]
        if ENGINE_NESTED_TMP_ANCESTOR in anterior:
            return True

    name = path.name
    if project_name is not None:
        if len(parts) == 1 and name == f"{project_name}{ENGINE_LOG_SUFFIX}":
            return True
    elif name.endswith(ENGINE_LOG_SUFFIX):
        return True
    if name in ENGINE_SERVICE_NAMES:
        return True
    return name.startswith(ENGINE_SERVICE_PREFIX) and name.endswith(
        ENGINE_SERVICE_SUFFIXES
    )


#: Pathspecs de git equivalentes, para desindexar o que a engine escreveu antes
#: de um commit automático. É uma lista literal de propósito: pathspec de git
#: tem sintaxe própria e derivá-la do predicado esconderia erro de tradução em
#: código que decide o conteúdo de commits. `test_engine_artifacts_sync.py`
#: confere que cada entrada aqui é reconhecida por `is_engine_artifact`.
GIT_RUNTIME_PATHSPECS = (
    "state/",
    "runs/",
    ".ft/runtime/",
    ".ft/cache/",
    ".ft/tmp/",
    ".ft/logs/",
    ".serve_url",
    ".serve_backend.pid",
    ".serve_frontend.pid",
    ".serve.pid",
    ".presented_artifact",
    ".presentation.pid",
    ".presentation.log",
    "src/.serve.log",
    "src/.serve.pid",
    ":(glob)**/.serve_url",
    ":(glob)**/.serve*.pid",
    ":(glob)**/.serve*.log",
    ":(glob)**/.presented_artifact",
    ":(glob)**/.presentation.pid",
    ":(glob)**/.presentation.log",
    ":(glob)**/.node-runs-tmp/**",
    ":(glob)**/.process-yaml-tmp/**",
    ":(glob)**/test-results/**/.tmp/**",
    ":(glob)*_log.md",
    ":(glob)**/*_log.md",
)
