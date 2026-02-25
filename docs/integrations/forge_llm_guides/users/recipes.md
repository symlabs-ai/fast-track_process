# Common Recipes

Practical examples for common ForgeLLM use cases.

## Simple Q&A Bot

```python
from forge_llm import ChatAgent, ChatSession

def create_qa_bot(provider="openai", model="gpt-4o-mini"):
    """Create a simple Q&A bot."""
    agent = ChatAgent(provider=provider, model=model)
    session = ChatSession(
        system_prompt="You are a helpful assistant. Answer questions clearly and concisely."
    )

    def ask(question: str) -> str:
        response = agent.chat(question, session=session)
        return response.content

    return ask

# Usage
qa = create_qa_bot()
print(qa("What is Python?"))
print(qa("Give me an example"))  # Has context from previous question
```

## Code Assistant

```python
from forge_llm import ChatAgent, ChatSession

agent = ChatAgent(provider="openai", model="gpt-4o-mini")
session = ChatSession(
    system_prompt="""You are a Python coding assistant.
    - Always respond with working Python code
    - Include brief comments explaining the code
    - Follow PEP 8 style guidelines"""
)

def get_code(task: str) -> str:
    """Get Python code for a task."""
    response = agent.chat(task, session=session)
    return response.content

# Usage
code = get_code("Write a function to check if a number is prime")
print(code)
```

## Weather Tool Agent

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

registry = ToolRegistry()

@registry.tool
def get_weather(location: str, units: str = "celsius") -> str:
    """Get current weather for a location.

    Args:
        location: City name or coordinates
        units: Temperature units (celsius or fahrenheit)
    """
    # In production, call a real weather API
    return f"Weather in {location}: Sunny, 22°{'C' if units == 'celsius' else 'F'}"

@registry.tool
def get_forecast(location: str, days: int = 3) -> str:
    """Get weather forecast for upcoming days.

    Args:
        location: City name
        days: Number of days (1-7)
    """
    return f"Forecast for {location}: Sunny for {days} days"

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)

# Agent automatically uses tools
response = agent.chat("What's the weather like in Tokyo? Also give me a 5 day forecast.")
print(response.content)
```

## Multi-Provider Fallback

```python
from forge_llm import ChatAgent
from forge_llm.domain import AuthenticationError, RequestTimeoutError

def chat_with_fallback(message: str, providers: list[dict]) -> str:
    """Try multiple providers in order until one succeeds."""
    for config in providers:
        try:
            agent = ChatAgent(**config)
            response = agent.chat(message)
            return response.content
        except (AuthenticationError, RequestTimeoutError) as e:
            print(f"Provider {config['provider']} failed: {e}")
            continue

    raise Exception("All providers failed")

# Usage
providers = [
    {"provider": "openai", "model": "gpt-4o-mini"},
    {"provider": "anthropic", "model": "claude-3-haiku-20240307"},
    {"provider": "ollama", "model": "llama2"},
]

response = chat_with_fallback("Hello!", providers)
```

## Streaming Chat Interface

```python
from forge_llm import ChatAgent, ChatSession

def interactive_chat(provider="openai", model="gpt-4o-mini"):
    """Interactive streaming chat."""
    agent = ChatAgent(provider=provider, model=model)
    session = ChatSession(system_prompt="Be helpful and friendly.")

    print("Chat started. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ")
        if user_input.lower() == 'quit':
            break

        print("AI: ", end="")
        for chunk in agent.stream_chat(user_input, session=session):
            if chunk.content:
                print(chunk.content, end="", flush=True)
        print("\n")

# Usage
interactive_chat()
```

## Document Summarizer

```python
from forge_llm import ChatAgent

def summarize(text: str, max_words: int = 100) -> str:
    """Summarize text to specified word count."""
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")

    prompt = f"""Summarize the following text in {max_words} words or less:

{text}

Summary:"""

    response = agent.chat(prompt)
    return response.content

# Usage
long_text = """..."""  # Your document
summary = summarize(long_text, max_words=50)
```

## Structured Data Extraction

```python
import json
from forge_llm import ChatAgent

def extract_entities(text: str) -> dict:
    """Extract structured data from text."""
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")

    prompt = f"""Extract the following from the text and return as JSON:
- names (list of person names)
- locations (list of places)
- dates (list of dates mentioned)

Text: {text}

JSON output:"""

    response = agent.chat(prompt)

    # Parse JSON from response
    try:
        return json.loads(response.content)
    except json.JSONDecodeError:
        return {"error": "Failed to parse", "raw": response.content}

# Usage
text = "John met Sarah in Paris on January 15th, 2024."
entities = extract_entities(text)
print(entities)
```

## Token-Aware Long Conversation

```python
from forge_llm import ChatAgent, ChatSession, TruncateCompactor

def create_long_conversation_bot():
    """Bot that handles long conversations with auto-compaction."""
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")
    session = ChatSession(
        system_prompt="You are a helpful assistant with memory of our conversation.",
        max_tokens=4000,
        compactor=TruncateCompactor(),
        safety_margin=0.7,
    )

    def chat(message: str) -> str:
        response = agent.chat(message, session=session)
        tokens = session.estimate_tokens()
        return f"{response.content}\n[~{tokens} tokens used]"

    return chat

