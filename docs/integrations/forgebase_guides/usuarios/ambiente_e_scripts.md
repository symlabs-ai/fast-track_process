# Ambiente de Desenvolvimento e Scripts Úteis

Este documento descreve como preparar o ambiente local do ForgeBase, usar os scripts utilitários e integrar qualidade automática com pre-commit e lint.

> Regras importantes do repositório: não gravar nada na raiz, não fazer push, tags ou alterar versão sem solicitação explícita do usuário. A fonte da verdade de versão é `VERSION.MD`.

## Requisitos

- Python 3.12 (recomendado)
- Git
- pip (já incluso no venv ao usar Python 3.12)

Verifique a versão:

```bash
python3.12 -V
```

## Setup Rápido do Ambiente

Criar o ambiente virtual `.venv` com Python 3.12:

```bash
python3.12 -m venv .venv
```

Ativar o ambiente virtual:

- Linux/macOS: `source .venv/bin/activate`
- Windows (PowerShell): `.venv\Scripts\Activate.ps1`
- Windows (cmd): `.venv\Scripts\activate.bat`

Atualizar ferramentas básicas e instalar dependências de desenvolvimento (linter + hooks):

```bash
python -m pip install --upgrade pip setuptools wheel
pip install -r scripts/dev-requirements.txt
```

Opcional: instalar extras de desenvolvimento (pytest, mypy, black, etc.) definidos no `setup.py`:

```bash
pip install -e .[dev]
```

## Lint e Estilo (Ruff)

- Configuração: `scripts/ruff.toml`
- Comando de checagem:

```bash
bash scripts/lint.sh
```

- Aplicar correções automáticas quando possível:

```bash
bash scripts/lint.sh --fix
```

Regras ativas principais: pycodestyle (E), pyflakes (F), isort (I), bugbear (B), pyupgrade (UP), comprehensions (C4), simplify (SIM), return (RET), naming (N). Algumas regras são ignoradas para compatibilidade com formatação (E203, E266, E501).

## Hooks de pre-commit

Configuração dos hooks: `scripts/pre-commit-config.yaml`

Instalação (com venv ativado):

```bash
bash scripts/install_precommit.sh
```

Rodar em todos os arquivos (baseline):

```bash
pre-commit run --config scripts/pre-commit-config.yaml --all-files
```

Sobre os hooks configurados:
- `pre-commit-hooks`: trailing whitespace, end-of-file, arquivos grandes, YAML
- `ruff-pre-commit`: linter + auto-fix usando `scripts/ruff.toml`

Caso você não use shell Bash no Windows, é possível instalar sem o script:

```powershell
.venv\Scripts\pre-commit.exe install --config scripts/pre-commit-config.yaml
.venv\Scripts\pre-commit.exe run --config scripts/pre-commit-config.yaml --all-files
```

## Testes

Instale os extras de dev, caso ainda não tenha:

```bash
pip install -e .[dev]
```

Rodar testes:

```bash
pytest -q
```

Com cobertura:

```bash
pytest --cov=src/forgebase --cov-report=term-missing
```

Observação: alguns testes de infraestrutura SQL são condicionais (só rodam se `sqlalchemy` estiver instalado).

## Scripts Disponíveis

- `scripts/lint.sh`
  - Checa (`bash scripts/lint.sh`) ou corrige (`bash scripts/lint.sh --fix`) com Ruff.
- `scripts/ruff.toml`
  - Configuração central de lint e ordenação de imports.
- `scripts/dev-requirements.txt`
  - Dependências mínimas de desenvolvimento (ruff, pre-commit).
- `scripts/pre-commit-config.yaml`
  - Hooks de pre-commit (sem gravar config na raiz).
- `scripts/install_precommit.sh`
  - Instala e prepara o pre-commit usando a configuração em `scripts/` e executa baseline.

## Solução de Problemas

- `pip` não encontrado no venv
  - Garanta que o venv foi criado com Python 3.12 e rode `python -m ensurepip --upgrade` dentro do venv.
- Hooks falham modificando arquivos
  - Isso é esperado em baseline. Faça commit das correções e rode novamente se necessário.
- `ruff` acusa métodos vazios em classes base (B027)
  - Torne-os `@abstractmethod` ou adicione implementação mínima. Alternativamente, ajuste a regra em `scripts/ruff.toml`.

## Avisos Importantes

- Não escreva na raiz do repositório, não faça push remoto, tags ou alterações de versão sem solicitação explícita.
- A versão atual e histórico estão em `VERSION.MD`.
