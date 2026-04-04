---
name: Testing conventions and mock patterns
description: How to mock AsyncDatabase, external APIs, and write tests in this project
type: feedback
---

Mock `AsyncDatabase` not `Database` in all tests. Use `AsyncMock()` for DB instances, never `MagicMock()`.

**Why:** Phase 5 migrated all callers to AsyncDatabase. Tests that mock `Database` get `AttributeError: module does not have attribute 'Database'`. Tests with `MagicMock` DB instances get `TypeError: object X can't be used in 'await' expression`.

**How to apply:**
- `@patch("src.api.app.AsyncDatabase")` for app.py endpoints
- `@patch("src.core.database.AsyncDatabase")` for endpoints that lazy-import inside the function
- For external API clients (Meta, Google), mock at the client class level, not httpx internals
- Async test functions are auto-detected (pytest-asyncio mode=AUTO)
