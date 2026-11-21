import asyncio
import random
import time
import httpx
from aiolimiter import AsyncLimiter


class EcommerceHttpClient:
    """Throttled, polite async HTTP client with rate-limiting, jitter, and retry capabilities."""

    def __init__(
        self,
        requests_per_minute: int = 30,
        min_delay: float = 1.0,
        max_delay: float = 2.5,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        self.limiter = AsyncLimiter(requests_per_minute, 60)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.last_request = 0.0
        self.max_retries = max_retries
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36 (ProductDataMiner/2.0)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    async def _pacing(self):
        async with self.limiter:
            elapsed = time.monotonic() - self.last_request
            delay = random.uniform(self.min_delay, self.max_delay)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self.last_request = time.monotonic()

    async def get(self, url: str) -> httpx.Response:
        for attempt in range(1, self.max_retries + 1):
            await self._pacing()
            try:
                response = await self.client.get(url)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else 10.0
                    except ValueError:
                        wait = 10.0
                    print(f"[RETRY] 429 Rate limited on {url} - waiting {wait}s (attempt {attempt}/{self.max_retries})")
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise exc
                await asyncio.sleep(1.5 * attempt)
        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries} attempts")

    async def post(self, url: str, json: dict = None) -> httpx.Response:
        await self._pacing()
        return await self.client.post(url, json=json)

    async def close(self):
        await self.client.aclose()
