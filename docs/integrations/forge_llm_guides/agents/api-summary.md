# ForgeLLM API Summary for AI Agents

Machine-readable API reference for AI coding agents.

## Quick Reference

```python
# IMPORTS
from forge_llm import ChatAgent, ChatSession, ChatMessage, ChatResponse, ChatChunk
from forge_llm import TruncateCompactor, ToolRegistry
from forge_llm.application.tools import ToolRegistry
from forge_llm.domain import (
    ProviderNotConfiguredError, AuthenticationError, InvalidMessageError,
    RequestTimeoutError, ContextOverflowError, ToolNotFoundError
)

# BASIC CHAT
agent = ChatAgent(provider="openai", model="gpt-4o-mini")
response = agent.chat("message")  # Returns ChatResponse
content = response.content        # String content

# STREAMING
for chunk in agent.stream_chat("message"):
    if chunk.content:
        print(chunk.content, end="")

# SESSION
session = ChatSession(system_prompt="...", max_tokens=4000)
agent.chat("message", session=session)

# TOOLS
registry = ToolRegistry()
@registry.tool
def func(arg: str) -> str:
    """Docstring."""
    return result
agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)
```

## ChatAgent

### Constructor

```python
ChatAgent(
    provider: str,              # "openai" | "anthropic" | "ollama" | "openrouter"
    api_key: str | None,        # Auto-loaded from env if None
    model: str | None,          # Model name
    tools: ToolRegistry | None, # Optional tools
)
```

### Methods

| Method | Signature | Returns |
|--------|-----------|---------|
| `chat` | `(messages, session?, auto_execute_tools?)` | `ChatResponse` |
| `stream_chat` | `(messages, session?, auto_execute_tools?)` | `Generator[ChatChunk]` |
| `execute_tool_calls` | `(tool_calls)` | `list[ToolResult]` |
| `get_tool_definitions` | `()` | `list[ToolDefinition]` |

## ChatSession

### Constructor

```python
ChatSession(
    session_id: str | None,
    system_prompt: str | None,
    max_tokens: int | None,
    compactor: SessionCompactor | None,
    safety_margin: float = 0.8,
)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `session_id` | `str` | Unique identifier |
| `messages` | `list[ChatMessage]` | Copy of messages |
| `last_message` | `ChatMessage | None` | Most recent |

### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `add_message` | `(message: ChatMessage)` | Add message |
| `add_response` | `(response: ChatResponse)` | Add response |
| `clear` | `(preserve_system=True)` | Clear messages |
| `estimate_tokens` | `()` | Token estimate |
| `compact` | `(target_tokens?)` | Manual compaction |

## ChatMessage

### Factory Methods

```python
ChatMessage.user(content: str) -> ChatMessage
ChatMessage.assistant(content: str) -> ChatMessage
ChatMessage.system(content: str) -> ChatMessage
ChatMessage.tool(content: str, tool_call_id: str) -> ChatMessage
```

### Attributes

| Attribute | Type |
|-----------|------|
| `role` | `"system" | "user" | "assistant" | "tool"` |
| `content` | `str | None` |
| `tool_calls` | `list[dict] | None` |
| `tool_call_id` | `str | None` |

## ChatResponse

| Attribute | Type |
|-----------|------|
| `message` | `ChatMessage` |
| `metadata` | `ResponseMetadata` |
| `token_usage` | `TokenUsage | None` |
| `content` | `str | None` (shortcut) |

## ChatChunk (Streaming)

| Attribute | Type |
|-----------|------|
| `content` | `str` |
| `role` | `str` |
| `is_final` | `bool` |
| `finish_reason` | `str | None` |
| `tool_calls` | `list[dict] | None` |

## ToolRegistry

```python
registry = ToolRegistry()

# Decorator registration
@registry.tool
def my_tool(arg: str) -> str:
    """Tool description for LLM."""
    return result

# Methods
registry.list_tools() -> list[str]
registry.get_definitions() -> list[ToolDefinition]
registry.execute(call: ToolCall) -> ToolResult
```

## Exceptions

| Exception | Code | When |
|-----------|------|------|
| `ProviderNotConfiguredError` | `PROVIDER_NOT_CONFIGURED` | Missing API key |
| `UnsupportedProviderError` | `UNSUPPORTED_PROVIDER` | Unknown provider |
| `AuthenticationError` | `AUTHENTICATION_ERROR` | Invalid API key |
| `InvalidMessageError` | `INVALID_MESSAGE` | Empty message |
| `RequestTimeoutError` | `REQUEST_TIMEOUT` | Provider timeout |
| `ContextOverflowError` | `CONTEXT_OVERFLOW` | Token limit exceeded |
| `ToolNotFoundError` | `TOOL_NOT_FOUND` | Unknown tool |
| `ToolValidationError` | `TOOL_VALIDATION_ERROR` | Invalid tool args |

## Environment Variables

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
```

## Providers

| Provider | Models |
|----------|--------|
| `openai` | gpt-4o, gpt-4o-mini, gpt-4-turbo |
| `anthropic` | claude-3-opus-20240229, claude-3-sonnet-20240229, claude-3-haiku-20240307 |
| `ollama` | llama2, mistral, codellama (local) |
| `openrouter` | provider/model format |

## Patterns

### Basic Chat
```python
agent = ChatAgent(provider="openai", model="gpt-4o-mini")
response = agent.chat("Hello")
print(response.content)
```

### With Session
```python
session = ChatSession(system_prompt="Be helpful.")
agent.chat("My name is Alice", session=session)
agent.chat("What's my name?", session=session)
```

### With Tools
```python
registry = ToolRegistry()
@registry.tool
def get_data(id: str) -> str:
    """Get data by ID."""
    return f"Data for {id}"
agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)
```

### Error Handling
```python
try:
    response = agent.chat("Hello")
except ProviderNotConfiguredError:
    print("Set API key")
except AuthenticationError:
    print("Invalid key")
```
