# Installing ForgeLLM

Guide for installing ForgeLLM directly from the Git repository.

## Quick Install (pip from Git)

```bash
pip install git+https://github.com/symlabs-ai/forge_llm.git
```

### Specific Version

```bash
# Install specific version tag
pip install git+https://github.com/symlabs-ai/forge_llm.git@v0.4.0

# Install from specific branch
pip install git+https://github.com/symlabs-ai/forge_llm.git@main
```

## Verify Installation

```python
import forge_llm
print(forge_llm.__version__)  # Should print "0.4.0"
```

## Quick Start After Install

```python
from forge_llm import ChatAgent

# Create agent (uses OPENAI_API_KEY from environment)
agent = ChatAgent(provider="openai", model="gpt-4o-mini")

# Chat
response = agent.chat("Hello!")
print(response.content)
```

## For AI Coding Agents

After installing, access documentation programmatically:

```python
from forge_llm.dev import get_agent_quickstart

# Get complete API guide
guide = get_agent_quickstart()
print(guide)
```

## Requirements

- Python 3.11+
- API key for your chosen provider (OpenAI, Anthropic, etc.)

## Environment Variables

Set your API keys as environment variables:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Development Installation

For contributing or local development:

```bash
# Clone repository
git clone https://github.com/symlabs-ai/forge_llm.git
cd forge_llm

# Install with Poetry (recommended)
poetry install

# Or with pip in editable mode
pip install -e .
```

## Troubleshooting

### ImportError: No module named 'forge_llm'

Ensure pip installed to the correct Python environment:

```bash
# Check which Python is being used
which python
pip show forge-llm
```

### Version Mismatch

Force reinstall to get the latest version:

```bash
pip install --force-reinstall git+https://github.com/symlabs-ai/forge_llm.git
```

## Links

- **Repository**: https://github.com/symlabs-ai/forge_llm
- **Documentation**: See `/docs/product/` in repository
- **Examples**: See `/docs/product/examples/` in repository
