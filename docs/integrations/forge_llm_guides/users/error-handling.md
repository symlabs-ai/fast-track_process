# Error Handling Guide

ForgeLLM provides structured exceptions for robust error handling.

## Exception Hierarchy

```
ForgeLLMError (base)
├── ProviderError
│   ├── ProviderNotConfiguredError
│   ├── UnsupportedProviderError
│   └── AuthenticationError
├── ChatError
│   ├── InvalidMessageError
│   └── RequestTimeoutError
├── SessionError
│   ├── SessionNotFoundError
│   └── ContextOverflowError
└── ToolError
    ├── ToolNotFoundError
    └── ToolValidationError
```

## Importing Exceptions

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

## Provider Errors

### ProviderNotConfiguredError

Raised when API key is missing.

```python
from forge_llm import ChatAgent
from forge_llm.domain import ProviderNotConfiguredError

try:
    # No API key in environment
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    agent.chat("Hello")
except ProviderNotConfiguredError as e:
    print(f"Configure API key: {e.provider}")
    # Set OPENAI_API_KEY environment variable
```

### AuthenticationError

Raised when API key is invalid.

```python
from forge_llm.domain import AuthenticationError

try:
    agent = ChatAgent(provider="openai", api_key="invalid-key", model="gpt-4o-mini")
    agent.chat("Hello")
except AuthenticationError as e:
    print(f"Invalid key for {e.provider}")
```

### UnsupportedProviderError

Raised for unknown provider names.

```python
from forge_llm.domain import UnsupportedProviderError

try:
    agent = ChatAgent(provider="unknown_provider", model="model")
except UnsupportedProviderError as e:
    print(f"Unknown provider: {e.provider}")
    print("Supported: openai, anthropic, ollama, openrouter")
```

## Chat Errors

### InvalidMessageError

Raised for empty or invalid messages.

```python
from forge_llm.domain import InvalidMessageError

try:
    agent.chat("")  # Empty message
except InvalidMessageError as e:
    print(f"Invalid message: {e.message}")
```

### RequestTimeoutError

Raised when provider times out.

```python
from forge_llm.domain import RequestTimeoutError

try:
    response = agent.chat("Very complex question...")
except RequestTimeoutError as e:
    print(f"Timeout after {e.timeout}s with {e.provider}")
```

## Session Errors

### ContextOverflowError

Raised when context exceeds token limit.

```python
from forge_llm import ChatSession
from forge_llm.domain import ContextOverflowError

session = ChatSession(max_tokens=1000)  # No compactor

try:
    for i in range(100):
        agent.chat(f"Long message {i}...", session=session)
except ContextOverflowError as e:
    print(f"Overflow: {e.current_tokens} > {e.max_tokens}")
```

**Solution:** Use a compactor:

```python
from forge_llm import TruncateCompactor

session = ChatSession(
    max_tokens=1000,
    compactor=TruncateCompactor(),  # Auto-compacts
)
```

## Tool Errors

### ToolNotFoundError

Raised when executing unknown tool.

```python
from forge_llm.domain import ToolNotFoundError

try:
    result = registry.execute(ToolCall(name="unknown_tool", ...))
except ToolNotFoundError as e:
    print(f"Tool not found: {e.tool_name}")
```

### ToolValidationError

Raised when tool arguments fail validation.

```python
from forge_llm.domain import ToolValidationError

@registry.tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

# If LLM passes wrong types, validation fails
# Result contains error message
```

## Comprehensive Error Handling

```python
from forge_llm import ChatAgent
from forge_llm.domain import (
    ForgeLLMError,
    ProviderNotConfiguredError,
    AuthenticationError,
    RequestTimeoutError,
    ContextOverflowError,
)

def safe_chat(agent, message, session=None):
    """Chat with comprehensive error handling."""
    try:
        return agent.chat(message, session=session)

    except ProviderNotConfiguredError:
        print("ERROR: API key not configured")
        print("Set environment variable for your provider")
        return None

    except AuthenticationError:
        print("ERROR: Invalid API key")
        return None

    except RequestTimeoutError as e:
        print(f"ERROR: Request timed out after {e.timeout}s")
        print("Try again or use a simpler prompt")
        return None

    except ContextOverflowError as e:
        print(f"ERROR: Context too large ({e.current_tokens} tokens)")
        print("Clear session or enable compaction")
        return None

    except ForgeLLMError as e:
        print(f"ERROR: {e.code} - {e.message}")
        return None

    except Exception as e:
        print(f"Unexpected error: {e}")
        return None
```

## Error Codes

Each exception has a `code` attribute:

| Exception | Code |
|-----------|------|
| `ProviderNotConfiguredError` | `PROVIDER_NOT_CONFIGURED` |
| `UnsupportedProviderError` | `UNSUPPORTED_PROVIDER` |
| `AuthenticationError` | `AUTHENTICATION_ERROR` |
| `InvalidMessageError` | `INVALID_MESSAGE` |
| `RequestTimeoutError` | `REQUEST_TIMEOUT` |
| `ContextOverflowError` | `CONTEXT_OVERFLOW` |
| `ToolNotFoundError` | `TOOL_NOT_FOUND` |
| `ToolValidationError` | `TOOL_VALIDATION_ERROR` |

```python
try:
    agent.chat("")
except ForgeLLMError as e:
    if e.code == "INVALID_MESSAGE":
        print("Empty message not allowed")
    elif e.code == "AUTHENTICATION_ERROR":
        print("Check your API key")
```

## Retry Pattern

```python
import time
from forge_llm.domain import RequestTimeoutError

def chat_with_retry(agent, message, max_retries=3):
    """Chat with automatic retry on timeout."""
    for attempt in range(max_retries):
        try:
            return agent.chat(message)
        except RequestTimeoutError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # Exponential backoff
                print(f"Timeout, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

    return None
```

## Logging Errors

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    response = agent.chat("Hello")
except ForgeLLMError as e:
    logger.error(f"ForgeLLM error: {e.code} - {e.message}")
    raise
```
