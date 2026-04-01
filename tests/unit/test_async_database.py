"""Tests for AsyncDatabase wrapper."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.database import AsyncDatabase


@pytest.fixture
def async_db():
    """Create AsyncDatabase with mocked Supabase client."""
    with patch("src.core.database.get_supabase", return_value=MagicMock()):
        return AsyncDatabase()


async def test_async_get_order_by_shopify_id(async_db):
    """AsyncDatabase should delegate to sync Database and return the result."""
    fake_order = {"id": 1, "shopify_order_id": 12345, "total": 99.99}
    async_db._sync.get_order_by_shopify_id = MagicMock(return_value=fake_order)

    result = await async_db.get_order_by_shopify_id(12345)
    assert result == fake_order
    async_db._sync.get_order_by_shopify_id.assert_called_once_with(12345)


async def test_async_upsert_order(async_db):
    """Args should be forwarded correctly through to_thread."""
    order_data = {"shopify_order_id": 999, "total": 50.0}
    expected = {**order_data, "id": 42}
    async_db._sync.upsert_order = MagicMock(return_value=expected)

    result = await async_db.upsert_order(order_data)
    assert result == expected
    async_db._sync.upsert_order.assert_called_once_with(order_data)


async def test_async_exception_propagation(async_db):
    """Sync exceptions should propagate to async callers."""
    async_db._sync.get_order_by_shopify_id = MagicMock(
        side_effect=RuntimeError("DB connection failed")
    )

    with pytest.raises(RuntimeError, match="DB connection failed"):
        await async_db.get_order_by_shopify_id(12345)


async def test_async_concurrent_access(async_db):
    """Multiple concurrent calls should complete without error."""
    call_count = 0

    def slow_query(shopify_id):
        nonlocal call_count
        call_count += 1
        return {"shopify_order_id": shopify_id}

    async_db._sync.get_order_by_shopify_id = MagicMock(side_effect=slow_query)

    results = await asyncio.gather(
        async_db.get_order_by_shopify_id(1),
        async_db.get_order_by_shopify_id(2),
        async_db.get_order_by_shopify_id(3),
        async_db.get_order_by_shopify_id(4),
        async_db.get_order_by_shopify_id(5),
    )
    assert len(results) == 5
    assert call_count == 5


async def test_async_database_per_instance():
    """Two AsyncDatabase instances should have separate Database objects."""
    with patch("src.core.database.get_supabase", return_value=MagicMock()):
        db1 = AsyncDatabase()
        db2 = AsyncDatabase()
    assert db1._sync is not db2._sync
