# Quickstart Guide

Get started with ForgeLLM in 5 minutes.

## Installation

```bash
pip install forge-llm
```

## Setup API Keys

Set your API keys as environment variables:

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

# Or use a .env file
```

## Basic Usage

### Simple Chat

```python
from forge_llm import ChatAgent

# Create an agent (API key auto-loaded from environment)
agent = ChatAgent(provider="openai", model="gpt-4o-mini")

# Send a message
response = agent.chat("What is Python?")
print(response.content)
```

### With Anthropic

```python
agent = ChatAgent(provider="anthropic", model="claude-3-haiku-20240307")
response = agent.chat("Explain machine learning in one sentence.")
print(response.content)
```

### Streaming Responses

```python
agent = ChatAgent(provider="openai", model="gpt-4o-mini")

for chunk in agent.stream_chat("Write a short poem about code."):
    if chunk.content:
        print(chunk.content, end="", flush=True)
print()  # newline at end
```

## Conversation Sessions

```python
from forge_llm import ChatAgent, ChatSession

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession(system_prompt="You are a helpful coding assistant.")

# Multi-turn conversation
agent.chat("My name is Alice.", session=session)
response = agent.chat("What's my name?", session=session)
print(response.content)  # "Your name is Alice"
```

## Tool Calling

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

# Create tool registry
registry = ToolRegistry()

@registry.tool
def get_weather(location: str) -> str:
    """Get the current weather for a location."""
    return f"Sunny, 22°C in {location}"

# Create agent with tools
agent = ChatAgent(
    provider="openai",
    model="gpt-4o-mini",
    tools=registry,
)

# Agent will automatically call the tool
response = agent.chat("What's the weather in Paris?")
print(response.content)  # Mentions sunny, 22°C
```

## Provider Portability

Same code works with any provider:

```python
from forge_llm import ChatAgent

def ask_question(provider: str, model: str, question: str) -> str:
    agent = ChatAgent(provider=provider, model=model)
    response = agent.chat(question)
    return response.content

# Works with OpenAI
print(ask_question("openai", "gpt-4o-mini", "Hello!"))

# Same code works with Anthropic
print(ask_question("anthropic", "claude-3-haiku-20240307", "Hello!"))
```

## Next Steps

- [API Reference](./api-reference.md) - Complete API documentation
- [Tools](./tools.md) - Advanced tool calling
- [Sessions](./sessions.md) - Session management and compaction
- [Streaming](./streaming.md) - Real-time streaming
