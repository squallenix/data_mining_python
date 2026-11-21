import asyncio
import os
import random
import time
from urllib.parse import urlparse
import httpx
from aiolimiter import AsyncLimiter

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False


REALISTIC_BROWSER_HEADERS = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Microsoft Edge";v="130", "Chromium";v="130", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    },
]


class EcommerceHttpClient:
    """Throttled, resilient async HTTP client with TLS browser impersonation,
    realistic headers, rate-limiting, and automatic retry for e-commerce sites."""

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
        self.timeout = timeout
        self._header_idx = 0

        # Dedicated HTTP client for backend REST communication (Node / Express API)
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"Content-Type": "application/json"},
        )

        # Optional proxy for bypassing cloud datacenter geo-blocks (e.g. Startech on Render)
        self.proxy = os.environ.get("SCRAPER_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")

        # TLS Browser Impersonation session for scraping external e-commerce sites
        self._curl_session = None
        if HAS_CURL_CFFI:
            try:
                self._curl_session = CurlAsyncSession(
                    impersonate="chrome124",
                    proxy=self.proxy if self.proxy else None,
                )
            except Exception as e:
                print(f"[HTTP] Note: curl_cffi session init: {e}. Falling back to httpx.")
                self._curl_session = None

        # Fallback HTTP client for scraping if curl_cffi is unavailable
        self._scraper_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            proxy=self.proxy if self.proxy else None,
        )

    async def _pacing(self):
        async with self.limiter:
            elapsed = time.monotonic() - self.last_request
            delay = random.uniform(self.min_delay, self.max_delay)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self.last_request = time.monotonic()

    async def get(self, url: str):
        parsed = urlparse(url)
        domain = parsed.netloc.lower() or "target site"

        for attempt in range(1, self.max_retries + 1):
            await self._pacing()

            # Method A: Use curl_cffi for TLS fingerprint bypass
            if self._curl_session is not None:
                try:
                    response = await self._curl_session.get(
                        url,
                        timeout=self.timeout,
                        allow_redirects=True,
                    )
                    if response.status_code == 429:
                        wait = 10.0
                        print(f"[RETRY] 429 Rate limited on {url} - waiting {wait}s (attempt {attempt}/{self.max_retries})")
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code == 403:
                        if attempt < self.max_retries:
                            print(f"[WARN] 403 Forbidden on {url} - retrying with delay (attempt {attempt}/{self.max_retries})")
                            await asyncio.sleep(2.0 * attempt)
                            continue
                        raise PermissionError(
                            f"HTTP 403 Forbidden: {domain} blocked access (Cloudflare geo/datacenter protection active). "
                            "Try another site (e.g. gadgetandgear.com, custommacbd.com, books.toscrape.com) or configure a SCRAPER_PROXY."
                        )

                    response.raise_for_status()
                    return response
                except PermissionError:
                    raise
                except Exception as exc:
                    if attempt == self.max_retries:
                        raise exc
                    await asyncio.sleep(1.5 * attempt)
            else:
                # Method B: Fallback to httpx with rotating realistic browser headers
                headers = REALISTIC_BROWSER_HEADERS[self._header_idx % len(REALISTIC_BROWSER_HEADERS)]
                try:
                    response = await self._scraper_client.get(url, headers=headers)
                    if response.status_code == 429:
                        wait = 10.0
                        print(f"[RETRY] 429 Rate limited on {url} - waiting {wait}s (attempt {attempt}/{self.max_retries})")
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code == 403:
                        self._header_idx += 1
                        if attempt < self.max_retries:
                            print(f"[WARN] 403 Forbidden on {url} - rotating browser headers (attempt {attempt}/{self.max_retries})")
                            await asyncio.sleep(2.0 * attempt)
                            continue
                        raise PermissionError(
                            f"HTTP 403 Forbidden: {domain} blocked access (Cloudflare geo/datacenter protection active). "
                            "Try another site (e.g. gadgetandgear.com, custommacbd.com, books.toscrape.com) or configure a SCRAPER_PROXY."
                        )

                    response.raise_for_status()
                    return response
                except PermissionError:
                    raise
                except (httpx.HTTPError, httpx.TimeoutException) as exc:
                    if attempt == self.max_retries:
                        raise exc
                    await asyncio.sleep(1.5 * attempt)

        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries} attempts")

    async def post(self, url: str, json: dict = None) -> httpx.Response:
        await self._pacing()
        return await self.client.post(url, json=json)

    async def close(self):
        if self._curl_session is not None:
            try:
                await self._curl_session.close()
            except Exception:
                pass
        if self._scraper_client is not None:
            try:
                await self._scraper_client.aclose()
            except Exception:
                pass
        await self.client.aclose()
