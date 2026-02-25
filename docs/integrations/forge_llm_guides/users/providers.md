# Provider Configuration

ForgeLLM supports multiple LLM providers with a unified interface.

## Supported Providers

| Provider | Key Env Variable | Models |
|----------|------------------|--------|
| OpenAI | `OPENAI_API_KEY` | gpt-4o, gpt-4o-mini, gpt-4-turbo |
| Anthropic | `ANTHROPIC_API_KEY` | claude-3-opus, claude-3-sonnet, claude-3-haiku |
| Ollama | (none - local) | All local models |
| OpenRouter | `OPENROUTER_API_KEY` | All routed models |

## OpenAI

```python
from forge_llm import ChatAgent

# Auto-load from OPENAI_API_KEY
agent = ChatAgent(provider="openai", model="gpt-4o-mini")

# Or explicit key
agent = ChatAgent(
    provider="openai",
    api_key="sk-...",
    model="gpt-4o",
)
```

### Supported Models

- `gpt-4o` - Latest GPT-4o
- `gpt-4o-mini` - Faster, cheaper GPT-4o
- `gpt-4-turbo` - GPT-4 Turbo
- `gpt-3.5-turbo` - GPT-3.5

## Anthropic

```python
from forge_llm import ChatAgent

# Auto-load from ANTHROPIC_API_KEY
agent = ChatAgent(provider="anthropic", model="claude-3-haiku-20240307")

# Or explicit key
agent = ChatAgent(
    provider="anthropic",
    api_key="sk-ant-...",
    model="claude-3-5-sonnet-20241022",
)
```

### Supported Models

- `claude-3-5-sonnet-20241022` - Latest Sonnet
- `claude-3-5-haiku-20241022` - Latest Haiku
- `claude-3-opus-20240229` - Claude 3 Opus
- `claude-3-sonnet-20240229` - Claude 3 Sonnet
- `claude-3-haiku-20240307` - Claude 3 Haiku (cheap, fast)

### Anthropic-Specific Notes

- System prompts are automatically handled (extracted and passed as `system` parameter)
- Tool calling is converted to Anthropic format automatically

## Ollama

Local models via Ollama.

```python
from forge_llm import ChatAgent

# Connect to local Ollama
agent = ChatAgent(
    provider="ollama",
    model="llama2",
    base_url="http://localhost:11434",  # Optional, this is default
)
```

### Requirements

1. Install Ollama: https://ollama.ai
2. Pull a model: `ollama pull llama2`
3. Ollama must be running

### Supported Models

Any model available in your Ollama installation:
- `llama2`
- `mistral`
- `codellama`
- `mixtral`
- etc.

## OpenRouter

Access many models through OpenRouter.

```python
from forge_llm import ChatAgent

agent = ChatAgent(
    provider="openrouter",
    api_key="sk-or-...",  # or OPENROUTER_API_KEY
    model="openai/gpt-4o-mini",
)
```

### Model Format

OpenRouter uses format: `provider/model-name`

Examples:
- `openai/gpt-4o`
- `anthropic/claude-3-opus`
- `meta-llama/llama-3-70b-instruct`

## Provider Portability

The same code works across providers:

```python
from forge_llm import ChatAgent, ChatSession

def chat_with_provider(provider: str, model: str) -> str:
    agent = ChatAgent(provider=provider, model=model)
    session = ChatSession(system_prompt="Be concise.")

    response = agent.chat("What is 2+2?", session=session)
    return response.content

# Same function, different providers
openai_answer = chat_with_provider("openai", "gpt-4o-mini")
anthropic_answer = chat_with_provider("anthropic", "claude-3-haiku-20240307")
```

## Error Handling

```python
from forge_llm import ChatAgent
from forge_llm.domain import (
    ProviderNotConfiguredError,
    AuthenticationError,
    UnsupportedProviderError,
)

try:
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    response = agent.chat("Hello")
except ProviderNotConfiguredError:
    print("Set OPENAI_API_KEY environment variable")
except AuthenticationError:
    print("Invalid API key")
except UnsupportedProviderError as e:
    print(f"Unknown provider: {e.provider}")
```

## Environment Variables

Recommended setup in `.env`:

```bash
# OpenAI
OPENAI_API_KEY=sk-...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# OpenRouter
OPENROUTER_API_KEY=sk-or-...
```

Load with python-dotenv:

```python
from dotenv import load_dotenv
load_dotenv()

from forge_llm import ChatAgent
agent = ChatAgent(provider="openai", model="gpt-4o-mini")
```
