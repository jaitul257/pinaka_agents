"""Tests for the in-process event bus."""

import pytest

from src.core.events import EventBus


@pytest.fixture
def bus():
    return EventBus()


async def test_emit_calls_handler(bus):
    """Handlers should be called when their event fires."""
    received = []

    async def handler(data):
        received.append(data)

    bus.on("order.created", handler)
    await bus.emit("order.created", {"id": 1})

    assert len(received) == 1
    assert received[0]["id"] == 1


async def test_emit_multiple_handlers(bus):
    """Multiple handlers on the same event should all fire."""
    calls = []

    async def h1(data):
        calls.append("h1")

    async def h2(data):
        calls.append("h2")

    bus.on("order.created", h1)
    bus.on("order.created", h2)
    await bus.emit("order.created", {})

    assert calls == ["h1", "h2"]


async def test_emit_no_handlers(bus):
    """Emitting an event with no handlers should not raise."""
    await bus.emit("unknown.event", {"data": True})


async def test_handler_error_isolated(bus):
    """A failing handler should not prevent other handlers from running."""
    calls = []

    async def bad_handler(data):
        raise ValueError("boom")

    async def good_handler(data):
        calls.append("ok")

    bus.on("test", bad_handler)
    bus.on("test", good_handler)
    await bus.emit("test", {})

    assert calls == ["ok"]


async def test_different_events_independent(bus):
    """Handlers registered for different events should not cross-fire."""
    calls = []

    async def order_handler(data):
        calls.append("order")

    async def customer_handler(data):
        calls.append("customer")

    bus.on("order.created", order_handler)
    bus.on("customer.created", customer_handler)

    await bus.emit("order.created", {})
    assert calls == ["order"]
