"""
Async Chat Example - Non-blocking chat with AsyncChatAgent.

This example demonstrates async/await patterns for concurrent LLM calls.
"""
import asyncio
import os

from forge_llm import AsyncChatAgent, AsyncSummarizeCompactor, ChatSession


async def basic_async_chat() -> None:
    """Basic async chat example."""
    agent = AsyncChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # Single async chat
    response = await agent.chat("What is 2 + 2?")
    print(f"Response: {response.content}")


async def concurrent_requests() -> None:
    """Run multiple requests concurrently."""
    agent = AsyncChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    questions = [
        "What is the capital of Japan?",
        "What is the largest planet?",
        "What year did WW2 end?",
    ]

    # Run multiple requests concurrently
    tasks = [agent.chat(q) for q in questions]
    responses = await asyncio.gather(*tasks)

    for question, response in zip(questions, responses, strict=False):
        print(f"\nQ: {question}")
        print(f"A: {response.content}")


async def streaming_example() -> None:
    """Example of async streaming."""
    agent = AsyncChatAgent(
        provider="anthropic",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model="claude-3-haiku-20240307",
    )

    print("\nStreaming response:")
    async for chunk in agent.stream_chat("Tell me a short joke"):
        print(chunk.content, end="", flush=True)
    print()


async def async_with_session() -> None:
    """Async chat with session management."""
    agent = AsyncChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    session = ChatSession(
        system_prompt="You are a helpful assistant. Remember our conversation.",
        max_tokens=4000,
    )

    messages = [
        "My name is Alice.",
        "I work as a data scientist.",
        "What's my name and profession?",
    ]

    for msg in messages:
        response = await agent.chat(msg, session=session)
        print(f"User: {msg}")
        print(f"Assistant: {response.content}")
        print()


async def async_with_summarization() -> None:
    """Async chat with LLM-based context summarization."""
    agent = AsyncChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # AsyncSummarizeCompactor for async context compaction
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

    messages = [
        "My name is Bob and I'm building a startup.",
        "We're focused on AI-powered developer tools.",
        "Our main product is a code review assistant.",
        "We've raised $2M in seed funding.",
        "What do you remember about my company?",
    ]

    for msg in messages:
        response = await agent.chat(msg, session=session)
        print(f"User: {msg}")
        print(f"Assistant: {response.content[:200]}...")

        # Compact if approaching token limit
        if session.estimate_tokens() > 1500:
            print("  [Compacting context...]")
            compacted = await compactor.compact(
                session.messages, target_tokens=1000
            )
            session._messages = compacted
            print(f"  [Compacted to {len(session.messages)} messages]")

        print()


async def batch_processing() -> None:
    """Process multiple items concurrently with rate limiting."""
    agent = AsyncChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    items = [
        "Summarize: Python is a programming language.",
        "Summarize: JavaScript runs in browsers.",
        "Summarize: Rust is memory-safe.",
        "Summarize: Go has great concurrency.",
    ]

    # Process in batches of 2 for rate limiting
    batch_size = 2
    results = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        tasks = [agent.chat(item) for item in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        print(f"Processed batch {i // batch_size + 1}")

    for item, result in zip(items, results, strict=False):
        print(f"\nInput: {item}")
        print(f"Output: {result.content}")


if __name__ == "__main__":
    print("=== Basic Async Chat ===")
    asyncio.run(basic_async_chat())

    # print("\n=== Concurrent Requests ===")
    # asyncio.run(concurrent_requests())

    # print("\n=== Streaming ===")
    # asyncio.run(streaming_example())

    # print("\n=== Async with Session ===")
    # asyncio.run(async_with_session())

    # print("\n=== Async with Summarization ===")
    # asyncio.run(async_with_summarization())

    # print("\n=== Batch Processing ===")
    # asyncio.run(batch_processing())
