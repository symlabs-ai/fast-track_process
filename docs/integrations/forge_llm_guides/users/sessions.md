# Session Management

ForgeLLM provides `ChatSession` for managing conversation history.

## Basic Session Usage

```python
from forge_llm import ChatAgent, ChatSession

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession(system_prompt="You are a helpful assistant.")

# Conversation maintains context
agent.chat("My name is Alice.", session=session)
response = agent.chat("What's my name?", session=session)
print(response.content)  # "Your name is Alice"
```

## Session with System Prompt

```python
session = ChatSession(
    system_prompt="You are a Python expert. Answer only with code."
)

agent.chat("How do I read a file?", session=session)
# Response will be Python code
```

## Token Limits

Prevent context overflow with `max_tokens`:

```python
session = ChatSession(
    system_prompt="Be concise.",
    max_tokens=2000,  # Limit context size
)

try:
    for i in range(100):
        agent.chat(f"Message {i}", session=session)
except ContextOverflowError:
    print("Context exceeded limit!")
```

## Auto-Compaction

Enable automatic message compaction:

```python
from forge_llm import TruncateCompactor

session = ChatSession(
    system_prompt="Be helpful.",
    max_tokens=2000,
    compactor=TruncateCompactor(),  # Auto-compact when limit reached
    safety_margin=0.8,  # Compact at 80% capacity
)

# Session automatically removes old messages when needed
for i in range(100):
    agent.chat(f"Message {i}", session=session)
    print(f"Token estimate: {session.estimate_tokens()}")
```

## Manual Compaction

Compact messages on demand:

```python
session = ChatSession(
    system_prompt="Be helpful.",
    compactor=TruncateCompactor(),
)

# Add many messages
for i in range(50):
    agent.chat(f"Tell me about topic {i}", session=session)

# Manually compact
session.compact(target_tokens=1000)
print(f"Messages after compaction: {len(session.messages)}")
```

## Session Properties

```python
session = ChatSession(system_prompt="Hello")
agent.chat("Hi there", session=session)

# Access session data
print(session.session_id)        # Unique ID
print(session.messages)          # All messages
print(session.last_message)      # Most recent message
print(session.estimate_tokens()) # Estimated token count
```

## Clearing Session

```python
# Clear but keep system prompt
session.clear(preserve_system=True)

# Clear everything
session.clear(preserve_system=False)
```

## Cross-Provider Sessions

Same session works with multiple providers:

```python
from forge_llm import ChatAgent, ChatSession

# Create session
session = ChatSession(system_prompt="Answer in one word.")

# Use with OpenAI
openai_agent = ChatAgent(provider="openai", model="gpt-4o-mini")
openai_agent.chat("What color is the sky?", session=session)

# Continue with Anthropic (has context from OpenAI)
anthropic_agent = ChatAgent(provider="anthropic", model="claude-3-haiku-20240307")
response = anthropic_agent.chat("What was my question about?", session=session)
print(response.content)  # Knows about "color" or "sky"
```

## Manual Message Management

```python
from forge_llm import ChatMessage

session = ChatSession()

# Add messages manually
session.add_message(ChatMessage.user("Hello"))
session.add_message(ChatMessage.assistant("Hi! How can I help?"))
session.add_message(ChatMessage.user("Tell me a joke"))

# Use with agent
response = agent.chat(session=session)
```

## Session Serialization

```python
# Convert to dict list (for storage)
messages_dict = session.to_dict_list()

# Recreate session
new_session = ChatSession()
for msg_dict in messages_dict:
    new_session.add_message(ChatMessage.from_dict(msg_dict))
```

## Safety Margin

The `safety_margin` prevents overflow before hitting exact limit:

```python
session = ChatSession(
    max_tokens=2000,
    safety_margin=0.8,  # Effective limit is 1600 tokens (80%)
)
```

This ensures room for:
- The next user message
- The response
- Token estimation inaccuracies

## Best Practices

1. **Always use sessions** for multi-turn conversations
2. **Set max_tokens** for production to prevent runaway costs
3. **Use compaction** for long conversations
4. **Keep safety_margin** at 0.7-0.8 for reliability
5. **Use system prompts** to set consistent behavior
