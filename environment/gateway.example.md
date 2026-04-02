# Gateway — Configuração Local

> Copie este arquivo para `gateway.md` e preencha com os dados do seu workspace.
> Este arquivo é gitignored — nunca commitar `gateway.md`.

## Workspace

- **Gateway URL**: https://symgateway.symlabs.ai  ← nunca staging
- **Workspace**: <nome do workspace, ex: "Symlabs [DEV]">
- **API Key**: <nome da sua key, ex: "palhano">

## Como registrar um novo projeto

Ao rodar `ft init` em um novo projeto, o gateway bloqueia com 403 se o projeto
não estiver registrado. Siga os passos abaixo antes de rodar `ft continue`.

### Passo 1 — Pedir ao DevOps

Use `/ask devops` com a seguinte instrução:

```
Registrar o projeto `<folder_name>` no gateway de produção (symgateway.symlabs.ai).
Inserir na tabela `projects` com `folder_name='<folder_name>'` e vincular à API key
`<sua_key>` em `project_api_keys`.
```

O DevOps vai executar via SSH + SQLAlchemy no servidor.

### Passo 2 — Reiniciar o processo

Após confirmação do DevOps, limpar o estado bloqueado:

```bash
# Editar project/state/engine_state.yml:
# node_status: blocked → ready
# blocked_reason: null
# _lock: null
```

Depois rodar novamente:

```bash
python -m ft.cli.main -p <process_path> continue --mvp
```

## Notas deste workspace

<!-- Adicione aqui notas específicas do seu ambiente -->
