"""Generic RSS/Atom feed adapter (job boards, event feeds, association news)."""
from __future__ import annotations

import feedparser

from ..models import Opportunity, normalize_date


def parse(source, payload: str) -> list[Opportunity]:
    feed = feedparser.parse(payload)
    opportunities = []
    for entry in feed.entries:
        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
        # non-standard feed fields some boards use (WeWorkRemotely: region/country)
        location = entry.get("location") or entry.get("region") or entry.get("country") or ""
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=entry.get("title", ""),
            org=source.org or entry.get("author", "") or source.name,
            location=location,
            url=entry.get("link", ""),
            description=(entry.get("summary") or "")[:1000],
            posted_date=normalize_date(entry.get("published") or entry.get("updated")),
            tags=tags,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url)
    return parse(source, response.text)
