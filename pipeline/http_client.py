"""Polite HTTP client: shared session, proper User-Agent, per-request delay,
and robots.txt checks for scraped (non-API) sources."""
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
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        wait = self.delay_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def allowed_by_robots(self, url: str) -> bool:
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
        if check_robots and not self.allowed_by_robots(url):
            raise PermissionError(f"robots.txt disallows fetching {url}")
        self._throttle()
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    def post_json(self, url: str, payload: dict, **kwargs) -> dict:
        """POST a JSON body (some job APIs, e.g. Workday, are POST-only)."""
        self._throttle()
        response = self.session.post(url, json=payload, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response.json()
