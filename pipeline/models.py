"""Core data model. Opportunity-type-agnostic: jobs, hackathons, conferences, etc."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

OPPORTUNITY_TYPES = ("job", "hackathon", "conference", "other")

# query params that vary per-visit and must not affect dedupe
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ref$|source$)")


def normalize_url(url: str) -> str:
    """Canonical form of a URL used for deduplication."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k.lower())]
    return urlunsplit((
        "https",
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        urlencode(query),
        "",
    ))


def normalize_date(value) -> str | None:
    """Best-effort conversion of assorted source date formats to YYYY-MM-DD."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # epoch millis vs seconds
        ts = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


@dataclass
class Opportunity:
    opportunity_type: str
    source: str
    title: str
    org: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    posted_date: str | None = None  # YYYY-MM-DD
    tags: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        """URL-based when possible, else a content hash."""
        normalized = normalize_url(self.url)
        if normalized:
            return normalized
        raw = f"{self.title.lower().strip()}|{self.org.lower().strip()}|{self.posted_date or ''}"
        return "hash:" + hashlib.sha256(raw.encode()).hexdigest()
