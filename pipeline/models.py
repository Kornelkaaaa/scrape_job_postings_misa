"""Core data model. Opportunity-type-agnostic: jobs, hackathons, conferences, etc.

LEARNING NOTES — what this file demonstrates:
- @dataclass: a Python shortcut that auto-generates __init__, __repr__, etc.
  from the field list, so we don't write boilerplate constructor code.
- @property: a method that *looks like* an attribute when accessed
  (opp.dedupe_key, no parentheses) but runs code to compute its value.
- URL parsing with urllib: never manipulate URLs with string slicing;
  the standard library splits them into proper parts for you.
- Hashing (sha256): turning arbitrary text into a fixed-length "fingerprint"
  string. Same input -> same hash, so it works as a deduplication key.
"""
from __future__ import annotations  # lets us write list[str] on older Pythons

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# The pipeline is designed to handle more than jobs. Every stored row has one
# of these types, which is how the newsletter knows to build separate sections.
OPPORTUNITY_TYPES = ("job", "hackathon", "conference", "other")

# Pre-compiled regex for query params that vary per-visit (tracking junk).
# "utm_source=newsletter" tells the site where a click came from - it doesn't
# identify the job, so two URLs differing only in utm_* are the SAME posting.
# re.compile() once at import time is faster than re-parsing the pattern
# on every call.
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ref$|source$)")


def normalize_url(url: str) -> str:
    """Canonical form of a URL used for deduplication.

    Example: 'HTTP://Example.com/jobs/1/?utm_source=x' and
             'https://example.com/jobs/1' both normalize to the same string,
    so the dedupe logic treats them as one posting.
    """
    if not url:
        return ""
    # urlsplit breaks a URL into (scheme, netloc, path, query, fragment)
    parts = urlsplit(url.strip())
    # parse_qsl turns "a=1&b=2" into [("a","1"), ("b","2")] - a list we can filter
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k.lower())]
    # urlunsplit reassembles the pieces; we force https, lowercase the host,
    # drop any trailing slash, and drop the #fragment (last arg = "")
    return urlunsplit((
        "https",
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        urlencode(query),
        "",
    ))


def normalize_date(value) -> str | None:
    """Best-effort conversion of assorted source date formats to YYYY-MM-DD.

    Every job board formats dates differently:
    - Lever sends epoch milliseconds (1782864000000 = ms since Jan 1 1970)
    - Greenhouse sends ISO 8601 ("2026-07-01T10:30:00-04:00")
    - RSS feeds send RFC 822 ("Wed, 01 Jul 2026 09:00:00 GMT")
    This function tries each format in turn and returns None if all fail -
    returning None instead of raising means one weird date never crashes a run.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # Heuristic: epoch SECONDS for today are ~1.7e9, epoch MILLISECONDS
        # ~1.7e12. Anything over 1e11 must be milliseconds, so divide by 1000.
        ts = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    # Attempt 1: ISO 8601. Python's fromisoformat doesn't accept the 'Z'
    # (Zulu/UTC) suffix on all versions, so we swap it for +00:00 first.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass  # not ISO - fall through and try the next format
    # Attempt 2: RFC 822, the format used inside RSS/email headers
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


@dataclass
class Opportunity:
    """One job/hackathon/conference posting, as a plain in-memory object.

    Adapters produce these; the DB layer stores them. Fields with defaults
    can be omitted when constructing: Opportunity(opportunity_type="job",
    source="X", title="Y") is valid.
    """
    opportunity_type: str
    source: str          # which sources.yaml entry found this
    title: str
    org: str = ""        # the employer/organizer
    location: str = ""
    url: str = ""
    description: str = ""
    posted_date: str | None = None  # YYYY-MM-DD, or None when unknown
    # NOTE: mutable defaults (like []) must use field(default_factory=list) -
    # a plain "tags: list = []" would make every instance SHARE one list,
    # a classic Python gotcha.
    tags: list[str] = field(default_factory=list)
    # Adapters set this when their URLs are unstable (e.g. Adzuna serves the
    # same job under multiple ad ids); it wins over URL-based dedupe.
    dedupe_override: str | None = None

    @property
    def dedupe_key(self) -> str:
        """The identity string used to decide 'have we seen this before?'.

        Priority: adapter override first, then normalized URL, else a
        content hash. Accessed like an attribute: opp.dedupe_key
        """
        if self.dedupe_override:
            return self.dedupe_override
        normalized = normalize_url(self.url)
        if normalized:
            return normalized
        # No URL at all: fingerprint the content instead. The "|" separator
        # prevents ("ab","c") and ("a","bc") from producing the same string.
        raw = f"{self.title.lower().strip()}|{self.org.lower().strip()}|{self.posted_date or ''}"
        return "hash:" + hashlib.sha256(raw.encode()).hexdigest()
