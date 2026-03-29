"""Tests for the generic rate-limited HTTP client."""

import asyncio

import pytest

from src.core.rate_limiter import RateLimitedClient


@pytest.mark.asyncio
async def test_token_bucket_refill():
    """Tokens should refill over time based on QPS."""
    client = RateLimitedClient(qps=10.0)
    # Consume all tokens
    client._tokens = 0
    client._last_refill = asyncio.get_event_loop().time() - 1.0

    await client._refill_tokens()
    assert client._tokens >= 9.0  # ~10 tokens refilled in 1 second
    await client.close()


@pytest.mark.asyncio
async def test_token_bucket_cap():
    """Tokens should never exceed max (qps)."""
    client = RateLimitedClient(qps=5.0)
    client._tokens = 3.0
    client._last_refill = asyncio.get_event_loop().time() - 10.0

    await client._refill_tokens()
    assert client._tokens == 5.0  # Capped at max
    await client.close()
