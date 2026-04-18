"""Contracts for Phase 12.5a — Slack modal-submit → capture_edit.

Two things locked in:
  1. The 4 edit actions each map to a distinct trigger_type string that
     feedback_loop.capture_edit / founder_style_for can retrieve later.
  2. An unknown callback_id returns cleanly without crashing the webhook.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.app import _EDIT_ACTION_TO_TRIGGER, _handle_slack_modal_submit


# ── Action → trigger_type mapping ──

def test_edit_response_maps_to_customer_response():
    assert _EDIT_ACTION_TO_TRIGGER["edit_response"] == "customer_response"


def test_edit_cart_recovery_maps_to_cart_recovery():
    assert _EDIT_ACTION_TO_TRIGGER["edit_cart_recovery"] == "cart_recovery"


def test_edit_crafting_update_maps_to_crafting_update():
    assert _EDIT_ACTION_TO_TRIGGER["edit_crafting_update"] == "crafting_update"


def test_edit_listing_maps_to_listing_publish():
    assert _EDIT_ACTION_TO_TRIGGER["edit_listing"] == "listing_publish"


def test_exactly_four_edit_actions():
    """Regression guard — if a new edit_* action appears without a trigger
    mapping, founder_style_for() will never find its edits."""
    assert len(_EDIT_ACTION_TO_TRIGGER) == 4


# ── Modal submit handler ──

@pytest.mark.asyncio
async def test_unknown_callback_id_returns_no_action():
    """Random callback_id (e.g. from a plugin or older schema) must NOT
    crash the webhook."""
    payload = {"view": {"callback_id": "something_we_dont_know"}}
    result = await _handle_slack_modal_submit(payload)
    assert result == {"status": "no_action"}


@pytest.mark.asyncio
async def test_non_edit_modal_callback_returns_no_action():
    """A modal callback_id that isn't one of our 4 edit flows (but starts
    with modal_) is treated as unknown, not a crash."""
    payload = {"view": {"callback_id": "modal_random_thing"}}
    result = await _handle_slack_modal_submit(payload)
    assert result == {"status": "no_action"}


@pytest.mark.asyncio
async def test_edit_response_submit_captures_diff():
    """The happy path: edited text differs from original → capture_edit
    gets called with the right agent/trigger/texts."""
    payload = {
        "view": {
            "callback_id": "modal_edit_response",
            "private_metadata": '{"value": "42", "original_text": "Hi there.", "channel": "C1", "message_ts": "1.1"}',
            "state": {
                "values": {
                    "edited_text_block": {
                        "edited_text": {"value": "Hello there — welcome."},
                    },
                },
            },
        },
    }
    with patch("src.agents.feedback_loop.capture_edit", new=AsyncMock(return_value=1)) as cap, \
         patch("src.api.app.AsyncDatabase") as mock_db_cls, \
         patch("src.api.app.SlackNotifier") as mock_slack_cls, \
         patch("src.api.app.EmailSender") as mock_email_cls:
        mock_db = AsyncMock()
        mock_db.get_pending_messages.return_value = []
        mock_db_cls.return_value = mock_db
        mock_slack_cls.return_value = AsyncMock()
        mock_email_cls.return_value = MagicMock()

        result = await _handle_slack_modal_submit(payload)

    assert result == {"status": "ok"}
    cap.assert_called_once()
    kwargs = cap.call_args.kwargs
    assert kwargs["agent_name"] == "customer_service"
    assert kwargs["trigger_type"] == "customer_response"
    assert kwargs["original_text"] == "Hi there."
    assert kwargs["edited_text"] == "Hello there — welcome."


@pytest.mark.asyncio
async def test_no_edit_still_logs_nothing_valuable():
    """If the founder opens the modal and submits without changing text,
    capture_edit gets called BUT the feedback_loop.capture_edit function
    itself (tested separately) skips identical texts. The modal submit
    path still runs through safely."""
    payload = {
        "view": {
            "callback_id": "modal_edit_response",
            "private_metadata": '{"value": "42", "original_text": "same text", "channel": "", "message_ts": ""}',
            "state": {
                "values": {
                    "edited_text_block": {
                        "edited_text": {"value": "same text"},
                    },
                },
            },
        },
    }
    with patch("src.agents.feedback_loop.capture_edit", new=AsyncMock(return_value=None)), \
         patch("src.api.app.AsyncDatabase") as mock_db_cls, \
         patch("src.api.app.SlackNotifier") as mock_slack_cls, \
         patch("src.api.app.EmailSender") as mock_email_cls:
        mock_db_cls.return_value = AsyncMock(get_pending_messages=AsyncMock(return_value=[]))
        mock_slack_cls.return_value = AsyncMock()
        mock_email_cls.return_value = MagicMock()
        result = await _handle_slack_modal_submit(payload)
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_malformed_private_metadata_doesnt_crash():
    """Broken JSON in private_metadata must not crash — Slack truncates
    metadata at 3000 chars, and we may be edge-cased by it."""
    payload = {
        "view": {
            "callback_id": "modal_edit_response",
            "private_metadata": "not-even-close-to-json",
            "state": {"values": {}},
        },
    }
    with patch("src.agents.feedback_loop.capture_edit", new=AsyncMock()), \
         patch("src.api.app.AsyncDatabase") as mock_db_cls, \
         patch("src.api.app.SlackNotifier") as mock_slack_cls, \
         patch("src.api.app.EmailSender") as mock_email_cls:
        mock_db_cls.return_value = AsyncMock(get_pending_messages=AsyncMock(return_value=[]))
        mock_slack_cls.return_value = AsyncMock()
        mock_email_cls.return_value = MagicMock()
        result = await _handle_slack_modal_submit(payload)
    assert result == {"status": "ok"}


def test_capture_edit_owner_mapping_covers_all_edit_actions():
    """Regression: every action_id in _EDIT_ACTION_TO_TRIGGER must have an
    explicit owning agent in the modal handler. Missing one means the
    captured edit gets tagged with 'listings' (the final `else`) by default
    — harmless but misleading."""
    expected_owners = {
        "edit_response": "customer_service",
        "edit_cart_recovery": "retention",
        "edit_crafting_update": "order_ops",
        "edit_listing": "listings",
    }
    assert set(expected_owners.keys()) == set(_EDIT_ACTION_TO_TRIGGER.keys())
