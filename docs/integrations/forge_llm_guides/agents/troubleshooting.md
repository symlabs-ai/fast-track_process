# Troubleshooting Guide for AI Agents

Common issues and solutions when using ForgeLLM.

## Error: ProviderNotConfiguredError

**Message:** `Provider 'openai' is not configured. Check API key.`

**Cause:** API key not set.

**Solution:**
```bash
# Set environment variable
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or pass explicitly:
```python
agent = ChatAgent(provider="openai", api_key="sk-...", model="gpt-4o-mini")
```

## Error: AuthenticationError

**Message:** `Authentication failed for provider 'openai'`

**Cause:** Invalid or expired API key.

**Solution:**
1. Verify API key is correct
2. Check API key hasn't expired
3. Ensure key has correct permissions

```python
# Test with fresh key
import os
os.environ["OPENAI_API_KEY"] = "sk-your-new-key"
agent = ChatAgent(provider="openai", model="gpt-4o-mini")
```

## Error: UnsupportedProviderError

**Message:** `Provider 'xyz' is not supported.`

**Cause:** Unknown provider name.

**Solution:** Use supported providers:
```python
# Supported providers
providers = ["openai", "anthropic", "ollama", "openrouter"]

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
```

## Error: InvalidMessageError

**Message:** `Invalid message: Message cannot be empty`

**Cause:** Empty string or None passed to chat().

**Solution:**
```python
# BAD
agent.chat("")
agent.chat(None)

# GOOD
agent.chat("Your message here")
```

## Error: RequestTimeoutError

**Message:** `Request to 'openai' timed out after 30.0s`

**Cause:** Provider took too long to respond.

**Solution:**
1. Simplify prompt
2. Retry request
3. Check provider status

```python
import time
from forge_llm.domain import RequestTimeoutError

def chat_with_retry(agent, message, retries=3):
    for i in range(retries):
        try:
            return agent.chat(message)
        except RequestTimeoutError:
            if i < retries - 1:
                time.sleep(2 ** i)
    raise
```

## Error: ContextOverflowError

**Message:** `Context overflow: 5000 tokens exceeds limit of 4000`

**Cause:** Session has too many messages.

**Solution:** Use compaction:
```python
from forge_llm import ChatSession, TruncateCompactor

session = ChatSession(
    max_tokens=4000,
    compactor=TruncateCompactor(),  # Auto-compacts
)
```

Or manually clear:
```python
session.clear(preserve_system=True)
```

## Problem: Tool Not Being Called

**Symptom:** LLM responds without using the tool.

**Causes:**
1. Unclear tool description
2. Prompt doesn't suggest tool use
3. LLM decides tool isn't needed

**Solution:**
```python
@registry.tool
def get_weather(location: str) -> str:
    """Get the current weather for a location.
    You MUST use this tool when asked about weather."""  # Explicit instruction
    return f"Sunny in {location}"

# Explicit prompt
response = agent.chat("Use the get_weather tool to get weather in Paris.")
```

## Problem: Session Not Maintaining Context

**Symptom:** Agent forgets previous messages.

**Cause:** Not using session, or creating new session each time.

**Solution:**
```python
# BAD - new session each call
agent.chat("My name is Alice", session=ChatSession())
agent.chat("What's my name?", session=ChatSession())

# GOOD - reuse session
session = ChatSession()
agent.chat("My name is Alice", session=session)
agent.chat("What's my name?", session=session)
```

## Problem: Streaming Not Showing Output

**Symptom:** Stream appears to hang.

**Cause:** Not flushing output.

**Solution:**
```python
# BAD
for chunk in agent.stream_chat("Hello"):
    print(chunk.content, end="")

# GOOD - flush output
for chunk in agent.stream_chat("Hello"):
    if chunk.content:
        print(chunk.content, end="", flush=True)
print()  # Final newline
```

## Problem: Wrong Model for Provider

**Symptom:** Model not found error.

**Cause:** Using wrong model name for provider.

**Solution:**
```python
# OpenAI models
agent = ChatAgent(provider="openai", model="gpt-4o-mini")

# Anthropic models
agent = ChatAgent(provider="anthropic", model="claude-3-haiku-20240307")

# Ollama models (must be installed)
agent = ChatAgent(provider="ollama", model="llama2")
```

## Problem: Import Errors

**Symptom:** `ModuleNotFoundError: No module named 'forge_llm'`

**Solution:**
```bash
pip install forge-llm
```

Or for development:
```bash
pip install -e .
```

## Problem: Tool Arguments Invalid

**Symptom:** Tool returns validation error.

**Cause:** LLM passed wrong argument types.

**Solution:** Use clear type hints and descriptions:
```python
@registry.tool
def calculate(a: int, b: int, operation: str) -> str:
    """Calculate result of operation on two numbers.

    Args:
        a: First integer number
        b: Second integer number
        operation: One of 'add', 'subtract', 'multiply', 'divide'
    """
    # Implementation
```

## Problem: Anthropic System Prompt Error

**Symptom:** Error about system role in messages.

**Cause:** Anthropic API requires system prompt as separate parameter.

**Solution:** ForgeLLM handles this automatically. Ensure you're using latest version:
```python
# This works correctly
session = ChatSession(system_prompt="You are helpful.")
agent = ChatAgent(provider="anthropic", model="claude-3-haiku-20240307")
agent.chat("Hello", session=session)
```

## Debugging Tips

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Now ForgeLLM will log debug info
agent = ChatAgent(provider="openai", model="gpt-4o-mini")
agent.chat("Hello")
```

### Check Session State

```python
print(f"Session ID: {session.session_id}")
print(f"Message count: {len(session.messages)}")
print(f"Token estimate: {session.estimate_tokens()}")
print(f"Last message: {session.last_message}")
```

### Inspect Response

```python
response = agent.chat("Hello")
print(f"Content: {response.content}")
print(f"Model: {response.metadata.model}")
print(f"Provider: {response.metadata.provider}")
print(f"Tokens: {response.token_usage}")
print(f"Tool calls: {response.message.tool_calls}")
```

### Test Provider Connection

```python
from forge_llm import ChatAgent
from forge_llm.domain import ForgeLLMError

def test_provider(provider: str, model: str) -> bool:
    try:
        agent = ChatAgent(provider=provider, model=model)
        response = agent.chat("Say 'ok'")
        return response.content is not None
    except ForgeLLMError as e:
        print(f"Error: {e.code} - {e.message}")
        return False

# Test each provider
test_provider("openai", "gpt-4o-mini")
test_provider("anthropic", "claude-3-haiku-20240307")
```

## Getting Help

1. Check documentation: `from forge_llm.dev import get_agent_quickstart`
2. Read API reference: [docs/product/users/api-reference.md](../users/api-reference.md)
3. Check patterns: [docs/product/agents/patterns.md](./patterns.md)
