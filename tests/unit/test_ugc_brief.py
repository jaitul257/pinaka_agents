"""Tests for the weekly UGC filming brief generator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.marketing.ugc_brief import UGCBriefGenerator


@pytest.fixture
def gen():
    """UGCBriefGenerator with DB/Slack/Claude mocked."""
    with patch("src.marketing.ugc_brief.AsyncDatabase") as mock_db_cls, \
         patch("src.marketing.ugc_brief.SlackNotifier") as mock_slack_cls, \
         patch("src.marketing.ugc_brief.anthropic.AsyncAnthropic"):
        mock_db_cls.return_value = AsyncMock()
        mock_slack_cls.return_value = AsyncMock()
        g = UGCBriefGenerator()
        yield g


def _claude_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


@pytest.mark.asyncio
async def test_valid_claude_response_posts_3_briefs(gen):
    gen._db.get_all_products = AsyncMock(return_value=[
        {"sku": "AB001", "name": "Classic Tennis", "story": "Handcrafted...",
         "materials": {"metal": "yellow-gold"}}
    ])
    gen._db.get_creative_metrics_range = AsyncMock(return_value=[])

    payload = {
        "briefs": [
            {
                "title": "Wrist in window light",
                "archetype": "wrist-in-the-wild",
                "setup": "Morning light at your desk. Wear the bracelet.",
                "script_beats": ["Raise wrist", "Tilt 45°", "Close-up"],
                "hook_line": "3 seconds before I put it on.",
                "why": "Wrist-first beats product-first at fine-jewelry AOV.",
            },
            {"title": "Atelier clasp", "archetype": "atelier-process", "setup": "x", "script_beats": [], "hook_line": "", "why": ""},
            {"title": "Founder story", "archetype": "founder-talking-head", "setup": "x", "script_beats": [], "hook_line": "", "why": ""},
        ]
    }
    import json as _json
    gen._claude.messages.create = AsyncMock(
        return_value=_claude_response(_json.dumps(payload))
    )

    result = await gen.run_weekly()

    assert len(result.briefs) == 3
    assert result.briefs[0]["archetype"] == "wrist-in-the-wild"
    gen._slack.send_blocks.assert_awaited_once()
    blocks = gen._slack.send_blocks.call_args[0][0]
    text_blob = str(blocks)
    assert "Film this week" in text_blob
    assert "Wrist in window light" in text_blob


@pytest.mark.asyncio
async def test_caps_briefs_at_three_even_if_more_returned(gen):
    gen._db.get_all_products = AsyncMock(return_value=[])
    gen._db.get_creative_metrics_range = AsyncMock(return_value=[])

    import json as _json
    payload = {"briefs": [{"title": f"b{i}", "archetype": "x", "setup": "",
                           "script_beats": [], "hook_line": "", "why": ""} for i in range(5)]}
    gen._claude.messages.create = AsyncMock(
        return_value=_claude_response(_json.dumps(payload))
    )

    result = await gen.run_weekly()
    assert len(result.briefs) == 3


@pytest.mark.asyncio
async def test_claude_error_posts_empty_brief(gen):
    gen._db.get_all_products = AsyncMock(return_value=[])
    gen._db.get_creative_metrics_range = AsyncMock(return_value=[])
    gen._claude.messages.create = AsyncMock(side_effect=Exception("Claude timeout"))

    result = await gen.run_weekly()
    assert result.briefs == []
    gen._slack.send_blocks.assert_awaited_once()
    blocks = gen._slack.send_blocks.call_args[0][0]
    assert "Could not generate" in str(blocks)


@pytest.mark.asyncio
async def test_invalid_json_returns_empty(gen):
    gen._db.get_all_products = AsyncMock(return_value=[])
    gen._db.get_creative_metrics_range = AsyncMock(return_value=[])
    gen._claude.messages.create = AsyncMock(
        return_value=_claude_response("I don't do JSON, here's prose about bracelets.")
    )

    result = await gen.run_weekly()
    assert result.briefs == []


@pytest.mark.asyncio
async def test_works_with_no_products(gen):
    """Empty product table shouldn't crash generation — seasonal context still useful."""
    gen._db.get_all_products = AsyncMock(return_value=[])
    gen._db.get_creative_metrics_range = AsyncMock(return_value=[])

    import json as _json
    payload = {"briefs": [{"title": "b1", "archetype": "founder-talking-head",
                           "setup": "", "script_beats": [], "hook_line": "", "why": ""}]}
    gen._claude.messages.create = AsyncMock(
        return_value=_claude_response(_json.dumps(payload))
    )

    result = await gen.run_weekly()
    assert len(result.briefs) == 1


@pytest.mark.asyncio
async def test_top_archetype_extracted_from_metrics(gen):
    """When ad_creative_metrics has spend data, top-spending ad name is surfaced."""
    gen._db.get_all_products = AsyncMock(return_value=[])
    gen._db.get_creative_metrics_range = AsyncMock(return_value=[
        {"ad_name": "Variant A — window light", "spend": 50.0},
        {"ad_name": "Variant B — atelier", "spend": 120.0},
        {"ad_name": "Variant B — atelier", "spend": 30.0},
    ])
    import json as _json
    gen._claude.messages.create = AsyncMock(
        return_value=_claude_response(_json.dumps({"briefs": []}))
    )

    result = await gen.run_weekly()
    assert result.top_archetype_last_14d == "Variant B — atelier"
    prompt_text = gen._claude.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Variant B — atelier" in prompt_text
