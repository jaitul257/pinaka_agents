"""Tests for SendGrid email sender."""

from unittest.mock import MagicMock, patch

from src.core.email import EmailSender


def _make_sender():
    """Create an EmailSender with mocked SendGrid client."""
    with patch("src.core.email.SendGridAPIClient") as mock_sg:
        sender = EmailSender()
        mock_response = MagicMock()
        mock_response.status_code = 202
        sender._client.send.return_value = mock_response
        return sender


def test_send_basic():
    """Basic send should call SendGrid with template data."""
    sender = _make_sender()
    result = sender.send(
        to_email="test@example.com",
        template_id="d-abc123",
        template_data={"name": "Test"},
    )
    assert result is True
    sender._client.send.assert_called_once()


def test_send_failure():
    """Failed send should return False and not raise."""
    sender = _make_sender()
    sender._client.send.side_effect = Exception("API error")
    result = sender.send(
        to_email="test@example.com",
        template_id="d-abc123",
        template_data={},
    )
    assert result is False


def test_send_cart_recovery():
    """Cart recovery email should use the cart recovery template."""
    sender = _make_sender()
    with patch.object(sender, "send", return_value=True) as mock_send:
        result = sender.send_cart_recovery(
            to_email="buyer@example.com",
            customer_name="Jane",
            cart_items=["Diamond Bracelet"],
            cart_value=2850.0,
        )
        assert result is True
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["template_data"]["customer_name"] == "Jane"
        assert call_kwargs[1]["template_data"]["cart_value"] == "$2,850.00"


def test_send_crafting_update():
    """Crafting update should use the crafting update template."""
    sender = _make_sender()
    with patch.object(sender, "send", return_value=True) as mock_send:
        result = sender.send_crafting_update(
            to_email="buyer@example.com",
            customer_name="Jane",
            order_number="12345",
            email_body="Your bracelet is being crafted.",
        )
        assert result is True
        mock_send.assert_called_once()


def test_send_service_reply():
    """Service reply should use the service reply template."""
    sender = _make_sender()
    with patch.object(sender, "send", return_value=True) as mock_send:
        result = sender.send_service_reply(
            to_email="buyer@example.com",
            customer_name="Jane",
            subject="Re: Sizing question",
            email_body="We recommend...",
        )
        assert result is True
        mock_send.assert_called_once()


def test_send_non_2xx_returns_false():
    """Non-2xx status code should return False."""
    sender = _make_sender()
    mock_response = MagicMock()
    mock_response.status_code = 400
    sender._client.send.return_value = mock_response
    result = sender.send(
        to_email="test@example.com",
        template_id="d-abc123",
        template_data={},
    )
    assert result is False
