"""Tests for Phase 9.2 welcome series engine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.customer.welcome import WelcomeSeriesEngine


@pytest.fixture
def engine():
    with patch("src.customer.welcome.AsyncDatabase") as mock_db_cls, \
         patch("src.customer.welcome.EmailSender") as mock_email_cls:
        mock_db_cls.return_value = AsyncMock()
        mock_email_cls.return_value = MagicMock()
        e = WelcomeSeriesEngine()
        yield e


@pytest.mark.asyncio
async def test_no_candidates_returns_empty_result(engine):
    engine._db.get_welcome_candidates = AsyncMock(return_value=[])
    result = await engine.send_due()
    assert result == {
        "sent": 0, "candidates": 0,
        "skipped_missing_template": 0, "failed": 0,
    }


@pytest.mark.asyncio
async def test_sends_next_step_to_due_candidate(engine):
    engine._db.get_welcome_candidates = AsyncMock(return_value=[
        {"id": 1, "email": "new@example.com", "name": "New", "_next_step": 2},
    ])
    engine._email.send_welcome_email = MagicMock(return_value=True)
    engine._db.mark_welcome_step_sent = AsyncMock()

    with patch("src.customer.welcome.settings") as s:
        s.sendgrid_welcome_1_template_id = "d-1"
        s.sendgrid_welcome_2_template_id = "d-2"
        s.sendgrid_welcome_3_template_id = ""
        s.sendgrid_welcome_4_template_id = ""
        s.sendgrid_welcome_5_template_id = ""
        result = await engine.send_due()

    assert result["sent"] == 1
    assert result["candidates"] == 1
    engine._email.send_welcome_email.assert_called_once_with("new@example.com", "New", 2)
    engine._db.mark_welcome_step_sent.assert_awaited_once_with(1, 2)


@pytest.mark.asyncio
async def test_skips_when_template_not_configured(engine):
    """Step with no SENDGRID_WELCOME_N_TEMPLATE_ID → counted as skipped, not failed."""
    engine._db.get_welcome_candidates = AsyncMock(return_value=[
        {"id": 1, "email": "new@example.com", "name": "New", "_next_step": 5},
    ])

    with patch("src.customer.welcome.settings") as s:
        s.sendgrid_welcome_1_template_id = ""
        s.sendgrid_welcome_2_template_id = ""
        s.sendgrid_welcome_3_template_id = ""
        s.sendgrid_welcome_4_template_id = ""
        s.sendgrid_welcome_5_template_id = ""  # missing
        result = await engine.send_due()

    assert result["skipped_missing_template"] == 1
    assert result["sent"] == 0


@pytest.mark.asyncio
async def test_send_failure_increments_failed(engine):
    engine._db.get_welcome_candidates = AsyncMock(return_value=[
        {"id": 1, "email": "new@example.com", "name": "New", "_next_step": 1},
    ])
    engine._email.send_welcome_email = MagicMock(return_value=False)

    with patch("src.customer.welcome.settings") as s:
        s.sendgrid_welcome_1_template_id = "d-1"
        for n in range(2, 6):
            setattr(s, f"sendgrid_welcome_{n}_template_id", "")
        result = await engine.send_due()

    assert result["failed"] == 1
    assert result["sent"] == 0
    engine._db.mark_welcome_step_sent.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_next_step_skipped(engine):
    """A candidate with _next_step=7 (out of range) is silently skipped."""
    engine._db.get_welcome_candidates = AsyncMock(return_value=[
        {"id": 1, "email": "x@y.com", "name": "X", "_next_step": 7},
    ])
    with patch("src.customer.welcome.settings"):
        result = await engine.send_due()
    assert result["sent"] == 0
