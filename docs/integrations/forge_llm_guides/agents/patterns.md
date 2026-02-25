# Common Patterns for AI Agents

Solutions to common tasks when using ForgeLLM.

## Pattern: Simple Chat

**Task:** Send a message and get a response.

```python
from forge_llm import ChatAgent

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
response = agent.chat("Your question here")
print(response.content)
```

## Pattern: Multi-Turn Conversation

**Task:** Maintain context across multiple messages.

```python
from forge_llm import ChatAgent, ChatSession

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession(system_prompt="You are a helpful assistant.")

# First turn
agent.chat("My name is Alice.", session=session)

# Second turn (has context)
response = agent.chat("What's my name?", session=session)
print(response.content)  # "Alice"
```

## Pattern: Streaming Response

**Task:** Display response as it generates.

```python
from forge_llm import ChatAgent

agent = ChatAgent(provider="openai", model="gpt-4o-mini")

for chunk in agent.stream_chat("Tell me a story."):
    if chunk.content:
        print(chunk.content, end="", flush=True)
print()  # Final newline
```

## Pattern: Tool Calling

**Task:** Let LLM call Python functions.

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

registry = ToolRegistry()

@registry.tool
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression."""
    try:
        return str(eval(expression))
    except:
        return "Error"

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)
response = agent.chat("What is 25 * 4?")
print(response.content)  # "100"
```

## Pattern: Provider Switching

**Task:** Same code, different provider.

```python
from forge_llm import ChatAgent

def get_response(provider: str, model: str, question: str) -> str:
    agent = ChatAgent(provider=provider, model=model)
    return agent.chat(question).content

# OpenAI
answer1 = get_response("openai", "gpt-4o-mini", "Hello!")

# Anthropic
answer2 = get_response("anthropic", "claude-3-haiku-20240307", "Hello!")
```

## Pattern: Error Handling

**Task:** Handle API errors gracefully.

```python
from forge_llm import ChatAgent
from forge_llm.domain import (
    ProviderNotConfiguredError,
    AuthenticationError,
    RequestTimeoutError,
)

try:
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    response = agent.chat("Hello")
    print(response.content)
except ProviderNotConfiguredError:
    print("Set OPENAI_API_KEY environment variable")
except AuthenticationError:
    print("Invalid API key")
except RequestTimeoutError:
    print("Request timed out, try again")
```

## Pattern: Long Conversation with Compaction

**Task:** Handle long conversations without overflow.

```python
from forge_llm import ChatAgent, ChatSession, TruncateCompactor

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession(
    system_prompt="You are helpful.",
    max_tokens=4000,
    compactor=TruncateCompactor(),
)

# Can handle many messages without overflow
for i in range(100):
    response = agent.chat(f"Message {i}", session=session)
```

## Pattern: System Prompt Templates

**Task:** Different behaviors for different tasks.

```python
from forge_llm import ChatAgent, ChatSession

PROMPTS = {
    "coder": "You are a Python expert. Respond with code only.",
    "teacher": "You are a patient teacher. Explain simply.",
    "translator": "Translate to the requested language.",
}

def create_agent(role: str):
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    session = ChatSession(system_prompt=PROMPTS[role])
    return agent, session

# Usage
agent, session = create_agent("coder")
response = agent.chat("Sort a list", session=session)
```

## Pattern: Batch Processing

**Task:** Process multiple prompts.

```python
from forge_llm import ChatAgent

def process_batch(prompts: list[str]) -> list[str]:
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    return [agent.chat(p).content for p in prompts]

results = process_batch([
    "What is Python?",
    "What is JavaScript?",
    "What is Rust?",
])
```

## Pattern: Fallback Chain

**Task:** Try providers in order until one works.

```python
from forge_llm import ChatAgent
from forge_llm.domain import ForgeLLMError

PROVIDERS = [
    ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-3-haiku-20240307"),
    ("ollama", "llama2"),
]

def chat_with_fallback(message: str) -> str:
    for provider, model in PROVIDERS:
        try:
            agent = ChatAgent(provider=provider, model=model)
            return agent.chat(message).content
        except ForgeLLMError:
            continue
    raise Exception("All providers failed")
```

## Pattern: Token Monitoring

**Task:** Track token usage.

```python
from forge_llm import ChatAgent, ChatSession

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession()

response = agent.chat("Hello", session=session)

# Check token usage
if response.token_usage:
    print(f"Prompt tokens: {response.token_usage.prompt_tokens}")
    print(f"Completion tokens: {response.token_usage.completion_tokens}")
    print(f"Total tokens: {response.token_usage.total_tokens}")

# Estimate session tokens
print(f"Session estimate: {session.estimate_tokens()}")
```

## Pattern: Streaming with Tool Calls

**Task:** Stream responses while using tools.

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

registry = ToolRegistry()

@registry.tool
def get_data(id: str) -> str:
    """Fetch data by ID."""
    return f"Data for {id}"

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)

for chunk in agent.stream_chat("Get data for user123"):
    if chunk.tool_calls:
        print(f"[Tool: {chunk.tool_calls}]")
    if chunk.content:
        print(chunk.content, end="")
print()
```

## Pattern: Manual Tool Execution

**Task:** Control when tools are executed.

```python
from forge_llm import ChatAgent, ToolCall
from forge_llm.application.tools import ToolRegistry

registry = ToolRegistry()

@registry.tool
def risky_operation(data: str) -> str:
    """Perform risky operation."""
    return f"Processed: {data}"

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)

# Disable auto-execution
response = agent.chat("Process important data", auto_execute_tools=False)

# Check if tools were requested
if response.message.tool_calls:
    # Confirm before executing
    print("Agent wants to call:", response.message.tool_calls)
    # Execute manually if approved
    tool_calls = [ToolCall.from_openai(tc) for tc in response.message.tool_calls]
    results = agent.execute_tool_calls(tool_calls)
```

## Anti-Patterns to Avoid

### Don't: Create new agent per message

```python
# BAD - creates new agent each time
for msg in messages:
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    agent.chat(msg)

# GOOD - reuse agent
agent = ChatAgent(provider="openai", model="gpt-4o-mini")
for msg in messages:
    agent.chat(msg)
```

### Don't: Ignore sessions for conversations

```python
# BAD - no context
agent.chat("My name is Alice")
agent.chat("What's my name?")  # Won't know

# GOOD - use session
session = ChatSession()
agent.chat("My name is Alice", session=session)
agent.chat("What's my name?", session=session)  # Knows it's Alice
```

### Don't: Ignore errors

```python
# BAD
response = agent.chat("Hello")

# GOOD
try:
    response = agent.chat("Hello")
except ForgeLLMError as e:
    print(f"Error: {e.code}")
```
