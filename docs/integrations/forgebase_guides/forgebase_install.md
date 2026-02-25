# Instalação do ForgeBase

## Instalação Rápida

```bash
pip install git+https://github.com/symlabs-ai/forgebase.git
```

## Instalação para Desenvolvimento

```bash
git clone https://github.com/symlabs-ai/forgebase.git
cd forgebase
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Documentação Completa

- **[Início Rápido](usuarios/inicio-rapido.md)** — Instalação, primeiro uso e exemplos
- **[Ambiente e Scripts](usuarios/ambiente_e_scripts.md)** — Setup de venv, lint (Ruff), pre-commit hooks
