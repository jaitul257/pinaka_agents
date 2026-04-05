"""Integration tests for Phase 6.1 dashboard ad-creatives routes.

Pattern: FastAPI TestClient + @patch on the dashboard module's _get_db and the
downstream generator/Meta client. No real Claude or Meta calls in CI.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.marketing.ad_generator import AdVariant
from src.marketing.meta_creative import MetaAdResult, MetaCreativeError, MetaCreativeResult


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_cookie():
    """Patches dashboard auth to accept any cookie (dev mode) for the test."""
    with patch("src.dashboard.web._check_auth", return_value=True):
        yield {"dash_token": "test-token"}


@pytest.fixture
def mock_db():
    """Patch the dashboard's _get_db() helper to return a configured MagicMock."""
    db = MagicMock()
    db.get_recent_ad_creatives.return_value = []
    db.get_product_by_sku.return_value = {
        "sku": "DTB-LG-7-14KYG",
        "name": "Diamond Tennis Bracelet",
        "images": ["https://cdn.shopify.com/a.jpg"],
    }
    db.create_generation_batch.return_value = {
        "id": "batch-uuid-new",
        "sku": "DTB-LG-7-14KYG",
    }
    with patch("src.dashboard.web._get_db", return_value=db):
        yield db


@pytest.fixture
def ready_meta_settings():
    """Patch is_meta_creative_ready to True so Approve buttons are enabled.

    By default is_meta_ad_ready is False (no default ad set), so Go Live falls back
    to creative-only mode. Individual tests can override is_meta_ad_ready=True to
    exercise the Phase 6.2 Ad auto-creation path.
    """
    with patch("src.core.settings.settings") as s:
        s.is_meta_creative_ready = True
        s.is_meta_ad_ready = False
        s.meta_ads_access_token = "t"
        s.meta_ad_account_id = "act_1"
        s.meta_facebook_page_id = "123"
        s.meta_default_adset_id = ""
        yield s


# ── Auth ──


def test_list_page_requires_auth_redirects_to_login(client):
    """No cookie → 303 to /dashboard/login."""
    with patch("src.dashboard.web._check_auth", return_value=False):
        resp = client.get("/dashboard/ad-creatives", follow_redirects=False)
    assert resp.status_code == 303
    assert "/dashboard/login" in resp.headers.get("location", "")


# ── List page ──


def test_list_page_empty_state(client, auth_cookie, mock_db):
    """Empty DB renders the empty-state copy."""
    mock_db.get_recent_ad_creatives.return_value = []
    resp = client.get("/dashboard/ad-creatives", cookies=auth_cookie)
    assert resp.status_code == 200
    assert "No ad drafts yet" in resp.text
    assert "Go to Products" in resp.text


def test_list_page_renders_batches(client, auth_cookie, mock_db):
    """Drafts grouped by generation_batch_id, variants sorted A/B/C."""
    mock_db.get_recent_ad_creatives.return_value = [
        {
            "id": 1, "sku": "DTB-LG-7", "variant_label": "A",
            "headline": "Clean headline A", "primary_text": "Primary A",
            "description": "desc", "cta": "SHOP_NOW",
            "image_url": "https://cdn.shopify.com/a.jpg",
            "status": "pending_review", "generation_batch_id": "batch-1",
            "created_at": "2026-04-05T10:00:00Z",
            "meta_creative_id": None, "validation_warning": None,
        },
        {
            "id": 2, "sku": "DTB-LG-7", "variant_label": "B",
            "headline": "Clean headline B", "primary_text": "Primary B",
            "description": "", "cta": "LEARN_MORE",
            "image_url": "https://cdn.shopify.com/b.jpg",
            "status": "pending_review", "generation_batch_id": "batch-1",
            "created_at": "2026-04-05T10:00:00Z",
            "meta_creative_id": None, "validation_warning": None,
        },
    ]
    resp = client.get("/dashboard/ad-creatives", cookies=auth_cookie)
    assert resp.status_code == 200
    assert "Clean headline A" in resp.text
    assert "Clean headline B" in resp.text
    assert "batch-1"[:8] in resp.text
    assert "DTB-LG-7" in resp.text


