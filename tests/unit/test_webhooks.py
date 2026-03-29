"""Tests for Shopify webhook HMAC verification."""

import base64
import hashlib
import hmac
from unittest.mock import patch, MagicMock


def test_hmac_valid():
    """Valid HMAC should return True."""
    secret = "test_webhook_secret"
    body = b'{"id": 123}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    header = base64.b64encode(digest).decode("utf-8")

    with patch("src.core.database.get_supabase", return_value=MagicMock()), \
         patch("src.core.slack.AsyncWebClient"):
        from src.api.shopify_webhooks import verify_shopify_hmac
        with patch("src.api.shopify_webhooks.settings") as mock_settings:
            mock_settings.shopify_webhook_secret = secret
            assert verify_shopify_hmac(body, header) is True


def test_hmac_invalid():
    """Invalid HMAC should return False."""
    with patch("src.core.database.get_supabase", return_value=MagicMock()), \
         patch("src.core.slack.AsyncWebClient"):
        from src.api.shopify_webhooks import verify_shopify_hmac
        with patch("src.api.shopify_webhooks.settings") as mock_settings:
            mock_settings.shopify_webhook_secret = "real_secret"
            assert verify_shopify_hmac(b'{"id": 123}', "bad_hmac") is False


def test_hmac_no_secret_configured():
    """Missing webhook secret should skip verification (returns True)."""
    with patch("src.core.database.get_supabase", return_value=MagicMock()), \
         patch("src.core.slack.AsyncWebClient"):
        from src.api.shopify_webhooks import verify_shopify_hmac
        with patch("src.api.shopify_webhooks.settings") as mock_settings:
            mock_settings.shopify_webhook_secret = ""
            assert verify_shopify_hmac(b'{"id": 123}', "anything") is True
