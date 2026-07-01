"""
HTTP session wrapper for play.usaultimate.org.

play.usaultimate.org is a classic ASP.NET WebForms site.  Every POST that
drives a GridView pager or filter must carry back the three hidden ASP.NET
fields (__VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION) from the most
recent page response.  This module maintains that state across requests and
provides polite rate-limiting and an on-disk response cache.
"""

import hashlib
import html as ihtml
import json
import logging
import os
import time
import random

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://play.usaultimate.org"

# A real browser UA is required; the server silently refuses/resets bot UAs.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

logger = logging.getLogger(__name__)


class USAUClient:
    """
    Thin wrapper around requests.Session for play.usaultimate.org.

    Attributes
    ----------
    session:       underlying requests.Session
    viewstate:     dict of the three ASP.NET hidden fields, updated after
                   every response
    cache_dir:     path to on-disk cache (None = disabled)
    delay:         base delay in seconds between requests
    """

    def __init__(self, cache_dir: str | None = "./cache", delay: float = 1.5):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.viewstate: dict[str, str] = {}
        self.cache_dir = cache_dir
        self.delay = delay
        self._last_request_time = 0.0

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, path: str, params: dict | None = None, *, use_cache: bool = True) -> BeautifulSoup:
        """GET a path (relative to BASE_URL), return a BeautifulSoup."""
        url = BASE_URL + path
        cache_key = self._cache_key("GET", url, params)
        cached = self._load_cache(cache_key) if use_cache else None
        if cached is not None:
            logger.debug("cache hit  GET %s", url)
            soup = BeautifulSoup(cached, "lxml")
            self._update_viewstate(soup)
            return soup

        self._throttle()
        resp = self._request_with_retry("GET", url, params=params)
        self._save_cache(cache_key, resp.text)
        soup = BeautifulSoup(resp.content, "lxml")
        self._update_viewstate(soup)
        return soup

    def post(
        self,
        path: str,
        extra_fields: dict,
        *,
        use_cache: bool = True,
    ) -> BeautifulSoup:
        """
        POST a path with the current ASP.NET viewstate merged with
        *extra_fields*.  Returns a BeautifulSoup and refreshes the stored
        viewstate from the response.
        """
        url = BASE_URL + path
        data = {**self.viewstate, **extra_fields}
        cache_key = self._cache_key("POST", url, data)
        cached = self._load_cache(cache_key) if use_cache else None
        if cached is not None:
            logger.debug("cache hit  POST %s  %s", url, _short(extra_fields))
            soup = BeautifulSoup(cached, "lxml")
            self._update_viewstate(soup)
            return soup

        self._throttle()
        resp = self._request_with_retry("POST", url, data=data)
        self._save_cache(cache_key, resp.text)
        soup = BeautifulSoup(resp.content, "lxml")
        self._update_viewstate(soup)
        return soup

    def clear_cache(self) -> None:
        """Delete all cached responses."""
        if self.cache_dir and os.path.isdir(self.cache_dir):
            for f in os.listdir(self.cache_dir):
                if f.endswith(".json"):
                    os.remove(os.path.join(self.cache_dir, f))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_viewstate(self, soup: BeautifulSoup) -> None:
        """Extract the three ASP.NET hidden fields from a parsed page."""
        for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            tag = soup.find("input", {"id": name})
            if tag:
                self.viewstate[name] = ihtml.unescape(tag.get("value", ""))

    def _throttle(self) -> None:
        """Sleep enough to keep inter-request gap >= delay (+random jitter)."""
        elapsed = time.time() - self._last_request_time
        wait = self.delay + random.uniform(0.2, 0.8) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_time = time.time()

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Retry up to 4 times on network errors / 5xx, with exponential back-off."""
        backoff = 2.0
        last_exc = None
        for attempt in range(4):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                if resp.status_code == 200:
                    return resp
                if resp.status_code < 500:
                    resp.raise_for_status()
                logger.warning(
                    "HTTP %s on attempt %d for %s %s; retrying in %.1fs",
                    resp.status_code, attempt + 1, method, url, backoff,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                logger.warning(
                    "Network error on attempt %d for %s %s: %s; retrying in %.1fs",
                    attempt + 1, method, url, exc, backoff,
                )
                last_exc = exc
            time.sleep(backoff)
            backoff *= 2
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Failed to fetch {method} {url} after 4 attempts")

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, method: str, url: str, data: dict | None) -> str:
        payload = json.dumps({"m": method, "u": url, "d": data or {}}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key + ".json")

    def _load_cache(self, key: str) -> str | None:
        if not self.cache_dir:
            return None
        path = self._cache_path(key)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)["html"]
        return None

    def _save_cache(self, key: str, html: str) -> None:
        if not self.cache_dir:
            return
        path = self._cache_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"html": html}, f)


def _short(d: dict) -> str:
    """One-line summary of a dict for log messages."""
    items = {k: v for k, v in d.items() if not k.startswith("__")}
    return str(items)[:120]
