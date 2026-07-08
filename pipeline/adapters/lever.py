"""Lever public postings API.

Lever is another ATS like Greenhouse; its public feed is a JSON *list*
(Greenhouse wraps its list in an object - every API is a little different,
which is exactly why each gets its own small adapter).

Config needs options.company (the slug from https://jobs.lever.co/<company>).
"""
from __future__ import annotations

from ..models import Opportunity, normalize_date

API_URL = "https://api.lever.co/v0/postings/{company}?mode=json"


def parse(source, payload: list) -> list[Opportunity]:
    opportunities = []
    for job in payload:
        categories = job.get("categories") or {}
        # Build tags from whichever of team/commitment exist (None is skipped)
        tags = [v for v in (categories.get("team"), categories.get("commitment")) if v]
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=job.get("text", ""),          # Lever calls the title "text"
            org=source.org or source.name,
            location=categories.get("location", ""),
            url=job.get("hostedUrl", ""),
            # [:1000] slices to the first 1000 chars - descriptions can be
            # huge and we only need enough for keyword matching/preview
            description=(job.get("descriptionPlain") or "")[:1000],
            # createdAt is epoch milliseconds; normalize_date handles that
            posted_date=normalize_date(job.get("createdAt")),
            tags=tags,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(API_URL.format(company=source.options["company"]))
    return parse(source, response.json())
