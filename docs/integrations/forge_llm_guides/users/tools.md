# Tool Calling Guide

ForgeLLM enables LLMs to call Python functions as tools.

## Basic Tool Registration

```python
from forge_llm import ChatAgent
from forge_llm.application.tools import ToolRegistry

# Create registry
registry = ToolRegistry()

# Register tool using decorator
@registry.tool
def get_weather(location: str) -> str:
    """Get the current weather for a location."""
    # In real usage, call a weather API
    return f"Sunny, 22°C in {location}"

# Create agent with tools
agent = ChatAgent(
    provider="openai",
    model="gpt-4o-mini",
    tools=registry,
)

# LLM will call the tool automatically
response = agent.chat("What's the weather in Paris?")
print(response.content)  # "The weather in Paris is sunny, 22°C"
```

## Tool Requirements

Tools must have:

1. **Type hints** - All parameters need type annotations
2. **Docstring** - Description for the LLM
3. **Return type** - Should return str (or be convertible to str)

```python
@registry.tool
def calculate(operation: str, a: float, b: float) -> str:
    """
    Perform a mathematical calculation.

    Args:
        operation: The operation (add, subtract, multiply, divide)
        a: First number
        b: Second number
    """
    ops = {
        "add": a + b,
        "subtract": a - b,
        "multiply": a * b,
        "divide": a / b if b != 0 else "undefined",
    }
    result = ops.get(operation, "unknown operation")
    return f"{a} {operation} {b} = {result}"
```

## Multiple Tools

Register multiple tools in the same registry:

```python
registry = ToolRegistry()

@registry.tool
def get_weather(location: str) -> str:
    """Get weather for a location."""
    return f"Sunny in {location}"

@registry.tool
def get_time(timezone: str) -> str:
    """Get current time in a timezone."""
    return f"12:00 PM in {timezone}"

@registry.tool
def search_web(query: str) -> str:
    """Search the web for information."""
    return f"Results for: {query}"

agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)
```

## Manual Tool Execution

Disable auto-execution to handle tools yourself:

```python
agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)

# Disable auto-execution
response = agent.chat(
    "What's the weather?",
    auto_execute_tools=False,
)

# Check for tool calls
if response.message.tool_calls:
    for call in response.message.tool_calls:
        print(f"Tool: {call['function']['name']}")
        print(f"Args: {call['function']['arguments']}")

    # Execute manually
    from forge_llm import ToolCall
    tool_calls = [ToolCall.from_openai(tc) for tc in response.message.tool_calls]
    results = agent.execute_tool_calls(tool_calls)

    for result in results:
        print(f"Result: {result.content}")
```

## Tool Validation

ForgeLLM validates tool arguments automatically:

```python
@registry.tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    return f"Email sent to {to}"

# If LLM passes wrong types, validation catches it
# Result will contain error message instead of crashing
```

## Tool Chaining

LLMs can call multiple tools in sequence:

```python
@registry.tool
def get_user_location(user_id: str) -> str:
    """Get user's current location."""
    return "Paris, France"

@registry.tool
def get_weather(location: str) -> str:
    """Get weather for location."""
    return "Sunny, 22°C"

# LLM will:
# 1. Call get_user_location to find location
# 2. Call get_weather with that location
response = agent.chat("What's the weather where user123 is?")
```

## Streaming with Tools

Tools work with streaming too:

```python
for chunk in agent.stream_chat("What's the weather in Tokyo?"):
    if chunk.tool_calls:
        print("Tool called:", chunk.tool_calls)
    if chunk.content:
        print(chunk.content, end="")
```

## Cross-Provider Tools

Same tools work with any provider:

```python
registry = ToolRegistry()

@registry.tool
def translate(text: str, target_language: str) -> str:
    """Translate text to target language."""
    return f"[Translated to {target_language}]: {text}"

# Works with OpenAI
openai_agent = ChatAgent(provider="openai", model="gpt-4o-mini", tools=registry)
openai_agent.chat("Translate 'hello' to Spanish")

# Same tools work with Anthropic
anthropic_agent = ChatAgent(provider="anthropic", model="claude-3-haiku-20240307", tools=registry)
anthropic_agent.chat("Translate 'hello' to Spanish")
```

## Error Handling in Tools

Handle errors gracefully:

```python
@registry.tool
def divide(a: float, b: float) -> str:
    """Divide two numbers."""
    try:
        if b == 0:
            return "Error: Cannot divide by zero"
        return str(a / b)
    except Exception as e:
        return f"Error: {str(e)}"
```

## Best Practices

1. **Clear descriptions** - LLMs decide when to use tools based on descriptions
2. **Simple types** - Use str, int, float, bool for parameters
3. **Return strings** - Results are sent back to LLM as text
4. **Handle errors** - Return error messages instead of raising exceptions
5. **Be specific** - "Get current weather" is better than "Get data"

## Advanced: Custom Tool Port

For complex tools, implement `IToolPort`:

```python
from forge_llm.application.ports import IToolPort
from forge_llm.domain.entities import ToolCall, ToolDefinition, ToolResult

class MyCustomTool(IToolPort):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="custom_tool",
            description="A custom tool",
            parameters={
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                },
                "required": ["input"]
            }
        )

    def execute(self, call: ToolCall) -> ToolResult:
        # Custom logic
        return ToolResult(
            tool_call_id=call.id,
            content="Custom result"
        )

# Register custom tool
registry.register(MyCustomTool())
```
