"""Greenhouse public job-board API.

Config needs options.board_token (the slug from the company's careers page,
e.g. https://boards.greenhouse.io/<board_token>).
"""
from __future__ import annotations

from ..models import Opportunity, normalize_date

API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse(source, payload: dict) -> list[Opportunity]:
    opportunities = []
    for job in payload.get("jobs", []):
        departments = [d.get("name", "") for d in job.get("departments", []) if d.get("name")]
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=job.get("title", ""),
            org=source.org or source.name,
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
