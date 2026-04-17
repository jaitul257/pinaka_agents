"""Tests for the weekly competitor intelligence brief."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.marketing.competitor_brief import CompetitorBrief


@pytest.fixture
def brief():
    with patch("src.marketing.competitor_brief.SlackNotifier") as mock_slack_cls, \
         patch("src.marketing.competitor_brief.anthropic.AsyncAnthropic"):
        mock_slack_cls.return_value = AsyncMock()
        b = CompetitorBrief()
        yield b


def _claude_response(text: str):
    """Build a response mock with one text block containing `text`."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    resp = MagicMock()
    resp.content = [text_block]
    return resp


def _claude_response_with_tools(final_text: str):
    """Build a response with web_search tool blocks followed by a final text block."""
    tool_use = MagicMock()
    tool_use.type = "server_tool_use"
    tool_result = MagicMock()
    tool_result.type = "web_search_tool_result"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = final_text
    resp = MagicMock()
    resp.content = [tool_use, tool_result, text_block]
    return resp


@pytest.mark.asyncio
async def test_valid_response_3_observations(brief):
    payload = {
        "observations": [
            {"who": "Vrai", "what": "Lead with 7-day free return",
             "why_matters": "signals confidence at high AOV", "action": "test"},
            {"who": "Catbird", "what": "Editorial founder story on home",
             "why_matters": "trust at $5K", "action": "borrow"},
            {"who": "Aurate", "what": "Gift set bundles",
             "why_matters": "upsell lever", "action": "counter"},
        ]
    }
    brief._claude.messages.create = AsyncMock(
        return_value=_claude_response(json.dumps(payload))
    )

    result = await brief.run_weekly()
    assert len(result.observations) == 3
    assert result.observations[0]["who"] == "Vrai"
    brief._slack.send_blocks.assert_awaited_once()
    blocks = brief._slack.send_blocks.call_args[0][0]
    assert "Competitor Watch" in str(blocks)


@pytest.mark.asyncio
async def test_extracts_text_from_mixed_tool_content(brief):
    """Claude's response interleaves tool_use, tool_result, text blocks.
    We must pick the final text block, not the tool outputs."""
    payload = {"observations": [
        {"who": "Mejuri", "what": "Monday drop", "why_matters": "ritual", "action": "borrow"}
    ]}
    brief._claude.messages.create = AsyncMock(
        return_value=_claude_response_with_tools(json.dumps(payload))
    )

    result = await brief.run_weekly()
    assert len(result.observations) == 1
    assert result.observations[0]["who"] == "Mejuri"


@pytest.mark.asyncio
async def test_caps_observations_at_five(brief):
    payload = {"observations": [
        {"who": f"Brand{i}", "what": "x", "why_matters": "y", "action": "z"}
        for i in range(10)
    ]}
    brief._claude.messages.create = AsyncMock(
        return_value=_claude_response(json.dumps(payload))
    )

    result = await brief.run_weekly()
    assert len(result.observations) == 5


@pytest.mark.asyncio
async def test_claude_failure_posts_empty_notice(brief):
    brief._claude.messages.create = AsyncMock(side_effect=Exception("API down"))

    result = await brief.run_weekly()
    assert result.observations == []
    brief._slack.send_blocks.assert_awaited_once()
    blocks = brief._slack.send_blocks.call_args[0][0]
    assert "No usable observations" in str(blocks) or "empty" in str(blocks).lower()


@pytest.mark.asyncio
async def test_malformed_json_returns_empty(brief):
    brief._claude.messages.create = AsyncMock(
        return_value=_claude_response("no JSON here, just prose about jewelry")
    )
    result = await brief.run_weekly()
    assert result.observations == []


@pytest.mark.asyncio
async def test_web_search_tool_declared_in_request(brief):
    payload = {"observations": []}
    brief._claude.messages.create = AsyncMock(
        return_value=_claude_response(json.dumps(payload))
    )

    await brief.run_weekly()
    call_kwargs = brief._claude.messages.create.call_args.kwargs
    tools = call_kwargs.get("tools", [])
    assert any(t.get("type", "").startswith("web_search") for t in tools), \
        "Competitor brief must declare web_search tool"
