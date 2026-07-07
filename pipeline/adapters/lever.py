"""Lever public postings API.

Config needs options.company (the slug from https://jobs.lever.co/<company>).
"""
from __future__ import annotations

from ..models import Opportunity, normalize_date

API_URL = "https://api.lever.co/v0/postings/{company}?mode=json"


def parse(source, payload: list) -> list[Opportunity]:
    opportunities = []
    for job in payload:
        categories = job.get("categories") or {}
        tags = [v for v in (categories.get("team"), categories.get("commitment")) if v]
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=job.get("text", ""),
            org=source.org or source.name,
            location=categories.get("location", ""),
            url=job.get("hostedUrl", ""),
            description=(job.get("descriptionPlain") or "")[:1000],
            posted_date=normalize_date(job.get("createdAt")),
            tags=tags,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(API_URL.format(company=source.options["company"]))
    return parse(source, response.json())
