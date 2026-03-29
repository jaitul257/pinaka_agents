"""Simple in-process event bus for webhook-driven architecture.

Handlers register for event types. When an event fires, all handlers
run with try/except isolation — one failing handler doesn't block others.
"""

import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """In-process async event dispatcher with error isolation."""

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type."""
        self._handlers[event_type].append(handler)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event to all registered handlers. Errors are logged, not raised."""
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(data)
            except Exception:
                logger.exception(
                    "Event handler %s failed for event %s",
                    handler.__name__,
                    event_type,
                )


event_bus = EventBus()
