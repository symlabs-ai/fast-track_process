"""
Tool Calling Example - Extend LLM capabilities with custom tools.

This example demonstrates how to define and use tools with the chat agent.
"""
import os

from forge_llm import ChatAgent, ToolRegistry


def main() -> None:
    # Create tool registry
    registry = ToolRegistry()

    # Define tools using decorator
    @registry.tool
    def get_weather(location: str, unit: str = "celsius") -> str:
        """Get the current weather for a location.

        Args:
            location: The city name
            unit: Temperature unit (celsius or fahrenheit)
        """
        # In a real app, call a weather API
        temps = {"celsius": "22C", "fahrenheit": "72F"}
        return f"Sunny, {temps.get(unit, '22C')} in {location}"

    @registry.tool
    def calculate(expression: str) -> str:
        """Calculate a mathematical expression.

        Args:
            expression: Math expression like "2 + 2" or "sqrt(16)"
        """
        import math

        # Safe evaluation with limited scope
        allowed = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos}
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)

    @registry.tool
    def search_database(query: str, limit: int = 5) -> str:
        """Search a database for records.

        Args:
            query: Search query string
            limit: Maximum number of results to return
        """
        # Simulated database search
        return f"Found {limit} results for '{query}'"

    # Create agent with tools
    agent = ChatAgent(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
        tools=registry,
    )

    # Tools are automatically called
    print("Question: What's the weather in Tokyo?")
    response = agent.chat("What's the weather in Tokyo?")
    print(f"Answer: {response.content}\n")

    print("Question: Calculate sqrt(144) + 10")
    response = agent.chat("Calculate sqrt(144) + 10")
    print(f"Answer: {response.content}\n")


if __name__ == "__main__":
    main()
