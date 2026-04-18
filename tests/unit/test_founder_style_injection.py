"""Contracts for Phase 12.5b — founder_style_for injected into prompts.

The key property: no rolled style yet → base prompt unchanged (zero
extra tokens). Once a rule exists → it's appended with a clear separator.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.feedback_loop import augment_system_prompt


@pytest.mark.asyncio
async def test_no_style_returns_base_prompt_unchanged():
    """Before any edits accumulate, system prompt must NOT grow."""
    base = "You are a customer service agent."
    with patch("src.agents.feedback_loop.founder_style_for",
               new=AsyncMock(return_value=None)):
        result = await augment_system_prompt(base, "customer_service", "customer_response")
    assert result == base


@pytest.mark.asyncio
async def test_style_appended_to_base():
    base = "You are a customer service agent."
    style = "- Always use em dashes\n- Sign off as 'J'"
    with patch("src.agents.feedback_loop.founder_style_for",
               new=AsyncMock(return_value=style)):
        result = await augment_system_prompt(base, "customer_service", "customer_response")
    assert result.startswith(base)
    assert style in result
    assert "Founder voice rules" in result
    # The appended rules must be positioned AFTER base for token-locality
    assert result.index(base) < result.index(style)


@pytest.mark.asyncio
async def test_style_rules_explicitly_override_base():
    """When rules conflict with base prompt, the rules must win — the
    prompt says so. Regression guard on the phrasing."""
    with patch("src.agents.feedback_loop.founder_style_for",
               new=AsyncMock(return_value="- Always say 'thanks'")):
        result = await augment_system_prompt("base prompt", "x", "y")
    assert "rules win" in result.lower()


@pytest.mark.asyncio
async def test_empty_string_style_treated_as_no_style():
    """An empty string returned from the DB (edge case) must NOT append
    an empty block — that would look broken in the prompt."""
    with patch("src.agents.feedback_loop.founder_style_for",
               new=AsyncMock(return_value="")):
        result = await augment_system_prompt("base", "x", "y")
    assert result == "base"
