# Streaming Guide

Stream LLM responses in real-time with ForgeLLM.

## Basic Streaming

```python
from forge_llm import ChatAgent

agent = ChatAgent(provider="openai", model="gpt-4o-mini")

for chunk in agent.stream_chat("Write a short poem about code."):
    if chunk.content:
        print(chunk.content, end="", flush=True)
print()  # newline at end
```

## Stream with Session

```python
from forge_llm import ChatAgent, ChatSession

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession(system_prompt="Be creative.")

# First message
for chunk in agent.stream_chat("Start a story about a robot.", session=session):
    if chunk.content:
        print(chunk.content, end="")
print("\n")

# Continue the story (session has context)
for chunk in agent.stream_chat("Continue the story.", session=session):
    if chunk.content:
        print(chunk.content, end="")
print()
```

## Chunk Properties

```python
for chunk in agent.stream_chat("Hello"):
    print(f"Content: {chunk.content}")
    print(f"Role: {chunk.role}")
    print(f"Is Final: {chunk.is_final}")
    print(f"Finish Reason: {chunk.finish_reason}")
```

## Detecting Stream End

```python
full_response = ""

for chunk in agent.stream_chat("Tell me a joke."):
    if chunk.content:
        full_response += chunk.content
        print(chunk.content, end="")

    if chunk.is_final:
        print(f"\n--- Stream complete ---")
        print(f"Finish reason: {chunk.finish_reason}")

print(f"Full response length: {len(full_response)}")
```

## Streaming with Tools

Tools work seamlessly with streaming:

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

registry = ToolRegistry()

@registry.tool
def get_weather(location: str) -> str:
    """Get weather for a location."""
    return f"Sunny, 22°C in {location}"

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)

for chunk in agent.stream_chat("What's the weather in Tokyo?"):
    if chunk.tool_calls:
        print(f"[Tool called: {chunk.tool_calls}]")
    if chunk.content:
        print(chunk.content, end="")
print()
```

## Accumulating Full Response

```python
def stream_and_collect(agent, prompt):
    """Stream response and return full content."""
    content_parts = []

    for chunk in agent.stream_chat(prompt):
        if chunk.content:
            content_parts.append(chunk.content)
            print(chunk.content, end="", flush=True)

    print()  # newline
    return "".join(content_parts)

full_response = stream_and_collect(agent, "Explain Python in 3 sentences.")
print(f"Collected: {full_response}")
```

## Progress Indicator

```python
import sys

def stream_with_progress(agent, prompt):
    """Stream with a spinner indicator."""
    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    idx = 0
    content = ""

    for chunk in agent.stream_chat(prompt):
        if chunk.content:
            content += chunk.content
            print(chunk.content, end="", flush=True)
        else:
            # Show spinner while waiting
            sys.stdout.write(f'\r{spinner[idx % len(spinner)]}')
            sys.stdout.flush()
            idx += 1

    print()  # Final newline
    return content
```

## Handling Errors in Stream

```python
from forge_llm.domain import RequestTimeoutError

try:
    for chunk in agent.stream_chat("Long question..."):
        if chunk.content:
            print(chunk.content, end="")
except RequestTimeoutError:
    print("\n[Stream timed out]")
except Exception as e:
    print(f"\n[Error: {e}]")
```

## Cross-Provider Streaming

Same streaming code works with any provider:

```python
def stream_response(provider: str, model: str, prompt: str):
    """Provider-agnostic streaming."""
    agent = ChatAgent(provider=provider, model=model)

    content = ""
    for chunk in agent.stream_chat(prompt):
        if chunk.content:
            content += chunk.content
            print(chunk.content, end="")
    print()
    return content

# Works with any provider
stream_response("openai", "gpt-4o-mini", "Hello!")
stream_response("anthropic", "claude-3-haiku-20240307", "Hello!")
```

## Async Streaming (Coming Soon)

```python
# Future async support
async for chunk in agent.async_stream_chat("Hello"):
    if chunk.content:
        print(chunk.content, end="")
```

## Best Practices

1. **Always check `chunk.content`** - Not every chunk has content
2. **Use `flush=True`** - Ensure immediate display
3. **Handle stream end** - Check `is_final` or `finish_reason`
4. **Accumulate content** - Build full response for logging/storage
5. **Handle errors** - Wrap in try/except for reliability
