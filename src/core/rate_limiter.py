"""Generic rate-limited HTTP client wrapping httpx.

Token bucket algorithm with response header reading and exponential backoff on 429.
Shared by Etsy, Claude, and ShipStation clients.
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class RateLimitedClient:
    """httpx.AsyncClient wrapper with token bucket rate limiting and retry logic."""

    def __init__(
        self,
        *,
        base_url: str = "",
        qps: float = 10.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.qps = qps
        self.max_retries = max_retries
        self._tokens = qps
        self._max_tokens = qps
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers or {},
        )

    async def _refill_tokens(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self.qps)
        self._last_refill = now

    async def _acquire_token(self) -> None:
        async with self._lock:
            await self._refill_tokens()
            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self.qps
                logger.debug("Rate limit: waiting %.2fs", wait_time)
                await asyncio.sleep(wait_time)
                await self._refill_tokens()
            self._tokens -= 1

    def _read_rate_limit_headers(self, response: httpx.Response) -> None:
        """Read X-RateLimit-* headers and adjust tokens if server reports low remaining."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                server_remaining = int(remaining)
                if server_remaining < self._tokens:
                    self._tokens = float(server_remaining)
                    logger.debug("Server rate limit remaining: %d", server_remaining)
            except ValueError:
                pass

    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an HTTP request with rate limiting and retry on 429."""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            await self._acquire_token()

            try:
                response = await self._client.request(method, url, **kwargs)
                self._read_rate_limit_headers(response)

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else (2**attempt)
                    wait = min(wait, 60)
                    logger.warning(
                        "429 rate limited on %s %s, retry %d/%d in %.1fs",
                        method, url, attempt + 1, self.max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                return response

            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2**attempt
                    logger.warning(
                        "HTTP error on %s %s: %s, retry %d/%d in %.1fs",
                        method, url, e, attempt + 1, self.max_retries, wait,
                    )
                    await asyncio.sleep(wait)

        raise last_error or httpx.HTTPError("Max retries exceeded")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()
