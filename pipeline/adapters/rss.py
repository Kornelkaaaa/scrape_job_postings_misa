"""Generic RSS/Atom feed adapter (job boards, event feeds, association news).

RSS is a decades-old XML format for publishing "what's new" - blogs, podcasts,
and many job boards still offer it. The feedparser library handles all the
messy XML variants for us and gives back simple dict-like entries.
"""
from __future__ import annotations

import feedparser

from ..models import Opportunity, normalize_date


def parse(source, payload: str) -> list[Opportunity]:
    # feedparser accepts a raw XML string and never raises on bad input -
    # it just returns fewer/no entries (check feed.bozo for parse problems)
    feed = feedparser.parse(payload)
    opportunities = []
    for entry in feed.entries:
        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
        # Non-standard feed fields some boards use (WeWorkRemotely publishes
        # <region>USA Only</region> - which is who may apply, exactly what
        # our location filter needs). "a or b or c" returns the first truthy.
        location = entry.get("location") or entry.get("region") or entry.get("country") or ""
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=entry.get("title", ""),
            org=source.org or entry.get("author", "") or source.name,
            location=location,
            url=entry.get("link", ""),
            description=(entry.get("summary") or "")[:1000],
            # RSS dates are RFC 822 ("Wed, 01 Jul 2026 09:00:00 GMT")
            posted_date=normalize_date(entry.get("published") or entry.get("updated")),
            tags=tags,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url)
    return parse(source, response.text)
