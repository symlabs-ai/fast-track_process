# ForgeLLM Documentation

ForgeLLM is a unified LLM client library providing provider portability across OpenAI, Anthropic, Ollama, and OpenRouter.

## Documentation Structure

### For Users (Human Developers)

| Document | Description |
|----------|-------------|
| [Quickstart](./users/quickstart.md) | Get started in 5 minutes |
| [API Reference](./users/api-reference.md) | Complete API documentation |
| [Providers](./users/providers.md) | Provider-specific configuration |
| [Tools](./users/tools.md) | Tool calling and function execution |
| [Sessions](./users/sessions.md) | Conversation management |
| [Streaming](./users/streaming.md) | Streaming responses |
| [Error Handling](./users/error-handling.md) | Exception handling guide |
| [Recipes](./users/recipes.md) | Common use cases and patterns |

### For AI Agents

| Document | Description |
|----------|-------------|
| [Discovery](./agents/discovery.md) | Machine-readable API overview |
| [API Summary](./agents/api-summary.md) | Condensed API reference |
| [Patterns](./agents/patterns.md) | Common implementation patterns |
| [Troubleshooting](./agents/troubleshooting.md) | Issue diagnosis and fixes |

## Quick Example

```python
from forge_llm import ChatAgent

# Create agent (auto-loads API key from environment)
agent = ChatAgent(provider="openai", model="gpt-4o-mini")

# Simple chat
response = agent.chat("Hello, world!")
print(response.content)
```

## Installation

```bash
pip install forge-llm
```

## Supported Providers

| Provider | Models | Status |
|----------|--------|--------|
| OpenAI | gpt-4o, gpt-4o-mini, gpt-4-turbo | ✅ Full support |
| Anthropic | claude-3-opus, claude-3-sonnet, claude-3-haiku | ✅ Full support |
| Ollama | All local models | ✅ Full support |
| OpenRouter | All routed models | ✅ Full support |

## Key Features

- **Provider Portability**: Same code works with any provider
- **Tool Calling**: Register Python functions as LLM tools
- **Session Management**: Automatic conversation history with compaction
- **Streaming**: Real-time response streaming
- **Type Safety**: Full type hints throughout

## Environment Variables

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
```
