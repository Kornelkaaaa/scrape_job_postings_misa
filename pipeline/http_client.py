"""Polite HTTP client: shared session, proper User-Agent, per-request delay,
and robots.txt checks for scraped (non-API) sources.

LEARNING NOTES — being a good citizen when scraping:
- User-Agent: a header identifying WHO is making the request. Honest bots
  announce themselves and include contact info, so a site admin seeing our
  traffic can email us instead of just blocking us.
- Rate limiting: hammering a site with rapid requests can degrade it for
  real users (and gets you banned). We wait delay_seconds between requests.
- robots.txt: a file every site can publish saying which paths bots may
  visit. APIs made for programmatic access don't need the check; scraped
  HTML pages do.
- requests.Session: reuses one TCP/TLS connection across requests to the
  same host (faster) and carries shared headers.
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "MISA-OpportunityBot/0.1 (+student association newsletter; contact: kornelia.buszka@gmail.com)"
)


class PoliteClient:
    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, delay_seconds: float = 2.0,
                 timeout: int = 30):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        # Cache one robots.txt parser per site so we fetch each only once
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        """Sleep just long enough that requests are >= delay_seconds apart.

        time.monotonic() is a clock that only moves forward (unaffected by
        the system clock being changed) - always use it for measuring
        elapsed time, and time.time() only for wall-clock timestamps.
        """
        wait = self.delay_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def allowed_by_robots(self, url: str) -> bool:
        # origin = scheme + host, e.g. "https://example.com"
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        parser = self._robots_cache.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser(origin + "/robots.txt")
            try:
                parser.read()
            except Exception:
                # unreachable robots.txt -> assume allowed, stay polite via delay
                parser.allow_all = True
            self._robots_cache[origin] = parser
        return parser.can_fetch(self.session.headers["User-Agent"], url)

    def get(self, url: str, check_robots: bool = False, **kwargs) -> requests.Response:
        """GET a URL politely. **kwargs passes extras (params=, headers=)
        straight through to requests - a common 'wrapper' pattern."""
        if check_robots and not self.allowed_by_robots(url):
            raise PermissionError(f"robots.txt disallows fetching {url}")
        self._throttle()
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        # raise_for_status turns HTTP errors (404, 500...) into Python
        # exceptions, so callers can't accidentally parse an error page
        response.raise_for_status()
        return response

    def post_json(self, url: str, payload: dict, **kwargs) -> dict:
        """POST a JSON body (some job APIs, e.g. Workday, are POST-only)."""
        self._throttle()
        # json=payload sets the Content-Type header AND serializes the dict
        response = self.session.post(url, json=payload, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response.json()
