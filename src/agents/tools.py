"""ToolRegistry — wraps existing business functions as Claude tool_use tools.

Each tool has a name, description (prompt-engineered), JSON Schema for input,
a risk tier (1=read-only, 2=reversible, 3=irreversible), and the underlying
async callable.

The registry generates Claude-compatible tool definitions and dispatches
execution by name.
"""

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A single tool wrapping an existing function."""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]
    risk_tier: int = 1  # 1=read-only, 2=reversible, 3=irreversible


class ToolRegistry:
    """Registry of tools available to an agent.

    Usage:
        registry = ToolRegistry()
        registry.register(
            name="lookup_order",
            description="Look up order details by Shopify order ID.",
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "integer", "description": "Shopify order ID"}},
                "required": ["order_id"],
            },
            func=db.get_order_by_shopify_id,
            risk_tier=1,
        )
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        func: Callable[..., Any],
        risk_tier: int = 1,
    ) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            func=func,
            risk_tier=risk_tier,
        )

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        """Generate Claude-compatible tool definitions for the API call."""
        definitions = []
        for tool in self._tools.values():
            definitions.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            })
        return definitions

    async def execute(self, name: str, tool_input: dict[str, Any]) -> Any:
        """Execute a tool by name with the given input.

        Handles both sync and async functions transparently.
        """
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")

        func = tool.func
        result = func(**tool_input)

        # If the function is async, await it
        if inspect.isawaitable(result):
            result = await result

        return result

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)
