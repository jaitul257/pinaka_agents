"""Unit tests for Meta ad status reverse-sync + Shopify blog publish sync."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.content.seo_publish_sync import reconcile_seo_publish_status
from src.marketing.meta_ad_sync import (
    META_STATUS_TO_OURS,
    _bulk_get_status,
    reconcile_ad_statuses,
)


def _mk_resp(status_code: int, json_data: dict):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.text = ""
    r.json.return_value = json_data
    return r


def _mk_async_client(*responses):
    c = AsyncMock()
    c.__aenter__.return_value = c
    c.__aexit__.return_value = False
    if len(responses) == 1:
        c.get = AsyncMock(return_value=responses[0])
    else:
        c.get = AsyncMock(side_effect=list(responses))
    return c


# ── Meta ad status ──

def test_status_map_covers_standard_states():
    assert META_STATUS_TO_OURS["ACTIVE"] == "live"
    assert META_STATUS_TO_OURS["PAUSED"] == "paused"
    assert META_STATUS_TO_OURS["DELETED"] == "paused"
    assert META_STATUS_TO_OURS["PENDING_REVIEW"] == ""  # unchanged intentionally


@pytest.mark.asyncio
async def test_reconcile_ads_skip_when_unconfigured():
    with patch("src.marketing.meta_ad_sync.settings") as s:
        s.meta_ads_access_token = ""
        s.meta_ad_account_id = ""
        s.meta_graph_api_version = "v25.0"
        result = await reconcile_ad_statuses()
    assert result["skip_reason"] == "meta_not_configured"


@pytest.mark.asyncio
async def test_bulk_get_status_chunks():
    """>50 IDs triggers two API calls."""
    resp1 = _mk_resp(200, {f"id_{i}": {"effective_status": "ACTIVE"} for i in range(50)})
    resp2 = _mk_resp(200, {f"id_{i}": {"effective_status": "PAUSED"} for i in range(50, 55)})

    with patch("src.marketing.meta_ad_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(resp1, resp2)):
        s.meta_graph_api_version = "v25.0"
        s.meta_ads_access_token = "x"
        ids = [f"id_{i}" for i in range(55)]
        out = await _bulk_get_status(ids)

    assert len(out) == 55
    assert out["id_0"] == "ACTIVE"
    assert out["id_50"] == "PAUSED"


@pytest.mark.asyncio
async def test_reconcile_ads_updates_drifted_status():
    """Creative is 'live' in DB but Meta says PAUSED → update to 'paused'."""
    rows = [
        {"id": 101, "sku": "X", "status": "live", "meta_creative_id": "cr_1",
         "meta_ad_id": "ad_1", "variant_label": "A"},
    ]
    meta_response = _mk_resp(200, {"ad_1": {"effective_status": "PAUSED"}})

    mock_db = AsyncMock()
    mock_db._sync._client = MagicMock()

    # First async call: select ad_creatives. Second: update. Both go through asyncio.to_thread.
    call_log: list = []
    async def fake_thread(fn, *a, **kw):
        result = fn()
        call_log.append(result)
        return result

    # Make client.table().select()...execute() return our rows
    select_chain = MagicMock()
    select_chain.execute.return_value = MagicMock(data=rows)
    (mock_db._sync._client.table.return_value
        .select.return_value
        .in_.return_value
        .neq.return_value) = select_chain

    # Update chain returns something truthy
    update_chain = MagicMock()
    update_chain.execute.return_value = MagicMock(data=[{}])
    (mock_db._sync._client.table.return_value
        .update.return_value
        .eq.return_value) = update_chain

    with patch("src.marketing.meta_ad_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(meta_response)), \
         patch("asyncio.to_thread", fake_thread):
        s.meta_ads_access_token = "x"
        s.meta_ad_account_id = "act_1"
        s.meta_graph_api_version = "v25.0"
        result = await reconcile_ad_statuses(db=mock_db)

    assert result["checked"] == 1
    assert result["updated"] == 1


@pytest.mark.asyncio
async def test_reconcile_ads_marks_missing_meta_objects_paused():
    """Ad deleted in Meta → returned empty; our row gets set to paused."""
    rows = [
        {"id": 101, "sku": "X", "status": "live", "meta_creative_id": "cr_1",
         "meta_ad_id": "ad_gone", "variant_label": "A"},
    ]
    # Meta returns empty map → the ad_gone doesn't appear
    meta_response = _mk_resp(200, {})

    mock_db = AsyncMock()
    mock_db._sync._client = MagicMock()

    async def fake_thread(fn, *a, **kw):
        return fn()

    select_chain = MagicMock()
    select_chain.execute.return_value = MagicMock(data=rows)
    (mock_db._sync._client.table.return_value
        .select.return_value
        .in_.return_value
        .neq.return_value) = select_chain
    update_chain = MagicMock()
    update_chain.execute.return_value = MagicMock(data=[{}])
    (mock_db._sync._client.table.return_value
        .update.return_value
        .eq.return_value) = update_chain

    with patch("src.marketing.meta_ad_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(meta_response)), \
         patch("asyncio.to_thread", fake_thread):
        s.meta_ads_access_token = "x"
        s.meta_ad_account_id = "act_1"
        s.meta_graph_api_version = "v25.0"
        result = await reconcile_ad_statuses(db=mock_db)

    assert result["missing_from_meta"] == 1
    assert result["updated"] == 1


# ── SEO publish sync ──

@pytest.mark.asyncio
async def test_seo_sync_skipped_without_config():
    with patch("src.content.seo_publish_sync.settings") as s:
        s.shopify_shop_domain = ""
        s.shopify_access_token = ""
        s.shopify_blog_id = ""
        result = await reconcile_seo_publish_status()
    assert result["skip_reason"] == "shopify_blog_not_configured"


@pytest.mark.asyncio
async def test_seo_sync_detects_newly_published():
    rows = [
        {"id": 1, "keyword": "tennis bracelet anniversary",
         "last_shopify_article_id": 5001, "last_published_at": None},
    ]
    shopify_article_resp = _mk_resp(200, {
        "article": {
            "id": 5001,
            "handle": "tennis-bracelet-anniversary",
            "published_at": "2026-04-16T10:00:00Z",
        }
    })

    mock_db = AsyncMock()
    mock_db._sync._client = MagicMock()

    async def fake_thread(fn, *a, **kw):
        return fn()

    select_chain = MagicMock()
    select_chain.execute.return_value = MagicMock(data=rows)
    (mock_db._sync._client.table.return_value
        .select.return_value
        .neq.return_value) = select_chain
    update_chain = MagicMock()
    update_chain.execute.return_value = MagicMock(data=[{}])
    (mock_db._sync._client.table.return_value
        .update.return_value
        .eq.return_value) = update_chain

    # Slack is awaited; mock it out
    with patch("src.content.seo_publish_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(shopify_article_resp)), \
         patch("asyncio.to_thread", fake_thread), \
         patch("src.content.seo_publish_sync.SlackNotifier") as mock_slack_cls:
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_blog_id = "99"
        s.shopify_api_version = "2025-01"
        s.shopify_storefront_url = "https://pinakajewellery.com"
        mock_slack_cls.return_value = AsyncMock()
        result = await reconcile_seo_publish_status(db=mock_db)

    assert result["newly_published"] == 1
    assert result["checked"] == 1


@pytest.mark.asyncio
async def test_seo_sync_handles_deleted_article():
    """Shopify 404 → clear our last_shopify_article_id pointer."""
    rows = [
        {"id": 1, "keyword": "x", "last_shopify_article_id": 9999, "last_published_at": None},
    ]
    notfound_resp = _mk_resp(404, {})

    mock_db = AsyncMock()
    mock_db._sync._client = MagicMock()

    async def fake_thread(fn, *a, **kw):
        return fn()

    select_chain = MagicMock()
    select_chain.execute.return_value = MagicMock(data=rows)
    (mock_db._sync._client.table.return_value
        .select.return_value
        .neq.return_value) = select_chain
    update_chain = MagicMock()
    update_chain.execute.return_value = MagicMock(data=[{}])
    (mock_db._sync._client.table.return_value
        .update.return_value
        .eq.return_value) = update_chain

    with patch("src.content.seo_publish_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(notfound_resp)), \
         patch("asyncio.to_thread", fake_thread):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_blog_id = "99"
        s.shopify_api_version = "2025-01"
        s.shopify_storefront_url = "https://pinakajewellery.com"
        result = await reconcile_seo_publish_status(db=mock_db, notify_newly_published=False)

    assert result["missing"] == 1
