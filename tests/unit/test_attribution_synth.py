"""Unit tests for the weekly attribution synthesizer."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.marketing.attribution_synth import AttributionSynthesizer


def _mk_row(channel: str, detail: str = "", reason: str = "self_purchase") -> dict:
    return {
        "channel_primary": channel,
        "channel_detail": detail,
        "purchase_reason": reason,
    }


@pytest.fixture
def synth():
    """Synthesizer with DB/Slack/Claude all mocked."""
    with patch("src.marketing.attribution_synth.AsyncDatabase") as mock_db_cls, \
         patch("src.marketing.attribution_synth.SlackNotifier") as mock_slack_cls, \
         patch("src.marketing.attribution_synth.anthropic.AsyncAnthropic"):
        mock_db_cls.return_value = AsyncMock()
        mock_slack_cls.return_value = AsyncMock()
        s = AttributionSynthesizer()
        yield s


@pytest.mark.asyncio
async def test_empty_week_posts_no_responses_slack(synth):
    """Zero responses → friendly 'no data' Slack, not an error."""
    synth._db.get_attribution_range = AsyncMock(return_value=[])

    summary = await synth.run_weekly_report(window_days=7)

    assert summary.total_responses == 0
    assert summary.channel_counts == {}
    synth._slack.send_blocks.assert_awaited_once()
    # Claude should NOT have been called on empty input
    blocks_call = synth._slack.send_blocks.call_args[0][0]
    joined = str(blocks_call)
    assert "no responses" in joined.lower() or "no post-purchase" in joined.lower()


@pytest.mark.asyncio
async def test_aggregates_channel_counts_correctly(synth):
    """Multiple responses → sorted by count with correct percentages."""
    synth._db.get_attribution_range = AsyncMock(return_value=[
        _mk_row("instagram"),
        _mk_row("instagram"),
        _mk_row("google_search"),
        _mk_row("friend"),
    ])
    synth._synthesize_free_text = AsyncMock(return_value=[])

    summary = await synth.run_weekly_report(window_days=7)

    assert summary.total_responses == 4
    # Most common first
    assert list(summary.channel_counts.keys())[0] == "instagram"
    assert summary.channel_counts["instagram"] == 2
    assert summary.channel_percentages["instagram"] == 50.0
    assert summary.channel_percentages["google_search"] == 25.0


@pytest.mark.asyncio
async def test_free_text_passed_to_claude(synth):
    """Claude should be called with the non-empty free-text details."""
    synth._db.get_attribution_range = AsyncMock(return_value=[
        _mk_row("other", detail="Saw the Tim Ferris podcast"),
        _mk_row("friend", detail="My sister Ananya"),
        _mk_row("instagram", detail=""),  # empty detail should be excluded
    ])
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"observations": ["Two buyers mentioned personal referrals."]}')]
    synth._claude.messages.create = AsyncMock(return_value=mock_response)

    summary = await synth.run_weekly_report(window_days=7)

    synth._claude.messages.create.assert_awaited_once()
    prompt_arg = synth._claude.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Tim Ferris podcast" in prompt_arg
    assert "sister Ananya" in prompt_arg
    assert summary.ai_observations == ["Two buyers mentioned personal referrals."]


@pytest.mark.asyncio
async def test_claude_failure_graceful(synth):
    """Claude error → empty observations, Slack still posts."""
    synth._db.get_attribution_range = AsyncMock(return_value=[
        _mk_row("instagram", detail="something"),
    ])
    synth._claude.messages.create = AsyncMock(side_effect=Exception("Claude API down"))

    summary = await synth.run_weekly_report(window_days=7)

    assert summary.ai_observations == []
    synth._slack.send_blocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_claude_returns_invalid_json_graceful(synth):
    """Claude returns malformed JSON → empty observations."""
    synth._db.get_attribution_range = AsyncMock(return_value=[
        _mk_row("other", detail="something"),
    ])
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not valid json at all")]
    synth._claude.messages.create = AsyncMock(return_value=mock_response)

    summary = await synth.run_weekly_report(window_days=7)

    assert summary.ai_observations == []


@pytest.mark.asyncio
async def test_caps_observations_at_three(synth):
    """Never more than 3 observations even if Claude returns more."""
    synth._db.get_attribution_range = AsyncMock(return_value=[
        _mk_row("other", detail="x"),
    ])
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"observations": ["a", "b", "c", "d", "e"]}')]
    synth._claude.messages.create = AsyncMock(return_value=mock_response)

    summary = await synth.run_weekly_report(window_days=7)

    assert len(summary.ai_observations) == 3


@pytest.mark.asyncio
async def test_reason_counts_aggregated(synth):
    """Purchase reasons tallied independently of channels."""
    synth._db.get_attribution_range = AsyncMock(return_value=[
        _mk_row("instagram", reason="self_purchase"),
        _mk_row("instagram", reason="self_purchase"),
        _mk_row("friend", reason="gift"),
        _mk_row("google_search", reason="anniversary"),
    ])
    synth._synthesize_free_text = AsyncMock(return_value=[])

    summary = await synth.run_weekly_report(window_days=7)

    assert summary.reason_counts == {"self_purchase": 2, "gift": 1, "anniversary": 1}