def test_list_page_shows_warning_banner_when_page_id_missing(client, auth_cookie, mock_db):
    """FB Page not configured → red banner + disabled Approve button."""
    with patch("src.core.settings.settings") as s:
        s.is_meta_creative_ready = False
        s.meta_ads_access_function_token = ""
        s.meta_ads_access_token = "t"
        s.meta_ad_account_id = "act_1"
        s.meta_facebook_page_id = ""
        resp = client.get("/dashboard/ad-creatives", cookies=auth_cookie)
    assert resp.status_code == 200
    assert "Meta push disabled" in resp.text
    assert "META_FACEBOOK_PAGE_ID" in resp.text


# ── Generate route ──


def test_generate_creates_batch_and_redirects(client, auth_cookie, mock_db):
    """POST /generate → batch row created → 303 redirect to list page with pending param."""
    resp = client.post(
        "/dashboard/ad-creatives/generate",
        data={"sku": "DTB-LG-7-14KYG"},
        cookies=auth_cookie,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/dashboard/ad-creatives?pending=" in resp.headers["location"]
    assert mock_db.create_generation_batch.called
    call_args = mock_db.create_generation_batch.call_args[0][0]
    assert call_args["sku"] == "DTB-LG-7-14KYG"
    assert "idempotency_key" in call_args
    assert call_args["status"] == "generating"


# ── Approve route (atomic + Meta push) ──


def test_approve_happy_path_pushes_to_meta(client, auth_cookie, mock_db, ready_meta_settings):
    """Atomic transition works + Meta call succeeds + DB marked published."""
    mock_db.approve_ad_creative_atomic.return_value = {
        "id": 1, "sku": "DTB-LG-7", "variant_label": "A",
        "headline": "h", "primary_text": "p", "description": "",
        "cta": "SHOP_NOW", "image_url": "https://cdn.shopify.com/a.jpg",
        "generation_batch_id": "batch-1",
    }

    with patch("src.marketing.meta_creative.MetaCreativeClient") as MockClient:
        instance = MockClient.return_value
        instance.create_creative = AsyncMock(
            return_value=MetaCreativeResult(creative_id="creative_abc", status="PAUSED")
        )
        resp = client.post(
            "/dashboard/ad-creatives/1/approve",
            cookies=auth_cookie,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "msg=Approved" in resp.headers["location"]
    mock_db.approve_ad_creative_atomic.assert_called_once_with(1, approved_by="dashboard")
    mock_db.mark_ad_creative_published.assert_called_once_with(1, "creative_abc")


def test_approve_already_processed_returns_message(client, auth_cookie, mock_db, ready_meta_settings):
    """Atomic update returns None (race with another approver) → user sees message, no Meta call."""
    mock_db.approve_ad_creative_atomic.return_value = None

    with patch("src.marketing.meta_creative.MetaCreativeClient") as MockClient:
        resp = client.post(
            "/dashboard/ad-creatives/1/approve",
            cookies=auth_cookie,
            follow_redirects=False,
        )
        # Meta client should never be instantiated
        MockClient.return_value.create_creative.assert_not_called()

    assert resp.status_code == 303
    assert "Already" in resp.headers["location"]


def test_approve_meta_push_fails_rolls_back(client, auth_cookie, mock_db, ready_meta_settings):
    """Meta 400 → revert_ad_creative_to_pending called, draft visible again."""
    mock_db.approve_ad_creative_atomic.return_value = {
        "id": 5, "sku": "DTB-LG-7", "variant_label": "B",
        "headline": "h", "primary_text": "p", "description": "",
        "cta": "SHOP_NOW", "image_url": "https://cdn.shopify.com/a.jpg",
        "generation_batch_id": "batch-1",
    }

    with patch("src.marketing.meta_creative.MetaCreativeClient") as MockClient:
        instance = MockClient.return_value
        instance.create_creative = AsyncMock(
            side_effect=MetaCreativeError("Invalid page_id")
        )
        resp = client.post(
            "/dashboard/ad-creatives/5/approve",
            cookies=auth_cookie,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "Error" in resp.headers["location"]
    mock_db.revert_ad_creative_to_pending.assert_called_once_with(5)
    mock_db.mark_ad_creative_published.assert_not_called()


def test_approve_blocked_when_meta_not_configured(client, auth_cookie, mock_db):
    """is_meta_creative_ready=False → no atomic transition, no Meta call."""
    with patch("src.core.settings.settings") as s:
        s.is_meta_creative_ready = False
        resp = client.post(
            "/dashboard/ad-creatives/1/approve",
            cookies=auth_cookie,
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "Meta+push+not+configured" in resp.headers["location"]
    mock_db.approve_ad_creative_atomic.assert_not_called()


# ── Reject route ──


def test_reject_marks_rejected_no_meta_call(client, auth_cookie, mock_db):
    resp = client.post(
        "/dashboard/ad-creatives/1/reject",
        cookies=auth_cookie,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    mock_db.reject_ad_creative.assert_called_once_with(1)


# ── Set-live route (flip PAUSED to ACTIVE) ──


def test_set_live_creative_only_when_no_default_adset(
    client, auth_cookie, mock_db, ready_meta_settings
):
    """Backwards-compat: if META_DEFAULT_ADSET_ID is not set, flip creative but skip Ad creation."""
    mock_db.get_ad_creative.return_value = {
        "id": 10, "meta_creative_id": "creative_xyz", "sku": "DTB", "variant_label": "A",
        "status": "published",
    }
    ready_meta_settings.is_meta_ad_ready = False
    with patch("src.marketing.meta_creative.MetaCreativeClient") as MockClient:
        MockClient.return_value.set_creative_status = AsyncMock(
            return_value=MetaCreativeResult(creative_id="creative_xyz", status="ACTIVE")
        )
        MockClient.return_value.create_ad = AsyncMock()
        resp = client.post(
            "/dashboard/ad-creatives/10/set-live",
            cookies=auth_cookie,
            follow_redirects=False,
        )
        MockClient.return_value.create_ad.assert_not_called()
    assert resp.status_code == 303
    assert "LIVE" in resp.headers["location"]
    mock_db.set_ad_creative_live.assert_called_once_with(
        10, meta_ad_id=None, meta_adset_id=None
    )


def test_set_live_creates_ad_when_default_adset_configured(
    client, auth_cookie, mock_db, ready_meta_settings
):
    """Phase 6.2: Go Live flips creative AND creates an Ad under the default Ad Set."""
    mock_db.get_ad_creative.return_value = {
        "id": 11, "meta_creative_id": "creative_abc", "sku": "DTB-LBG-7",
        "variant_label": "B", "status": "published",
    }
    ready_meta_settings.is_meta_ad_ready = True
    ready_meta_settings.meta_default_adset_id = "adset_999"

    with patch("src.marketing.meta_creative.MetaCreativeClient") as MockClient:
        MockClient.return_value.set_creative_status = AsyncMock(
            return_value=MetaCreativeResult(creative_id="creative_abc", status="ACTIVE")
        )
        MockClient.return_value.create_ad = AsyncMock(
            return_value=MetaAdResult(
                ad_id="ad_12345", adset_id="adset_999",
                creative_id="creative_abc", status="ACTIVE",
            )
        )
        resp = client.post(
            "/dashboard/ad-creatives/11/set-live",
            cookies=auth_cookie,
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "ad_12345" in resp.headers["location"]
    mock_db.set_ad_creative_live.assert_called_once_with(
        11, meta_ad_id="ad_12345", meta_adset_id="adset_999"
    )


def test_set_live_fails_if_never_pushed(client, auth_cookie, mock_db, ready_meta_settings):
    mock_db.get_ad_creative.return_value = {"id": 99, "meta_creative_id": None}
    resp = client.post(
        "/dashboard/ad-creatives/99/set-live",
        cookies=auth_cookie,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Error" in resp.headers["location"]