# Usage - can handle very long conversations
bot = create_long_conversation_bot()
for i in range(100):
    print(bot(f"Tell me fact #{i} about space"))
```

## LLM-Summarized Long Conversation

For better context retention, use `SummarizeCompactor` which uses an LLM to create summaries instead of simply truncating messages.

```python
from forge_llm import ChatAgent, ChatSession, SummarizeCompactor

def create_smart_conversation_bot():
    """Bot that summarizes old messages to retain context."""
    agent = ChatAgent(provider="openai", model="gpt-4o-mini")

    # SummarizeCompactor uses the agent to generate summaries
    compactor = SummarizeCompactor(
        agent=agent,
        summary_tokens=200,  # Target summary size
        keep_recent=4,       # Keep last 4 messages intact
        max_retries=3,       # Retry on API failures
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant with memory of our conversation.",
        max_tokens=4000,
        compactor=compactor,
        safety_margin=0.7,
    )

    def chat(message: str) -> str:
        response = agent.chat(message, session=session)
        tokens = session.estimate_tokens()
        return f"{response.content}\n[~{tokens} tokens used]"

    return chat

# Usage - retains context through summarization
bot = create_smart_conversation_bot()
print(bot("My name is Alice and I work as a data scientist"))
print(bot("I'm interested in machine learning"))
# ... many messages later, the bot still knows your name via the summary
print(bot("What do you remember about me?"))
```

### Custom Summarization Prompt

You can customize the summarization behavior:

```python
from forge_llm import ChatAgent, ChatSession, SummarizeCompactor
from forge_llm.prompts import load_prompt

agent = ChatAgent(provider="openai", model="gpt-4o-mini")

# Option 1: Use built-in prompt from prompts module
compactor = SummarizeCompactor(agent)  # Uses prompts/summarization.md

# Option 2: Custom prompt string
compactor = SummarizeCompactor(
    agent,
    summary_prompt="""Create a brief summary focusing on:
- Key decisions made
- User preferences mentioned
- Important facts

Conversation:
{messages}

Summary:"""
)

# Option 3: Load from custom file
compactor = SummarizeCompactor(
    agent,
    prompt_file="my_prompts/custom_summary.md"
)
```

### Async Version for High-Performance Applications

```python
import asyncio
from forge_llm import AsyncChatAgent, ChatSession, AsyncSummarizeCompactor

async def async_conversation():
    agent = AsyncChatAgent(provider="openai", model="gpt-4o-mini")

    compactor = AsyncSummarizeCompactor(
        agent=agent,
        summary_tokens=200,
        keep_recent=4,
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant.",
        max_tokens=4000,
    )

    # For async compaction, call compact() directly when needed
    messages = session.messages
    if session.estimate_tokens() > 3000:
        compacted = await compactor.compact(messages, target_tokens=2000)
        session._messages = compacted

    response = await agent.chat("Hello!", session=session)
    return response.content

# Usage
result = asyncio.run(async_conversation())
```

## Batch Processing

```python
from forge_llm import ChatAgent

def batch_chat(prompts: list[str], provider="openai", model="gpt-4o-mini") -> list[str]:
    """Process multiple prompts."""
    agent = ChatAgent(provider=provider, model=model)
    results = []

    for prompt in prompts:
        response = agent.chat(prompt)
        results.append(response.content)

    return results

# Usage
questions = [
    "What is Python?",
    "What is JavaScript?",
    "What is Rust?",
]
answers = batch_chat(questions)
for q, a in zip(questions, answers):
    print(f"Q: {q}\nA: {a}\n")
```

## Translation Tool

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

registry = ToolRegistry()

@registry.tool
def translate(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text between languages.

    Args:
        text: Text to translate
        source_lang: Source language (e.g., 'English', 'Spanish')
        target_lang: Target language
    """
    # Use another LLM call for translation
    translator = ChatAgent(provider="openai", model="gpt-4o-mini")
    response = translator.chat(
        f"Translate from {source_lang} to {target_lang}: {text}"
    )
    return response.content

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)
response = agent.chat("Translate 'Hello, how are you?' to Spanish")
print(response.content)
```

## Custom System Prompts by Task

```python
from forge_llm import ChatAgent, ChatSession

PROMPTS = {
    "coder": "You are an expert programmer. Respond only with code.",
    "teacher": "You are a patient teacher. Explain concepts simply.",
    "editor": "You are a professional editor. Fix grammar and improve clarity.",
    "translator": "You are a translator. Translate to the requested language.",
}

def specialized_agent(task: str, provider="openai", model="gpt-4o-mini"):
    """Create a specialized agent for a task."""
    if task not in PROMPTS:
        raise ValueError(f"Unknown task: {task}")

    agent = ChatAgent(provider=provider, model=model)
    session = ChatSession(system_prompt=PROMPTS[task])

    def chat(message: str) -> str:
        return agent.chat(message, session=session).content

    return chat

# Usage
coder = specialized_agent("coder")
print(coder("Write a function to sort a list"))

teacher = specialized_agent("teacher")
print(teacher("Explain recursion"))
```
