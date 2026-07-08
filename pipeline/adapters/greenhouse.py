"""Greenhouse public job-board API.

Greenhouse is an ATS (Applicant Tracking System) - software companies use to
manage hiring. Thousands of companies host their careers page on it, and it
exposes each company's postings as public JSON: one adapter, many companies.

Config needs options.board_token (the slug from the company's careers page,
e.g. https://boards.greenhouse.io/<board_token>).
"""
from __future__ import annotations

from ..models import Opportunity, normalize_date

# {token} is filled in with str.format() at fetch time
API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse(source, payload: dict) -> list[Opportunity]:
    """Convert Greenhouse's JSON shape into our Opportunity objects.

    Greenhouse returns: {"jobs": [{"title": ..., "absolute_url": ...,
    "location": {"name": ...}, "departments": [{"name": ...}]}, ...]}
    """
    opportunities = []
    for job in payload.get("jobs", []):
        # list comprehension with a condition: collect department names,
        # skipping any that are empty/missing
        departments = [d.get("name", "") for d in job.get("departments", []) if d.get("name")]
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=job.get("title", ""),
            org=source.org or source.name,
            # "(x or {}).get(...)" safely handles location being null in JSON:
            # None.get() would crash, {}.get() returns the default
            location=(job.get("location") or {}).get("name", ""),
            url=job.get("absolute_url", ""),
            posted_date=normalize_date(job.get("updated_at")),
            tags=departments,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    token = source.options["board_token"]
    response = client.get(API_URL.format(token=token))
    return parse(source, response.json())
