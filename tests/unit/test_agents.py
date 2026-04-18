"""Tests for the agent framework: BaseAgent, ToolRegistry, PolicyEngine, AuditLogger."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import AgentResult, BaseAgent
from src.agents.guardrails import (
    ContentFilterPolicy,
    EmailRateLimitPolicy,
    FraudEscalationPolicy,
    HighValueOrderPolicy,
    HumanRequiredPolicy,
    PolicyDecision,
    PolicyEngine,
    SpendingLimitPolicy,
    TokenBudgetPolicy,
)
from src.agents.tools import ToolRegistry


# ── ToolRegistry Tests ──


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        registry.register(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
            func=lambda: "result",
            risk_tier=1,
        )
        assert "test_tool" in registry.tool_names
        assert len(registry) == 1
        assert registry.get("test_tool") is not None

    def test_get_definitions(self):
        registry = ToolRegistry()
        registry.register(
            name="lookup",
            description="Look up something",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
            func=lambda id: {"found": True},
        )
        defs = registry.get_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "lookup"
        assert defs[0]["description"] == "Look up something"
        assert "properties" in defs[0]["input_schema"]

    async def test_execute_sync_function(self):
        registry = ToolRegistry()
        registry.register(
            name="add",
            description="Add two numbers",
            input_schema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
            func=lambda a, b: a + b,
        )
        result = await registry.execute("add", {"a": 3, "b": 4})
        assert result == 7

    async def test_execute_async_function(self):
        async def async_lookup(id: int):
            return {"id": id, "name": "Test"}

        registry = ToolRegistry()
        registry.register(
            name="async_lookup",
            description="Async lookup",
            input_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
            func=async_lookup,
        )
        result = await registry.execute("async_lookup", {"id": 42})
        assert result == {"id": 42, "name": "Test"}

    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="Unknown tool"):
            await registry.execute("nonexistent", {})

    def test_get_nonexistent(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None


# ── PolicyEngine Tests ──


class TestPolicyEngine:
    async def test_default_allow(self):
        """No policies → allow."""
        engine = PolicyEngine(policies=[])
        decision = await engine.check("any_tool", {}, {})
        assert decision.action == "allow"

    async def test_human_required_refund(self):
        """Refund tools always escalate."""
        policy = HumanRequiredPolicy()
        decision = await policy.evaluate("process_refund", {}, {})
        assert decision is not None
        assert decision.action == "escalate"

    async def test_human_required_complaint_email(self):
        """Sending email for a complaint escalates."""
        policy = HumanRequiredPolicy()
        decision = await policy.evaluate("send_email", {"category": "complaint"}, {})
        assert decision is not None
        assert decision.action == "escalate"

    async def test_human_required_allows_normal_email(self):
        """Normal email category passes through."""
        policy = HumanRequiredPolicy()
        decision = await policy.evaluate("send_email", {"category": "order_status"}, {})
        assert decision is None  # No opinion

    async def test_fraud_escalation_flagged(self):
        """Flagged orders block action tools."""
        policy = FraudEscalationPolicy()
        context = {"fraud_check": {"is_flagged": True, "reasons": ["High value"]}}
        decision = await policy.evaluate("create_shipstation_order", {}, context)
        assert decision is not None
        assert decision.action == "escalate"

    async def test_fraud_escalation_clean(self):
        """Clean orders pass through."""
        policy = FraudEscalationPolicy()
        context = {"fraud_check": {"is_flagged": False, "reasons": []}}
        decision = await policy.evaluate("create_shipstation_order", {}, context)
        assert decision is None

    async def test_fraud_allows_read_tools(self):
        """Read-only tools are allowed even when fraud is flagged."""
        policy = FraudEscalationPolicy()
        context = {"fraud_check": {"is_flagged": True, "reasons": ["Velocity"]}}
        decision = await policy.evaluate("lookup_order", {}, context)
        assert decision is None

    async def test_high_value_escalation(self):
        """Orders above insurance cap escalate."""
        policy = HighValueOrderPolicy()
        context = {"order_total": 3000.00}
        decision = await policy.evaluate("create_shipstation_order", {}, context)
        assert decision is not None
        assert decision.action == "escalate"
        assert "$3,000.00" in decision.reason

    async def test_high_value_under_cap(self):
        """Orders under insurance cap pass through."""
        policy = HighValueOrderPolicy()
        context = {"order_total": 2000.00}
        decision = await policy.evaluate("create_shipstation_order", {}, context)
        assert decision is None

    async def test_spending_limit_over(self):
        """Budget exceeding daily cap is denied."""
        policy = SpendingLimitPolicy()
        decision = await policy.evaluate(
            "adjust_ad_budget", {"budget": 100.00}, {}
        )
        assert decision is not None
        assert decision.action == "deny"

    async def test_spending_limit_under(self):
        """Budget under daily cap passes."""
        policy = SpendingLimitPolicy()
        decision = await policy.evaluate(
            "adjust_ad_budget", {"budget": 50.00}, {}
        )
        assert decision is None

    async def test_spending_limit_ignores_other_tools(self):
        """Non-budget tools are ignored."""
        policy = SpendingLimitPolicy()
        decision = await policy.evaluate("send_email", {}, {})
        assert decision is None

    async def test_email_rate_limit_cart_recovery(self):
        """Cart recovery exceeding weekly limit is denied."""
        policy = EmailRateLimitPolicy()
        context = {"cart_recovery_count_this_week": 2}
        decision = await policy.evaluate("send_cart_recovery", {}, context)
        assert decision is not None
        assert decision.action == "deny"

    async def test_email_rate_limit_under(self):
        """Cart recovery under limit passes."""
        policy = EmailRateLimitPolicy()
        context = {"cart_recovery_count_this_week": 1}
        decision = await policy.evaluate("send_cart_recovery", {}, context)
        assert decision is None

    async def test_email_rate_reorder_cooldown(self):
        """Reorder reminder within cooldown is denied."""
        from datetime import datetime, timedelta, timezone

        policy = EmailRateLimitPolicy()
        recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        context = {"last_reorder_email_at": recent_date}
        decision = await policy.evaluate("send_reorder_reminder", {}, context)
        assert decision is not None
        assert decision.action == "deny"
        assert "cooldown" in decision.reason

    async def test_content_filter_clean(self):
        """Clean content passes."""
        policy = ContentFilterPolicy()
        decision = await policy.evaluate(
            "send_email",
            {"email_body": "Thank you for your order of the diamond bracelet."},
            {},
        )
        assert decision is None

    async def test_content_filter_banned(self):
        """Content with banned words is denied."""
        policy = ContentFilterPolicy()
        decision = await policy.evaluate(
            "send_email",
            {"email_body": "This is a cheap knockoff product."},
            {},
        )
        assert decision is not None
        assert decision.action == "deny"

    async def test_token_budget_exceeded(self):
        """Over token budget is denied."""
        policy = TokenBudgetPolicy()
        context = {"tokens_used_today": 600_000}
        decision = await policy.evaluate("any_tool", {}, context)
        assert decision is not None
        assert decision.action == "deny"

    async def test_token_budget_ok(self):
        """Under token budget passes."""
        policy = TokenBudgetPolicy()
        context = {"tokens_used_today": 100_000}
        decision = await policy.evaluate("any_tool", {}, context)
        assert decision is None

    async def test_engine_first_decision_wins(self):
        """PolicyEngine returns the first non-None decision."""
        engine = PolicyEngine(policies=[
            HumanRequiredPolicy(),
            FraudEscalationPolicy(),
        ])
        # HumanRequired fires first for process_refund
        decision = await engine.check("process_refund", {}, {})
        assert decision.action == "escalate"
        assert decision.policy_name == "human_required"

    async def test_validate_output_clean(self):
        """Clean output passes validation."""
        engine = PolicyEngine(policies=[])
        decision = await engine.validate_output("Your bracelet has shipped!", {})
        assert decision.action == "allow"

    async def test_validate_output_banned(self):
        """Output with banned words is denied."""
        engine = PolicyEngine(policies=[])
        decision = await engine.validate_output("This cheap product is a bargain.", {})
        assert decision.action == "deny"


# ── BaseAgent Tests ──


class TestBaseAgent:
    def _mock_response(self, stop_reason, content):
        """Create a mock Claude response."""
        response = MagicMock()
        response.stop_reason = stop_reason
        response.content = content
        response.usage = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50
        return response

    def _text_block(self, text):
        block = MagicMock()
        block.type = "text"
        block.text = text
        return block

    def _tool_use_block(self, name, input_dict, block_id="tool_1"):
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.input = input_dict
        block.id = block_id
        return block

    @patch("src.agents.base.anthropic.AsyncAnthropic")
    @patch("src.agents.audit.Database")
    async def test_simple_text_response(self, mock_audit_db, mock_anthropic):
        """Agent returns text without tool use."""
        client = AsyncMock()
        mock_anthropic.return_value = client

        text_block = self._text_block("Order processed successfully.")
        response = self._mock_response("end_turn", [text_block])
        client.messages.create = AsyncMock(return_value=response)

        agent = BaseAgent()
        agent.audit = AsyncMock()
        agent.audit.log = AsyncMock(return_value="audit-123")

        result = await agent.run("Process order #123")
        assert result.success is True
        assert "Order processed" in result.message
        assert result.tokens_used == 150

    @patch("src.agents.base.anthropic.AsyncAnthropic")
    @patch("src.agents.audit.Database")
    async def test_tool_use_then_end(self, mock_audit_db, mock_anthropic):
        """Agent calls a tool, then returns text."""
        client = AsyncMock()
        mock_anthropic.return_value = client

        # First response: tool use
        tool_block = self._tool_use_block("lookup_order", {"order_id": 123})
        response1 = self._mock_response("tool_use", [tool_block])

        # Second response: end turn
        text_block = self._text_block("Found the order.")
        response2 = self._mock_response("end_turn", [text_block])

        client.messages.create = AsyncMock(side_effect=[response1, response2])

        # Set up agent with a tool
        registry = ToolRegistry()
        registry.register(
            name="lookup_order",
            description="Look up order",
            input_schema={"type": "object", "properties": {"order_id": {"type": "integer"}}},
            func=lambda order_id: {"id": order_id, "total": 9998.00},
        )

        policies = PolicyEngine(policies=[])  # No policies = allow all

        agent = BaseAgent(tools=registry, policies=policies)
        agent.audit = AsyncMock()
        agent.audit.log = AsyncMock(return_value="audit-456")

        result = await agent.run("Process order #123")
        assert result.success is True
        assert len(result.actions_taken) == 1
        assert result.actions_taken[0]["tool"] == "lookup_order"
        assert result.actions_taken[0]["status"] == "executed"

    @patch("src.agents.base.anthropic.AsyncAnthropic")
    @patch("src.agents.audit.Database")
    async def test_policy_blocks_tool(self, mock_audit_db, mock_anthropic):
        """Policy denies a tool call → agent sees error and adapts."""
        client = AsyncMock()
        mock_anthropic.return_value = client

        # First response: tries to send email
        tool_block = self._tool_use_block("send_email", {"to": "test@example.com"})
        response1 = self._mock_response("tool_use", [tool_block])

        # Second response: adapts after denial
        text_block = self._text_block("Email blocked by policy. Escalating.")
        response2 = self._mock_response("end_turn", [text_block])

        client.messages.create = AsyncMock(side_effect=[response1, response2])

        registry = ToolRegistry()
        registry.register(
            name="send_email",
            description="Send email",
            input_schema={"type": "object", "properties": {}},
            func=lambda: None,
            risk_tier=3,
        )

        # Policy that always denies
        class AlwaysDeny:
            name = "test_deny"
            async def evaluate(self, tool_name, tool_input, context):
                return PolicyDecision(action="deny", reason="Test denial", policy_name="test_deny")

        policies = PolicyEngine(policies=[AlwaysDeny()])
        agent = BaseAgent(tools=registry, policies=policies)
        agent.audit = AsyncMock()
        agent.audit.log = AsyncMock(return_value="audit-789")

        result = await agent.run("Send email to customer")
        assert result.success is True
        assert len(result.actions_taken) == 1
        assert result.actions_taken[0]["status"] == "denied"

    @patch("src.agents.base.anthropic.AsyncAnthropic")
    @patch("src.agents.audit.Database")
    async def test_policy_escalates_tool(self, mock_audit_db, mock_anthropic):
        """Policy escalates a tool call → agent sees escalation, Slack notified."""
        client = AsyncMock()
        mock_anthropic.return_value = client

        tool_block = self._tool_use_block("process_refund", {"amount": 500})
        response1 = self._mock_response("tool_use", [tool_block])

        text_block = self._text_block("Escalated to human review.")
        response2 = self._mock_response("end_turn", [text_block])

        client.messages.create = AsyncMock(side_effect=[response1, response2])

        registry = ToolRegistry()
        registry.register(
            name="process_refund",
            description="Process refund",
            input_schema={"type": "object", "properties": {"amount": {"type": "number"}}},
            func=lambda amount: None,
        )

        policies = PolicyEngine(policies=[HumanRequiredPolicy()])
        agent = BaseAgent(tools=registry, policies=policies)
        agent.audit = AsyncMock()
        agent.audit.log = AsyncMock(return_value="audit-esc")
        agent._slack = AsyncMock()
        agent._slack.send_blocks = AsyncMock()

        result = await agent.run("Process refund for order")
        assert result.escalated is True
        assert result.actions_taken[0]["status"] == "escalated"

    @patch("src.agents.base.anthropic.AsyncAnthropic")
    @patch("src.agents.audit.Database")
    async def test_max_turns_exceeded(self, mock_audit_db, mock_anthropic):
        """Agent stops after max_turns with escalation."""
        client = AsyncMock()
        mock_anthropic.return_value = client

        # Always returns tool_use, never end_turn
        tool_block = self._tool_use_block("lookup_order", {"order_id": 1})
        response = self._mock_response("tool_use", [tool_block])
        client.messages.create = AsyncMock(return_value=response)

        registry = ToolRegistry()
        registry.register(
            name="lookup_order",
            description="Look up order",
            input_schema={"type": "object", "properties": {"order_id": {"type": "integer"}}},
            func=lambda order_id: {"id": order_id},
        )

        agent = BaseAgent(tools=registry, policies=PolicyEngine(policies=[]))
        agent.max_turns = 3
        agent.audit = AsyncMock()
        agent.audit.log = AsyncMock(return_value="audit-max")

        result = await agent.run("Loop forever")
        assert result.success is False
        assert result.escalated is True
        assert "max turns" in result.message.lower()

    @patch("src.agents.base.anthropic.AsyncAnthropic")
    @patch("src.agents.audit.Database")
    async def test_tool_execution_error(self, mock_audit_db, mock_anthropic):
        """Tool raising exception → agent sees is_error and adapts."""
        client = AsyncMock()
        mock_anthropic.return_value = client

        tool_block = self._tool_use_block("failing_tool", {})
        response1 = self._mock_response("tool_use", [tool_block])

        text_block = self._text_block("Tool failed, reporting error.")
        response2 = self._mock_response("end_turn", [text_block])

        client.messages.create = AsyncMock(side_effect=[response1, response2])

        def boom():
            raise RuntimeError("Connection failed")

        registry = ToolRegistry()
        registry.register(
            name="failing_tool",
            description="A tool that fails",
            input_schema={"type": "object", "properties": {}},
            func=boom,
        )

        agent = BaseAgent(tools=registry, policies=PolicyEngine(policies=[]))
        agent.audit = AsyncMock()
        agent.audit.log = AsyncMock(return_value="audit-err")

        result = await agent.run("Try the failing tool")
        assert result.success is True  # Agent handled error gracefully
        assert result.actions_taken[0]["status"] == "error"
        assert "Connection failed" in result.actions_taken[0]["error"]


# ── AgentResult Tests ──


class TestAgentResult:
    def test_defaults(self):
        result = AgentResult(success=True, message="Done")
        assert result.escalated is False
        assert result.actions_taken == []
        assert result.tokens_used == 0
        assert result.audit_id is None
