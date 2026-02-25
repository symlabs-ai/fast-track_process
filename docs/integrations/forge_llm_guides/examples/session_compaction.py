"""
Session Compaction Example - Manage long conversations with context compaction.

This example demonstrates different strategies for managing conversation context
when it exceeds the model's token limit.
"""
import os

from forge_llm import (
    AsyncChatAgent,
    AsyncSummarizeCompactor,
    ChatAgent,
    ChatSession,
    SummarizeCompactor,
    TruncateCompactor,
)


def truncate_strategy() -> None:
    """Use truncation to manage context - removes oldest messages."""
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # TruncateCompactor removes oldest messages to fit context window
    session = ChatSession(
        system_prompt="You are a helpful assistant.",
        max_tokens=4000,
        compactor=TruncateCompactor(),
    )

    # Have a long conversation
    topics = [
        "Tell me about Python",
        "How about JavaScript?",
        "What is Rust?",
        "Compare them all",
    ]

    for topic in topics:
        response = agent.chat(topic, session=session)
        print(f"Q: {topic}")
        print(f"A: {response.content[:100]}...")
        print(f"   Tokens: {response.token_usage.total_tokens}")
        print()


def summarize_strategy() -> None:
    """Use LLM summarization to compress older context."""
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # SummarizeCompactor uses the LLM to summarize old messages
    compactor = SummarizeCompactor(
        agent=agent,
        summary_tokens=200,  # Target summary length
        keep_recent=4,  # Keep last 4 messages intact
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant. Remember our conversation.",
        max_tokens=2000,  # Lower limit to trigger compaction sooner
        compactor=compactor,
    )

    # Long conversation
    messages = [
        "My name is Alice and I work as a data scientist.",
        "I'm interested in machine learning, especially NLP.",
        "I've been working with transformers for 2 years.",
        "What projects would you recommend for someone with my background?",
        "Can you summarize what you know about me?",
    ]

    for msg in messages:
        response = agent.chat(msg, session=session)
        print(f"User: {msg}")
        print(f"Assistant: {response.content[:200]}...")
        print(f"Session messages: {len(session.messages)}")
        print()


def summarize_with_retry() -> None:
    """Use SummarizeCompactor with retry configuration for reliability."""
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # Configure retry behavior for production reliability
    compactor = SummarizeCompactor(
        agent=agent,
        summary_tokens=200,
        keep_recent=4,
        max_retries=3,  # Retry up to 3 times on failure
        retry_delay=1.0,  # Start with 1 second delay (exponential backoff)
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant.",
        max_tokens=2000,
        compactor=compactor,
    )

    # If summarization fails after all retries, it falls back to truncation
    response = agent.chat("Hello! Tell me about yourself.", session=session)
    print(f"Response: {response.content}")


def summarize_with_custom_prompt() -> None:
    """Use a custom summarization prompt."""
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # Custom prompt focusing on specific aspects
    custom_prompt = """Create a brief summary of this conversation focusing on:
- User preferences and interests
- Key facts about the user
- Decisions or conclusions reached

Conversation:
{messages}

Summary:"""

    compactor = SummarizeCompactor(
        agent=agent,
        summary_tokens=150,
        keep_recent=4,
        summary_prompt=custom_prompt,
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant.",
        max_tokens=2000,
        compactor=compactor,
    )

    messages = [
        "I prefer Python over JavaScript for backend work.",
        "I'm building a REST API for my startup.",
        "We decided to use FastAPI for its async support.",
        "What middleware should I use for authentication?",
    ]

    for msg in messages:
        response = agent.chat(msg, session=session)
        print(f"User: {msg}")
        print(f"Assistant: {response.content[:150]}...")
        print()


def summarize_from_prompt_file() -> None:
    """Load summarization prompt from a markdown file."""
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # Load prompt from prompts/summarization.md (extracts first code block)
    compactor = SummarizeCompactor(
        agent=agent,
        prompt_file="prompts/summarization.md",
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant.",
        max_tokens=2000,
        compactor=compactor,
    )

    response = agent.chat("Hello! Let's have a conversation.", session=session)
    print(f"Response: {response.content}")


async def async_summarize_strategy() -> None:
    """Use AsyncSummarizeCompactor for async applications."""
    agent = AsyncChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # AsyncSummarizeCompactor for async code
    compactor = AsyncSummarizeCompactor(
        agent=agent,
        summary_tokens=200,
        keep_recent=4,
        max_retries=3,
        retry_delay=0.5,
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant.",
        max_tokens=2000,
    )

    # Chat normally
    messages = [
        "My name is Bob and I'm a software engineer.",
        "I specialize in distributed systems.",
        "I'm working on a microservices architecture.",
        "What do you know about me?",
    ]

    for msg in messages:
        response = await agent.chat(msg, session=session)
        print(f"User: {msg}")
        print(f"Assistant: {response.content[:150]}...")

        # Manually trigger compaction if needed
        if session.estimate_tokens() > 1500:
            print("Compacting session...")
            compacted = await compactor.compact(
                session.messages, target_tokens=1000
            )
            session._messages = compacted
            print(f"Session compacted to {len(session.messages)} messages")

        print()


def ollama_local_session() -> None:
    """Use Ollama for local LLM with session management."""
    agent = ChatAgent(
        provider="ollama",
        model="llama3",
        base_url="http://localhost:11434",  # Default Ollama URL
    )

    session = ChatSession(
        system_prompt="You are a helpful coding assistant.",
        max_tokens=4000,
        compactor=TruncateCompactor(),
    )

    # Chat with local model
    response = agent.chat(
        "Write a Python function to reverse a string", session=session
    )
    print(response.content)


if __name__ == "__main__":
    print("=== Truncate Strategy ===")
    truncate_strategy()

    # print("\n=== Summarize Strategy ===")
    # summarize_strategy()

    # print("\n=== Summarize with Retry ===")
    # summarize_with_retry()

    # print("\n=== Custom Prompt ===")
    # summarize_with_custom_prompt()

    # print("\n=== Prompt from File ===")
    # summarize_from_prompt_file()

    # print("\n=== Async Summarize ===")
    # asyncio.run(async_summarize_strategy())

    # print("\n=== Ollama Local ===")
    # ollama_local_session()
