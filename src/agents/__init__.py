"""Pinaka AI Agents — autonomous operations with guardrails.

This package provides the agentic layer that wraps existing business modules
(shipping, customer service, marketing, finance) with Claude tool_use reasoning
loops, centralized policy guardrails, and audit logging.

Usage:
    from src.agents.order_ops import OrderOpsAgent

    agent = OrderOpsAgent()
    result = await agent.run("Process new order #1234", context)
"""

from src.agents.base import AgentResult, BaseAgent
from src.agents.guardrails import PolicyDecision, PolicyEngine
from src.agents.tools import ToolRegistry

__all__ = [
    "BaseAgent",
    "AgentResult",
    "ToolRegistry",
    "PolicyEngine",
    "PolicyDecision",
]
