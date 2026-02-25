"""
OpenRouter Example - Access multiple LLM providers through a single API.

OpenRouter provides unified access to models from OpenAI, Anthropic, Google,
Meta, Mistral, and more through a single API key.
"""
import os

from forge_llm import ChatAgent


def main() -> None:
    # OpenRouter provides access to many models via a single API
    # Get your API key at https://openrouter.ai

    # Example 1: Use GPT-4 through OpenRouter
    agent_gpt4 = ChatAgent(
        provider="openrouter",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model="openai/gpt-4o",
    )

    response = agent_gpt4.chat("Explain quantum computing in one sentence.")
    print(f"GPT-4: {response.content}\n")

    # Example 2: Use Claude through OpenRouter
    agent_claude = ChatAgent(
        provider="openrouter",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model="anthropic/claude-3-haiku",
    )

    response = agent_claude.chat("Explain quantum computing in one sentence.")
    print(f"Claude: {response.content}\n")

    # Example 3: Use Llama through OpenRouter
    agent_llama = ChatAgent(
        provider="openrouter",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model="meta-llama/llama-3-70b-instruct",
    )

    response = agent_llama.chat("Explain quantum computing in one sentence.")
    print(f"Llama: {response.content}\n")


def compare_providers() -> None:
    """Compare responses from different providers."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    models = [
        "openai/gpt-4o-mini",
        "anthropic/claude-3-haiku",
        "mistralai/mistral-large",
    ]

    question = "What is the meaning of life? Answer in exactly 10 words."

    for model in models:
        agent = ChatAgent(
            provider="openrouter",
            api_key=api_key,
            model=model,
        )
        response = agent.chat(question)
        print(f"{model}:")
        print(f"  {response.content}\n")


if __name__ == "__main__":
    main()
    # compare_providers()
