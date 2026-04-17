"""Unit tests for VOC theme miner + customer profile aggregator (Phase 10.A + 10.C)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.customer.profile import CustomerProfileBuilder
from src.customer.voc import VoiceOfCustomer


# ── VOC ──

@pytest.fixture
def voc():
    with patch("src.customer.voc.AsyncDatabase") as mock_db_cls, \
         patch("src.customer.voc.SlackNotifier") as mock_slack_cls, \
         patch("src.customer.voc.anthropic.AsyncAnthropic"):
        db = AsyncMock()
        db._sync._client = MagicMock()
        mock_db_cls.return_value = db
        mock_slack_cls.return_value = AsyncMock()
        yield VoiceOfCustomer()


def _claude_themes(themes: list[dict]):
    resp = MagicMock()
    msg = MagicMock(text=json.dumps({"themes": themes}))
    resp.content = [msg]
    return resp


@pytest.mark.asyncio
async def test_no_data_posts_thin_week_slack(voc):
    voc._load_messages = AsyncMock(return_value=[])
    voc._load_chat_logs = AsyncMock(return_value=[])
    voc._load_survey_text = AsyncMock(return_value=[])

    # Mock persistence (to_thread in _persist)
    async def fake_thread(fn, *a, **kw): return MagicMock()
    with patch("asyncio.to_thread", fake_thread):
        result = await voc.run_weekly()

    assert result.themes == []
    voc._slack.send_blocks.assert_awaited_once()
    blocks = voc._slack.send_blocks.call_args[0][0]
    assert "too few signals" in str(blocks).lower() or "thin" in str(blocks).lower()


@pytest.mark.asyncio
async def test_themes_surface_from_claude_response(voc):
    voc._load_messages = AsyncMock(return_value=[
        {"body": "How should I clean the bracelet?", "subject": "Care question", "created_at": "2026-04-15"},
        {"body": "Is it safe in the shower?", "subject": "Shower", "created_at": "2026-04-14"},
    ])
    voc._load_chat_logs = AsyncMock(return_value=[])
    voc._load_survey_text = AsyncMock(return_value=[])

    mock_themes = [
        {
            "theme": "Daily wear care questions",
            "description": "Buyers want to know what activities are safe",
            "representative_quote": "Is it safe in the shower?",
            "count": 2, "source": "support_email",
            "suggested_action": "Add a one-line care disclaimer above the Add to Cart button",
        }
    ]
    voc._claude.messages.create = AsyncMock(return_value=_claude_themes(mock_themes))

    async def fake_thread(fn, *a, **kw): return MagicMock()
    with patch("asyncio.to_thread", fake_thread):
        result = await voc.run_weekly()

    assert len(result.themes) == 1
    assert result.themes[0]["theme"] == "Daily wear care questions"
    voc._slack.send_blocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_caps_themes_at_five(voc):
    voc._load_messages = AsyncMock(return_value=[{"body": "x", "subject": "y", "created_at": "2026-04-15"}])
    voc._load_chat_logs = AsyncMock(return_value=[{"user_message": "hi", "created_at": "2026-04-15"}])
    voc._load_survey_text = AsyncMock(return_value=[])

    many = [{"theme": f"T{i}", "description": "d", "representative_quote": "q",
             "count": 2, "source": "support_email", "suggested_action": "a"} for i in range(10)]
    voc._claude.messages.create = AsyncMock(return_value=_claude_themes(many))

    async def fake_thread(fn, *a, **kw): return MagicMock()
    with patch("asyncio.to_thread", fake_thread):
        result = await voc.run_weekly()
    assert len(result.themes) == 5


@pytest.mark.asyncio
async def test_claude_error_returns_empty_themes(voc):
    voc._load_messages = AsyncMock(return_value=[{"body": "x", "subject": "y", "created_at": "2026-04-15"}])
    voc._load_chat_logs = AsyncMock(return_value=[{"user_message": "hi", "created_at": "2026-04-15"}])
    voc._load_survey_text = AsyncMock(return_value=[])
    voc._claude.messages.create = AsyncMock(side_effect=Exception("rate limit"))

    async def fake_thread(fn, *a, **kw): return MagicMock()
    with patch("asyncio.to_thread", fake_thread):
        result = await voc.run_weekly()
    assert result.themes == []


# ── Profile ──

@pytest.fixture
def builder():
    with patch("src.customer.profile.AsyncDatabase") as mock_db_cls:
        db = AsyncMock()
        db._sync._client = MagicMock()
        mock_db_cls.return_value = db
        yield CustomerProfileBuilder()


@pytest.mark.asyncio
async def test_profile_returns_none_for_unknown_customer(builder):
    async def fake_thread(fn, *a, **kw):
        return MagicMock(data=[])
    with patch("asyncio.to_thread", fake_thread):
        profile = await builder.for_customer(99999)
    assert profile is None


@pytest.mark.asyncio
async def test_profile_aggregates_all_sources(builder):
    # Different tables return different shapes — dispatch by the `table(...)` call
    def make_thread(fn, *a, **kw):
        # Walk the lambda's free vars to find which table is being queried
        src = getattr(fn, '__code__', None)
        # Simpler: use a pre-configured client that responds based on call sequence
        pass

    # Easier approach: mock the whole table() chain directly on the client
    client = builder._db._sync._client
    # Sequential return values from .execute() — must be ordered matching profile.py's queries:
    # 1. customers
    # 2. orders
    # 3. customer_rfm
    # 4. messages (buyer_email filter)
    # 5. post_purchase_attribution (order IDs)
    # 6. customer_anniversaries
    call_count = {"n": 0}
    responses = [
        # 1. customers
        MagicMock(data=[{
            "id": 42, "shopify_customer_id": 7001,
            "email": "a@b.com", "name": "Customer A", "phone": "+1...",
            "lifecycle_stage": "first_purchase", "accepts_marketing": True,
            "created_at": "2026-01-01", "welcome_step": 3,
            "welcome_started_at": "2026-01-01",
            "lifecycle_emails_sent": {"care_guide_day10": "2026-01-11"},
        }]),
        # 2. orders
        MagicMock(data=[{
            "shopify_order_id": 5001, "total": 4900.0, "refund_amount": 0,
            "status": "paid", "created_at": "2026-03-01",
            "line_items_json": '[{"title":"Diamond Tennis Bracelet"}]',
        }]),
        # 3. customer_rfm
        MagicMock(data=[{
            "computed_date": "2026-04-16",
            "r_score": 5, "f_score": 1, "m_score": 1, "rfm_score_total": 7,
            "segment": "new", "recency_days": 46, "frequency": 1,
            "monetary": 4900.0, "avg_order_value": 4900.0, "projected_ltv_365d": 1470.0,
        }]),
        # 4. messages (count)
        MagicMock(data=[{"created_at": "2026-04-10"}], count=2),
        # 5. attribution
        MagicMock(data=[{
            "shopify_order_id": "5001", "channel_primary": "instagram",
            "channel_detail": None, "purchase_reason": "anniversary",
            "anniversary_date": "2027-06-15", "relationship": "wedding_anniversary",
            "created_at": "2026-03-01",
        }]),
        # 6. anniversaries
        MagicMock(data=[{
            "anniversary_date": "2027-06-15", "relationship": "wedding_anniversary", "notes": None,
        }]),
    ]

    async def fake_thread(fn, *a, **kw):
        r = responses[call_count["n"]]
        call_count["n"] += 1
        return r

    with patch("asyncio.to_thread", fake_thread):
        profile = await builder.for_customer(42)

    assert profile is not None
    assert profile.customer_id == 42
    assert profile.email == "a@b.com"
    assert profile.order_count == 1
    assert profile.net_spent == 4900.0
    assert profile.rfm is not None
    assert profile.rfm.segment == "new"
    assert profile.rfm.projected_ltv_365d == 1470.0
    assert profile.message_count == 2
    assert len(profile.survey_responses) == 1
    assert len(profile.anniversaries) == 1
    assert profile.welcome_step == 3
    assert profile.lifecycle_emails_sent == {"care_guide_day10": "2026-01-11"}


def test_profile_to_json_shape(builder):
    from src.customer.profile import CustomerProfile, OrderSummary, RFMSnapshot
    p = CustomerProfile(
        customer_id=42, shopify_customer_id=7001, email="a@b.com", name="A",
        phone="", lifecycle_stage="first_purchase", accepts_marketing=True,
        created_at="2026-01-01",
        orders=[OrderSummary(
            shopify_order_id="5001", total=5000, refund_amount=0, net=5000,
            status="paid", created_at="2026-03-01", line_items=["Bracelet"],
        )],
        total_spent=5000, net_spent=5000, order_count=1, avg_order_value=5000,
        last_order_date="2026-03-01",
        rfm=RFMSnapshot(
            computed_date="2026-04-16", r_score=5, f_score=1, m_score=1,
            rfm_score_total=7, segment="new", recency_days=46, frequency=1,
            monetary=5000, avg_order_value=5000, projected_ltv_365d=1500,
        ),
        generated_at="2026-04-16T00:00:00",
    )
    js = builder.to_json(p)
    assert js["customer_id"] == 42
    assert js["money"]["order_count"] == 1
    assert js["rfm"]["segment"] == "new"
    assert js["rfm"]["projected_ltv_365d"] == 1500
    assert js["pipeline"]["welcome_step"] == 0  # default
