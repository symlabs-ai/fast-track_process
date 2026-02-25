"""
Basic Chat Example - Getting started with ForgeLLM.

This example demonstrates basic chat functionality with different providers.
"""
import os

from forge_llm import ChatAgent


def main() -> None:
    # Create agent with OpenAI
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    # Simple chat
    response = agent.chat("What is the capital of France?")
    print(f"Response: {response.content}")
    print(f"Tokens used: {response.token_usage.total_tokens}")
    print(f"Model: {response.metadata.model}")


if __name__ == "__main__":
    main()
