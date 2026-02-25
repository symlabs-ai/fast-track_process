"""
Structured Logging Example - JSON logging with correlation IDs.

This example demonstrates how to use ForgeLLM's structured logging
for production observability.
"""
import os
import time

from forge_llm import ChatAgent
from forge_llm.infrastructure.logging import LogService, configure_logging


def main() -> None:
    # Configure JSON logging (enabled by default)
    configure_logging(json_output=True, log_level="DEBUG")

    logger = LogService("my_app")

    # Basic structured logging
    logger.info("Application started", version="1.0.0", environment="production")

    # Use correlation IDs for request tracing
    with LogService.correlation_context() as correlation_id:
        logger.info("Processing request", correlation_id=correlation_id)

        # All logs within this context will include the correlation ID
        process_chat_request()

        logger.info("Request completed", correlation_id=correlation_id)


def process_chat_request() -> None:
    """Process a chat request with timing."""
    logger = LogService("chat_processor")

    # Bind context for all subsequent logs
    bound_logger = logger.bind(request_type="chat", user_id="user123")

    # Time the LLM call
    with LogService.timed("llm_request", logger=bound_logger, provider="openai"):
        # Simulated LLM call
        time.sleep(0.1)
        bound_logger.info("LLM response received", tokens=150)


def example_with_real_agent() -> None:
    """Example with actual ChatAgent."""
    logger = LogService("chat_example")

    with LogService.correlation_context("req-12345"):
        logger.info("Starting chat session")

        with LogService.timed("chat_operation", provider="openai") as timing:
            agent = ChatAgent(
                provider="openai",
                api_key=os.getenv("OPENAI_API_KEY"),
                model="gpt-4o-mini",
            )

            response = agent.chat("Hello!")

        logger.info(
            "Chat completed",
            elapsed_ms=timing["elapsed_ms"],
            tokens=response.token_usage.total_tokens,
        )


if __name__ == "__main__":
    main()
    # example_with_real_agent()
