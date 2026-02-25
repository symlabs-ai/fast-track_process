# API Reference

Complete API documentation for ForgeLLM.

## Core Classes

### ChatAgent

Main agent for chat interactions with LLMs.

```python
from forge_llm import ChatAgent
```

#### Constructor

```python
ChatAgent(
    provider: str,              # "openai", "anthropic", "ollama", "openrouter"
    api_key: str | None = None, # API key (auto-loaded from env if None)
    model: str | None = None,   # Model name (e.g., "gpt-4o-mini")
    tools: ToolRegistry | list[ToolDefinition] | None = None,
    **kwargs                    # Additional provider config
)
```

#### Methods

##### `chat()`

Send messages and get a response.

```python
def chat(
    messages: str | list[ChatMessage] | None = None,
    config: ChatConfig | None = None,
    session: ChatSession | None = None,
    auto_execute_tools: bool = True,
) -> ChatResponse
```

**Parameters:**
- `messages`: Single message string or list of ChatMessage
- `config`: Optional chat configuration
- `session`: Optional ChatSession for conversation history
- `auto_execute_tools`: Auto-execute tool calls (default True)

**Returns:** `ChatResponse`

**Raises:**
- `InvalidMessageError`: If message is empty
- `RequestTimeoutError`: If provider times out
- `AuthenticationError`: If API key is invalid

##### `stream_chat()`

Stream response chunks.

```python
def stream_chat(
    messages: str | list[ChatMessage] | None = None,
    config: ChatConfig | None = None,
    session: ChatSession | None = None,
    auto_execute_tools: bool = True,
) -> Generator[ChatChunk, None, None]
```

**Yields:** `ChatChunk` objects with partial content

##### `execute_tool_calls()`

Execute tool calls manually.

```python
def execute_tool_calls(tool_calls: list[ToolCall]) -> list[ToolResult]
```

---

### ChatSession

Manages conversation history with optional compaction.

```python
from forge_llm import ChatSession
```

#### Constructor

```python
ChatSession(
    session_id: str | None = None,      # Auto-generated if None
    system_prompt: str | None = None,   # System message
    max_tokens: int | None = None,      # Max context tokens
    compactor: SessionCompactor | None = None,  # Compaction strategy
    safety_margin: float = 0.8,         # Buffer for token limit
)
```

#### Properties

- `session_id: str` - Unique session identifier
- `messages: list[ChatMessage]` - Copy of all messages
- `last_message: ChatMessage | None` - Most recent message

#### Methods

##### `add_message()`

Add a message to the session.

```python
def add_message(message: ChatMessage) -> None
```

**Raises:** `ContextOverflowError` if exceeds limit without compactor

##### `add_response()`

Add a ChatResponse to the session.

```python
def add_response(response: ChatResponse) -> None
```

##### `clear()`

Clear all messages.

```python
def clear(preserve_system: bool = True) -> None
```

##### `estimate_tokens()`

Estimate total token count.

```python
def estimate_tokens() -> int
```

---

### ChatMessage

A message in a conversation.

```python
from forge_llm import ChatMessage
```

#### Factory Methods

```python
ChatMessage.user(content: str) -> ChatMessage
ChatMessage.assistant(content: str) -> ChatMessage
ChatMessage.system(content: str) -> ChatMessage
ChatMessage.tool(content: str, tool_call_id: str) -> ChatMessage
```

#### Attributes

- `role: str` - "system", "user", "assistant", or "tool"
- `content: str | None` - Message content
- `tool_calls: list[dict] | None` - Tool calls (assistant messages)
- `tool_call_id: str | None` - For tool response messages

---

### ChatResponse

Response from chat() method.

```python
from forge_llm import ChatResponse
```

#### Attributes

- `message: ChatMessage` - The response message
- `metadata: ResponseMetadata` - Model and provider info
- `token_usage: TokenUsage | None` - Token consumption

#### Convenience Properties

- `content: str | None` - Shortcut for `message.content`

---

### ChatChunk

Streaming response chunk.

```python
from forge_llm import ChatChunk
```

#### Attributes

- `content: str` - Partial content
- `role: str` - Message role
- `finish_reason: str | None` - "stop", "tool_calls", etc.
- `is_final: bool` - True if last chunk
- `tool_calls: list[dict] | None` - Tool calls if any

---

### ToolRegistry

Registry for callable tools.

```python
from forge_llm.application.tools import ToolRegistry
```

#### Methods

##### `tool` (decorator)

Register a function as a tool.

```python
@registry.tool
def my_function(arg: str) -> str:
    """Description for LLM."""
    return result
```

##### `register_callable()`

Register a function manually.

```python
registry.register_callable(some_function)
```

##### `get_definitions()`

Get all tool definitions.

```python
def get_definitions() -> list[ToolDefinition]
```

##### `execute()`

Execute a tool call.

```python
def execute(call: ToolCall) -> ToolResult
```

##### `list_tools()`

List registered tool names.

```python
def list_tools() -> list[str]
```

---

### TruncateCompactor

Simple compaction strategy that removes old messages.

```python
from forge_llm import TruncateCompactor
```

#### Constructor

```python
TruncateCompactor(keep_system: bool = True)
```

---

## Exceptions

All exceptions inherit from `ForgeLLMError`.

```python
from forge_llm.domain import (
    ForgeLLMError,
    ProviderNotConfiguredError,
    UnsupportedProviderError,
    AuthenticationError,
    InvalidMessageError,
    RequestTimeoutError,
    ContextOverflowError,
    ToolNotFoundError,
    ToolValidationError,
)
```

| Exception | When Raised |
|-----------|-------------|
| `ProviderNotConfiguredError` | API key missing |
| `UnsupportedProviderError` | Unknown provider name |
| `AuthenticationError` | Invalid API key |
| `InvalidMessageError` | Empty or invalid message |
| `RequestTimeoutError` | Provider timeout |
| `ContextOverflowError` | Token limit exceeded |
| `ToolNotFoundError` | Tool not in registry |
| `ToolValidationError` | Invalid tool arguments |

---

## Type Hints

```python
from forge_llm import (
    ChatAgent,
    ChatMessage,
    ChatResponse,
    ChatChunk,
    ChatConfig,
    ChatSession,
    TokenUsage,
    ResponseMetadata,
    ToolRegistry,
    ToolDefinition,
    ToolCall,
    ToolResult,
)
```
